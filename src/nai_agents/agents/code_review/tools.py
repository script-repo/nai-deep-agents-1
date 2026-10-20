"""Tools for the multi-model code review agent: load a diff, static checks, per-model reviews, reconcile.

Each candidate model from the catalog reviews the same diff. With a real provider the model is asked for
JSON findings and the answer is parsed defensively. With the mock provider, a model's review is derived
from the static heuristics: each model "notices" each finding with a deterministic, skill-dependent
probability (`core.mock.stable_fraction`), may be a line off, may rate it one level lower and, if weak,
may add a false positive. Reconciliation merges findings by file + category + line (±2).
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated, Any

from deepagents.backends.utils import create_file_data
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.types import Command

from nai_agents.core.data import (
    PathError,
    load_json_state,
    read_text,
    resolve_input,
    save_json_state,
    to_json,
)
from nai_agents.core.mock import stable_fraction
from nai_agents.core.models import CatalogModel, build_candidate, find_model, load_catalog

DEFAULT_DIFF = "code_review/sample.diff"
DEFAULT_PROJECT = "code_review/sample_project"
REVIEW_FILE = "/review.md"
SEVERITIES = ["critical", "high", "medium", "low", "info"]
LINE_TOLERANCE = 2
MAX_DIFF_CHARS = 60_000
TEST_TIMEOUT_MAX_S = 1800

# Commands run_tests accepts (first words). No shell is ever used.
ALLOWED_TEST_COMMANDS = [
    ["pytest"],
    ["python", "-m", "pytest"],
    ["uv", "run", "pytest"],
    ["npm", "test"],
    ["npm", "run", "test"],
    ["pnpm", "test"],
    ["yarn", "test"],
    ["go", "test"],
    ["cargo", "test"],
]
SHELL_META = re.compile(r"[;&|`$<>\n\\]")


# ---- diff parsing -------------------------------------------------------------------------------


@dataclass
class DiffFile:
    path: str
    added: list[tuple[int, str]] = field(default_factory=list)
    removed: int = 0

    @property
    def is_test(self) -> bool:
        name = Path(self.path).name
        return (
            "/tests/" in f"/{self.path}"
            or "/test/" in f"/{self.path}"
            or name.startswith("test_")
            or bool(re.search(r"(_test\.py|\.test\.[jt]sx?|\.spec\.[jt]sx?|_test\.go)$", name))
        )

    @property
    def language(self) -> str:
        return {".py": "python", ".js": "js", ".jsx": "js", ".ts": "js", ".tsx": "js", ".go": "go"}.get(
            Path(self.path).suffix, "other"
        )


def parse_diff(text: str) -> list[DiffFile]:
    """Parse a unified (git) diff into files with their added lines and new-file line numbers."""
    files: list[DiffFile] = []
    current: DiffFile | None = None
    new_line = 0
    for raw in text.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].strip().split("\t")[0]
            if path == "/dev/null":
                current = None
                continue
            current = DiffFile(path=path[2:] if path.startswith("b/") else path)
            files.append(current)
            continue
        if raw.startswith("--- ") or raw.startswith("diff --git") or raw.startswith("index "):
            continue
        hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
        if hunk:
            new_line = int(hunk.group(1))
            continue
        if current is None:
            continue
        if raw.startswith("+"):
            current.added.append((new_line, raw[1:]))
            new_line += 1
        elif raw.startswith("-"):
            current.removed += 1
        elif raw.startswith(" ") or raw == "":
            new_line += 1
    return files


def _git_diff(repo: Path) -> str:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git is not installed")
    proc = subprocess.run(
        [git, "-C", str(repo), "--no-pager", "diff", "--no-color", "--no-ext-diff", "HEAD"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git diff failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def _load(source: str) -> tuple[str, list[DiffFile], str]:
    path = resolve_input(source or DEFAULT_DIFF, subdir="code_review")
    if path.is_dir():
        if not (path / ".git").exists():
            raise PathError(f"{source!r} is a folder but not a git repository")
        text = _git_diff(path)
    else:
        text = read_text(str(path))
    if len(text) > MAX_DIFF_CHARS:
        text = text[:MAX_DIFF_CHARS]
    digest = hashlib.sha256(text.encode()).hexdigest()[:12]
    return text, parse_diff(text), digest


_DEF_RE = re.compile(
    r"^\s*(?:async\s+)?def\s+([A-Za-z]\w*)\s*\(|^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)"
)


def load_diff(source: str = DEFAULT_DIFF) -> str:
    """Load a diff: a .diff/.patch file, or a git repository folder (its `git diff HEAD`), under the examples
    or data directory. Returns files changed, added/removed line counts, new functions and the raw diff."""
    text, files, digest = _load(source)
    return to_json(
        {
            "source": source or DEFAULT_DIFF,
            "diff_id": digest,
            "files": [
                {
                    "path": f.path,
                    "added": len(f.added),
                    "removed": f.removed,
                    "is_test": f.is_test,
                    "new_functions": [
                        m.group(1) or m.group(2) for _, s in f.added if (m := _DEF_RE.match(s))
                    ],
                }
                for f in files
            ],
            "diff": text[:20_000],
        }
    )


# ---- static checks -----------------------------------------------------------------------------


SQL_RE = re.compile(r"(?i)\b(select|insert|update|delete)\b.*\b(from|into|set|where)\b")
CONCAT_RE = re.compile(r"""["']\s*\+|\+\s*["']|\bf["'][^"']*\{|\.format\(|["']\s*%\s*[\w(]""")
SECRET_RES = [
    re.compile(
        r"""(?i)\b\w*(api[_-]?key|secret|token|passw(?:or)?d|private[_-]?key)\w*\s*[:=]\s*["'][^"'\s]{12,}["']"""
    ),
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

RULES: dict[str, dict[str, str]] = {
    "sql-injection": {
        "severity": "high",
        "message": "SQL built by string concatenation/interpolation; use parameterized queries.",
    },
    "hardcoded-secret": {
        "severity": "critical",
        "message": "Hard-coded credential in source; load it from "
        "the environment or a secret store and rotate it.",
    },
    "bare-except": {
        "severity": "medium",
        "message": "Bare `except:` swallows every error (even KeyboardInterrupt); catch specific exceptions.",
    },
    "tls-verify-disabled": {"severity": "high", "message": "TLS certificate verification is disabled."},
    "shell-injection": {
        "severity": "high",
        "message": "Shell command execution with possibly untrusted input.",
    },
    "eval-exec": {"severity": "high", "message": "eval/exec on dynamic input."},
    "debug-print": {"severity": "low", "message": "Debug print left in production code; use logging."},
    "missing-test": {"severity": "medium", "message": "New function has no test in this change."},
    "todo": {"severity": "info", "message": "TODO/FIXME left in the change."},
}


@dataclass
class Finding:
    file: str
    line: int
    category: str
    severity: str
    message: str
    snippet: str = ""
    source: str = "static"

    @property
    def key(self) -> str:
        return f"{self.category}:{self.file}:{self.line}"


def _mask(line: str) -> str:
    for rx in SECRET_RES:
        line = rx.sub(lambda m: m.group(0)[:8] + "…[redacted]", line)
    return line.strip()[:160]


def _f(rule: str, df: DiffFile, line: int, text: str, extra: str = "") -> Finding:
    meta = RULES[rule]
    return Finding(df.path, line, rule, meta["severity"], meta["message"] + extra, _mask(text))


def _static(files: Iterable[DiffFile]) -> list[Finding]:
    files = list(files)
    out: list[Finding] = []
    test_text = "\n".join(s for f in files if f.is_test for _, s in f.added)
    for df in files:
        for no, text in df.added:
            code = text.split("#", 1)[0] if df.language == "python" else text
            if SQL_RE.search(code) and CONCAT_RE.search(code):
                out.append(_f("sql-injection", df, no, text))
            if any(rx.search(text) for rx in SECRET_RES) and not re.search(
                r"environ|getenv|process\.env", text
            ):
                out.append(_f("hardcoded-secret", df, no, text))
            if df.language == "python" and re.match(r"^\s*except\s*:", text):
                out.append(_f("bare-except", df, no, text))
            if re.search(
                r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify:\s*true", code
            ):
                out.append(_f("tls-verify-disabled", df, no, text))
            if re.search(r"shell\s*=\s*True|\bos\.system\(|child_process\.exec\(", code):
                out.append(_f("shell-injection", df, no, text))
            if re.search(r"(?<![\w.])(eval|exec)\(", code):
                out.append(_f("eval-exec", df, no, text))
            if not df.is_test and (
                (df.language == "python" and re.match(r"^\s*print\(", text))
                or (df.language == "js" and "console.log(" in text)
            ):
                out.append(_f("debug-print", df, no, text))
            if re.search(r"\b(TODO|FIXME)\b", text):
                out.append(_f("todo", df, no, text))
            m = _DEF_RE.match(text)
            if m and not df.is_test and df.language != "other":
                name = m.group(1) or m.group(2)
                if not name.startswith("_") and not re.search(rf"\b{re.escape(name)}\b", test_text):
                    out.append(_f("missing-test", df, no, text, f" (`{name}`)"))
    return out


def static_checks(source: str = DEFAULT_DIFF) -> str:
    """Deterministic heuristics over the added lines of a diff, each with file and line: SQL built by string
    concatenation, hard-coded secrets (masked), bare except, disabled TLS verification, shell/eval use,
    debug prints, TODOs and new functions without tests."""
    _, files, digest = _load(source)
    findings = _static(files)
    return to_json({"diff_id": digest, "count": len(findings), "findings": [asdict(f) for f in findings]})


# ---- per-model review --------------------------------------------------------------------------


REVIEW_SYSTEM = (
    """You are a meticulous senior code reviewer. Review ONLY the added lines of the diff.
Report real problems: security (injection, secrets, TLS), correctness, error handling, missing tests.
Answer with JSON only, no prose, in this shape:
{"findings": [{"file": "path", "line": 12, "category": "sql-injection", "severity": "high",
"message": "one sentence"}]}
Categories: """
    + ", ".join(RULES)
    + """, bug, performance, style.
Severities: critical, high, medium, low, info. Line numbers are new-file line numbers as shown."""
)

CATEGORY_SYNONYMS = {
    "sql": "sql-injection",
    "injection": "sql-injection",
    "secret": "hardcoded-secret",
    "credential": "hardcoded-secret",
    "api key": "hardcoded-secret",
    "except": "bare-except",
    "exception": "bare-except",
    "tls": "tls-verify-disabled",
    "ssl": "tls-verify-disabled",
    "certificate": "tls-verify-disabled",
    "test": "missing-test",
    "print": "debug-print",
    "logging": "debug-print",
    "shell": "shell-injection",
    "eval": "eval-exec",
    "todo": "todo",
}


def _norm_category(value: str) -> str:
    v = str(value or "").strip().lower().replace("_", "-")
    if v in RULES or v in {"bug", "performance", "style"}:
        return v
    for word, cat in CATEGORY_SYNONYMS.items():
        if word in v:
            return cat
    return v or "bug"


def _norm_severity(value: str) -> str:
    v = str(value or "").strip().lower()
    aliases = {
        "blocker": "critical",
        "major": "high",
        "error": "high",
        "warning": "medium",
        "minor": "low",
        "nit": "info",
        "suggestion": "info",
        "moderate": "medium",
    }
    v = aliases.get(v, v)
    return v if v in SEVERITIES else "medium"


def parse_findings(text: str, model_name: str = "model") -> list[Finding]:
    """Robustly pull findings out of a model answer: JSON object/array, fenced or not, trailing commas,
    or as a last resort `path:line: severity: message` lines."""
    raw = re.sub(r"```(?:json)?", "", text or "").strip()
    data: Any = None
    for candidate in (raw, *(m.group(0) for m in re.finditer(r"\{.*\}|\[.*\]", raw, flags=re.S))):
        for attempt in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):
            try:
                data = json.loads(attempt)
                break
            except (json.JSONDecodeError, TypeError):
                continue
        if data is not None:
            break
    items: list[Any] = []
    if isinstance(data, dict):
        items = data.get("findings") or data.get("issues") or data.get("comments") or []
    elif isinstance(data, list):
        items = data
    out: list[Finding] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            line = int(str(item.get("line", item.get("line_number", 0))).split("-")[0])
        except ValueError:
            line = 0
        file = str(item.get("file") or item.get("path") or "").removeprefix("b/")
        if not file:
            continue
        out.append(
            Finding(
                file,
                line,
                _norm_category(item.get("category") or item.get("type") or ""),
                _norm_severity(item.get("severity") or item.get("level") or ""),
                str(item.get("message") or item.get("description") or "")[:400],
                source=model_name,
            )
        )
    if not out and data is None:
        for m in re.finditer(
            r"^\s*[-*]?\s*([\w./-]+\.\w+):(\d+):?\s*(?:\[?(\w+)\]?:?)?\s*(.+)$", raw, flags=re.M
        ):
            out.append(
                Finding(
                    m.group(1),
                    int(m.group(2)),
                    _norm_category(m.group(4)),
                    _norm_severity(m.group(3)),
                    m.group(4)[:400],
                    source=model_name,
                )
            )
    return out


def _review_mock(entry: CatalogModel, files: list[DiffFile], digest: str) -> list[Finding]:
    found: list[Finding] = []
    skill = min(entry.sim_skill, 1.15)
    bonus = {"critical": 0.3, "high": 0.18, "medium": 0.05, "low": -0.1, "info": -0.25}
    for f in _static(files):
        p = 0.2 + 0.6 * skill / 1.15 + bonus[f.severity]
        if stable_fraction(entry.name, entry.sim_version, f.key) >= p:
            continue
        offset = int(stable_fraction(entry.name, f.key, "line") * 3) - 1
        sev = f.severity
        if stable_fraction(entry.name, f.key, "sev") < 0.2 and sev != "info":
            sev = SEVERITIES[SEVERITIES.index(sev) + 1]
        found.append(
            Finding(f.file, max(1, f.line + offset), f.category, sev, f.message, f.snippet, entry.name)
        )
    # Weaker models sometimes flag something that isn't there.
    added = [(df, no, s) for df in files if not df.is_test for no, s in df.added if s.strip()]
    if added and stable_fraction(entry.name, digest, "fp") < max(0.0, 1.0 - skill) * 1.5:
        df, no, s = added[int(stable_fraction(entry.name, digest, "fp-line") * len(added))]
        found.append(
            Finding(df.path, no, "style", "low", "Consider renaming for clarity.", _mask(s), entry.name)
        )
    return found


def _review_real(model: BaseChatModel, text: str, model_name: str, focus: str = "") -> list[Finding]:
    numbered = []
    for df in parse_diff(text):
        numbered.append(f"### {df.path}")
        numbered += [f"{no:>5} + {s}" for no, s in df.added]
    prompt = (
        ("Focus: " + focus + "\n\n" if focus else "")
        + "Added lines with new-file line numbers:\n\n"
        + "\n".join(numbered)[:MAX_DIFF_CHARS]
    )
    msg = model.invoke([SystemMessage(content=REVIEW_SYSTEM), HumanMessage(content=prompt)])
    content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
    return parse_findings(content, model_name)


def _findings_file(digest: str, model_name: str) -> str:
    safe = re.sub(r"[^\w.-]", "_", model_name)
    return f"code_review/findings/{digest}/{safe}.json"


def list_review_models() -> str:
    """Candidate reviewer models from the catalog (name, provider, price per Mtok)."""
    return to_json([m.public() for m in load_catalog()])


def review_with_model(model_name: str, source: str = DEFAULT_DIFF, focus: str = "") -> str:
    """Have ONE catalog model review the diff and store its findings for reconciliation.

    Real providers get the added lines with line numbers and must answer in JSON (parsed defensively);
    mock models give a deterministic, skill-dependent review. Returns the model's findings.
    """
    entry = find_model(model_name)
    text, files, digest = _load(source)
    if entry.provider == "mock":
        findings = _review_mock(entry, files, digest)
    else:
        findings = _review_real(build_candidate(entry), text, entry.name, focus)
    save_json_state(_findings_file(digest, entry.name), [asdict(f) for f in findings])
    counts = {s: sum(1 for f in findings if f.severity == s) for s in SEVERITIES}
    return to_json(
        {
            "model": entry.name,
            "diff_id": digest,
            "count": len(findings),
            "by_severity": counts,
            "findings": [asdict(f) for f in findings],
        }
    )


# ---- reconciliation ----------------------------------------------------------------------------


def _reconcile(static: list[Finding], per_model: dict[str, list[Finding]]) -> list[dict[str, Any]]:
    models = sorted(per_model)
    clusters: list[dict[str, Any]] = []
    everything = [*static, *(f for fs in per_model.values() for f in fs)]
    everything.sort(key=lambda f: (f.file, f.category, f.line, f.source != "static"))
    for f in everything:
        home = next(
            (
                c
                for c in clusters
                if c["file"] == f.file
                and c["category"] == f.category
                and abs(c["line"] - f.line) <= LINE_TOLERANCE
            ),
            None,
        )
        if home is None:
            home = {
                "file": f.file,
                "line": f.line,
                "category": f.category,
                "severities": [],
                "sources": [],
                "messages": [],
                "snippet": f.snippet,
            }
            clusters.append(home)
        if f.source == "static":
            home["line"] = f.line  # static line numbers are exact
        home["severities"].append(f.severity)
        if f.source not in home["sources"]:
            home["sources"].append(f.source)
        if f.message and f.message not in home["messages"]:
            home["messages"].append(f.message)
        home["snippet"] = home["snippet"] or f.snippet
    out = []
    for c in clusters:
        agreeing = sorted(s for s in c["sources"] if s in models)
        confidence = round(len(agreeing) / len(models), 2) if models else 0.0
        severity = min(c["severities"], key=SEVERITIES.index)
        static_ok = "static" in c["sources"]
        if not static_ok and len(models) >= 2 and len(agreeing) * 2 <= len(models) and severity != "info":
            severity = SEVERITIES[SEVERITIES.index(severity) + 1]  # no majority, no static support: one lower
        out.append(
            {
                "file": c["file"],
                "line": c["line"],
                "category": c["category"],
                "severity": severity,
                "confidence": confidence,
                "models": agreeing,
                "static": static_ok,
                "message": c["messages"][0] if c["messages"] else "",
                "snippet": c["snippet"],
            }
        )
    out.sort(key=lambda x: (SEVERITIES.index(x["severity"]), -x["confidence"], x["file"], x["line"]))
    return out


def render_review(
    merged: list[dict[str, Any]], models: list[str], source: str, tests: dict | None = None
) -> str:
    lines = [
        "# Code review",
        "",
        f"Diff: `{source}` · reviewers: {', '.join(models) or 'static checks only'} + static checks",
        "",
        "| Severity | Count |",
        "|---|---|",
    ]
    lines += [f"| {s} | {sum(1 for x in merged if x['severity'] == s)} |" for s in SEVERITIES]
    for sev in SEVERITIES:
        items = [x for x in merged if x["severity"] == sev]
        if not items:
            continue
        lines += ["", f"## {sev.capitalize()}", ""]
        for x in items:
            who = ", ".join(x["models"]) or "no model"
            lines.append(
                f"- **{x['file']}:{x['line']}** `{x['category']}` — {x['message']} "
                f"(confidence {x['confidence']:.0%}: {who}{'; static check' if x['static'] else ''})"
            )
            if x["snippet"]:
                lines.append(f"  `{x['snippet']}`")
    if tests:
        lines += [
            "",
            "## Tests",
            "",
            f"`{tests.get('command')}` → {tests.get('status')}: {tests.get('summary', '')}",
        ]
    return "\n".join(lines) + "\n"


def reconcile_findings(
    source: str = DEFAULT_DIFF, models: str = "", tool_call_id: Annotated[str, InjectedToolCallId] = ""
) -> Command:
    """Merge every stored model review of this diff with the static checks and write /review.md.

    Findings merge when file and category match and lines are within ±2. Severity is the highest any
    reviewer gave (one level lower when at most half the
    models report it and no static check backs it); confidence is the share of
    models that agree. `models` optionally limits which stored reviews count (comma-separated).
    """
    _, files, digest = _load(source)
    wanted = [m.strip() for m in models.split(",") if m.strip()]
    folder = resolve_or_none(f"code_review/findings/{digest}")
    per_model: dict[str, list[Finding]] = {}
    if folder:
        for path in sorted(folder.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            name = data[0]["source"] if data else path.stem
            if wanted and name not in wanted and path.stem not in wanted:
                continue
            per_model[name] = [Finding(**d) for d in data]
    merged = _reconcile(_static(files), per_model)
    tests = load_json_state("code_review/last_test_run.json", None)
    md = render_review(merged, sorted(per_model), source or DEFAULT_DIFF, tests)
    save_json_state("code_review/reconciled.json", merged)
    summary = {
        "diff_id": digest,
        "models": sorted(per_model),
        "review_file": REVIEW_FILE,
        "count": len(merged),
        "by_severity": {s: sum(1 for x in merged if x["severity"] == s) for s in SEVERITIES},
        "findings": merged,
    }
    return Command(
        update={
            "files": {REVIEW_FILE: create_file_data(md)},
            "messages": [ToolMessage(to_json(summary), tool_call_id=tool_call_id, name="reconcile_findings")],
        }
    )


def resolve_or_none(path: str) -> Path | None:
    try:
        return resolve_input(path)
    except PathError:
        return None


# ---- tests ---------------------------------------------------------------------------------------


def _allowed(argv: list[str]) -> bool:
    return any(argv[: len(prefix)] == prefix for prefix in ALLOWED_TEST_COMMANDS)


def run_tests(cmd: str = "python -m pytest -q", cwd: str = DEFAULT_PROJECT, timeout_s: int = 300) -> str:
    """Run the project's test command. Needs approval.

    Only allow-listed commands (pytest, python -m pytest, uv run pytest, npm/pnpm/yarn test, go test,
    cargo test) with plain arguments; no shell. `cwd` must be a folder under the examples or data directory.
    """
    if SHELL_META.search(cmd):
        raise ValueError("Shell operators are not allowed in the test command")
    argv = shlex.split(cmd)
    if not argv or not _allowed(argv):
        raise ValueError(f"Not an allowed test command: {cmd!r}")
    folder = resolve_input(cwd or DEFAULT_PROJECT, subdir="code_review")
    if not folder.is_dir():
        raise PathError(f"{cwd!r} is not a folder")
    exe = sys.executable if argv[0] == "python" else shutil.which(argv[0])
    result: dict[str, Any] = {"command": cmd, "cwd": folder.name}
    if not exe:
        result.update(status="not_found", summary=f"{argv[0]} is not installed")
    else:
        try:
            proc = subprocess.run(
                [exe, *argv[1:]],
                cwd=folder,
                capture_output=True,
                text=True,
                check=False,
                timeout=min(max(int(timeout_s), 1), TEST_TIMEOUT_MAX_S),
                stdin=subprocess.DEVNULL,
                env={**_safe_env(), "PYTHONDONTWRITEBYTECODE": "1"},
            )
            out = proc.stdout + proc.stderr
            tail = [ln for ln in out.strip().splitlines() if ln.strip()]
            result.update(
                status="passed" if proc.returncode == 0 else "failed",
                exit_code=proc.returncode,
                summary=tail[-1][:300] if tail else "",
                output_tail=out[-4000:],
            )
        except subprocess.TimeoutExpired:
            result.update(status="timeout", summary=f"timed out after {timeout_s}s")
    save_json_state("code_review/last_test_run.json", result)
    return to_json(result)


def _safe_env() -> dict[str, str]:
    import os

    # Tests should not see the app's own secrets.
    return {k: v for k, v in os.environ.items() if not re.search(r"KEY|TOKEN|SECRET|PASSWORD", k, re.I)}


TOOLS = [list_review_models, load_diff, static_checks, review_with_model, reconcile_findings, run_tests]

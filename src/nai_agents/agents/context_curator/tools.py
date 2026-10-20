"""Tools for the context and memory curator: find, audit, prune and diff agent instruction files.

Every root a model passes in is resolved through `core.data.resolve_input`, so the curator can only read
the examples and data directories. Writes (`apply_changes`) go to a copy under the data directory, or in
place only when the root itself already lives under the data directory.
"""

from __future__ import annotations

import difflib
import re
import shutil
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any

from deepagents.backends.utils import create_file_data
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.types import Command

from nai_agents.core.data import PathError, resolve_input, save_json_state, state_path, to_json
from nai_agents.core.settings import get_settings

DEFAULT_ROOT = "context_curator/sample_repo"
DIFF_FILE = "/context-curator.diff"

# Canonical order: the first file that says something owns it; later copies are duplicates.
CONTEXT_PATTERNS = [
    "AGENTS.md",
    "CLAUDE.md",
    ".github/copilot-instructions.md",
    ".cursorrules",
    ".cursor/rules/*.md",
    ".cursor/rules/*.mdc",
    ".windsurfrules",
    "GEMINI.md",
    "CONVENTIONS.md",
    "memory/*.md",
    ".claude/*.md",
    "docs/agents/*.md",
]
MAX_FILE_BYTES = 512_000
STALE_DATE_DAYS = 365
DEFAULT_MAX_FILE_TOKENS = 400

# Mutually exclusive tool choices: two files prescribing different members is a contradiction.
CHOICE_GROUPS: dict[str, list[str]] = {
    "package manager": ["npm", "pnpm", "yarn", "bun"],
    "test runner": ["jest", "vitest", "mocha", "pytest", "unittest"],
    "formatter": ["prettier", "biome", "ruff format"],
    "python env": ["poetry", "uv", "pipenv", "conda"],
}
VERSIONED = ["node", "python", "typescript", "react", "postgres", "java", "go", "next.js"]
# Runtimes past end of life (major version strictly below the value is stale).
EOL_BELOW = {"node": 18, "python": 3.9, "postgres": 13, "java": 11}

PATH_IN_TICKS = re.compile(r"`([^`\s]+)`")
BARE_PATH = re.compile(r"(?<![\w/:.`@-])((?:\.{1,2}/)?[\w.-]+(?:/[\w.-]+)+/?)(?![\w`])")
DATE_RE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
DATE_CONTEXT = re.compile(r"(updated|reviewed|as of|current|valid until|last checked)", re.I)
MARKER_RE = re.compile(r"\b(TODO|FIXME|TBD|XXX)\b")
REMOVAL_WORDS = re.compile(
    r"\b(gone|removed|deleted|moved (?:out|away)|no longer|was renamed|deprecated)\b", re.I
)
VERSION_RE = re.compile(
    r"\b(" + "|".join(re.escape(v) for v in VERSIONED) + r")(?:\s*(?:v|version)?\s*)(\d+(?:\.\d+)?)", re.I
)
POLARITY_RE = re.compile(r"\b(always|never|do not|don't|must not|must|avoid|prefer)\b\s+(.*)", re.I)
STOP = {
    "the",
    "a",
    "an",
    "to",
    "for",
    "and",
    "or",
    "of",
    "in",
    "on",
    "all",
    "any",
    "your",
    "our",
    "with",
    "use",
}


# ---- discovery ------------------------------------------------------------------------------------


def _root(root: str) -> Path:
    path = resolve_input(root or DEFAULT_ROOT, subdir="context_curator")
    if not path.is_dir():
        raise PathError(f"{root!r} is not a directory")
    return path


def _context_files(root: Path) -> list[Path]:
    seen: list[Path] = []
    for pattern in CONTEXT_PATTERNS:
        for path in sorted(root.glob(pattern)):
            real = path.resolve()
            if path.is_file() and real not in seen and root in real.parents:
                seen.append(real)
    return seen


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _tokens(text: str) -> int:
    return len(text) // 4 + (1 if text else 0)


def _read(path: Path) -> str:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise PathError(f"{path.name} is larger than {MAX_FILE_BYTES} bytes")
    return path.read_text(encoding="utf-8", errors="replace")


def _kind(rel: str) -> str:
    if rel.startswith("memory/") or rel.startswith(".claude/"):
        return "memory"
    if "cursor" in rel or "windsurf" in rel:
        return "editor rules"
    if "copilot" in rel:
        return "copilot"
    return "agent instructions"


def find_context_files(root: str = DEFAULT_ROOT) -> str:
    """List the agent context files in a folder (AGENTS.md, CLAUDE.md, .cursorrules,
    .github/copilot-instructions.md, memory/*.md, ...) with size and an estimated token count.

    `root` is a folder under the examples or data directory (default: the sample repo).
    """
    base = _root(root)
    files = []
    for path in _context_files(base):
        text = _read(path)
        files.append(
            {
                "path": _rel(base, path),
                "kind": _kind(_rel(base, path)),
                "lines": text.count("\n") + 1,
                "bytes": len(text.encode()),
                "tokens_est": _tokens(text),
            }
        )
    return to_json(
        {"root": base.name, "files": files, "total_tokens_est": sum(f["tokens_est"] for f in files)}
    )


def read_context_file(path: str, root: str = DEFAULT_ROOT) -> str:
    """Read one context file (relative to `root`) with line numbers, for quoting evidence."""
    base = _root(root)
    real = (base / path).resolve()
    if base not in real.parents or not real.is_file():
        raise PathError(f"{path!r} is not a file under {base.name}")
    lines = _read(real).splitlines()
    return "\n".join(f"{i:>4}  {line}" for i, line in enumerate(lines, start=1))


# ---- analysis -------------------------------------------------------------------------------------


def _norm(line: str) -> str:
    text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line).strip().lower()
    return re.sub(r"\s+", " ", text).rstrip(".")


def _path_refs(line: str) -> list[str]:
    refs = []
    for token in PATH_IN_TICKS.findall(line):
        token = token.strip().rstrip(".,;:")
        if "://" in token or any(ch in token for ch in "*?{}<>$") or token.startswith("-"):
            continue
        if "/" in token or re.fullmatch(r"\.?[\w-]+\.[A-Za-z0-9]{1,5}", token):
            refs.append(token)
    for token in BARE_PATH.findall(PATH_IN_TICKS.sub(" ", line)):
        last = token.rstrip("/").rsplit("/", 1)[-1]
        if "://" not in line[max(0, line.find(token) - 3) : line.find(token)] and (
            "." in last or token.endswith("/")
        ):
            refs.append(token.rstrip(".,;:"))
    return list(dict.fromkeys(refs))


def _exists(ref: str, root: Path, file_dir: Path) -> bool:
    ref = ref.removeprefix("./")
    for base in (root, file_dir):
        cand = (base / ref).resolve()
        if (cand == root or root in cand.parents) and cand.exists():
            return True
    return False


def _stale(root: Path, rel: str, lines: list[str], today: date) -> list[dict[str, Any]]:
    out = []
    file_dir = (root / rel).parent
    for no, line in enumerate(lines, start=1):
        if not REMOVAL_WORDS.search(line):
            for ref in _path_refs(line):
                if not _exists(ref, root, file_dir):
                    out.append(
                        {
                            "file": rel,
                            "line": no,
                            "kind": "missing-path",
                            "ref": ref,
                            "detail": f"`{ref}` does not exist in the repo",
                            "text": line.strip(),
                        }
                    )
        for m in DATE_RE.finditer(line):
            if not DATE_CONTEXT.search(line):
                continue
            try:
                when = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            age = (today - when).days
            if age > STALE_DATE_DAYS:
                out.append(
                    {
                        "file": rel,
                        "line": no,
                        "kind": "old-date",
                        "ref": m.group(0),
                        "detail": f"dated {m.group(0)}, {age} days ago",
                        "text": line.strip(),
                    }
                )
        for m in VERSION_RE.finditer(line):
            name, ver = m.group(1).lower(), float(m.group(2))
            if name in EOL_BELOW and ver < EOL_BELOW[name]:
                out.append(
                    {
                        "file": rel,
                        "line": no,
                        "kind": "old-version",
                        "ref": m.group(0),
                        "detail": f"{m.group(0)} is past end of life",
                        "text": line.strip(),
                    }
                )
        marker = MARKER_RE.search(line)
        if marker:
            out.append(
                {
                    "file": rel,
                    "line": no,
                    "kind": "todo",
                    "ref": marker.group(1),
                    "detail": f"unresolved {marker.group(1)} left in agent context",
                    "text": line.strip(),
                }
            )
    return out


def _evidence(root: Path) -> dict[str, str]:
    """What the repo itself says: lockfiles, .nvmrc, package.json. Used to pick a side in conflicts."""
    ev: dict[str, str] = {}
    locks = {
        "pnpm-lock.yaml": "pnpm",
        "yarn.lock": "yarn",
        "package-lock.json": "npm",
        "bun.lockb": "bun",
        "poetry.lock": "poetry",
        "uv.lock": "uv",
        "Pipfile.lock": "pipenv",
    }
    for lock, tool in locks.items():
        if (root / lock).exists():
            group = "package manager" if tool in CHOICE_GROUPS["package manager"] else "python env"
            ev.setdefault(group, tool)
    nvmrc = root / ".nvmrc"
    if nvmrc.exists():
        m = re.search(r"\d+", nvmrc.read_text(encoding="utf-8"))
        if m:
            ev["node"] = m.group(0)
    pkg = root / "package.json"
    if pkg.exists():
        import json

        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
        pm = str(data.get("packageManager", "")).split("@")[0]
        if pm:
            ev.setdefault("package manager", pm)
        test_script = str(data.get("scripts", {}).get("test", ""))
        for runner in CHOICE_GROUPS["test runner"]:
            if re.search(rf"\b{runner}\b", test_script):
                ev.setdefault("test runner", runner)
        node = re.search(r"\d+", str(data.get("engines", {}).get("node", "")))
        if node:
            ev.setdefault("node", node.group(0))
    return ev


def _negated(line: str, term: str) -> bool:
    low = line.lower()
    m = re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", low)
    idx = m.start() if m else 0
    window = low[max(0, idx - 30) : idx]
    return bool(re.search(r"\b(never|not|don't|do not|avoid|instead of|rather than)\b", window)) or bool(
        re.search(rf"\b{re.escape(term)}\b[^.;]*\b(breaks|is banned|is not allowed)", low)
    )


def _claims(rel: str, lines: list[str]) -> list[dict[str, Any]]:
    """Prescriptive statements: tool choices, versions and always/never rules."""
    claims: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for no, line in enumerate(lines, start=1):
        low = line.lower()
        if DATE_RE.search(line) and rel.startswith("memory/"):
            # Dated log entries ("2023-03-02: switched npm to pnpm") are history, not instructions.
            continue
        for group, members in CHOICE_GROUPS.items():
            for term in members:
                if (
                    re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", low)
                    and not _negated(line, term)
                    and (group, term, no) not in seen
                ):
                    seen.add((group, term, no))
                    claims.append(
                        {
                            "type": "choice",
                            "key": group,
                            "value": term,
                            "file": rel,
                            "line": no,
                            "text": line.strip(),
                        }
                    )
        for m in VERSION_RE.finditer(line):
            claims.append(
                {
                    "type": "version",
                    "key": m.group(1).lower(),
                    "value": m.group(2).split(".")[0],
                    "file": rel,
                    "line": no,
                    "text": line.strip(),
                }
            )
        for sentence in re.split(r"(?<=[.;!?])\s+", line):
            m = POLARITY_RE.search(sentence)
            if not m:
                continue
            word = m.group(1).lower()
            polarity = "never" if word in {"never", "do not", "don't", "must not", "avoid"} else "always"
            words = [w.strip(".+-") for w in re.findall(r"[a-z][a-z0-9.+-]*", m.group(2).lower())]
            words = [w for w in words if w and w not in STOP][:2]
            if len(words) >= 2:
                claims.append(
                    {
                        "type": "rule",
                        "key": " ".join(words),
                        "value": polarity,
                        "file": rel,
                        "line": no,
                        "text": line.strip(),
                    }
                )
    return claims


def _contradictions(claims: list[dict[str, Any]], evidence: dict[str, str]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for c in claims:
        by_key[(c["type"], c["key"])].append(c)
    out = []
    for (kind, key), items in by_key.items():
        values = sorted({c["value"] for c in items})
        if len(values) < 2:
            continue
        files = sorted({c["file"] for c in items})
        if kind == "choice" and len(files) < 2 and len({c["line"] for c in items}) == 1:
            continue  # one line naming two tools ("npm to pnpm") is not a conflict by itself
        winner = evidence.get(key)
        basis = "repo evidence" if winner else None
        if not winner:
            # Fall back to the canonical (first) file that states it.
            winner, basis = items[0]["value"], f"first stated in {items[0]['file']}"
        label = {
            "choice": f"Conflicting {key}",
            "version": f"Conflicting {key} version",
            "rule": f"'always' vs 'never': {key}",
        }[kind]
        out.append(
            {
                "topic": label,
                "kind": kind,
                "key": key,
                "values": values,
                "statements": [{k: c[k] for k in ("file", "line", "value", "text")} for c in items],
                "resolution": winner,
                "resolution_basis": basis,
                # Without repo evidence the proposal follows the canonical file; a human should confirm.
                "needs_human": basis != "repo evidence",
            }
        )
    return out


def _duplicates(texts: dict[str, list[str]]) -> list[dict[str, Any]]:
    where: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for rel, lines in texts.items():
        for no, line in enumerate(lines, start=1):
            norm = _norm(line)
            if len(norm) >= 20 and not norm.startswith("#"):
                where[norm].append((rel, no))
    out = []
    for norm, places in where.items():
        if len(places) > 1:
            out.append(
                {
                    "text": norm,
                    "occurrences": [{"file": f, "line": n} for f, n in places],
                    "kept": f"{places[0][0]}:{places[0][1]}",
                }
            )
    return out


def _analyze(base: Path, max_file_tokens: int = DEFAULT_MAX_FILE_TOKENS) -> dict[str, Any]:
    today = datetime.now(UTC).date()
    files = _context_files(base)
    texts = {_rel(base, p): _read(p).splitlines() for p in files}
    stale, claims = [], []
    for rel, lines in texts.items():
        stale += _stale(base, rel, lines, today)
        claims += _claims(rel, lines)
    evidence = _evidence(base)
    contradictions = _contradictions(claims, evidence)
    duplicates = _duplicates(texts)
    dup_tokens = sum(_tokens(d["text"]) * (len(d["occurrences"]) - 1) for d in duplicates)
    bloat = []
    for rel, lines in texts.items():
        tokens = _tokens("\n".join(lines))
        filler = sum(
            1
            for line in lines
            if re.match(r"^\s*-?\s*(always|you are|you always|remember)\b", line, re.I)
            and not any(t in line.lower() for g in CHOICE_GROUPS.values() for t in g)
        )
        if tokens > max_file_tokens or filler >= 5:
            bloat.append(
                {
                    "file": rel,
                    "tokens_est": tokens,
                    "generic_filler_lines": filler,
                    "detail": "large file" if tokens > max_file_tokens else "mostly generic advice",
                }
            )
    total = sum(_tokens("\n".join(v)) for v in texts.values())
    return {
        "root": base.name,
        "files": list(texts),
        "evidence": evidence,
        "total_tokens_est": total,
        "duplicate_tokens_est": dup_tokens,
        "stale": stale,
        "contradictions": contradictions,
        "duplicates": duplicates,
        "bloat": bloat,
    }


def analyze_context(root: str = DEFAULT_ROOT, max_file_tokens: int = DEFAULT_MAX_FILE_TOKENS) -> str:
    """Audit every context file under `root` and return all findings as JSON.

    Finds stale context (paths that don't exist, old "last updated" dates, end-of-life versions,
    TODO/FIXME), contradictions (npm vs pnpm, jest vs vitest, Node 16 vs 20, "always X" vs "never X"),
    lines duplicated within or across files, and bloat (files over `max_file_tokens` or full of generic
    advice). Contradictions carry the side the repo itself supports (lockfiles, .nvmrc, package.json).
    """
    result = _analyze(_root(root), max_file_tokens)
    save_json_state(f"context_curator/{result['root']}-analysis.json", result)
    result["summary"] = {
        "files": len(result["files"]),
        "stale": len(result["stale"]),
        "contradictions": len(result["contradictions"]),
        "duplicates": len(result["duplicates"]),
        "bloated_files": [b["file"] for b in result["bloat"]],
    }
    return to_json(result)


def find_contradictions(root: str = DEFAULT_ROOT) -> str:
    """Only the contradictions across context files, with each conflicting statement (file, line, text)
    and the side the repo evidence supports."""
    base = _root(root)
    result = _analyze(base)
    return to_json(
        {"root": base.name, "evidence": result["evidence"], "contradictions": result["contradictions"]}
    )


# ---- pruning --------------------------------------------------------------------------------------


GENERIC_FILLER = re.compile(
    r"^\s*-?\s*(always (write|think|follow|handle|double|explain|consider|care)|"
    r"you (are|always|never|think|follow)|remember:)",
    re.I,
)


def _rewrite_line(line: str, rel: str, no: int, analysis: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (new_line or None to drop, reason)."""
    for c in analysis["contradictions"]:
        for s in c["statements"]:
            if s["file"] == rel and s["line"] == no and s["value"] != c["resolution"]:
                if c["kind"] == "rule":
                    return None, f"contradicts {c['topic']} (kept '{c['resolution']}')"
                pattern = (
                    rf"(?<![\w-]){re.escape(s['value'])}(?![\w-])"
                    if c["kind"] == "choice"
                    else rf"(?i)(\b{re.escape(c['key'])}\s*(?:v|version)?\s*){re.escape(s['value'])}\b"
                )
                if c["kind"] == "choice":

                    def repl(m: re.Match[str], to: str = c["resolution"]) -> str:
                        return to.capitalize() if m.group(0)[0].isupper() else to
                else:
                    repl = rf"\g<1>{c['resolution']}"
                new = re.sub(pattern, repl, line, flags=re.I)
                return new, f"{c['topic']}: {s['value']} → {c['resolution']}"
    return line, None


def _pruned(base: Path, analysis: dict[str, Any]) -> dict[str, dict[str, Any]]:
    stale_by_line = {(s["file"], s["line"]): s for s in analysis["stale"]}
    first_seen: dict[str, str] = {}
    for d in analysis["duplicates"]:
        first_seen[d["text"]] = d["kept"]
    out: dict[str, dict[str, Any]] = {}
    for rel in analysis["files"]:
        original = _read(base / rel)
        new_lines: list[str] = []
        changes: list[dict[str, Any]] = []
        for no, line in enumerate(original.splitlines(), start=1):
            norm = _norm(line)
            stale = stale_by_line.get((rel, no))
            kept_at = first_seen.get(norm)
            if kept_at and kept_at != f"{rel}:{no}":
                changes.append({"line": no, "action": "removed", "reason": f"duplicate of {kept_at}"})
                continue
            if stale and stale["kind"] in {"missing-path", "todo"}:
                changes.append({"line": no, "action": "removed", "reason": stale["detail"]})
                continue
            if GENERIC_FILLER.match(line):
                changes.append(
                    {"line": no, "action": "removed", "reason": "generic advice the model already follows"}
                )
                continue
            new, reason = _rewrite_line(line, rel, no, analysis)
            if stale and stale["kind"] == "old-date":
                new = DATE_RE.sub(datetime.now(UTC).date().isoformat(), new or line)
                reason = (reason + "; " if reason else "") + "refreshed review date"
            if new is None:
                changes.append({"line": no, "action": "removed", "reason": reason})
                continue
            if new != line:
                changes.append({"line": no, "action": "rewritten", "reason": reason})
            new_lines.append(new)
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(new_lines)).strip() + "\n"
        # Drop headings left with nothing under them.
        text = re.sub(r"(?m)^(#{2,6} .+)\n+(?=#{1,6} |\Z)", "", text)
        out[rel] = {
            "original": original,
            "pruned": text,
            "changes": changes,
            "tokens_before": _tokens(original),
            "tokens_after": _tokens(text),
        }
    return out


def propose_pruned(path: str, root: str = DEFAULT_ROOT) -> str:
    """Propose a pruned, de-duplicated version of ONE context file (relative to `root`).

    Removes duplicates of lines that already live in an earlier canonical file (AGENTS.md first), lines
    that point at missing paths, TODO/FIXME notes and generic filler; resolves conflicts in favour of the
    repo's own evidence; refreshes stale review dates. Returns the new text and every change with its reason.
    Nothing is written.
    """
    base = _root(root)
    versions = _pruned(base, _analyze(base))
    rel = Path(path).as_posix().removeprefix("./")
    if rel not in versions:
        raise PathError(f"{path!r} is not one of the context files: {', '.join(versions)}")
    v = versions[rel]
    return to_json(
        {
            "path": rel,
            "tokens_before": v["tokens_before"],
            "tokens_after": v["tokens_after"],
            "changes": v["changes"],
            "pruned": v["pruned"],
        }
    )


def _diff_text(versions: dict[str, dict[str, Any]]) -> str:
    chunks = []
    for rel, v in versions.items():
        if v["original"] == v["pruned"]:
            continue
        chunks.extend(
            difflib.unified_diff(
                v["original"].splitlines(keepends=True),
                v["pruned"].splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
            )
        )
    return "".join(c if c.endswith("\n") else c + "\n" for c in chunks)


def make_diff(root: str = DEFAULT_ROOT, tool_call_id: Annotated[str, InjectedToolCallId] = "") -> Command:
    """Build one unified diff (all proposed pruning across the context files) and write it to
    /context-curator.diff in your files. Returns token savings per file. Nothing on disk changes."""
    base = _root(root)
    versions = _pruned(base, _analyze(base))
    diff = _diff_text(versions)
    state_path("context_curator", f"{base.name}.diff").write_text(diff, encoding="utf-8")
    per_file = [
        {
            "path": rel,
            "tokens_before": v["tokens_before"],
            "tokens_after": v["tokens_after"],
            "changes": len(v["changes"]),
        }
        for rel, v in versions.items()
    ]
    before = sum(v["tokens_before"] for v in versions.values())
    after = sum(v["tokens_after"] for v in versions.values())
    summary = {
        "diff_file": DIFF_FILE,
        "diff_lines": diff.count("\n"),
        "files": per_file,
        "tokens_before": before,
        "tokens_after": after,
        "tokens_saved_pct": round((1 - after / before) * 100, 1) if before else 0.0,
    }
    return Command(
        update={
            "files": {DIFF_FILE: create_file_data(diff)},
            "messages": [ToolMessage(to_json(summary), tool_call_id=tool_call_id, name="make_diff")],
        }
    )


def apply_changes(root: str = DEFAULT_ROOT, paths: str = "") -> str:
    """Write the pruned versions to disk. Needs human approval.

    When `root` is under the data directory the files are changed in place. Otherwise the whole folder is
    copied to `<data dir>/context_curator/applied/<name>-<timestamp>/` and the copy is changed; the
    original is never touched. `paths` is an optional comma-separated subset of context files.
    """
    base = _root(root)
    versions = _pruned(base, _analyze(base))
    wanted = [p.strip() for p in paths.split(",") if p.strip()] or list(versions)
    unknown = [p for p in wanted if p not in versions]
    if unknown:
        raise PathError(f"Not context files: {', '.join(unknown)}")
    data_dir = get_settings().state_dir
    if base == data_dir or data_dir in base.parents:
        target, mode = base, "in place"
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        target = state_path("context_curator", "applied", f"{base.name}-{stamp}")
        shutil.copytree(base, target, ignore=shutil.ignore_patterns(".git", "node_modules"))
        mode = "copy"
    written = []
    for rel in wanted:
        if versions[rel]["original"] != versions[rel]["pruned"]:
            dest = (target / rel).resolve()
            if target.resolve() not in dest.parents:
                raise PathError(f"Refusing to write outside {target}")
            dest.write_text(versions[rel]["pruned"], encoding="utf-8")
            written.append(rel)
    return to_json({"status": "applied", "mode": mode, "target": str(target), "written": written})


TOOLS = [
    find_context_files,
    read_context_file,
    analyze_context,
    find_contradictions,
    propose_pruned,
    make_diff,
    apply_changes,
]

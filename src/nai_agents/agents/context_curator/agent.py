"""Agent 6: agent context and memory curator."""

from __future__ import annotations

import re

from nai_agents.agents.context_curator import tools as t
from nai_agents.core.mock import MockContext, RoleScript, Step
from nai_agents.core.registry import AgentSpec

SYSTEM_PROMPT = """You are the Agent Context Curator. Coding agents read AGENTS.md, CLAUDE.md, .cursorrules,
.github/copilot-instructions.md and memory/*.md on every request. Stale, contradictory, duplicated or bloated
context costs tokens on every call and makes agents do the wrong thing. You keep it lean and true.

How you work:
1. Plan with write_todos.
2. Inventory: find_context_files(root) — which files exist and what they cost in tokens.
3. Audit: analyze_context(root). Cite evidence as file:line. Categories:
   - stale: paths that don't exist, old "last updated" dates, end-of-life versions, TODO/FIXME/TBD
   - contradictions: tool choices (npm vs pnpm, jest vs vitest), versions (Node 16 vs 20), always/never rules
   - duplicates: the same instruction in several files (AGENTS.md is canonical; others should not repeat it)
   - bloat: large files and generic advice the model follows anyway ("always write clean code")
4. Delegate a careful contradiction review to the `contradiction-finder` sub-agent when there are several
   files; it explains which side the repo itself supports (lockfiles, .nvmrc, package.json).
5. Propose: propose_pruned(path) for files worth showing, then make_diff(root), which writes the full unified
   diff to /context-curator.diff.
6. Write /context-report.md: token totals before/after, a table per category (file:line, issue, fix),
   contradictions with the chosen side and why, and anything that needs a human decision.
7. Only then call apply_changes(root). A human must approve it. It writes to a copy under the data directory
   unless the folder already lives there; never claim the user's repo was changed in place otherwise.
Never invent findings: every claim must come from a tool result. Keep the report scannable.
"""

CONTRADICTION_PROMPT = """You review agent context files for contradictions only. Call find_contradictions for
the folder you are given; read_context_file when you need surrounding lines. Reply with one bullet per
contradiction: the topic, each conflicting statement as file:line, the side the repo evidence supports (or
"needs a human decision") and the one-line fix. No preamble."""


def _root(ctx: MockContext) -> str:
    m = re.search(r"(?:examples/)?([\w./-]*context_curator/[\w./-]+)", ctx.user_text)
    return m.group(1).rstrip("./") if m else t.DEFAULT_ROOT


def _report(ctx: MockContext) -> dict[str, str]:
    a = ctx.last_json("analyze_context") or {}
    d = ctx.last_json("make_diff") or {}
    lines = [
        "# Context curation report",
        "",
        f"Root: `{a.get('root', '?')}` · {len(a.get('files', []))} context files · "
        f"~{d.get('tokens_before', a.get('total_tokens_est', '?'))} tokens before, "
        f"~{d.get('tokens_after', '?')} after ({d.get('tokens_saved_pct', '?')}% saved)",
        "",
        "## Contradictions",
        "",
        "| Topic | Statements | Keep | Basis |",
        "|---|---|---|---|",
    ]
    for c in a.get("contradictions", []):
        where = "<br>".join(f"{s['file']}:{s['line']} ({s['value']})" for s in c["statements"])
        lines.append(f"| {c['topic']} | {where} | {c['resolution']} | {c['resolution_basis']} |")
    lines += ["", "## Stale context", "", "| Where | Kind | Detail |", "|---|---|---|"]
    lines += [f"| {s['file']}:{s['line']} | {s['kind']} | {s['detail']} |" for s in a.get("stale", [])]
    lines += ["", "## Duplicates", ""]
    for dup in a.get("duplicates", []):
        where = ", ".join(f"{o['file']}:{o['line']}" for o in dup["occurrences"])
        lines.append(f"- “{dup['text']}” — {where} (kept at {dup['kept']})")
    lines += ["", "## Bloat", ""]
    lines += [f"- {b['file']}: ~{b['tokens_est']} tokens, {b['detail']}" for b in a.get("bloat", [])] or [
        "- none"
    ]
    lines += ["", "## Proposed changes", "", f"The full unified diff is in `{t.DIFF_FILE}`.", ""]
    lines += [
        f"- {f['path']}: {f['tokens_before']} → {f['tokens_after']} tokens ({f['changes']} changes)"
        for f in d.get("files", [])
    ]
    human = [c["topic"] for c in a.get("contradictions", []) if c.get("needs_human")]
    if human:
        lines += ["", "## Needs a human decision", ""] + [f"- {h}" for h in human]
    return {"file_path": "/context-report.md", "content": "\n".join(lines) + "\n"}


def _final(ctx: MockContext) -> str:
    a = ctx.last_json("analyze_context") or {}
    d = ctx.last_json("make_diff") or {}
    applied = ctx.last_json("apply_changes")
    lines = [
        f"Audited {len(a.get('files', []))} context files in `{a.get('root', '?')}` (mock mode).",
        "",
        f"- **Contradictions:** {len(a.get('contradictions', []))} — "
        + "; ".join(f"{c['topic']} → keep {c['resolution']}" for c in a.get("contradictions", [])),
        f"- **Stale:** {len(a.get('stale', []))} items, e.g. "
        + ", ".join(dict.fromkeys(f"`{s['ref']}`" for s in a.get("stale", []) if s["kind"] == "missing-path"))
        + " no longer exist",
        f"- **Duplicates:** {len(a.get('duplicates', []))} repeated instructions",
        f"- **Tokens:** ~{d.get('tokens_before', '?')} → ~{d.get('tokens_after', '?')} "
        f"({d.get('tokens_saved_pct', '?')}% saved)",
        "",
    ]
    if isinstance(applied, dict) and applied.get("status") == "applied":
        lines.append(
            f"Applied the pruned files to a {applied['mode']} at `{applied['target']}` "
            f"({len(applied['written'])} files)."
        )
    else:
        lines.append("No files were changed on disk.")
    lines.append(f"Details: `/context-report.md`; diff: `{t.DIFF_FILE}`.")
    return "\n".join(lines)


def _finder_final(ctx: MockContext) -> str:
    data = ctx.last_json("find_contradictions") or {}
    out = []
    for c in data.get("contradictions", []):
        where = ", ".join(f"{s['file']}:{s['line']}" for s in c["statements"])
        side = "needs a human decision" if c["needs_human"] else f"repo supports {c['resolution']}"
        out.append(f"- {c['topic']}: {where} — {side}")
    return "\n".join(out) or "No contradictions found."


def mock_script() -> dict[str, RoleScript]:
    todos = [
        {"content": "Inventory context files", "status": "in_progress"},
        {"content": "Audit for stale, contradictory, duplicated and bloated context", "status": "pending"},
        {"content": "Propose a pruned version and a diff", "status": "pending"},
        {"content": "Write the curation report", "status": "pending"},
        {"content": "Apply changes (with approval)", "status": "pending"},
    ]
    done = [dict(x, status="completed") for x in todos]
    return {
        "context-curator": RoleScript(
            steps=[
                Step("write_todos", {"todos": todos}),
                Step("find_context_files", lambda c: {"root": _root(c)}),
                Step("analyze_context", lambda c: {"root": _root(c)}),
                Step(
                    "task",
                    lambda c: {
                        "subagent_type": "contradiction-finder",
                        "description": f"Find contradictions across the context files in {_root(c)}.",
                    },
                ),
                Step("propose_pruned", lambda c: {"path": "AGENTS.md", "root": _root(c)}),
                Step("make_diff", lambda c: {"root": _root(c)}),
                Step("write_file", _report),
                Step("apply_changes", lambda c: {"root": _root(c)}),
                Step("write_todos", {"todos": done}),
            ],
            final=_final,
        ),
        "contradiction-finder": RoleScript(
            steps=[Step("find_contradictions", lambda c: {"root": _root(c)})],
            final=_finder_final,
        ),
    }


SPEC = AgentSpec(
    id="context-curator",
    name="Agent context and memory curator",
    summary="Audits AGENTS.md, CLAUDE.md, .cursorrules and memory files for stale, contradictory and "
    "duplicated context, and proposes a pruned version as a diff.",
    icon="library",
    system_prompt=SYSTEM_PROMPT,
    tools=t.TOOLS,
    subagents=[
        {
            "name": "contradiction-finder",
            "description": "Finds contradictions across context files and which side the repo supports.",
            "system_prompt": CONTRADICTION_PROMPT,
            "tools": [t.find_contradictions, t.read_context_file],
        }
    ],
    interrupt_on={"apply_changes": {"allowed_decisions": ["approve", "edit", "reject"]}},
    example_prompts=[
        "Audit the agent context files in context_curator/sample_repo and propose a pruned version.",
        "Which instructions in context_curator/sample_repo contradict each other?",
        "How many tokens do our agent instruction files cost, and what can we cut?",
    ],
    mock_script=mock_script,
)

"""Agent 8: multi-model code review."""

from __future__ import annotations

import re

from nai_agents.agents.code_review import tools as t
from nai_agents.core.mock import MockContext, RoleScript, Step
from nai_agents.core.registry import AgentSpec

SYSTEM_PROMPT = """You are the Multi-Model Code Reviewer. Several models review the same change; you merge
their findings so agreement raises confidence and one model's blind spot is covered by another.

How you work:
1. Plan with write_todos.
2. load_diff(source): a .diff file or a git repository folder. Note new functions and whether tests changed.
3. static_checks(source): deterministic heuristics with exact file:line (SQL concatenation, secrets,
   bare except, disabled TLS, shell/eval, debug prints, missing tests).
4. list_review_models, then delegate each of 2–3 models to the `reviewer` sub-agent (one task per model:
   "Review <source> with model <name>"). Prefer models from different providers or sizes.
5. run_tests(cmd, cwd) with the project's own test command (e.g. "python -m pytest -q"). A human approves
   it first. Only allow-listed commands run; never try to chain shell commands.
6. reconcile_findings(source): merges the reviews with the static checks (file + category + line ±2),
   takes the max severity, sets confidence = agreeing models / total, and writes /review.md.
7. Answer with the critical and high findings (file:line, why, fix), the test result, and the confidence.
Never print secrets: refer to them as redacted. Don't invent findings that no tool reported.
"""

REVIEWER_PROMPT = """You run ONE model's review of a diff. Call review_with_model with the model name and
source you were given, then reply with its findings grouped by severity as `file:line category` bullets and
a one-line verdict. No preamble."""


def _source(ctx: MockContext) -> str:
    m = re.search(r"(code_review/[\w./-]+)", ctx.user_text)
    return m.group(1).rstrip(".") if m else t.DEFAULT_DIFF


def _models(ctx: MockContext) -> list[str]:
    catalog = ctx.last_json("list_review_models") or []
    ranked = sorted(catalog, key=lambda m: m.get("output_per_mtok", 0))
    return [m["name"] for m in ranked[-3:]]


def _review_step(i: int) -> Step:
    def args(ctx: MockContext) -> dict | None:
        models = _models(ctx)
        if i >= len(models):
            return None
        return {"subagent_type": "reviewer", "description": f"Review {_source(ctx)} with model {models[i]}."}

    return Step("task", args)


def _final(ctx: MockContext) -> str:
    data = ctx.last_json("reconcile_findings") or {}
    tests = ctx.last_json("run_tests")
    findings = data.get("findings", [])
    top = [f for f in findings if f["severity"] in {"critical", "high"}]
    by = data.get("by_severity", {})
    lines = [
        f"Reviewed `{_source(ctx)}` with {', '.join(data.get('models', []))} plus static checks (mock mode).",
        "",
        " · ".join(f"{s}: {n}" for s, n in by.items() if n),
        "",
        "**Must fix:**",
    ]
    for f in top:
        lines.append(
            f"- `{f['file']}:{f['line']}` {f['category']} ({f['severity']}, "
            f"confidence {f['confidence']:.0%}) — {f['message']}"
        )
    if isinstance(tests, dict):
        lines.append(
            f"\nTests: `{tests.get('command')}` → **{tests.get('status')}** {tests.get('summary', '')}"
        )
    else:
        lines.append("\nTests were not run (not approved).")
    lines.append(f"\nFull review: `{t.REVIEW_FILE}`.")
    return "\n".join(lines)


def _reviewer_args(ctx: MockContext) -> dict:
    m = re.search(r"with model ([\w.:/-]+?)\.?(?:\s|$)", ctx.user_text)
    return {"model_name": m.group(1) if m else "mock-medium", "source": _source(ctx)}


def _reviewer_final(ctx: MockContext) -> str:
    data = ctx.last_json("review_with_model") or {}
    lines = [f"{data.get('model')}: {data.get('count', 0)} findings"]
    lines += [
        f"- {f['severity']}: `{f['file']}:{f['line']}` {f['category']}" for f in data.get("findings", [])
    ]
    return "\n".join(lines)


def mock_script() -> dict[str, RoleScript]:
    todos = [
        {"content": "Load the diff", "status": "in_progress"},
        {"content": "Run static checks", "status": "pending"},
        {"content": "Review with several models", "status": "pending"},
        {"content": "Run the tests (with approval)", "status": "pending"},
        {"content": "Reconcile findings and write /review.md", "status": "pending"},
    ]
    done = [dict(x, status="completed") for x in todos]
    return {
        "code-review": RoleScript(
            steps=[
                Step("write_todos", {"todos": todos}),
                Step("load_diff", lambda c: {"source": _source(c)}),
                Step("static_checks", lambda c: {"source": _source(c)}),
                Step("list_review_models", {}),
                _review_step(0),
                _review_step(1),
                _review_step(2),
                Step("run_tests", {"cmd": "python -m pytest -q", "cwd": t.DEFAULT_PROJECT, "timeout_s": 300}),
                Step("reconcile_findings", lambda c: {"source": _source(c)}),
                Step("write_todos", {"todos": done}),
            ],
            final=_final,
        ),
        "reviewer": RoleScript(steps=[Step("review_with_model", _reviewer_args)], final=_reviewer_final),
    }


SPEC = AgentSpec(
    id="code-review",
    name="Multi-model code review",
    summary="Reviews a diff with several models plus static checks, runs the tests, and merges the findings "
    "into one review with severity and confidence.",
    icon="git-pull-request",
    system_prompt=SYSTEM_PROMPT,
    tools=t.TOOLS,
    subagents=[
        {
            "name": "reviewer",
            "description": "Runs ONE model's review of a diff (name model and diff) and summarizes it.",
            "system_prompt": REVIEWER_PROMPT,
            "tools": [t.review_with_model, t.load_diff],
        }
    ],
    interrupt_on={"run_tests": {"allowed_decisions": ["approve", "edit", "reject"]}},
    example_prompts=[
        "Review code_review/sample.diff with three models, run the tests and write one merged review.",
        "Is there anything security-critical in code_review/sample.diff?",
        "Review the uncommitted changes in my repo under the data directory (repos/my-service).",
    ],
    mock_script=mock_script,
)

"""Agent 9: eval watchdog."""

from __future__ import annotations

from nai_agents.agents.eval_watchdog import tools as t
from nai_agents.core.mock import MockContext, RoleScript, Step
from nai_agents.core.registry import AgentSpec

SYSTEM_PROMPT = """You are the Eval Watchdog. Providers ship new model versions and sometimes change a model
behind the same name. You notice both, re-run the team's evals on what changed, and report regressions
before users do.

How you work:
1. Plan with write_todos.
2. list_provider_models and detect_model_changes: new and removed models since the last check.
3. fingerprint_models: hash of canary answers at temperature 0. A changed fingerprint under the same name is
   a silent update.
4. run_watch_evals on every changed model (silent updates and new models) in one call.
5. For each regressed model, delegate to the `regression-analyst` sub-agent: which cases flipped and which
   capabilities (categories) got worse.
6. Write /watchdog-report.md: what changed (new / silent / removed), a table per model (baseline → current
   pass rate, delta, newly failing cases), the analyst's findings, and recommended actions (pin the previous
   version, re-route traffic, contact the provider).
7. Propose save_baseline only for models that should become the new reference: new models that pass, or a
   changed model the team accepts. A human approves each. Never re-baseline a regressed model on your own.
Say clearly when data is simulated (mock mode).
"""

ANALYST_PROMPT = """You analyze ONE model's eval regression. Call explain_regression for the model you are
given. Reply with: pass rate before → after, the categories that got worse with counts, two or three
telling examples (case id, before vs. now), and a one-line hypothesis. No preamble."""


def _changed(ctx: MockContext) -> list[str]:
    fps = ctx.last_json("fingerprint_models") or {}
    added = [m["id"] for m in (ctx.last_json("detect_model_changes") or {}).get("added", [])]
    return list(dict.fromkeys([*fps.get("changed", []), *added]))


def _regressed(ctx: MockContext) -> list[str]:
    return (ctx.last_json("run_watch_evals") or {}).get("regressions", [])


def _new_passing(ctx: MockContext) -> str | None:
    rows = (ctx.last_json("run_watch_evals") or {}).get("results", [])
    return next((r["model"] for r in rows if not r["has_baseline"] and r["pass_rate"] >= 0.8), None)


def _analyst_step(i: int) -> Step:
    def args(ctx: MockContext) -> dict | None:
        reg = _regressed(ctx)
        if i >= len(reg):
            return None
        return {
            "subagent_type": "regression-analyst",
            "description": f"Explain the eval regression of model {reg[i]}.",
        }

    return Step("task", args)


def _report(ctx: MockContext) -> dict[str, str]:
    changes = ctx.last_json("detect_model_changes") or {}
    fps = ctx.last_json("fingerprint_models") or {}
    evals = ctx.last_json("run_watch_evals") or {}
    lines = [
        "# Eval watchdog report",
        "",
        f"_Checked {evals.get('checked_at', '?')} · mock mode (simulated provider listing and models)._",
        "",
        "## What changed",
        "",
    ]
    lines += [
        f"- **New model:** {m['id']} (created {m.get('created', '?')})" for m in changes.get("added", [])
    ]
    lines += [f"- **Removed:** {m}" for m in changes.get("removed", [])]
    for row in fps.get("models", []):
        if row["changed"]:
            lines.append(
                f"- **Silent update:** {row['model']} — fingerprint {row['accepted']} → {row['fingerprint']}"
            )
    lines += [
        "",
        "## Eval results",
        "",
        "| Model | Baseline | Now | Δ | Newly failing | Status |",
        "|---|---|---|---|---|---|",
    ]
    for r in evals.get("results", []):
        if r["has_baseline"]:
            status = "**REGRESSED**" if r["regressed"] else "ok"
            lines.append(
                f"| {r['model']} | {r['baseline_pass_rate']:.0%} | {r['current_pass_rate']:.0%} | "
                f"{r['delta']:+.0%} | {', '.join(r['newly_failing']) or '—'} | {status} |"
            )
        else:
            lines.append(f"| {r['model']} | — | {r['pass_rate']:.0%} | — | — | new, no baseline |")
    analyses = [content for name, content in ctx.tool_results if name == "task"]
    if analyses:
        lines += ["", "## Regression analysis", "", *analyses]
    lines += ["", "## Recommended actions", ""]
    for m in _regressed(ctx):
        lines.append(
            f"- {m}: pin traffic to a known-good version or re-route its categories; ask the provider "
            "what changed. Do not re-baseline until explained."
        )
    new = _new_passing(ctx)
    if new:
        lines.append(
            f"- {new}: passes the suite threshold; save it as a baseline so future changes are caught."
        )
    lines += ["", "Run this on a schedule: see docs/agents/09-eval-watchdog.md."]
    return {"file_path": t.REPORT_FILE, "content": "\n".join(lines) + "\n"}


def _final(ctx: MockContext) -> str:
    evals = ctx.last_json("run_watch_evals") or {}
    saved = ctx.last_json("save_baseline")
    lines = ["Watchdog check complete (mock mode)."]
    for r in evals.get("results", []):
        if r["has_baseline"] and r["regressed"]:
            lines.append(
                f"- **{r['model']} regressed** (silent update): {r['baseline_pass_rate']:.0%} → "
                f"{r['current_pass_rate']:.0%}; newly failing: {', '.join(r['newly_failing'])}"
            )
        elif r["has_baseline"]:
            lines.append(f"- {r['model']}: no regression ({r['current_pass_rate']:.0%})")
        else:
            lines.append(f"- **{r['model']}** is new: {r['pass_rate']:.0%} pass rate, no baseline yet")
    if isinstance(saved, dict) and saved.get("status") == "saved":
        lines.append(f"\nSaved a baseline for {saved['model']}.")
    else:
        lines.append("\nNo baseline was saved.")
    lines.append(f"Report: `{t.REPORT_FILE}`.")
    return "\n".join(lines)


def _analyst_final(ctx: MockContext) -> str:
    d = ctx.last_json("explain_regression") or {}
    worse = ", ".join(f"{c} ({n})" for c, n in d.get("newly_failing_by_category", {}).items())
    flips = [f for f in d.get("flips", []) if f["change"] == "now failing"][:3]
    lines = [
        f"**{d.get('model')}**: {d.get('baseline_pass_rate', 0):.0%} → {d.get('current_pass_rate', 0):.0%} "
        f"(version {d.get('baseline_version')} → {d.get('current_version')}). Worse in: {worse or 'none'}."
    ]
    lines += [
        f"- `{f['case']}` ({f['category']}): was “{f['baseline_output'][:40]}”, "
        f"now “{f['current_output'][:40]}”"
        for f in flips
    ]
    lines.append(
        "Hypothesis: the provider changed the serving build (e.g. quantization); reasoning and "
        "exact-format answers degraded first."
    )
    return "\n".join(lines)


def mock_script() -> dict[str, RoleScript]:
    todos = [
        {"content": "Check the provider's model listing", "status": "in_progress"},
        {"content": "Fingerprint models for silent updates", "status": "pending"},
        {"content": "Re-run evals on changed models", "status": "pending"},
        {"content": "Analyze regressions", "status": "pending"},
        {"content": "Write the watchdog report", "status": "pending"},
        {"content": "Propose new baselines (with approval)", "status": "pending"},
    ]
    done = [dict(x, status="completed") for x in todos]
    return {
        "eval-watchdog": RoleScript(
            steps=[
                Step("write_todos", {"todos": todos}),
                Step("list_provider_models", {}),
                Step("detect_model_changes", {}),
                Step("fingerprint_models", {}),
                Step("run_watch_evals", lambda c: {"models": ",".join(_changed(c))} if _changed(c) else None),
                _analyst_step(0),
                _analyst_step(1),
                Step("write_file", _report),
                Step("save_baseline", lambda c: {"model_name": n} if (n := _new_passing(c)) else None),
                Step("write_todos", {"todos": done}),
            ],
            final=_final,
        ),
        "regression-analyst": RoleScript(
            steps=[
                Step(
                    "explain_regression",
                    lambda c: {"model_name": c.user_text.split("model ")[-1].strip(" .")},
                )
            ],
            final=_analyst_final,
        ),
    }


SPEC = AgentSpec(
    id="eval-watchdog",
    name="Eval watchdog",
    summary="Detects new model versions and silent provider updates, re-runs your evals and reports "
    "regressions against saved baselines.",
    icon="shield-alert",
    system_prompt=SYSTEM_PROMPT,
    tools=t.TOOLS,
    subagents=[
        {
            "name": "regression-analyst",
            "description": "Explains ONE model's eval regression case by case (name the model).",
            "system_prompt": ANALYST_PROMPT,
            "tools": [t.explain_regression],
        }
    ],
    interrupt_on={"save_baseline": {"allowed_decisions": ["approve", "reject"]}},
    example_prompts=[
        "Check our models for new versions and silent updates, re-run the evals on anything that changed and "
        "report regressions.",
        "Has mock-small changed since last week? Fingerprint it and compare with the baseline.",
        "List the models our provider serves right now.",
    ],
    mock_script=mock_script,
)

"""Agent 10: new-model release scout."""

from __future__ import annotations

from nai_agents.agents.release_scout import tools as t
from nai_agents.core.mock import MockContext, RoleScript, Step
from nai_agents.core.registry import AgentSpec

SYSTEM_PROMPT = """You are the New-Model Release Scout. You watch providers' release feeds and model hubs,
spot new models, benchmark them on the team's evals and say whether any should replace the production model.

How you work:
1. Plan with write_todos.
2. list_sources, then find_new_models: model releases only (not blog posts, not quantized re-uploads), not
   seen before, with provider, date, context window and price when the post states them.
3. Benchmark every new model: delegate each to the `benchmarker` sub-agent (one task per model, give the
   model id). Models that can't be reached are reported, not guessed.
4. compare_to_production: quality and monthly cost against the production model under the switch policy.
5. Write /scout-report.md: a table of new models (provider, date, context, $/Mtok in/out, pass rate, monthly
   cost, decision), the recommendation with reasons and caveats (simulated benchmarks, assumed self-hosted
   prices, thin eval coverage), and next steps (a shadow test before any switch).
6. Then propose mark_seen with the new ids so the next run skips them. A human approves it.
Never recommend a model the evals didn't cover. Say plainly when results are simulated.
"""

BENCHMARKER_PROMPT = """You benchmark ONE candidate model. Call benchmark_model with the model id you were
given. Reply with: pass rate, whether it meets the threshold, the weakest category, p50 latency and price per
Mtok (say if the price is assumed). One short paragraph."""


def _new(ctx: MockContext) -> list[dict]:
    return (ctx.last_json("find_new_models") or {}).get("new", [])


def _bench_step(i: int) -> Step:
    def args(ctx: MockContext) -> dict | None:
        new = _new(ctx)
        if i >= len(new):
            return None
        return {"subagent_type": "benchmarker", "description": f"Benchmark the new model {new[i]['id']}."}

    return Step("task", args)


def _price(c: dict) -> str:
    if c.get("input_per_mtok") is None:
        return "open weights"
    return f"${c['input_per_mtok']:g} / ${c['output_per_mtok']:g}"


def _report(ctx: MockContext) -> dict[str, str]:
    new = {c["id"]: c for c in _new(ctx)}
    cmp = ctx.last_json("compare_to_production") or {}
    prod = cmp.get("production", {})
    rec = cmp.get("recommendation", {})
    lines = [
        "# New-model scout report",
        "",
        "_Mock mode: feeds are local samples with fictional models; benchmarks are simulated._",
        "",
        f"Production: **{prod.get('model')}** — pass rate {prod.get('pass_rate', 0):.0%}, "
        f"${prod.get('monthly_cost_usd', 0):,.0f}/month",
        "",
        "| Model | Provider | Released | Context | $/Mtok in/out | Pass rate | $/month | Decision |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in cmp.get("candidates", []):
        c = new.get(row["model"], {})
        ctx_w = f"{c['context_window']:,}" if c.get("context_window") else "?"
        lines.append(
            f"| {c.get('name', row['model'])} | {c.get('provider', '?')} | {c.get('date', '?')} | {ctx_w} | "
            f"{_price(c)} | {row.get('pass_rate', 0):.0%} | ${row.get('monthly_cost_usd', 0):,.0f} | "
            f"**{row['decision']}** |"
        )
    lines += [
        "",
        "## Recommendation",
        "",
        f"**{rec.get('action', 'hold').upper()}** → `{rec.get('model')}`",
        "",
    ]
    lines += [f"- {w}" for w in rec.get("why", [])]
    lines += ["", "## Why the others hold", ""]
    lines += [
        f"- {r['model']}: {'; '.join(r['reasons'])}"
        for r in cmp.get("candidates", [])
        if r["decision"] == "hold"
    ]
    lines += [
        "",
        "## Next steps",
        "",
        "- Shadow-test the recommended model on a slice of real traffic before switching.",
        "- Re-run the cost router (agent 1) per category: a cheaper model may win some categories only.",
    ]
    return {"file_path": t.REPORT_FILE, "content": "\n".join(lines) + "\n"}


def _final(ctx: MockContext) -> str:
    new = _new(ctx)
    cmp = ctx.last_json("compare_to_production") or {}
    rec = cmp.get("recommendation", {})
    seen = ctx.last_json("mark_seen")
    lines = [
        f"Found {len(new)} new models (mock feeds): "
        + ", ".join(f"{c['name']} ({c['provider']})" for c in new)
        + ".",
        "",
        f"**Recommendation: {rec.get('action', 'hold')} → {rec.get('model')}** — "
        + "; ".join(rec.get("why", [])),
    ]
    if isinstance(seen, dict) and seen.get("status") == "saved":
        lines.append(f"\nMarked {len(seen['added'])} models as seen.")
    else:
        lines.append("\nThe models were not marked as seen; they will show up again next run.")
    lines.append(f"Report: `{t.REPORT_FILE}`.")
    return "\n".join(lines)


def _bench_final(ctx: MockContext) -> str:
    b = ctx.last_json("benchmark_model") or {}
    if b.get("status") != "benchmarked":
        return f"{b.get('model')}: not benchmarked ({b.get('error', 'unknown error')})."
    weakest = min(b["by_category"].items(), key=lambda kv: kv[1]) if b.get("by_category") else ("?", 0)
    price = f"${b['input_per_mtok']:g}/${b['output_per_mtok']:g} per Mtok" + (
        " (assumed)" if b["price_assumed"] else ""
    )
    return (
        f"{b['model']}: pass rate {b['pass_rate']:.0%} "
        f"({'meets' if b['meets_threshold'] else 'below'} threshold), "
        f"weakest category {weakest[0]} ({weakest[1]:.0%}), p50 {b['p50_latency_ms']} ms, {price}. Simulated."
    )


def mock_script() -> dict[str, RoleScript]:
    todos = [
        {"content": "Read the release feeds", "status": "in_progress"},
        {"content": "Find new model releases", "status": "pending"},
        {"content": "Benchmark the new models", "status": "pending"},
        {"content": "Compare with production", "status": "pending"},
        {"content": "Write the scout report", "status": "pending"},
        {"content": "Mark the models as seen (with approval)", "status": "pending"},
    ]
    done = [dict(x, status="completed") for x in todos]
    return {
        "release-scout": RoleScript(
            steps=[
                Step("write_todos", {"todos": todos}),
                Step("list_sources", {}),
                Step("find_new_models", {}),
                *[_bench_step(i) for i in range(10)],
                Step("compare_to_production", {}),
                Step("write_file", _report),
                Step(
                    "mark_seen",
                    lambda c: {"model_ids": ",".join(x["id"] for x in _new(c))} if _new(c) else None,
                ),
                Step("write_todos", {"todos": done}),
            ],
            final=_final,
        ),
        "benchmarker": RoleScript(
            steps=[
                Step("benchmark_model", lambda c: {"model_id": c.user_text.split("model ")[-1].strip(" .")})
            ],
            final=_bench_final,
        ),
    }


SPEC = AgentSpec(
    id="release-scout",
    name="New-model release scout",
    summary="Watches release feeds and model hubs for new models, benchmarks them on your evals and "
    "recommends switch or hold against your production model.",
    icon="telescope",
    system_prompt=SYSTEM_PROMPT,
    tools=t.TOOLS,
    subagents=[
        {
            "name": "benchmarker",
            "description": "Benchmarks ONE new candidate model (give its id) on the production eval suite.",
            "system_prompt": BENCHMARKER_PROMPT,
            "tools": [t.benchmark_model],
        }
    ],
    interrupt_on={"mark_seen": {"allowed_decisions": ["approve", "edit", "reject"]}},
    example_prompts=[
        "Check the release feeds for new models, benchmark them and tell me if we should switch from our "
        "production model.",
        "Which new models were released this week, and what do they cost?",
        "Benchmark Nova-5 mini against our production model.",
    ],
    mock_script=mock_script,
)

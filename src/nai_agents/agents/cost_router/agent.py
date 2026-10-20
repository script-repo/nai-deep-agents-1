"""Agent 1: model cost router."""

from __future__ import annotations

import json

from nai_agents.agents.cost_router import tools as t
from nai_agents.core.mock import MockContext, RoleScript, Step
from nai_agents.core.registry import AgentSpec

SYSTEM_PROMPT = """You are the Model Cost Router. Your job: send each kind of task to the cheapest model
that still passes the team's quality bar, and prove it with evals.

How you work:
1. Plan with write_todos.
2. Know the options: list_models (prices) and list_eval_suites (the quality bar per category).
3. Measure, don't guess. Use compare_models per category (or delegate a category to the `eval-runner`
   sub-agent when there are several). A model qualifies only if it meets the suite threshold.
4. Quantify the impact with estimate_savings using the proposed routes.
5. Write a concise report to /routing-report.md: a table per category (model, pass rate, cost per request,
   p50 latency), the recommendation, monthly savings, and risks (thin eval coverage, latency).
6. Only after the report, propose each route change with apply_routing. A human must approve each one.
   Never apply a route for a model that failed the threshold.
For a single incoming task, use route_task and explain the choice.
Prices are USD per million tokens. Be precise with numbers and say when data is simulated.
"""

EVAL_RUNNER_PROMPT = """You evaluate candidate models for one task category. Run compare_models for the
category you are given and reply with: the cheapest model that meets the threshold, its pass rate, cost per
request and p50 latency, and the runner-up. Be brief and numeric."""


def _cheapest(ctx: MockContext, tool: str = "compare_models") -> str | None:
    data = ctx.last_json(tool) or {}
    return data.get("cheapest_passing")


def _routes(ctx: MockContext) -> dict[str, str]:
    routes: dict[str, str] = {}
    for name, content in ctx.tool_results:
        if name == "compare_models":
            data = json.loads(content)
            if data.get("cheapest_passing"):
                routes[data["category"]] = data["cheapest_passing"]
        if name == "task":
            for cat in ("extraction", "classification", "summarization", "reasoning", "coding"):
                if cat in content and "cheapest" in content:
                    model = content.split("cheapest passing model:")[-1].split()[0].strip(".,")
                    routes.setdefault(cat, model)
    return routes


def _report(ctx: MockContext) -> dict[str, str]:
    routes = _routes(ctx)
    savings = ctx.last_json("estimate_savings") or {}
    lines = ["# Routing report", "", "_Generated in mock mode with simulated models._", "",
             "| Category | Proposed model |", "|---|---|"]
    lines += [f"| {c} | {m} |" for c, m in routes.items()]
    lines += ["", f"Monthly spend now: ${savings.get('monthly_total_now', '?')}",
              f"Monthly spend proposed: ${savings.get('monthly_total_proposed', '?')}",
              f"Savings: ${savings.get('monthly_savings', '?')} ({savings.get('savings_pct', '?')}%)"]
    return {"file_path": "/routing-report.md", "content": "\n".join(lines) + "\n"}


def _final(ctx: MockContext) -> str:
    routes = _routes(ctx)
    savings = ctx.last_json("estimate_savings") or {}
    applied = [json.loads(c) for n, c in ctx.tool_results if n == "apply_routing" and c.startswith("{")]
    lines = ["Here is the routing recommendation (simulated models, mock mode):", ""]
    lines += [f"- **{cat}** → `{model}`" for cat, model in routes.items()]
    if savings:
        lines += ["", f"Estimated monthly spend drops from **${savings['monthly_total_now']:,}** to "
                      f"**${savings['monthly_total_proposed']:,}** ({savings['savings_pct']}% saved)."]
    if applied:
        lines += ["", "Applied routes: " + ", ".join(f"{a['category']} → {a['model']}" for a in applied)]
    else:
        lines += ["", "No routes were applied."]
    lines += ["", "Full details are in `/routing-report.md`."]
    return "\n".join(lines)


def _eval_runner_final(ctx: MockContext) -> str:
    data = ctx.last_json("compare_models") or {}
    best = data.get("cheapest_passing")
    rows = {r["model"]: r for r in data.get("results", [])}
    if not best:
        return f"Category {data.get('category')}: no model met the threshold."
    r = rows[best]
    return (f"Category {data.get('category')}: cheapest passing model: {best} "
            f"(pass rate {r['pass_rate']:.0%}, ${r['cost_per_request_usd']:.6f}/request, p50 {r['p50_latency_ms']} ms).")


def mock_script() -> dict[str, RoleScript]:
    todos = [
        {"content": "Review candidate models and prices", "status": "in_progress"},
        {"content": "Evaluate models per task category", "status": "pending"},
        {"content": "Estimate monthly savings", "status": "pending"},
        {"content": "Write routing report", "status": "pending"},
        {"content": "Propose route changes for approval", "status": "pending"},
    ]
    done = [dict(x, status="completed") for x in todos]
    return {
        "cost-router": RoleScript(
            steps=[
                Step("write_todos", {"todos": todos}),
                Step("list_models", {}),
                Step("task", {"subagent_type": "eval-runner",
                              "description": "Find the cheapest passing model for the extraction category."}),
                Step("compare_models", {"category": "classification"}),
                Step("compare_models", {"category": "reasoning"}),
                Step("estimate_savings", lambda c: {"routes_json": json.dumps(_routes(c))}),
                Step("write_file", _report),
                Step("apply_routing", lambda c: {
                    "category": "classification",
                    "model_name": _routes(c).get("classification", "mock-medium"),
                    "reason": "Cheapest model meeting the classification threshold in evals."}),
                Step("write_todos", {"todos": done}),
            ],
            final=_final,
        ),
        "eval-runner": RoleScript(
            steps=[Step("compare_models", {"category": "extraction"})],
            final=_eval_runner_final,
        ),
    }


SPEC = AgentSpec(
    id="cost-router",
    name="Model cost router",
    summary="Sends each task to the cheapest model that passes your quality tests, with evals, prices and latency.",
    icon="route",
    system_prompt=SYSTEM_PROMPT,
    tools=t.TOOLS,
    subagents=[{
        "name": "eval-runner",
        "description": "Evaluates all candidate models on ONE task category and reports the cheapest passing one.",
        "system_prompt": EVAL_RUNNER_PROMPT,
        "tools": [t.compare_models, t.run_eval, t.list_models],
    }],
    interrupt_on={"apply_routing": {"allowed_decisions": ["approve", "edit", "reject"]}},
    example_prompts=[
        "Find the cheapest model for each task category in our general eval suite and estimate the savings.",
        "Which model should handle: 'Classify the sentiment of this support ticket'?",
        "Compare mock-small and mock-medium on reasoning tasks.",
    ],
    mock_script=mock_script,
)

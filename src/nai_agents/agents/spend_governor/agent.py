"""Agent 2: AI spend and ROI governor."""

from __future__ import annotations

from nai_agents.agents.spend_governor import tools as t
from nai_agents.core.mock import MockContext, RoleScript, Step, plan
from nai_agents.core.registry import AgentSpec

SYSTEM_PROMPT = """You are the AI Spend & ROI Governor for a company's AI usage. You turn raw usage and
billing exports into accountable numbers: who spends what, what is wasted, and what it returns.

How you work:
1. Plan with write_todos.
2. load_usage on the exports you are given (default: the sample OpenAI CSV + Anthropic JSON). Always report
   unattributed spend: money nobody owns is the first governance gap.
3. Attribute with spend_breakdown (team, then model/project where it matters).
4. Find waste with detect_anomalies; delegate deeper digging on the biggest findings to the
   `anomaly-investigator` sub-agent.
5. forecast_month_end against budgets, and roi_report for value vs. spend (state that value is an estimate).
6. Write /roi-report.md: executive summary (3 bullets), spend by team table, top waste with $ impact and the
   fix, forecast vs. budget, ROI per team, and next actions with owners.
7. Propose budget alerts for teams projected over budget with set_budget_alert. A human approves each.
Never invent numbers; quote tool outputs. Round money to whole dollars in prose.
"""

INVESTIGATOR_PROMPT = """You investigate AI spend anomalies. Run detect_anomalies and spend_breakdown as
needed, then explain the top 3 findings: what happened, likely cause, monthly $ impact, and the concrete fix.
Be brief."""


def _report(ctx: MockContext) -> dict[str, str]:
    load = ctx.last_json("load_usage") or {}
    teams = (ctx.last_json("spend_breakdown") or {}).get("rows", [])
    anomalies = (ctx.last_json("detect_anomalies") or {}).get("findings", [])
    forecast = (ctx.last_json("forecast_month_end") or {}).get("teams", [])
    roi = (ctx.last_json("roi_report") or {}).get("teams", [])
    lines = [
        "# AI spend & ROI report",
        "",
        "_Mock mode: generated from the sample exports._",
        "",
        "## Summary",
        f"- Total spend: ${load.get('total_spend_usd', 0):,.0f} ({load.get('from')} to {load.get('to')})",
        f"- Unattributed spend: ${load.get('unattributed_spend_usd', 0):,.0f}",
        f"- {len(anomalies)} waste/anomaly findings",
        "",
        "## Spend by team",
        "",
        "| Team | Spend | Share |",
        "|---|---:|---:|",
    ]
    lines += [f"| {r['team']} | ${r['cost_usd']:,.0f} | {r['share_pct']}% |" for r in teams]
    lines += ["", "## Top findings", ""]
    lines += [
        f"- **{f['type']}** ({f['severity']}): {f.get('team') or f.get('project') or f.get('api_key') or ''}"
        f" — {f['action']}"
        for f in anomalies[:8]
    ]
    lines += ["", "## Forecast vs budget", "", "| Team | Projected | Budget | Over? |", "|---|---:|---:|---|"]
    lines += [
        f"| {r['team']} | ${r['projected_usd']:,.0f} | {r['budget_usd'] or '-'} | "
        f"{'yes' if r['over_budget'] else 'no'} |"
        for r in forecast
    ]
    lines += ["", "## ROI (estimated)", "", "| Team | Spend | Est. value | ROI |", "|---|---:|---:|---:|"]
    lines += [
        f"| {r['team']} | ${r['spend_usd']:,.0f} | ${r['estimated_value_usd']:,.0f} | {r['roi_multiple']}x |"
        for r in roi
    ]
    return {"file_path": "/roi-report.md", "content": "\n".join(lines) + "\n"}


def _over_budget(ctx: MockContext) -> dict | None:
    forecast = (ctx.last_json("forecast_month_end") or {}).get("teams", [])
    over = [r for r in forecast if r["over_budget"]]
    if not over:
        return None
    r = over[0]
    return {"team": r["team"], "monthly_limit_usd": r["budget_usd"], "action": "notify"}


def _final(ctx: MockContext) -> str:
    load = ctx.last_json("load_usage") or {}
    anomalies = (ctx.last_json("detect_anomalies") or {}).get("findings", [])
    forecast = (ctx.last_json("forecast_month_end") or {}).get("teams", [])
    over = [r["team"] for r in forecast if r["over_budget"]]
    alerts = [n for n, _ in ctx.tool_results if n == "set_budget_alert"]
    kinds = sorted({f["type"] for f in anomalies})
    return "\n".join(
        [
            f"Analyzed **${load.get('total_spend_usd', 0):,.0f}** of AI spend ({load.get('rows')} rows, "
            f"{load.get('from')} to {load.get('to')}).",
            "",
            f"- **${load.get('unattributed_spend_usd', 0):,.0f} is unattributed** (no owning team).",
            f"- {len(anomalies)} findings: {', '.join(kinds)}.",
            f"- Projected over budget this month: {', '.join(over) or 'none'}.",
            f"- Budget alerts proposed: {len(alerts)}.",
            "",
            "The full report is in `/roi-report.md`.",
        ]
    )


def mock_script() -> dict[str, RoleScript]:
    todos = [
        {"content": c, "status": "pending"}
        for c in (
            "Ingest usage exports",
            "Attribute spend by team",
            "Find waste and anomalies",
            "Forecast vs budget and ROI",
            "Write report",
            "Propose budget alerts",
        )
    ]
    todos[0]["status"] = "in_progress"
    labels = [t["content"] for t in todos]
    return {
        "spend-governor": RoleScript(
            steps=[
                Step("write_todos", plan(labels, 0)),
                Step("load_usage", {}),
                Step("write_todos", plan(labels, 1)),
                Step("spend_breakdown", {"dimension": "team"}),
                Step("detect_anomalies", {}),
                Step("write_todos", plan(labels, 2)),
                Step(
                    "task",
                    {
                        "subagent_type": "anomaly-investigator",
                        "description": "Explain the top 3 spend anomalies and their fixes.",
                    },
                ),
                Step("forecast_month_end", {}),
                Step("write_todos", plan(labels, 3)),
                Step("roi_report", {}),
                Step("write_file", _report),
                Step("write_todos", plan(labels, 5)),
                Step("set_budget_alert", _over_budget),
                Step("write_todos", plan(labels, len(labels))),
            ],
            final=_final,
        ),
        "anomaly-investigator": RoleScript(
            steps=[Step("detect_anomalies", {})],
            final=lambda c: (
                "Top findings:\n"
                + "\n".join(
                    f"- {f['type']} ({f['severity']}): {f['action']}"
                    for f in (c.last_json("detect_anomalies") or {}).get("findings", [])[:3]
                )
            ),
        ),
    }


SPEC = AgentSpec(
    id="spend-governor",
    name="AI spend & ROI governor",
    summary="Ingests AI usage and billing exports, attributes spend by team, flags waste and writes an ROI report.",
    icon="wallet",
    system_prompt=SYSTEM_PROMPT,
    tools=t.TOOLS,
    subagents=[
        {
            "name": "anomaly-investigator",
            "description": "Digs into spend anomalies and waste, and explains causes, $ impact and fixes.",
            "system_prompt": INVESTIGATOR_PROMPT,
            "tools": [t.detect_anomalies, t.spend_breakdown],
        }
    ],
    interrupt_on={"set_budget_alert": {"allowed_decisions": ["approve", "edit", "reject"]}},
    example_prompts=[
        "Analyze our AI spend from the sample exports and write an ROI report.",
        "Which teams will go over budget this month, and why?",
        "Where are we wasting money on AI tools and seats?",
    ],
    mock_script=mock_script,
)

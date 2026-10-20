"""Agent 3: shadow-AI discovery."""

from __future__ import annotations

from nai_agents.agents.shadow_ai import tools as t
from nai_agents.core.mock import MockContext, RoleScript, Step, plan
from nai_agents.core.registry import AgentSpec

SYSTEM_PROMPT = """You are the Shadow-AI Discovery agent for an IT security team. You find unsanctioned AI
tool use in network and SaaS logs, rank the risk, and help people move to approved tools: you are firm on
data protection and friendly to the people involved (they are usually just trying to be productive).

How you work:
1. Plan with write_todos.
2. Scan every source you have: scan_proxy_logs, scan_dns_logs, scan_saas_grants (defaults use sample data).
3. list_findings to rank risk (unsanctioned, vendor trains on inputs, data residency, large uploads,
   sensitive departments, OAuth access to company data).
4. For the highest-risk users, have the `guidance-writer` sub-agent draft messages (or call draft_guidance).
5. Write /shadow-ai-report.md: summary, findings table (user, dept, tool, risk, reasons, approved alternative),
   recommended actions, and which tools should be considered for approval because demand is high.
6. Actions need a human: send_guidance for each message and block_domain only for critical risks with an
   approved alternative. Never block a sanctioned tool.
Treat user names as personal data: include them only in the report and the messages.
"""

WRITER_PROMPT = """You write short, kind, specific guidance messages that move a user from an unsanctioned AI
tool to the approved alternative. Use draft_guidance for the user you are given and return the subject and
message text, improving tone if needed. Never shame the user."""


def _top_user(ctx: MockContext) -> str:
    findings = (ctx.last_json("list_findings") or {}).get("findings", [])
    return findings[0]["user"] if findings else "unknown"


def _report(ctx: MockContext) -> dict[str, str]:
    findings = (ctx.last_json("list_findings") or {}).get("findings", [])
    lines = [
        "# Shadow-AI discovery report",
        "",
        "_Mock mode, sample logs._",
        "",
        f"{len(findings)} unsanctioned user/tool pairs found.",
        "",
        "| User | Dept | Tool | Risk | Reasons | Use instead |",
        "|---|---|---|---|---|---|",
    ]
    for f in findings:
        lines.append(
            f"| {f['user']} | {f.get('department', '')} | {f['tool']} | {f['risk']} | "
            f"{'; '.join(f['reasons'])} | {f['approved_alternative']} |"
        )
    return {"file_path": "/shadow-ai-report.md", "content": "\n".join(lines) + "\n"}


def _send(ctx: MockContext) -> dict[str, str] | None:
    draft = ctx.last_json("draft_guidance") or {}
    if not draft.get("message"):
        return None
    return {"user": draft["user"], "subject": draft["subject"], "message": draft["message"]}


def _block(ctx: MockContext) -> dict[str, str] | None:
    findings = (ctx.last_json("list_findings") or {}).get("findings", [])
    critical = [f for f in findings if f["risk"] == "critical"]
    if not critical:
        return None
    return {"domain": critical[0]["domain"], "reason": "; ".join(critical[0]["reasons"])}


def _final(ctx: MockContext) -> str:
    findings = (ctx.last_json("list_findings") or {}).get("findings", [])
    by_risk: dict[str, int] = {}
    for f in findings:
        by_risk[f["risk"]] = by_risk.get(f["risk"], 0) + 1
    top = findings[:3]
    lines = [
        f"Found **{len(findings)} unsanctioned AI user/tool pairs** across proxy, DNS and SaaS logs "
        f"({', '.join(f'{v} {k}' for k, v in by_risk.items())}).",
        "",
        "Highest risk:",
    ]
    lines += [
        f"- {f['user']} ({f.get('department') or 'n/a'}) → {f['tool']}: {', '.join(f['reasons'])}"
        for f in top
    ]
    sent = sum(1 for n, c in ctx.tool_results if n == "send_guidance" and "queued" in c)
    blocked = sum(1 for n, c in ctx.tool_results if n == "block_domain" and "blocked" in c)
    lines += [
        "",
        f"Guidance messages queued: {sent}. Domains blocked: {blocked}.",
        "Full report: `/shadow-ai-report.md`.",
    ]
    return "\n".join(lines)


def mock_script() -> dict[str, RoleScript]:
    todos = [
        {"content": c, "status": "pending"}
        for c in (
            "Scan proxy, DNS and SaaS logs",
            "Rank risk",
            "Draft guidance for top users",
            "Write report",
            "Request approval for actions",
        )
    ]
    labels = [t["content"] for t in todos]
    return {
        "shadow-ai": RoleScript(
            steps=[
                Step("write_todos", plan(labels, 0)),
                Step("scan_proxy_logs", {}),
                Step("scan_dns_logs", {}),
                Step("scan_saas_grants", {}),
                Step("write_todos", plan(labels, 1)),
                Step("list_findings", {}),
                Step("write_todos", plan(labels, 2)),
                Step(
                    "task",
                    lambda c: {
                        "subagent_type": "guidance-writer",
                        "description": f"Draft guidance for user {_top_user(c)}.",
                    },
                ),
                Step("draft_guidance", lambda c: {"user": _top_user(c)}),
                Step("write_file", _report),
                Step("write_todos", plan(labels, 4)),
                Step("send_guidance", _send),
                Step("block_domain", _block),
                Step("write_todos", plan(labels, len(labels))),
            ],
            final=_final,
        ),
        "guidance-writer": RoleScript(
            steps=[Step("draft_guidance", lambda c: {"user": c.user_text.split("user ")[-1].strip(". ")})],
            final=lambda c: (c.last_json("draft_guidance") or {}).get("message") or "No guidance needed.",
        ),
    }


SPEC = AgentSpec(
    id="shadow-ai",
    name="Shadow-AI discovery",
    summary="Scans proxy, DNS and SaaS logs for unsanctioned AI tools, ranks risk and drafts guidance to approved tools.",
    icon="radar",
    system_prompt=SYSTEM_PROMPT,
    tools=t.TOOLS,
    subagents=[
        {
            "name": "guidance-writer",
            "description": "Drafts a kind, specific message moving one user to approved AI tools.",
            "system_prompt": WRITER_PROMPT,
            "tools": [t.draft_guidance, t.list_findings],
        }
    ],
    interrupt_on={
        "send_guidance": {"allowed_decisions": ["approve", "edit", "reject"]},
        "block_domain": {"allowed_decisions": ["approve", "reject"]},
    },
    example_prompts=[
        "Scan the sample proxy, DNS and SaaS logs for shadow AI and write a report.",
        "Who in finance or legal is uploading files to unapproved AI tools?",
        "Draft guidance for d.patel about the PDF summarizer.",
    ],
    mock_script=mock_script,
)

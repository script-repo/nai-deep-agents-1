"""Agent 4: agent identity & permissions broker."""

from __future__ import annotations

import json

from nai_agents.agents.identity_broker import tools as t
from nai_agents.core.mock import MockContext, RoleScript, Step
from nai_agents.core.registry import AgentSpec

SYSTEM_PROMPT = """You are the Agent Identity & Permissions Broker. Other AI agents ask you for access; you
grant the least privilege that gets their task done, for the shortest time, and you keep an audit trail.

Rules:
- Review every request with review_request before issuing anything. Prefer the least_privilege_scopes and
  the recommended TTL; explain any narrowing to the requester.
- issue_credential only for verdict auto_approvable (low/medium risk within ceiling).
- High-risk scopes go through issue_privileged_credential, which pauses for a human. Include a crisp
  justification tied to the task.
- Never issue wildcard, unknown or critical scopes, or anything to unregistered agents: deny and say why.
- Tokens are shown once: never repeat a token in reports or files.
- When asked to process the queue: list_pending_requests, have the `policy-reviewer` sub-agent review the
  tricky ones, act, then write /access-decisions.md (request, decision, scopes granted, TTL, reason) and
  finish with the audit_log summary.
"""

REVIEWER_PROMPT = """You review one agent permission request against least privilege. Use review_request and
list_scope_catalog, then answer: verdict, the minimal scopes that still accomplish the task, TTL, and a
one-line reason. Be strict about PII, production data and sending on behalf of the company."""


def _requests(ctx: MockContext) -> list[dict]:
    data = ctx.last_json("list_pending_requests")
    return data if isinstance(data, list) else []


def _reviews(ctx: MockContext) -> list[dict]:
    return [json.loads(c) for n, c in ctx.tool_results if n == "review_request" and c.startswith("{")]


def _review_step(i: int):
    def args(ctx: MockContext) -> dict | None:
        reqs = _requests(ctx)
        if i >= len(reqs):
            return None
        r = reqs[i]
        return {
            "agent": r["agent"],
            "scopes": ",".join(r["scopes"]),
            "ttl_minutes": r["ttl_minutes"],
            "task": r["task"],
        }

    return args


def _issue_auto(ctx: MockContext) -> dict | None:
    for r in _reviews(ctx):
        if r["verdict"] == "auto_approvable":
            return {
                "agent": r["agent"],
                "scopes": ",".join(r["requested_scopes"]),
                "ttl_minutes": r["recommended_ttl_minutes"],
                "request_id": "req-101",
            }
    return None


def _issue_priv(ctx: MockContext) -> dict | None:
    for r in _reviews(ctx):
        if r["verdict"] == "needs_human_approval":
            return {
                "agent": r["agent"],
                "scopes": ",".join(r["requested_scopes"]),
                "ttl_minutes": r["recommended_ttl_minutes"],
                "request_id": "req-103",
                "justification": f"Task: {r['task']}. Read-only, time-boxed to the refresh window.",
            }
    return None


def _decisions_file(ctx: MockContext) -> dict:
    lines = [
        "# Access decisions",
        "",
        "_Mock mode: simulated credential store._",
        "",
        "| Agent | Requested | Verdict | Least privilege | TTL | Issues |",
        "|---|---|---|---|---|---|",
    ]
    for r in _reviews(ctx):
        lines.append(
            f"| {r['agent']} | {', '.join(r['requested_scopes'])} | {r['verdict']} | "
            f"{', '.join(r['least_privilege_scopes']) or '-'} | {r['recommended_ttl_minutes']} min | "
            f"{' '.join(r['issues']) or '-'} |"
        )
    return {"file_path": "/access-decisions.md", "content": "\n".join(lines) + "\n"}


def _final(ctx: MockContext) -> str:
    reviews = _reviews(ctx)
    issued = [
        json.loads(c)
        for n, c in ctx.tool_results
        if n in {"issue_credential", "issue_privileged_credential"} and '"issued"' in c
    ]
    counts: dict[str, int] = {}
    for r in reviews:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    lines = [
        f"Processed **{len(reviews)} access requests**: "
        + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items())
        + ".",
        "",
    ]
    for c in issued:
        lines.append(
            f"- Issued `{c['id']}` to **{c['agent']}**: {', '.join(c['scopes'])}, expires {c['expires_at']}"
            f" (approved by {c['approved_by']})."
        )
    denied = [r for r in reviews if r["verdict"].startswith("deny")]
    for r in denied:
        lines.append(f"- Denied **{r['agent']}** ({', '.join(r['requested_scopes'])}): {r['issues'][0]}")
    lines += ["", "Decisions are in `/access-decisions.md`; tokens are never written to files."]
    return "\n".join(lines)


def mock_script() -> dict[str, RoleScript]:
    todos = [
        {"content": c, "status": "pending"}
        for c in (
            "Load pending requests",
            "Review each against least privilege",
            "Issue safe credentials",
            "Escalate high-risk access to a human",
            "Record decisions",
        )
    ]
    return {
        "identity-broker": RoleScript(
            steps=[
                Step("write_todos", {"todos": todos}),
                Step("list_pending_requests", {}),
                *[Step("review_request", _review_step(i)) for i in range(5)],
                Step(
                    "task",
                    {
                        "subagent_type": "policy-reviewer",
                        "description": "Review req-102: support-agent wants crm:export and email:send for 240 min "
                        "to reply to 40 customers about the outage.",
                    },
                ),
                Step("issue_credential", _issue_auto),
                Step("issue_privileged_credential", _issue_priv),
                Step("write_file", _decisions_file),
                Step("audit_log", {"limit": 20}),
                Step("write_todos", {"todos": [dict(x, status="completed") for x in todos]}),
            ],
            final=_final,
        ),
        "policy-reviewer": RoleScript(
            steps=[
                Step(
                    "review_request",
                    {
                        "agent": "support-agent",
                        "scopes": "crm:export,email:send",
                        "ttl_minutes": 240,
                        "task": "Reply to customers about the outage",
                    },
                )
            ],
            final=lambda c: (
                "Verdict: deny_or_narrow. Grant crm:read + email:draft for 60 min instead: the agent "
                "can look customers up and prepare drafts; a human sends them. Bulk export and sending "
                "as the company are not needed for this task."
            ),
        ),
    }


SPEC = AgentSpec(
    id="identity-broker",
    name="Agent identity & permissions broker",
    summary="Issues scoped, short-lived credentials to agents, enforces least privilege, and escalates risky scopes.",
    icon="key",
    system_prompt=SYSTEM_PROMPT,
    tools=t.TOOLS,
    subagents=[
        {
            "name": "policy-reviewer",
            "description": "Strict least-privilege review of one permission request; proposes minimal scopes.",
            "system_prompt": REVIEWER_PROMPT,
            "tools": [t.review_request, t.list_scope_catalog],
        }
    ],
    interrupt_on={"issue_privileged_credential": {"allowed_decisions": ["approve", "edit", "reject"]}},
    example_prompts=[
        "Process the pending access requests from our agents.",
        "release-bot needs to push the 2.4.1 release branch. Issue what it needs.",
        "Review: data-analyst wants db:prod:read for 2 hours to refresh the churn dashboard.",
    ],
    mock_script=mock_script,
)

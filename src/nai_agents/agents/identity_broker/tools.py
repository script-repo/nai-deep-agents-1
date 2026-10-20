"""Tools for the agent identity & permissions broker: least-privilege review, short-lived credentials.

The credential store is simulated (a JSON file under the data dir). Tokens are random, shown once, and only
their SHA-256 is stored, the way a real broker (Vault, a cloud STS, SPIFFE) would treat them.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import yaml

from nai_agents.core.data import load_json_state, read_records, read_text, save_json_state, to_json

SUBDIR = "identity_broker"
STORE = "identity_broker/credentials.json"
AUDIT = "identity_broker/audit.json"
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _scopes() -> dict[str, dict[str, Any]]:
    return (yaml.safe_load(read_text("scopes.yaml", SUBDIR)) or {}).get("scopes", {})


def _agents() -> dict[str, dict[str, Any]]:
    return (yaml.safe_load(read_text("agents.yaml", SUBDIR)) or {}).get("agents", {})


def _now() -> datetime:
    return datetime.now(UTC)


def _audit(event: str, **fields: Any) -> None:
    log = load_json_state(AUDIT, [])
    log.append({"at": _now().isoformat(), "event": event, **fields})
    save_json_state(AUDIT, log[-2000:])


def list_scope_catalog() -> str:
    """List every grantable scope with its risk level, maximum TTL and narrower alternatives."""
    return to_json(_scopes())


def list_pending_requests(path: str = "identity_broker/requests.json") -> str:
    """List permission requests waiting for review (agent, scopes, TTL, task, justification)."""
    return to_json(read_records(path, SUBDIR))


def _review(agent: str, scopes: list[str], ttl_minutes: int, task: str = "") -> dict[str, Any]:
    catalog, agents = _scopes(), _agents()
    issues: list[str] = []
    suggestions: dict[str, list[str]] = {}
    registered = agent in agents
    ceiling = set(agents.get(agent, {}).get("ceiling", []))
    if not registered:
        issues.append(f"Agent {agent!r} is not registered (no owner, no ceiling).")
    max_risk = "low"
    allowed_ttl = ttl_minutes
    for s in scopes:
        meta = catalog.get(s)
        if meta is None:
            issues.append(f"Unknown scope {s!r}.")
            max_risk = "critical"
            continue
        if RISK_ORDER[meta["risk"]] > RISK_ORDER[max_risk]:
            max_risk = meta["risk"]
        if registered and s not in ceiling:
            issues.append(f"{s} exceeds {agent}'s ceiling.")
        if meta.get("narrower"):
            suggestions[s] = meta["narrower"]
        allowed_ttl = min(allowed_ttl, int(meta.get("max_ttl_minutes", 60)))
    if allowed_ttl < ttl_minutes:
        issues.append(f"TTL {ttl_minutes} min exceeds the scope maximum; capped at {allowed_ttl} min.")
    if "*" in scopes or not registered:
        verdict = "deny"
    elif any("exceeds" in i and "ceiling" in i for i in issues):
        verdict = "deny_or_narrow"
    elif RISK_ORDER[max_risk] >= RISK_ORDER["high"]:
        verdict = "needs_human_approval"
    else:
        verdict = "auto_approvable"
    least = sorted(
        {
            n
            for s in scopes
            for n in suggestions.get(s, [s])
            if n in catalog and (not registered or n in ceiling or n in suggestions.get(s, []))
        }
    )
    if verdict == "deny":
        least = []
    return {
        "agent": agent,
        "requested_scopes": scopes,
        "max_risk": max_risk,
        "verdict": verdict,
        "issues": issues,
        "narrower_alternatives": suggestions,
        "least_privilege_scopes": least,
        "recommended_ttl_minutes": allowed_ttl,
        "owner": agents.get(agent, {}).get("owner"),
        "task": task,
    }


def review_request(agent: str, scopes: str, ttl_minutes: int = 60, task: str = "") -> str:
    """Review requested scopes (comma-separated) for an agent against least privilege.

    Checks registration, the agent's scope ceiling, each scope's risk and max TTL, and suggests narrower
    scopes. Verdict: auto_approvable | needs_human_approval | deny_or_narrow | deny.
    """
    result = _review(agent, [s.strip() for s in scopes.split(",") if s.strip()], int(ttl_minutes), task)
    _audit("review", agent=agent, scopes=scopes, verdict=result["verdict"])
    return to_json(result)


def _issue(agent: str, scope_list: list[str], ttl: int, request_id: str, approved_by: str) -> dict[str, Any]:
    token = "nda_sim_" + secrets.token_urlsafe(24)
    cred = {
        "id": "cred-" + uuid.uuid4().hex[:10],
        "agent": agent,
        "scopes": scope_list,
        "request_id": request_id,
        "issued_at": _now().isoformat(),
        "expires_at": (_now() + timedelta(minutes=ttl)).isoformat(),
        "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        "status": "active",
        "approved_by": approved_by,
    }
    store = load_json_state(STORE, [])
    store.append(cred)
    save_json_state(STORE, store)
    _audit("issue", agent=agent, scopes=scope_list, credential=cred["id"], approved_by=approved_by, ttl=ttl)
    public = {k: v for k, v in cred.items() if k != "token_sha256"}
    return {
        **public,
        "result": "issued",
        "token": token,
        "note": "Simulated token, shown once. Only its hash is stored.",
    }


def issue_credential(agent: str, scopes: str, ttl_minutes: int = 60, request_id: str = "") -> str:
    """Issue a short-lived credential for LOW/MEDIUM-risk scopes within the agent's ceiling (no approval needed).

    High or critical scopes are refused here: use issue_privileged_credential, which requires a human.
    """
    scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
    review = _review(agent, scope_list, int(ttl_minutes))
    if review["verdict"] != "auto_approvable":
        return to_json(
            {
                "status": "refused",
                "reason": review["verdict"],
                "issues": review["issues"],
                "hint": "Narrow the scopes, or use issue_privileged_credential for human approval.",
            }
        )
    return to_json(_issue(agent, scope_list, review["recommended_ttl_minutes"], request_id, "policy:auto"))


def issue_privileged_credential(
    agent: str, scopes: str, ttl_minutes: int, justification: str, request_id: str = ""
) -> str:
    """Issue a credential that includes HIGH-risk scopes. Always pauses for human approval.

    Still refuses wildcard/unknown scopes, unregistered agents and scopes over the agent's ceiling; the TTL is
    capped at the strictest scope maximum.
    """
    scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
    review = _review(agent, scope_list, int(ttl_minutes))
    if review["verdict"] in {"deny", "deny_or_narrow"} or review["max_risk"] == "critical":
        return to_json(
            {
                "status": "refused",
                "reason": review["verdict"],
                "issues": review["issues"],
                "note": "Critical scopes are break-glass only and are never issued by this broker.",
            }
        )
    return to_json(
        _issue(agent, scope_list, review["recommended_ttl_minutes"], request_id, "human")
        | {"justification": justification}
    )


def list_credentials(active_only: bool = True) -> str:
    """List credentials (never tokens). Expired ones are marked expired."""
    store = load_json_state(STORE, [])
    now = _now().isoformat()
    for c in store:
        if c["status"] == "active" and c["expires_at"] < now:
            c["status"] = "expired"
    save_json_state(STORE, store)
    rows = [
        {k: v for k, v in c.items() if k != "token_sha256"}
        for c in store
        if not active_only or c["status"] == "active"
    ]
    return to_json(rows)


def verify_token(token: str, scope: str) -> str:
    """Check whether a presented token is valid, unexpired, and carries `scope` (what a resource server does)."""
    digest = hashlib.sha256(token.encode()).hexdigest()
    for c in load_json_state(STORE, []):
        if c["token_sha256"] == digest:
            valid = c["status"] == "active" and c["expires_at"] > _now().isoformat()
            return to_json(
                {
                    "valid": valid and scope in c["scopes"],
                    "credential": c["id"],
                    "agent": c["agent"],
                    "status": c["status"] if valid else "expired_or_revoked",
                    "has_scope": scope in c["scopes"],
                }
            )
    return to_json({"valid": False, "reason": "unknown token"})


def revoke_credential(credential_id: str, reason: str) -> str:
    """Revoke a credential immediately (safe action: reduces access)."""
    store = load_json_state(STORE, [])
    for c in store:
        if c["id"] == credential_id:
            c["status"] = "revoked"
            c["revoked_reason"] = reason
            save_json_state(STORE, store)
            _audit("revoke", credential=credential_id, reason=reason)
            return to_json({"status": "revoked", "id": credential_id})
    return to_json({"status": "not_found", "id": credential_id})


def audit_log(limit: int = 50) -> str:
    """The broker's audit trail: reviews, issues and revocations, newest last."""
    return to_json(load_json_state(AUDIT, [])[-limit:])


TOOLS = [
    list_scope_catalog,
    list_pending_requests,
    review_request,
    issue_credential,
    issue_privileged_credential,
    list_credentials,
    verify_token,
    revoke_credential,
    audit_log,
]

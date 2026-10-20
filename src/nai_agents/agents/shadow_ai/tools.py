"""Tools for shadow-AI discovery: scan proxy/DNS/SaaS exports, score risk, draft guidance, act with approval."""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import yaml

from nai_agents.core.data import (
    load_json_state,
    read_records,
    read_text,
    save_json_state,
    state_path,
    to_json,
)

SUBDIR = "shadow_ai"
FINDINGS = "shadow_ai/findings.json"
OUTBOX = "shadow_ai/outbox.json"
BLOCKLIST = "shadow_ai/blocklist.json"
DNS_RE = re.compile(r"client\s+(?:@\S+\s+)?(?P<ip>[\d.]+)#\d+\s+\((?P<host>[^)]+)\)")


def _catalog() -> list[dict[str, Any]]:
    return (yaml.safe_load(read_text("ai_catalog.yaml", SUBDIR)) or {}).get("tools", [])


def _policy() -> dict[str, Any]:
    return yaml.safe_load(read_text("policy.yaml", SUBDIR)) or {}


def _match(host: str) -> dict[str, Any] | None:
    host = host.lower().strip(".")
    best = None
    for tool in _catalog():
        d = tool["domain"].lower()
        if (host == d or host.endswith("." + d)) and (best is None or len(d) > len(best["domain"])):
            best = tool
    return best


def _store(source: str, hits: dict[tuple[str, str], dict[str, Any]]) -> None:
    findings = load_json_state(FINDINGS, {})
    for (user, domain), h in hits.items():
        key = f"{user}|{domain}"
        prev = findings.get(key, {})
        merged = {**prev, **h}
        by_source = {**prev.get("requests_by_source", {}), source: h.get("requests", 0)}
        merged["requests_by_source"] = by_source
        merged["requests"] = sum(by_source.values())
        merged["bytes_out"] = max(prev.get("bytes_out", 0), h.get("bytes_out", 0))
        merged["department"] = h.get("department") or prev.get("department", "")
        merged["sources"] = sorted(by_source)
        findings[key] = merged
    save_json_state(FINDINGS, findings)


def _summarize(hits: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    unsanctioned = [h for h in hits.values() if not h["sanctioned"]]
    return {
        "ai_user_tool_pairs": len(hits),
        "unsanctioned_pairs": len(unsanctioned),
        "unsanctioned_tools": sorted({h["tool"] for h in unsanctioned}),
        "users_on_unsanctioned_tools": sorted({h["user"] for h in unsanctioned}),
    }


def scan_proxy_logs(path: str = "shadow_ai/proxy_log.csv") -> str:
    """Scan a web proxy / secure web gateway export (CSV/JSON) for traffic to AI services.

    Needs columns like user, department, dest_host (or host/url), bytes_out. Aggregates per user and tool.
    """
    hits: dict[tuple[str, str], dict[str, Any]] = {}
    unknown_ai_like = defaultdict(int)
    for r in read_records(path, SUBDIR):
        host = r.get("dest_host") or r.get("host") or r.get("url", "").split("/")[2:3] or [""]
        host = host if isinstance(host, str) else host[0]
        tool = _match(host)
        if not tool:
            if re.search(r"(^|[.-])(ai|gpt|llm|chat|copilot)([.-]|$)", host):
                unknown_ai_like[host] += 1
            continue
        key = (r.get("user", "unknown"), tool["domain"])
        h = hits.setdefault(
            key,
            {
                "user": key[0],
                "department": r.get("department", ""),
                "domain": tool["domain"],
                "tool": tool["tool"],
                "category": tool["category"],
                "sanctioned": bool(tool["sanctioned"]),
                "requests": 0,
                "bytes_out": 0,
                "first_seen": r.get("timestamp"),
                "last_seen": r.get("timestamp"),
            },
        )
        h["requests"] += 1
        h["bytes_out"] += int(float(r.get("bytes_out") or 0))
        h["last_seen"] = r.get("timestamp") or h["last_seen"]
    _store("proxy", hits)
    return to_json(_summarize(hits) | {"unknown_ai_like_hosts": dict(unknown_ai_like)})


def scan_dns_logs(path: str = "shadow_ai/dns_queries.log", ip_map: str = "shadow_ai/dns_ip_map.csv") -> str:
    """Scan DNS query logs (BIND-style query log lines) for AI service lookups; map client IPs to users."""
    ips = (
        {row["src_ip"]: row for row in csv.DictReader(io.StringIO(read_text(ip_map, SUBDIR)))}
        if ip_map
        else {}
    )
    hits: dict[tuple[str, str], dict[str, Any]] = {}
    for line in read_text(path, SUBDIR).splitlines():
        m = DNS_RE.search(line)
        if not m:
            continue
        tool = _match(m["host"])
        if not tool:
            continue
        who = ips.get(m["ip"], {})
        user = who.get("user", m["ip"])
        key = (user, tool["domain"])
        h = hits.setdefault(
            key,
            {
                "user": user,
                "department": who.get("department", ""),
                "domain": tool["domain"],
                "tool": tool["tool"],
                "category": tool["category"],
                "sanctioned": bool(tool["sanctioned"]),
                "requests": 0,
                "bytes_out": 0,
            },
        )
        h["requests"] += 1
    _store("dns", hits)
    return to_json(_summarize(hits))


def scan_saas_grants(path: str = "shadow_ai/saas_oauth_grants.json") -> str:
    """Scan a SaaS/IdP OAuth grant export for AI apps users connected to company data (drive, mail, calendar)."""
    hits: dict[tuple[str, str], dict[str, Any]] = {}
    for g in read_records(path, SUBDIR):
        tool = _match(g.get("app_domain", ""))
        if not tool:
            continue
        key = (g["user"], tool["domain"])
        hits[key] = {
            "user": g["user"],
            "domain": tool["domain"],
            "tool": tool["tool"],
            "category": tool["category"],
            "sanctioned": bool(tool["sanctioned"]),
            "oauth_scopes": g.get("scopes", []),
            "granted_at": g.get("granted_at"),
            "requests": 0,
        }
    _store("saas", hits)
    return to_json(_summarize(hits))


def _score(f: dict[str, Any], tool: dict[str, Any], policy: dict[str, Any]) -> tuple[int, list[str]]:
    score, why = 0, []
    if not f.get("sanctioned"):
        score += 3
        why.append("unsanctioned tool")
    if tool.get("trains_on_inputs"):
        score += 2
        why.append("vendor may train on inputs")
    if tool.get("region") in {"cn", "unknown"}:
        score += 2
        why.append(f"data residency: {tool.get('region')}")
    if tool.get("vendor") == "unknown":
        score += 2
        why.append("unknown vendor")
    if f.get("bytes_out", 0) >= policy.get("large_upload_bytes", 1_000_000):
        score += 2
        why.append(f"large uploads ({f['bytes_out'] / 1e6:.1f} MB)")
    if f.get("department") in policy.get("sensitive_departments", []):
        score += 2
        why.append(f"sensitive department ({f['department']})")
    risky = set(policy.get("risky_oauth_scopes", []))
    granted = [s for s in f.get("oauth_scopes", []) if s in risky]
    if granted and not f.get("sanctioned"):
        score += 3
        why.append("OAuth access to company data: " + ", ".join(granted))
    return score, why


def list_findings(min_risk: str = "low", unsanctioned_only: bool = True) -> str:
    """List discovered AI use per user and tool with a risk level (low/medium/high/critical) and reasons."""
    policy = _policy()
    catalog = {t["domain"]: t for t in _catalog()}
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    out = []
    for f in load_json_state(FINDINGS, {}).values():
        if unsanctioned_only and f.get("sanctioned"):
            continue
        score, why = _score(f, catalog.get(f["domain"], {}), policy)
        level = "critical" if score >= 9 else "high" if score >= 7 else "medium" if score >= 4 else "low"
        if order[level] < order.get(min_risk, 0):
            continue
        out.append(
            {
                **f,
                "risk_score": score,
                "risk": level,
                "reasons": why,
                "approved_alternative": policy.get("approved_alternatives", {}).get(f["category"]),
            }
        )
    out.sort(key=lambda x: -x["risk_score"])
    return to_json({"count": len(out), "findings": out})


def draft_guidance(user: str) -> str:
    """Draft a friendly, specific message to a user explaining which AI tools to move off and what to use instead."""
    findings = [f for f in __import__("json").loads(list_findings())["findings"] if f["user"] == user]
    if not findings:
        return to_json({"user": user, "message": None, "note": "No unsanctioned AI use found for this user."})
    lines = [
        f"Hi {user},",
        "",
        "Thanks for finding ways to work faster with AI. Our network logs show you've been using some AI "
        "tools that aren't approved for company data yet:",
        "",
    ]
    for f in findings:
        lines.append(
            f"- {f['tool']} — {', '.join(f['reasons'])}. Please use instead: {f['approved_alternative']}."
        )
    lines += [
        "",
        "Approved tools keep company and customer data inside our controls (no training on your inputs, "
        "and on-prem where it matters). If something you need is missing, reply and we'll look at approving "
        "it.",
        "",
        "Thanks,",
        "IT Security",
    ]
    return to_json(
        {
            "user": user,
            "subject": "Moving your AI tools to approved options",
            "message": "\n".join(lines),
            "tools": [f["tool"] for f in findings],
        }
    )


def send_guidance(user: str, subject: str, message: str) -> str:
    """Send the guidance message to the user (simulated: queued in the outbox file). Needs approval."""
    outbox = load_json_state(OUTBOX, [])
    outbox.append(
        {"to": user, "subject": subject, "message": message, "queued_at": datetime.now(UTC).isoformat()}
    )
    save_json_state(OUTBOX, outbox)
    return to_json({"status": "queued", "to": user, "outbox": str(state_path(OUTBOX).name)})


def block_domain(domain: str, reason: str) -> str:
    """Add a domain to the proxy blocklist (simulated: written to blocklist.json for your SWG). Needs approval."""
    blocklist = load_json_state(BLOCKLIST, {})
    blocklist[domain] = {"reason": reason, "added_at": datetime.now(UTC).isoformat()}
    save_json_state(BLOCKLIST, blocklist)
    return to_json({"status": "blocked", "domain": domain, "total_blocked": len(blocklist)})


TOOLS = [
    scan_proxy_logs,
    scan_dns_logs,
    scan_saas_grants,
    list_findings,
    draft_guidance,
    send_guidance,
    block_domain,
]

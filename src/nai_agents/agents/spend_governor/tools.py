"""Tools for the AI spend and ROI governor: ingest billing exports, attribute, find waste, report ROI."""

from __future__ import annotations

import csv
import io
import statistics
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from nai_agents.core.data import load_json_state, read_records, read_text, save_json_state, to_json

SUBDIR = "spend_governor"
NORMALIZED = "spend_governor/normalized.json"
POLICIES = "spend_governor/budget_alerts.json"
DEFAULT_PATHS = "spend_governor/usage_openai.csv,spend_governor/usage_anthropic.json"

ALIASES = {
    "date": ["date", "usage_date", "day", "start_time", "timestamp", "bucket_start"],
    "project": ["project", "workspace", "project_id", "workspace_id", "app", "application"],
    "model": ["model", "model_id", "snapshot_id"],
    "requests": ["requests", "request_count", "num_requests", "n_requests", "num_model_requests"],
    "input_tokens": ["input_tokens", "prompt_tokens", "n_context_tokens_total", "tokens_in"],
    "output_tokens": ["output_tokens", "completion_tokens", "n_generated_tokens_total", "tokens_out"],
    "cost_usd": ["cost_usd", "cost", "amount", "amount_usd", "line_item_cost"],
    "api_key": ["api_key_name", "api_key", "key_name", "api_key_id"],
    "user": ["user", "user_email", "user_id"],
}


def _pick(row: dict[str, Any], field: str) -> Any:
    for key in ALIASES[field]:
        if key in row and row[key] not in (None, ""):
            value = row[key]
            if isinstance(value, dict):  # {"amount": 1.2, "currency": "USD"}
                value = value.get("amount", value.get("value"))
            return value
    return None


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _config() -> dict[str, Any]:
    return yaml.safe_load(read_text("teams.yaml", SUBDIR)) or {}


def _rows() -> list[dict[str, Any]]:
    rows = load_json_state(NORMALIZED, None)
    if rows is None:
        load_usage()
        rows = load_json_state(NORMALIZED, [])
    return rows


def load_usage(paths: str = DEFAULT_PATHS) -> str:
    """Ingest AI usage/billing exports (comma-separated paths; CSV, JSON or JSONL from any provider).

    Columns are normalized (date, project, model, requests, tokens, cost, api key, user) and attributed to a
    team via teams.yaml. Returns row counts, date range, total spend and how much spend is unattributed.
    """
    mapping = _config().get("projects", {})
    out: list[dict[str, Any]] = []
    per_file = {}
    for path in [p.strip() for p in paths.split(",") if p.strip()]:
        records = read_records(path, SUBDIR)
        provider = Path(path).stem.replace("usage_", "")
        per_file[path] = len(records)
        for r in records:
            day = str(_pick(r, "date") or "")[:10]
            project = str(_pick(r, "project") or "")
            out.append(
                {
                    "date": day,
                    "provider": provider,
                    "project": project or "(none)",
                    "team": mapping.get(project, "unattributed"),
                    "model": str(_pick(r, "model") or "unknown"),
                    "requests": int(_num(_pick(r, "requests"))),
                    "input_tokens": int(_num(_pick(r, "input_tokens"))),
                    "output_tokens": int(_num(_pick(r, "output_tokens"))),
                    "cost_usd": round(_num(_pick(r, "cost_usd")), 6),
                    "api_key": str(_pick(r, "api_key") or ""),
                    "user": str(_pick(r, "user") or ""),
                }
            )
    save_json_state(NORMALIZED, out)
    dates = sorted(r["date"] for r in out if r["date"])
    total = sum(r["cost_usd"] for r in out)
    unattributed = sum(r["cost_usd"] for r in out if r["team"] == "unattributed")
    return to_json(
        {
            "rows": len(out),
            "files": per_file,
            "from": dates[0] if dates else None,
            "to": dates[-1] if dates else None,
            "total_spend_usd": round(total, 2),
            "unattributed_spend_usd": round(unattributed, 2),
            "teams": sorted({r["team"] for r in out}),
        }
    )


def spend_breakdown(dimension: str = "team", top: int = 10, month: str = "") -> str:
    """Total spend grouped by team, project, model, provider, api_key, user, day or month.

    `month` (YYYY-MM) limits to one month. Returns rows sorted by spend with share of total.
    """
    rows = [r for r in _rows() if not month or r["date"].startswith(month)]
    key = {"day": "date"}.get(dimension, dimension)
    groups: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        g = r["date"][:7] if dimension == "month" else str(r.get(key, ""))
        groups[g]["cost_usd"] += r["cost_usd"]
        groups[g]["requests"] += r["requests"]
        groups[g]["tokens"] += r["input_tokens"] + r["output_tokens"]
    total = sum(v["cost_usd"] for v in groups.values()) or 1
    ordered = sorted(
        groups.items(), key=lambda kv: kv[0] if dimension in {"day", "month"} else -kv[1]["cost_usd"]
    )
    result = [
        {
            dimension: k,
            "cost_usd": round(v["cost_usd"], 2),
            "share_pct": round(v["cost_usd"] / total * 100, 1),
            "requests": int(v["requests"]),
            "cost_per_1k_requests": round(v["cost_usd"] / v["requests"] * 1000, 3) if v["requests"] else None,
        }
        for k, v in ordered
    ]
    return to_json(
        {
            "dimension": dimension,
            "month": month or "all",
            "total_usd": round(total, 2),
            "rows": result if dimension in {"day", "month"} else result[:top],
        }
    )


def _seat_rows() -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(read_text("saas_seats.csv", SUBDIR))))


def detect_anomalies(z_threshold: float = 3.5) -> str:
    """Find waste and anomalies.

    Checks: daily spend spikes per team (robust z-score on median absolute deviation), premium models used for
    tiny outputs (classification-like traffic on frontier models), unattributed spend (unknown projects/keys),
    idle SaaS seats (<30% active), and overlapping subscriptions (two tools of one category in one team).
    """
    rows = _rows()
    findings: list[dict[str, Any]] = []
    # 1) spikes
    daily: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        daily[r["team"]][r["date"]] += r["cost_usd"]
    for team, series in daily.items():
        values = list(series.values())
        if len(values) < 7:
            continue
        med = statistics.median(values)
        mad = statistics.median(abs(v - med) for v in values) or 1e-9
        for day, v in sorted(series.items()):
            z = 0.6745 * (v - med) / mad
            if z > z_threshold:
                findings.append(
                    {
                        "type": "spend_spike",
                        "severity": "high" if z > z_threshold * 3 else "medium",
                        "team": team,
                        "date": day,
                        "cost_usd": round(v, 2),
                        "typical_usd": round(med, 2),
                        "robust_z": round(z, 1),
                        "action": "Check for a runaway job or loop; add a per-day cap on this key.",
                    }
                )
    # 2) premium model, tiny outputs
    by_proj: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        k = (r["project"], r["model"])
        by_proj[k]["req"] += r["requests"]
        by_proj[k]["out"] += r["output_tokens"]
        by_proj[k]["cost"] += r["cost_usd"]
    for (proj, model), v in by_proj.items():
        avg_out = v["out"] / v["req"] if v["req"] else 0
        if "frontier" in model and avg_out < 30 and v["cost"] > 50:
            findings.append(
                {
                    "type": "model_overkill",
                    "severity": "high",
                    "project": proj,
                    "model": model,
                    "avg_output_tokens": round(avg_out, 1),
                    "cost_usd": round(v["cost"], 2),
                    "est_savings_usd": round(v["cost"] * 0.85, 2),
                    "action": "Short, classification-like outputs: route to a small model "
                    "(use the cost router to prove quality).",
                }
            )
    # 3) unattributed
    unattributed = defaultdict(float)
    for r in rows:
        if r["team"] == "unattributed":
            unattributed[r["api_key"] or r["project"]] += r["cost_usd"]
    for key, cost in unattributed.items():
        findings.append(
            {
                "type": "unattributed_spend",
                "severity": "medium",
                "api_key": key,
                "cost_usd": round(cost, 2),
                "action": "Find the owner, map the key to a project in teams.yaml, or revoke it.",
            }
        )
    # 4) idle seats + 5) overlap
    seats = _seat_rows()
    by_team_cat: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for s in seats:
        used = int(s["active_users_30d"]) / max(int(s["seats"]), 1)
        by_team_cat[(s["team"], s["category"])].append(s)
        if used < 0.3:
            idle = int(s["seats"]) - int(s["active_users_30d"])
            per_seat = float(s["monthly_cost_usd"]) / max(int(s["seats"]), 1)
            findings.append(
                {
                    "type": "idle_seats",
                    "severity": "medium",
                    "tool": s["tool"],
                    "team": s["team"],
                    "seats": int(s["seats"]),
                    "active": int(s["active_users_30d"]),
                    "monthly_waste_usd": round(idle * per_seat, 2),
                    "action": "Reclaim unused seats at renewal.",
                }
            )
    for (team, cat), tools in by_team_cat.items():
        if len(tools) > 1:
            findings.append(
                {
                    "type": "overlapping_tools",
                    "severity": "low",
                    "team": team,
                    "category": cat,
                    "tools": [t["tool"] for t in tools],
                    "monthly_cost_usd": round(sum(float(t["monthly_cost_usd"]) for t in tools), 2),
                    "action": "Standardize on one tool per category per team.",
                }
            )
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order[f["severity"]])
    return to_json({"count": len(findings), "findings": findings})


def forecast_month_end(month: str = "") -> str:
    """Project each team's month-end spend from month-to-date run rate and compare it with its budget.

    `month` is YYYY-MM; empty means the latest month in the data.
    """
    rows = _rows()
    month = month or max(r["date"][:7] for r in rows)
    in_month = [r for r in rows if r["date"].startswith(month)]
    last_day = max(int(r["date"][8:10]) for r in in_month)
    y, m = map(int, month.split("-"))
    days_in_month = ((date(y + m // 12, m % 12 + 1, 1)) - date(y, m, 1)).days
    budgets = _config().get("budgets", {})
    spend: dict[str, float] = defaultdict(float)
    for r in in_month:
        spend[r["team"]] += r["cost_usd"]
    out = []
    for team, mtd in sorted(spend.items(), key=lambda kv: -kv[1]):
        projected = mtd / last_day * days_in_month
        budget = budgets.get(team)
        out.append(
            {
                "team": team,
                "month_to_date_usd": round(mtd, 2),
                "projected_usd": round(projected, 2),
                "budget_usd": budget,
                "projected_vs_budget_pct": round(projected / budget * 100, 1) if budget else None,
                "over_budget": bool(budget and projected > budget),
            }
        )
    return to_json({"month": month, "days_elapsed": last_day, "days_in_month": days_in_month, "teams": out})


def roi_report(month: str = "") -> str:
    """Estimate value vs. spend per team using value.yaml (minutes saved x hourly rate x success rate)."""
    rows = [r for r in _rows() if not month or r["date"].startswith(month)]
    value_cfg = yaml.safe_load(read_text("value.yaml", SUBDIR)) or {}
    defaults = value_cfg.get("defaults", {})
    spend: dict[str, float] = defaultdict(float)
    reqs: dict[str, int] = defaultdict(int)
    for r in rows:
        spend[r["team"]] += r["cost_usd"]
        reqs[r["team"]] += r["requests"]
    out = []
    for team in sorted(spend, key=lambda t: -spend[t]):
        cfg = {**defaults, **value_cfg.get("teams", {}).get(team, {})}
        value = (
            reqs[team] * cfg["success_rate"] * cfg["minutes_saved_per_request"] / 60 * cfg["hourly_rate_usd"]
        )
        out.append(
            {
                "team": team,
                "spend_usd": round(spend[team], 2),
                "requests": reqs[team],
                "estimated_value_usd": round(value, 2),
                "roi_multiple": round(value / spend[team], 1) if spend[team] else None,
                "assumptions": cfg if team in value_cfg.get("teams", {}) else "defaults",
            }
        )
    return to_json(
        {
            "month": month or "all",
            "teams": out,
            "caveat": "Value is an estimate from team-owned assumptions in value.yaml, not measured.",
        }
    )


def list_budget_alerts() -> str:
    """Show the budget alerts and caps currently configured."""
    return to_json(load_json_state(POLICIES, {}))


def set_budget_alert(team: str, monthly_limit_usd: float, action: str = "notify") -> str:
    """Create or change a team's monthly budget alert. action: notify | throttle | block. Needs approval."""
    if action not in {"notify", "throttle", "block"}:
        return to_json({"error": "action must be notify, throttle or block"})
    policies = load_json_state(POLICIES, {})
    previous = policies.get(team)
    policies[team] = {
        "monthly_limit_usd": float(monthly_limit_usd),
        "action": action,
        "set_at": datetime.now(UTC).isoformat(),
        "previous": previous,
    }
    save_json_state(POLICIES, policies)
    return to_json({"status": "saved", "team": team, **policies[team]})


TOOLS = [
    load_usage,
    spend_breakdown,
    detect_anomalies,
    forecast_month_end,
    roi_report,
    list_budget_alerts,
    set_budget_alert,
]

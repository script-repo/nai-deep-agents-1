"""Tools for the model cost router: catalog, evals, comparison, routing table, savings."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path

import yaml

from nai_agents.core.data import load_json_state, read_text, resolve_input, save_json_state, to_json
from nai_agents.core.evals import load_suite, run_suite
from nai_agents.core.models import find_model, load_catalog
from nai_agents.core.settings import get_settings

ROUTES_FILE = "cost_router/routes.json"
HISTORY_FILE = "cost_router/eval_history.json"
DEFAULT_SUITE = "evals/general.yaml"


def list_models() -> str:
    """List candidate models from the catalog with their price per million input/output tokens."""
    return to_json([m.public() for m in load_catalog()])


def list_eval_suites() -> str:
    """List available eval suites (YAML/JSON files under examples/evals) with their categories."""
    out = []
    for path in sorted((get_settings().examples_dir / "evals").glob("*.y*ml")):
        suite = load_suite(path)
        out.append({"path": f"evals/{path.name}", "name": suite.name, "cases": len(suite.cases),
                    "categories": suite.categories(), "threshold": suite.threshold})
    return to_json(out)


def _record(summary: dict) -> None:
    history = load_json_state(HISTORY_FILE, [])
    history.append({**summary, "at": datetime.now(UTC).isoformat()})
    save_json_state(HISTORY_FILE, history[-500:])


def run_eval(model_name: str, suite_path: str = DEFAULT_SUITE, category: str = "") -> str:
    """Run an eval suite (optionally one category) against one catalog model.

    Returns pass rate, latency percentiles, cost of the run and failed case ids.
    """
    suite = load_suite(resolve_input(suite_path)).subset(category or None)
    report = run_suite(find_model(model_name), suite)
    summary = report.summary() | {"category": category or "all"}
    _record(summary)
    return to_json(summary)


def compare_models(category: str = "", suite_path: str = DEFAULT_SUITE, models: str = "") -> str:
    """Evaluate several models (comma-separated names; default: the whole catalog) on a category.

    Returns them sorted by cost per request, marks which meet the suite's quality threshold, and names
    the cheapest model that passes.
    """
    catalog = load_catalog()
    wanted = [m.strip() for m in models.split(",") if m.strip()]
    entries = [find_model(n, catalog) for n in wanted] if wanted else catalog
    suite = load_suite(resolve_input(suite_path)).subset(category or None)
    rows = []
    for entry in entries:
        report = run_suite(entry, suite)
        summary = report.summary() | {"category": category or "all"}
        _record(summary)
        rows.append({
            "model": entry.name, "pass_rate": report.pass_rate, "meets_threshold": report.meets_threshold,
            "cost_per_request_usd": round(report.cost_usd / max(report.total, 1), 8),
            "p50_latency_ms": report.p50_latency_ms, "p95_latency_ms": report.p95_latency_ms,
        })
    rows.sort(key=lambda r: r["cost_per_request_usd"])
    passing = [r for r in rows if r["meets_threshold"]]
    return to_json({
        "category": category or "all", "threshold": suite.threshold, "results": rows,
        "cheapest_passing": passing[0]["model"] if passing else None,
        "note": None if passing else "No model met the threshold; keep the strongest model or improve prompts.",
    })


def _rules() -> dict:
    return yaml.safe_load(read_text("cost_router/routing_rules.yaml")) or {}


def route_task(task: str) -> str:
    """Pick the model for a single incoming task: classify it, then look up the routing table.

    Falls back to the most capable catalog model when the category has no approved route yet.
    """
    rules = _rules()
    text = task.lower()
    scores = {cat: sum(1 for kw in kws if kw in text) for cat, kws in rules.get("categories", {}).items()}
    category = max(scores, key=lambda c: scores[c]) if any(scores.values()) else rules.get("default_category")
    routes = load_json_state(ROUTES_FILE, {})
    catalog = load_catalog()
    fallback = max(catalog, key=lambda m: m.output_per_mtok).name
    route = routes.get(category)
    return to_json({"category": category, "keyword_scores": scores,
                    "model": route["model"] if route else fallback,
                    "source": "routing table" if route else "fallback (no approved route yet)"})


def show_routes() -> str:
    """Show the current routing table (category -> model) and when each route was approved."""
    return to_json(load_json_state(ROUTES_FILE, {}))


def apply_routing(category: str, model_name: str, reason: str) -> str:
    """Write a route (category -> model) to the routing table used in production. Needs approval."""
    find_model(model_name)
    routes = load_json_state(ROUTES_FILE, {})
    previous = routes.get(category, {}).get("model")
    routes[category] = {"model": model_name, "reason": reason, "approved_at": datetime.now(UTC).isoformat(),
                        "previous": previous}
    path = save_json_state(ROUTES_FILE, routes)
    return to_json({"status": "applied", "category": category, "model": model_name, "previous": previous,
                    "routes_file": str(Path(path).name)})


def estimate_savings(routes_json: str = "", workload_path: str = "cost_router/workload.csv") -> str:
    """Estimate monthly spend now vs. with proposed routes.

    `routes_json` is a JSON object {category: model}; empty means use the approved routing table.
    The workload CSV has: category, monthly_requests, avg_input_tokens, avg_output_tokens, current_model.
    """
    import json

    proposed = json.loads(routes_json) if routes_json.strip() else {
        k: v["model"] for k, v in load_json_state(ROUTES_FILE, {}).items()}
    catalog = load_catalog()
    rows, now_total, new_total = [], 0.0, 0.0
    for row in csv.DictReader(io.StringIO(read_text(workload_path))):
        req = int(row["monthly_requests"])
        tin, tout = int(row["avg_input_tokens"]) * req, int(row["avg_output_tokens"]) * req
        current = find_model(row["current_model"], catalog)
        target = find_model(proposed.get(row["category"], current.name), catalog)
        now, new = current.cost(tin, tout), target.cost(tin, tout)
        now_total += now
        new_total += new
        rows.append({"category": row["category"], "current_model": current.name, "proposed_model": target.name,
                     "monthly_cost_now": round(now, 2), "monthly_cost_proposed": round(new, 2)})
    return to_json({"rows": rows, "monthly_total_now": round(now_total, 2),
                    "monthly_total_proposed": round(new_total, 2),
                    "monthly_savings": round(now_total - new_total, 2),
                    "savings_pct": round((1 - new_total / now_total) * 100, 1) if now_total else 0.0})


TOOLS = [list_models, list_eval_suites, run_eval, compare_models, route_task, show_routes, apply_routing,
         estimate_savings]

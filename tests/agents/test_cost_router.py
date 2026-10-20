import json

from nai_agents.agents.cost_router import SPEC
from nai_agents.agents.cost_router import tools as t
from tests.helpers import interrupted_tools, run_agent, run_and_reject, tool_calls


def test_catalog_and_suites():
    models = json.loads(t.list_models())
    assert {m["name"] for m in models} >= {"mock-tiny", "mock-small", "mock-medium", "mock-large"}
    suites = json.loads(t.list_eval_suites())
    assert suites[0]["path"] == "evals/general.yaml"
    assert "reasoning" in suites[0]["categories"]


def test_compare_models_picks_cheapest_passing():
    data = json.loads(t.compare_models(category="classification"))
    costs = [r["cost_per_request_usd"] for r in data["results"]]
    assert costs == sorted(costs)
    best = data["cheapest_passing"]
    assert best is not None
    passing = [r for r in data["results"] if r["meets_threshold"]]
    assert passing[0]["model"] == best
    # deterministic: same answer twice
    assert json.loads(t.compare_models(category="classification"))["cheapest_passing"] == best


def test_route_task_uses_table_after_apply():
    before = json.loads(t.route_task("Classify the sentiment of this ticket"))
    assert before["category"] == "classification"
    assert before["source"].startswith("fallback")
    t.apply_routing("classification", "mock-small", "test")
    after = json.loads(t.route_task("Classify the sentiment of this ticket"))
    assert after["model"] == "mock-small" and after["source"] == "routing table"


def test_estimate_savings():
    data = json.loads(
        t.estimate_savings(json.dumps({"classification": "mock-small", "extraction": "mock-small"}))
    )
    assert data["monthly_total_proposed"] < data["monthly_total_now"]
    assert data["monthly_savings"] > 0


def test_end_to_end_with_approval():
    result = run_agent(SPEC)
    assert interrupted_tools(result) == ["apply_routing"]
    calls = tool_calls(result)
    assert "task" in calls and "compare_models" in tool_calls(result, agent="eval-runner")
    snap = result["snapshot"]
    assert "/routing-report.md" in snap["files"]
    assert all(todo["status"] == "completed" for todo in snap["todos"])
    assert "Applied routes: classification" in result["final"]
    assert json.loads(t.show_routes())["classification"]["model"]


def test_rejection_leaves_routes_untouched():
    result = run_and_reject(SPEC)
    assert "No routes were applied." in result["final"]
    assert json.loads(t.show_routes()) == {}

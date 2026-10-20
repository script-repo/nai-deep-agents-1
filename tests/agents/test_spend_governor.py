import json

from nai_agents.agents.spend_governor import SPEC
from nai_agents.agents.spend_governor import tools as t
from tests.helpers import interrupted_tools, run_agent, run_and_reject


def test_load_and_attribute():
    data = json.loads(t.load_usage())
    assert data["rows"] > 400
    assert data["unattributed_spend_usd"] > 0
    teams = json.loads(t.spend_breakdown("team"))["rows"]
    assert teams[0]["team"] == "engineering"
    assert abs(sum(r["share_pct"] for r in teams) - 100) < 0.5


def test_anomalies_find_planted_issues():
    t.load_usage()
    findings = json.loads(t.detect_anomalies())["findings"]
    kinds = {(f["type"], f.get("team") or f.get("project") or f.get("api_key")) for f in findings}
    assert ("spend_spike", "data-science") in kinds
    assert ("model_overkill", "support-bot") in kinds
    assert ("unattributed_spend", "temp-key-jsmith") in kinds
    assert ("idle_seats", "finance") in kinds
    assert any(f["type"] == "overlapping_tools" and f["team"] == "marketing" for f in findings)


def test_forecast_and_roi():
    t.load_usage()
    over = {r["team"] for r in json.loads(t.forecast_month_end())["teams"] if r["over_budget"]}
    assert {"engineering", "data-science"} <= over
    roi = json.loads(t.roi_report())["teams"]
    assert all(r["estimated_value_usd"] >= 0 for r in roi)


def test_budget_alert_validation():
    assert "error" in json.loads(t.set_budget_alert("sales", 100, action="explode"))


def test_end_to_end():
    result = run_agent(SPEC)
    assert interrupted_tools(result) == ["set_budget_alert"]
    assert "/roi-report.md" in result["snapshot"]["files"]
    assert "unattributed" in result["final"]
    assert json.loads(t.list_budget_alerts())


def test_rejection():
    run_and_reject(SPEC)
    assert json.loads(t.list_budget_alerts()) == {}

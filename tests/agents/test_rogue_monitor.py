import json

from nai_agents.agents.rogue_monitor import SPEC
from nai_agents.agents.rogue_monitor import tools as t
from tests.helpers import interrupted_tools, run_agent, run_and_reject


def test_detectors():
    assert "build-bot" in json.loads(t.detect_sandbox_escape())["agents"]
    deception = json.loads(t.detect_deception())["agents"]
    assert any("tests passed" in h["technique"] for h in deception["report-writer"])
    exfil = json.loads(t.detect_exfiltration())["agents"]
    assert list(exfil) == ["data-sync"]
    covert = json.loads(t.detect_covert_channels())["agents"]
    assert any("DNS tunnelling" in h["technique"] for h in covert["helper-7"])


def test_benign_agent_not_flagged():
    ranking = json.loads(t.threat_summary())["ranking"]
    ids = [r["agent_id"] for r in ranking]
    assert "faq-bot" not in ids
    actions = {r["agent_id"]: r["recommended_action"] for r in ranking}
    assert actions["data-sync"] == "kill" and actions["build-bot"] == "kill"
    assert actions["helper-7"] == "quarantine"


def test_end_to_end():
    result = run_agent(SPEC)
    assert set(interrupted_tools(result)) == {"kill_agent", "quarantine_agent"}
    files = result["snapshot"]["files"]
    assert "/incident-summary.md" in files and any(p.startswith("/incidents/") for p in files)
    status = json.loads(t.containment_status())
    assert status["data-sync"]["status"] == "killed"
    assert status["helper-7"]["status"] == "quarantined"


def test_rejection_contains_nothing():
    run_and_reject(SPEC)
    assert json.loads(t.containment_status()) == {}

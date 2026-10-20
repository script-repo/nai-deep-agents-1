import json

from nai_agents.agents.shadow_ai import SPEC
from nai_agents.agents.shadow_ai import tools as t
from nai_agents.core.data import load_json_state
from tests.helpers import interrupted_tools, run_agent, run_and_reject


def scan_all():
    t.scan_proxy_logs()
    t.scan_dns_logs()
    t.scan_saas_grants()


def test_scans_find_unsanctioned_tools():
    proxy = json.loads(t.scan_proxy_logs())
    assert "DeepSeek Chat" in proxy["unsanctioned_tools"]
    assert "GitHub Copilot Business" not in proxy["unsanctioned_tools"]
    dns = json.loads(t.scan_dns_logs())
    assert "tl;dv" in dns["unsanctioned_tools"]
    saas = json.loads(t.scan_saas_grants())
    assert saas["unsanctioned_pairs"] == 2


def test_risk_ranking():
    scan_all()
    findings = json.loads(t.list_findings())["findings"]
    top = findings[0]
    assert top["user"] == "d.patel" and top["risk"] == "critical"
    assert any("OAuth access" in r for r in top["reasons"])
    assert all(not f["sanctioned"] for f in findings)
    assert {f["risk"] for f in findings} >= {"critical", "medium", "low"}


def test_guidance_mentions_alternative():
    scan_all()
    msg = json.loads(t.draft_guidance("i.moreau"))["message"]
    assert "DeepSeek" in msg and "Internal AI Chat" in msg
    assert json.loads(t.draft_guidance("j.osei"))["message"] is None


def test_end_to_end():
    result = run_agent(SPEC)
    assert interrupted_tools(result) == ["send_guidance", "block_domain"]
    assert "/shadow-ai-report.md" in result["snapshot"]["files"]
    assert len(load_json_state("shadow_ai/outbox.json", [])) == 1
    assert "pdf-ai-summarizer.example.net" in load_json_state("shadow_ai/blocklist.json", {})


def test_rejection_sends_nothing():
    run_and_reject(SPEC)
    assert load_json_state("shadow_ai/outbox.json", []) == []
    assert load_json_state("shadow_ai/blocklist.json", {}) == {}

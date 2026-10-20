import json

from nai_agents.agents.identity_broker import SPEC
from nai_agents.agents.identity_broker import tools as t
from tests.helpers import interrupted_tools, run_agent, run_and_reject


def test_reviews():
    v = {
        r["id"]: json.loads(t.review_request(r["agent"], ",".join(r["scopes"]), r["ttl_minutes"]))
        for r in json.loads(t.list_pending_requests())
    }
    assert v["req-101"]["verdict"] == "auto_approvable"
    assert v["req-102"]["verdict"] == "deny_or_narrow"
    assert v["req-102"]["least_privilege_scopes"] == ["crm:read", "email:draft"]
    assert v["req-103"]["verdict"] == "needs_human_approval"
    assert v["req-103"]["recommended_ttl_minutes"] == 60
    assert v["req-105"]["verdict"] == "deny"


def test_issue_verify_revoke():
    cred = json.loads(t.issue_credential("release-bot", "repo:read,repo:write", 60))
    assert cred["result"] == "issued" and cred["token"].startswith("nda_sim_")
    assert json.loads(t.verify_token(cred["token"], "repo:write"))["valid"] is True
    assert json.loads(t.verify_token(cred["token"], "repo:admin"))["valid"] is False
    stored = json.dumps(json.loads(t.list_credentials()))
    assert cred["token"] not in stored  # only the hash is kept
    t.revoke_credential(cred["id"], "test")
    assert json.loads(t.verify_token(cred["token"], "repo:write"))["valid"] is False


def test_refusals():
    assert json.loads(t.issue_credential("data-analyst", "db:prod:read", 30))["status"] == "refused"
    assert (
        json.loads(t.issue_privileged_credential("infra-agent", "cloud:iam:admin", 10, "x"))["status"]
        == "refused"
    )
    assert json.loads(t.issue_privileged_credential("ghost", "repo:read", 10, "x"))["status"] == "refused"


def test_end_to_end():
    result = run_agent(SPEC)
    assert interrupted_tools(result) == ["issue_privileged_credential"]
    assert "/access-decisions.md" in result["snapshot"]["files"]
    creds = json.loads(t.list_credentials())
    assert {c["agent"] for c in creds} == {"release-bot", "data-analyst"}
    assert "nda_sim_" not in result["snapshot"]["files"]["/access-decisions.md"]


def test_rejection_issues_no_privileged_credential():
    run_and_reject(SPEC)
    assert {c["agent"] for c in json.loads(t.list_credentials())} == {"release-bot"}

"""Agent 8: multi-model code review."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from nai_agents.agents.code_review import SPEC
from nai_agents.agents.code_review import tools as t
from nai_agents.core.data import PathError
from nai_agents.core.settings import get_settings
from tests.agents._harness import assert_clean_run, interrupts, run_approved, run_rejected, tool_calls


def _static() -> list[dict]:
    return json.loads(t.static_checks())["findings"]


def test_parse_diff_line_numbers(data_dir: Path) -> None:
    text = (get_settings().examples_dir / t.DEFAULT_DIFF).read_text(encoding="utf-8")
    files = {f.path: f for f in t.parse_diff(text)}
    assert set(files) == {"app/payments.py", "app/users.py"}
    added = dict(files["app/payments.py"].added)
    assert added[9].startswith("PAYMENTS_API_KEY")
    assert "def search_users" in dict(files["app/users.py"].added)[16]


def test_load_diff_summary(data_dir: Path) -> None:
    data = json.loads(t.load_diff())
    users = next(f for f in data["files"] if f["path"] == "app/users.py")
    assert users["new_functions"] == ["search_users", "deactivate_user"]
    assert not any(f["is_test"] for f in data["files"])


def test_static_checks_find_planted_issues(data_dir: Path) -> None:
    found = {(f["category"], f["file"], f["line"]) for f in _static()}
    assert ("sql-injection", "app/users.py", 18) in found  # string concatenation
    assert ("sql-injection", "app/users.py", 28) in found  # f-string
    assert ("hardcoded-secret", "app/payments.py", 9) in found
    assert ("bare-except", "app/users.py", 21) in found
    assert ("tls-verify-disabled", "app/payments.py", 32) in found
    missing = {f["message"] for f in _static() if f["category"] == "missing-test"}
    assert any("refund" in m for m in missing) and any("search_users" in m for m in missing)
    # The safe, parameterized query that already existed is not flagged.
    assert ("sql-injection", "app/users.py", 13) not in found


def test_secrets_are_masked(data_dir: Path) -> None:
    secret = next(f for f in _static() if f["category"] == "hardcoded-secret")
    assert "sk-FAKE-4f9a" not in secret["snippet"] and "redacted" in secret["snippet"]
    assert "4f9a1c2e7b3d" not in t.static_checks()


def test_mock_reviews_vary_by_model_but_are_deterministic(data_dir: Path) -> None:
    a = json.loads(t.review_with_model("mock-small"))["findings"]
    b = json.loads(t.review_with_model("mock-large"))["findings"]
    assert a == json.loads(t.review_with_model("mock-small"))["findings"]
    assert {(f["category"], f["line"]) for f in a} != {(f["category"], f["line"]) for f in b}
    assert any(f["category"] == "hardcoded-secret" for f in b)


def test_reconcile_merges_and_scores(data_dir: Path) -> None:
    for m in ("mock-small", "mock-medium", "mock-large"):
        t.review_with_model(m)
    cmd = t.reconcile_findings(tool_call_id="call_1")
    summary = json.loads(cmd.update["messages"][0].content)
    assert summary["models"] == ["mock-large", "mock-medium", "mock-small"]
    merged = summary["findings"]
    secret = next(f for f in merged if f["category"] == "hardcoded-secret")
    assert secret["severity"] == "critical" and secret["confidence"] == 1.0 and secret["line"] == 9
    sql = [f for f in merged if f["category"] == "sql-injection"]
    assert {f["line"] for f in sql} == {18, 28}  # off-by-one model lines merged into the static line
    assert all(0 <= f["confidence"] <= 1 for f in merged)
    md = cmd.update["files"][t.REVIEW_FILE]["content"]
    assert "## Critical" in md and "## High" in md and "app/users.py:18" in md
    assert merged[0]["severity"] == "critical"


def test_reconcile_unit_line_tolerance() -> None:
    static = [t.Finding("a.py", 10, "bug", "medium", "m")]
    per_model = {
        "m1": [t.Finding("a.py", 12, "bug", "high", "x", source="m1")],
        "m2": [
            t.Finding("a.py", 13, "bug", "low", "y", source="m2"),
            t.Finding("b.py", 1, "style", "medium", "z", source="m2"),
        ],
    }
    merged = t._reconcile(static, per_model)
    first = next(f for f in merged if f["file"] == "a.py" and f["line"] == 10)
    assert first["severity"] == "high" and first["models"] == ["m1"] and first["confidence"] == 0.5
    lone = next(f for f in merged if f["file"] == "b.py")
    assert lone["severity"] == "low"  # one model of two, no static support: downgraded
    assert len(merged) == 3  # line 13 is 3 lines from the static finding: its own cluster


def test_parse_findings_is_robust() -> None:
    fenced = (
        '```json\n{"findings": [{"file": "b/app/x.py", "line": "7", "category": "SQL Injection",'
        ' "severity": "Major", "message": "concat"},]}\n```'
    )
    out = t.parse_findings(fenced, "m")
    assert out[0].file == "app/x.py" and out[0].line == 7
    assert out[0].category == "sql-injection" and out[0].severity == "high"
    plain = t.parse_findings("- app/y.py:3: high: hard-coded secret here", "m")
    assert plain[0].category == "hardcoded-secret" and plain[0].line == 3
    assert t.parse_findings("no findings, looks good", "m") == []


def test_real_model_path_with_fake_llm(data_dir: Path) -> None:
    text = (get_settings().examples_dir / t.DEFAULT_DIFF).read_text(encoding="utf-8")
    answer = json.dumps(
        {
            "findings": [
                {
                    "file": "app/users.py",
                    "line": 18,
                    "category": "sql-injection",
                    "severity": "critical",
                    "message": "injection",
                }
            ]
        }
    )
    out = t._review_real(FakeListChatModel(responses=[answer]), text, "fake")
    assert [(f.file, f.line, f.severity, f.source) for f in out] == [("app/users.py", 18, "critical", "fake")]


def test_run_tests_allowlist_and_confinement(data_dir: Path) -> None:
    with pytest.raises(ValueError):
        t.run_tests("rm -rf /")
    with pytest.raises(ValueError):
        t.run_tests("pytest; curl evil.example.com")
    with pytest.raises(PathError):
        t.run_tests("pytest", cwd="/")


def test_run_tests_runs_pytest(data_dir: Path) -> None:
    out = json.loads(t.run_tests("python -m pytest -q"))
    assert out["status"] == "passed" and "3 passed" in out["summary"]
    project = get_settings().examples_dir / t.DEFAULT_PROJECT
    assert not list(project.rglob("__pycache__")) and not (project / ".pytest_cache").exists()


@pytest.mark.skipif(not shutil.which("git"), reason="git not installed")
def test_load_diff_from_git_repo(data_dir: Path) -> None:
    repo = data_dir / "repos" / "svc"
    repo.mkdir(parents=True)
    git = [
        "git",
        "-C",
        str(repo),
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=t",
        "-c",
        "commit.gpgsign=false",
    ]
    subprocess.run([*git, "init", "-q"], check=True)
    (repo / "db.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    (repo / "db.py").write_text('x = 1\nq = "SELECT * FROM t WHERE id = " + uid\n', encoding="utf-8")
    found = json.loads(t.static_checks(str(repo)))["findings"]
    assert [(f["category"], f["line"]) for f in found] == [("sql-injection", 2)]


async def test_end_to_end_approved(data_dir: Path) -> None:
    result = await run_approved(SPEC)
    assert_clean_run(result)
    assert [i["actions"][0]["name"] for i in interrupts(result)] == ["run_tests"]
    reviewers = [c["args"]["description"] for c in tool_calls(result, "task")]
    assert len(reviewers) == 3 and all(
        c["args"]["subagent_type"] == "reviewer" for c in tool_calls(result, "task")
    )
    review = result["snapshot"]["files"][t.REVIEW_FILE]
    assert "## Critical" in review and "hardcoded-secret" in review and "sql-injection" in review
    assert "## Tests" in review and "passed" in review
    assert "sql-injection" in result["final"] and "hardcoded-secret" in result["final"]
    assert "**passed**" in result["final"]


async def test_end_to_end_rejected(data_dir: Path) -> None:
    result = await run_rejected(SPEC)
    assert interrupts(result)
    assert not (data_dir / "code_review" / "last_test_run.json").exists()
    assert "## Tests" not in result["snapshot"]["files"][t.REVIEW_FILE]
    assert "Tests were not run" in result["final"]

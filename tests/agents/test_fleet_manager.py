"""Agent 7: coding-agent fleet manager."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from nai_agents.agents.fleet_manager import SPEC
from nai_agents.agents.fleet_manager import tools as t
from nai_agents.core.data import load_json_state
from nai_agents.core.settings import reset_settings_cache
from tests.agents._harness import assert_clean_run, interrupts, run_approved, run_rejected, tool_calls


def _ledger() -> list[dict]:
    return load_json_state(t.LEDGER_FILE, [])


def test_plan_goal_from_file(data_dir: Path) -> None:
    plan = json.loads(t.plan_goal())
    assert plan["title"].startswith("Goal: production-ready health checks")
    assert [x["kind"] for x in plan["tasks"]] == ["code", "code", "test", "docs"]
    assert [x["id"] for x in plan["tasks"]] == ["t1", "t2", "t3", "t4"]
    assert any("offline" in c for c in plan["constraints"])


def test_plan_goal_from_text(data_dir: Path) -> None:
    plan = json.loads(t.plan_goal("Add a login form. Write tests for it. Update the README."))
    assert [x["kind"] for x in plan["tasks"]] == ["code", "test", "docs"]


def test_detect_clis_does_not_run_anything(data_dir: Path) -> None:
    data = json.loads(t.detect_coding_clis())
    assert set(data["clis"]) == {"claude", "codex", "cursor-agent"}
    assert data["external_enabled"] is False and data["usable"] == []


def test_assign_and_record_builtin(data_dir: Path) -> None:
    out = json.loads(t.assign_task("t1", "Add a health endpoint"))
    assert out["status"] == "assigned" and out["agent"] == t.BUILTIN
    rec = json.loads(t.record_result("t1", "done", "added endpoint", files="/workspace/a.py"))
    assert rec["status"] == "done" and rec["cost_usd"] > 0 and rec["files"] == ["/workspace/a.py"]
    status = json.loads(t.fleet_status())
    assert status["agents"][t.BUILTIN]["tasks"] == 1
    results = json.loads(t.collect_results())
    assert results["done"] == 1 and results["total_cost_usd"] == rec["cost_usd"]
    with pytest.raises(ValueError):
        t.record_result("nope", "done", "x")


def test_refuses_over_limit(data_dir: Path) -> None:
    limits = data_dir / "tight-limits.yaml"
    data_dir.mkdir(parents=True, exist_ok=True)
    limits.write_text(
        "agents:\n  builtin-coder:\n    kind: builtin\n    daily_tasks: 2\n    daily_tokens: 100000\n"
        "    max_tokens_per_task: 20000\n    input_per_mtok: 1\n    output_per_mtok: 2\n",
        encoding="utf-8",
    )
    lp = str(limits)
    assert json.loads(t.assign_task("a", "small task one", limits_path=lp))["status"] == "assigned"
    assert json.loads(t.assign_task("b", "small task two", limits_path=lp))["status"] == "assigned"
    third = json.loads(t.assign_task("c", "small task three", limits_path=lp))
    assert third["status"] == "refused" and "daily task limit" in third["reason"]
    big = json.loads(t.assign_task("d", "x", estimated_tokens=50_000, limits_path=lp))
    assert big["status"] == "refused"
    assert [e["status"] for e in _ledger()] == ["assigned", "assigned", "refused", "refused"]


def test_task_ids_are_validated(data_dir: Path) -> None:
    with pytest.raises(ValueError):
        t.assign_task("../../etc", "x")


def test_dispatch_external_blocked_when_disabled(data_dir: Path) -> None:
    out = json.loads(t.dispatch_external("t9", "codex", "write docs"))
    assert out["status"] == "blocked" and "APP_ENABLE_EXTERNAL_CLI" in out["reason"]
    assert _ledger()[-1]["status"] == "blocked"


def test_dispatch_external_runs_confined(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENABLE_EXTERNAL_CLI", "true")
    reset_settings_cache()
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/local/bin/{name}")
    seen: dict = {}

    def fake_run(argv: list[str], cwd: Path, timeout_s: float) -> tuple[int, str, str]:
        seen.update(argv=argv, cwd=cwd, timeout=timeout_s)
        (cwd / "README.md").write_text("# docs\n", encoding="utf-8")
        return 0, "Wrote README.md", ""

    monkeypatch.setattr(t, "_run_cli", fake_run)
    assigned = json.loads(t.assign_task("t4", "Document the endpoints", agent="auto"))
    assert assigned["agent"] in t.EXTERNAL_CLIS
    out = json.loads(t.dispatch_external("t4", "codex", "Document the endpoints", timeout_s=99999))
    assert out["status"] == "done" and out["files"] == ["README.md"]
    assert seen["argv"][1:] == ["exec", "Document the endpoints"]
    assert data_dir.resolve() in Path(seen["cwd"]).resolve().parents
    assert seen["timeout"] == t.MAX_TIMEOUT_S
    statuses = [(e["task_id"], e["status"]) for e in _ledger()]
    assert statuses == [("t4", "done")]  # the reservation was replaced by the run


@pytest.mark.skipif(not any(shutil.which(c) for c in t.EXTERNAL_CLIS), reason="no coding CLI installed")
def test_detect_real_cli(data_dir: Path) -> None:
    data = json.loads(t.detect_coding_clis())
    assert any(c["installed"] for c in data["clis"].values())


async def test_end_to_end_approved(data_dir: Path) -> None:
    result = await run_approved(SPEC)
    assert_clean_run(result)
    assert [i["actions"][0]["name"] for i in interrupts(result)] == ["dispatch_external"]
    assert {c["args"]["subagent_type"] for c in tool_calls(result, "task")} == {"coder"}
    files = result["snapshot"]["files"]
    for path in (
        "/workspace/orders/app/health.py",
        "/workspace/orders/app/ready.py",
        "/workspace/orders/tests/test_health.py",
        "/workspace/orders/docs/health.md",
        "/fleet-report.md",
    ):
        assert path in files, path
    assert "codex:blocked" in files["/fleet-report.md"]
    assert "Finished 4 of 4 tasks" in result["final"] and "blocked" in result["final"]
    # Approved, the dispatch ran and was recorded (blocked by config, not by the human).
    assert any(e["agent"] == "codex" and e["status"] == "blocked" for e in _ledger())


async def test_end_to_end_rejected(data_dir: Path) -> None:
    result = await run_rejected(SPEC)
    assert interrupts(result)
    assert not any(e["agent"] == "codex" for e in _ledger())
    assert "not approved" in result["final"]
    assert "/workspace/orders/docs/health.md" in result["snapshot"]["files"]

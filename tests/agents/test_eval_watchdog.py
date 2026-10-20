"""Agent 9: eval watchdog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from _harness import assert_clean_run, interrupts, run_approved, run_rejected, tool_calls

from nai_agents.agents.eval_watchdog import SPEC
from nai_agents.agents.eval_watchdog import tools as t
from nai_agents.core.data import load_json_state
from nai_agents.core.settings import reset_settings_cache


def test_detects_new_model_in_listing(data_dir: Path) -> None:
    data = json.loads(t.detect_model_changes())
    assert [m["id"] for m in data["added"]] == ["mock-medium-2026-10"]
    assert data["removed"] == [] and data["source"] == "snapshot"


def test_fingerprint_flags_only_the_silently_updated_model(data_dir: Path) -> None:
    data = json.loads(t.fingerprint_models())
    assert data["changed"] == ["mock-small"]
    small = next(r for r in data["models"] if r["model"] == "mock-small")
    assert small["fingerprint"] != small["accepted"]


def test_fingerprint_is_deterministic(data_dir: Path) -> None:
    entry = t._entry("mock-large")
    assert t.compute_fingerprint(entry) == t.compute_fingerprint(entry)


def test_silent_update_regresses_against_baseline(data_dir: Path) -> None:
    data = json.loads(t.run_watch_evals("mock-small,mock-medium,mock-medium-2026-10"))
    assert data["regressions"] == ["mock-small"]
    small = next(r for r in data["results"] if r["model"] == "mock-small")
    assert small["delta"] < -0.05 and small["newly_failing"]
    new = next(r for r in data["results"] if r["model"] == "mock-medium-2026-10")
    assert new["has_baseline"] is False
    assert load_json_state("eval_watchdog/last_check.json", {})["regressions"] == ["mock-small"]


def test_explain_regression(data_dir: Path) -> None:
    t.run_watch_evals("mock-small")
    data = json.loads(t.explain_regression("mock-small"))
    assert data["current_version"] == "2026-10-18-q4" and data["baseline_version"] == "1"
    failing = [f["case"] for f in data["flips"] if f["change"] == "now failing"]
    assert set(failing) == set(json.loads(t.run_watch_evals("mock-small"))["results"][0]["newly_failing"])


def test_save_baseline_updates_known_models_and_fingerprint(data_dir: Path) -> None:
    with pytest.raises(ValueError):
        t.save_baseline("mock-medium-2026-10")
    t.run_watch_evals("mock-medium-2026-10")
    out = json.loads(t.save_baseline("mock-medium-2026-10"))
    assert out["status"] == "saved"
    assert json.loads(t.detect_model_changes())["added"] == []
    assert (data_dir / "eval_watchdog" / "baselines" / "mock-medium-2026-10.json").exists()
    again = json.loads(t.run_watch_evals("mock-medium-2026-10"))["results"][0]
    assert again["has_baseline"] and not again["regressed"] and again["delta"] == 0


def test_openai_compatible_listing(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://nai.example.com/api/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-FAKE-test")
    reset_settings_cache()
    seen: dict[str, Any] = {}

    def fake_get(url: str, headers: dict, timeout: float) -> httpx.Response:
        seen.update(url=url, headers=headers)
        body = {"object": "list", "data": [{"id": "llama-3-3-70b", "owned_by": "nai"}, {"id": "qwen3-32b"}]}
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    data = json.loads(t.list_provider_models())
    assert data["source"] == "api" and [m["id"] for m in data["models"]] == ["llama-3-3-70b", "qwen3-32b"]
    assert seen["url"] == "https://nai.example.com/api/v1/models"
    assert seen["headers"]["Authorization"] == "Bearer sk-FAKE-test"
    # First run with a real provider: everything listed is new.
    assert len(json.loads(t.detect_model_changes())["added"]) == 2


async def test_end_to_end_approved(data_dir: Path) -> None:
    result = await run_approved(SPEC)
    assert_clean_run(result)
    assert [i["actions"][0]["name"] for i in interrupts(result)] == ["save_baseline"]
    assert tool_calls(result, "task")[0]["args"]["subagent_type"] == "regression-analyst"
    report = result["snapshot"]["files"][t.REPORT_FILE]
    assert "Silent update:** mock-small" in report and "**REGRESSED**" in report
    assert "mock-medium-2026-10" in report
    assert (
        "mock-small regressed" in result["final"]
        and "Saved a baseline for mock-medium-2026-10" in result["final"]
    )
    assert (data_dir / "eval_watchdog" / "baselines" / "mock-medium-2026-10.json").exists()


async def test_end_to_end_rejected(data_dir: Path) -> None:
    result = await run_rejected(SPEC)
    assert interrupts(result)
    assert not list((data_dir / "eval_watchdog").rglob("baselines/*.json"))
    assert "No baseline was saved" in result["final"]

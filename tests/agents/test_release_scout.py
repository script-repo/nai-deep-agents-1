"""Agent 10: new-model release scout."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _harness import assert_clean_run, interrupts, run_approved, run_rejected, tool_calls

from nai_agents.agents.release_scout import SPEC
from nai_agents.agents.release_scout import tools as t
from nai_agents.core.data import load_json_state
from nai_agents.core.settings import get_settings, reset_settings_cache

NEW_IDS = {"nova-5", "nova-5-mini", "sonata-4.5", "etude-4.5", "kestrel-24b-instruct", "kestrel-4b-instruct"}


def _feed(name: str) -> str:
    return (get_settings().examples_dir / "release_scout" / "feeds" / name).read_text(encoding="utf-8")


def test_parsers(data_dir: Path) -> None:
    rss = t.parse_rss(_feed("northwind_blog.rss"))
    assert rss[0]["title"] == "Introducing Nova-5" and rss[0]["date"] == "2026-10-14"
    atom = t.parse_atom(_feed("lumen_news.atom"))
    assert atom[0]["title"] == "Sonata 4.5 is now available" and atom[0]["link"].endswith("sonata-4-5")
    html = t.parse_listing(_feed("hf_acme_models.html"))
    assert [i["title"] for i in html][:2] == ["acme-ml/Kestrel-24B-Instruct", "acme-ml/Kestrel-4B-Instruct"]
    assert html[0]["date"] == "2026-10-12"


def test_finds_exactly_the_new_models(data_dir: Path) -> None:
    data = json.loads(t.find_new_models())
    assert {c["id"] for c in data["new"]} == NEW_IDS
    why = {s["title"]: s["why"] for s in data["skipped"]}
    assert why["DevDay 2026 recap"] == "not a model release"
    assert why["Introducing Nova-4.5"] == "already seen"
    assert why["acme-ml/Kestrel-24B-Instruct-GGUF"] == "not a model release"  # quantized re-upload
    assert data["errors"] == []


def test_extracts_metadata(data_dir: Path) -> None:
    new = {c["id"]: c for c in json.loads(t.find_new_models())["new"]}
    assert (new["nova-5"]["provider"], new["nova-5"]["date"]) == ("Northwind AI", "2026-10-14")
    assert (
        new["nova-5"]["context_window"],
        new["nova-5"]["input_per_mtok"],
        new["nova-5"]["output_per_mtok"],
    ) == (400_000, 2.5, 10.0)
    assert (new["sonata-4.5"]["context_window"], new["sonata-4.5"]["output_per_mtok"]) == (500_000, 15.0)
    assert (new["etude-4.5"]["input_per_mtok"], new["etude-4.5"]["output_per_mtok"]) == (0.6, 3.0)
    assert (
        new["kestrel-24b-instruct"]["open_weights"] and new["kestrel-24b-instruct"]["input_per_mtok"] is None
    )
    assert (
        new["nova-5-mini"]["provider"] == "Northwind AI" and new["nova-5-mini"]["context_window"] == 128_000
    )


def test_benchmark_and_compare(data_dir: Path) -> None:
    t.find_new_models()
    for mid in sorted(NEW_IDS):
        bench = json.loads(t.benchmark_model(mid))
        assert bench["status"] == "benchmarked" and bench["simulated"]
    assert json.loads(t.benchmark_model("nova-5")) == json.loads(t.benchmark_model("nova-5"))  # deterministic
    cmp = json.loads(t.compare_to_production())
    assert cmp["production"]["model"] == "mock-medium"
    decisions = {r["model"]: r["decision"] for r in cmp["candidates"]}
    assert decisions["sonata-4.5"] == "hold"  # better but far more expensive
    assert (cmp["recommendation"]["action"], cmp["recommendation"]["model"]) == (
        "switch",
        "kestrel-24b-instruct",
    )
    with pytest.raises(KeyError):
        t.benchmark_model("not-a-model")


def test_mark_seen_hides_models_next_time(data_dir: Path) -> None:
    t.find_new_models()
    t.mark_seen("nova-5,sonata-4.5")
    assert {c["id"] for c in json.loads(t.find_new_models())["new"]} == NEW_IDS - {"nova-5", "sonata-4.5"}


def test_refuses_entities_and_non_http(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]>'
        "<rss><channel><item><title>&a;</title></item></channel></rss>"
    )
    with pytest.raises(ValueError):
        t.parse_rss(bomb)
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    reset_settings_cache()
    with pytest.raises(ValueError):
        t._fetch({"name": "x", "url": "file:///etc/passwd", "sample": "feeds/northwind_blog.rss"})


def test_live_fetch_uses_httpx(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    reset_settings_cache()
    body = _feed("lumen_news.atom").encode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "lumen.example.com"
        return httpx.Response(200, content=body, headers={"content-type": "application/atom+xml"})

    monkeypatch.setattr(
        httpx,
        "stream",
        lambda method, url, **kw: httpx.Client(transport=httpx.MockTransport(handler)).stream(
            method, url, **{k: v for k, v in kw.items() if k in {"headers", "timeout"}}
        ),
    )
    data = json.loads(t.fetch_feeds("lumen-news"))
    assert data["errors"] == [] and data["count"] == 4


async def test_end_to_end_approved(data_dir: Path) -> None:
    result = await run_approved(SPEC)
    assert_clean_run(result)
    assert [i["actions"][0]["name"] for i in interrupts(result)] == ["mark_seen"]
    assert len(tool_calls(result, "task")) == len(NEW_IDS)
    report = result["snapshot"]["files"][t.REPORT_FILE]
    assert "Nova-5 mini" in report and "**SWITCH**" in report
    assert "Found 6 new models" in result["final"] and "kestrel-24b-instruct" in result["final"]
    assert set(load_json_state(t.SEEN_FILE, [])) == NEW_IDS


async def test_end_to_end_rejected(data_dir: Path) -> None:
    result = await run_rejected(SPEC)
    assert interrupts(result)
    assert load_json_state(t.SEEN_FILE, []) == []
    assert "not marked as seen" in result["final"]

import json

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from nai_agents.core import data
from nai_agents.core.evals import EvalCase, compare_reports, grade, load_suite, run_suite
from nai_agents.core.mock import RoleScript, ScriptedChatModel, SimulatedAnswerModel, Step
from nai_agents.core.models import ModelConfig, build_chat_model, find_model, load_catalog
from nai_agents.core.registry import AGENT_MODULES
from nai_agents.core.settings import get_settings


@pytest.mark.parametrize(
    ("grader", "expected", "output", "ok"),
    [
        ("exact", "391", "391.", True),
        ("exact", "391", "It is 391", False),
        ("contains", "storage", "Ran out of STORAGE", True),
        ("regex", r"select\s+count", "SELECT  COUNT(*) FROM users", True),
        ("number", "1284.50", "Total: $1,284.5", True),
        ("number", "12", "eleven", False),
    ],
)
def test_graders(grader, expected, output, ok):
    assert grade(EvalCase(id="x", prompt="p", expected=expected, grader=grader), output) is ok


def test_json_key_grader():
    case = EvalCase(id="j", prompt="p", expected="billing", grader="json_key", key="label")
    assert grade(case, 'Sure: {"label": "billing"}')
    assert not grade(case, "billing")


def test_suite_and_simulated_models_are_deterministic():
    suite = load_suite(get_settings().examples_dir / "evals" / "general.yaml")
    assert len(suite.cases) >= 15
    large, tiny = find_model("mock-large"), find_model("mock-tiny")
    r1, r2 = run_suite(large, suite), run_suite(large, suite)
    assert r1.pass_rate == r2.pass_rate
    assert run_suite(tiny, suite).pass_rate < r1.pass_rate
    assert r1.cost_usd > run_suite(tiny, suite).cost_usd


def test_compare_reports_flags_regression():
    suite = load_suite(get_settings().examples_dir / "evals" / "general.yaml")
    entry = find_model("mock-large")
    base = run_suite(entry, suite)
    baseline = {
        "pass_rate": base.pass_rate,
        "by_category": base.by_category,
        "results": [{"id": r.id, "passed": r.passed} for r in base.results],
        "p50_latency_ms": 1,
    }
    worse = SimulatedAnswerModel(model_name="mock-large", skill=0.4, answers=suite.answers)
    cmp = compare_reports(baseline, run_suite(entry, suite, model=worse))
    assert cmp["regressed"] and cmp["newly_failing"]


def test_scripted_model_plays_roles():
    model = ScriptedChatModel(
        scripts={
            "a": RoleScript(
                steps=[Step("x", {"v": 1}), Step("skip", lambda c: None), Step("y", {})],
                final=lambda c: "done",
            )
        }
    )
    msg = model.invoke([SystemMessage("ROLE: a"), HumanMessage("go")])
    assert msg.tool_calls[0]["name"] == "x"
    unknown = model.invoke([SystemMessage("ROLE: zzz"), HumanMessage("go")])
    assert not unknown.tool_calls and "zzz" in unknown.content


def test_model_factory_providers(monkeypatch):
    # httpx rejects bracketed IPv6 entries some sandboxes put in NO_PROXY; irrelevant to this test.
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(var, "localhost,127.0.0.1")
    assert isinstance(build_chat_model(ModelConfig("mock", "m")), ScriptedChatModel)
    oc = build_chat_model(
        ModelConfig("openai_compatible", "llama-3-3-70b", base_url="http://localhost:11434/v1")
    )
    assert oc.model_name == "llama-3-3-70b"
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        build_chat_model(ModelConfig("openai_compatible", "x"))
    assert build_chat_model(ModelConfig("anthropic", "claude-x", api_key="test")).model == "claude-x"
    assert build_chat_model(ModelConfig("ollama", "llama3.2")).model == "llama3.2"
    with pytest.raises(ValueError):
        build_chat_model(ModelConfig("nope", "x"))


def test_model_config_never_exposes_key():
    cfg = ModelConfig("openai", "gpt", api_key="sk-FAKE-123")
    assert "sk-FAKE" not in json.dumps(cfg.public())


def test_catalog_loads():
    assert len(load_catalog()) >= 4


def test_safe_paths(tmp_path):
    assert data.resolve_input("evals/general.yaml").name == "general.yaml"
    for bad in ("../../../../etc/passwd", "/etc/passwd", "evals/../../pyproject.toml"):
        with pytest.raises(data.PathError):
            data.resolve_input(bad)


def test_registry_lists_ten_agents():
    assert len(AGENT_MODULES) == 10

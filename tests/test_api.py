import base64
import json

import pytest
from fastapi.testclient import TestClient

from nai_agents.core.settings import get_settings, reset_settings_cache
from nai_agents.server.app import create_app


def sse_events(response) -> list[dict]:
    events = []
    for line in response.iter_lines():
        if line.startswith("data:"):
            events.append(json.loads(line[5:].strip()))
    return events


@pytest.fixture
def client():
    with TestClient(create_app(get_settings())) as c:
        yield c


def test_health_and_config(client):
    assert client.get("/api/health").json()["status"] == "ok"
    cfg = client.get("/api/config").json()
    assert cfg["auth"] == "none" and cfg["mock"] is True


def test_lists_ten_agents_in_order(client):
    agents = client.get("/api/agents").json()
    assert [a["number"] for a in agents] == list(range(1, 11))
    assert agents[0]["id"] == "cost-router"
    assert all(a["example_prompts"] for a in agents)
    detail = client.get("/api/agents/cost-router").json()
    assert "ROLE:" not in detail["system_prompt"]
    assert client.get("/api/agents/nope").status_code == 404


def test_models_and_settings(client):
    assert client.get("/api/models").json()
    s = client.get("/api/settings").json()
    assert s["model"]["provider"] == "mock" and "api_key" not in s["model"]
    r = client.put(
        "/api/settings",
        json={
            "provider": "openai_compatible",
            "model": "llama",
            "base_url": "http://localhost:9/v1",
            "temperature": 0.2,
        },
    )
    assert r.status_code == 200 and r.json()["model"]["base_url"] == "http://localhost:9/v1"
    assert (
        client.put("/api/settings", json={"provider": "openai_compatible", "model": "x"}).status_code == 400
    )
    r = client.put("/api/settings", json={"provider": "openai", "model": "x", "api_key": "sk-FAKE-1"})
    assert r.status_code == 403  # keys from the UI are off by default
    client.put("/api/settings", json={"provider": "mock", "model": "mock-large"})


def test_thread_lifecycle_with_interrupt(client):
    thread = client.post("/api/threads", json={"agent_id": "cost-router"}).json()
    tid = thread["id"]
    with client.stream(
        "POST", f"/api/threads/{tid}/messages", json={"content": "Find the cheapest models"}
    ) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        events = sse_events(r)
    kinds = [e["type"] for e in events]
    assert {"token", "tool_call", "tool_result", "todos", "files", "subagent", "interrupt", "done"} <= set(
        kinds
    )
    assert events[-1]["type"] == "done" and events[-1]["interrupted"] is True

    snap = client.get(f"/api/threads/{tid}").json()
    assert snap["thread"]["status"] == "interrupted"
    assert snap["thread"]["title"] == "Find the cheapest models"
    assert snap["interrupts"][0]["actions"][0]["name"] == "apply_routing"
    assert "/routing-report.md" in snap["files"]

    with client.stream("POST", f"/api/threads/{tid}/resume", json={"decisions": [{"type": "approve"}]}) as r:
        events = sse_events(r)
    assert events[-1] == {"type": "done", "final": events[-1]["final"], "interrupted": False}
    assert "Applied routes" in events[-1]["final"]
    snap = client.get(f"/api/threads/{tid}").json()
    assert snap["thread"]["status"] == "idle" and not snap["interrupts"]
    assert any(m["role"] == "user" for m in snap["messages"])

    assert [t["id"] for t in client.get("/api/threads?agent_id=cost-router").json()] == [tid]
    assert client.delete(f"/api/threads/{tid}").status_code == 204
    assert client.get(f"/api/threads/{tid}").status_code == 404


def test_unknown_agent_thread(client):
    assert client.post("/api/threads", json={"agent_id": "nope"}).status_code == 404


def test_spa_fallback(client):
    assert client.get("/").status_code == 200
    assert client.get("/some/deep/link").status_code == 200
    assert client.get("/api/does-not-exist").status_code == 404


@pytest.fixture
def secured(monkeypatch):
    monkeypatch.setenv("APP_AUTH_TOKEN", "test-token-123")
    monkeypatch.setenv(
        "APP_BASIC_AUTH",
        "alice:wonderland,bob:sha256:" + __import__("hashlib").sha256(b"builder").hexdigest(),
    )
    reset_settings_cache()
    with TestClient(create_app(get_settings())) as c:
        yield c


def basic(user: str, pw: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def test_auth_required(secured):
    assert secured.get("/api/health").status_code == 200
    assert secured.get("/api/config").json()["auth"] == "token+basic"
    assert secured.get("/api/agents").status_code == 401
    assert secured.get("/api/agents", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert secured.get("/api/agents", headers={"Authorization": "Bearer test-token-123"}).status_code == 200
    assert secured.get("/api/me", headers=basic("alice", "wonderland")).json() == {"user": "alice"}
    assert secured.get("/api/me", headers=basic("bob", "builder")).json() == {"user": "bob"}
    assert secured.get("/api/me", headers=basic("alice", "nope")).status_code == 401


def test_threads_are_per_user(secured):
    a, b = basic("alice", "wonderland"), basic("bob", "builder")
    tid = secured.post("/api/threads", json={"agent_id": "shadow-ai"}, headers=a).json()["id"]
    assert secured.get(f"/api/threads/{tid}", headers=b).status_code == 404
    assert secured.get("/api/threads", headers=b).json() == []
    assert len(secured.get("/api/threads", headers=a).json()) == 1

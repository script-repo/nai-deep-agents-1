"""Helpers to run an agent graph end to end in mock mode (imported by the agent tests)."""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from nai_agents.core.models import ModelConfig
from nai_agents.core.registry import AgentSpec
from nai_agents.core.runner import resume_command, run_to_completion, stream_events, thread_snapshot


def build(spec: AgentSpec) -> Any:
    return spec.build(ModelConfig(provider="mock", model="mock-large"), checkpointer=InMemorySaver())


async def run_approved(spec: AgentSpec, prompt: str | None = None) -> dict[str, Any]:
    """Run to the end, approving every interrupt."""
    graph = build(spec)
    return await run_to_completion(
        graph, prompt or spec.example_prompts[0], str(uuid.uuid4()), auto_approve=True
    )


async def run_rejected(spec: AgentSpec, prompt: str | None = None, max_resumes: int = 10) -> dict[str, Any]:
    """Run to the end, rejecting every interrupt."""
    graph = build(spec)
    thread = str(uuid.uuid4())
    result = await run_to_completion(graph, prompt or spec.example_prompts[0], thread, auto_approve=False)
    events = list(result["events"])
    for _ in range(max_resumes):
        pending = [e for e in events if e["type"] == "interrupt"]
        done = [e for e in events if e["type"] == "done"]
        if not pending or not done or not done[-1]["interrupted"]:
            break
        n = max(len(pending[-1]["actions"]), 1)
        decision = {"type": "reject", "message": "Rejected in a test."}
        async for ev in stream_events(graph, resume_command([decision] * n), thread):
            events.append(ev)
    final = next((e["final"] for e in reversed(events) if e["type"] == "done"), "")
    return {"events": events, "final": final, "snapshot": await thread_snapshot(graph, thread)}


def tool_calls(result: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [e for e in result["events"] if e["type"] == "tool_call" and e["name"] == name]


def interrupts(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in result["events"] if e["type"] == "interrupt"]


def assert_clean_run(result: dict[str, Any]) -> None:
    errors = [e for e in result["events"] if e["type"] == "error"]
    assert not errors, errors
    todos = result["snapshot"]["todos"]
    assert todos and all(t["status"] == "completed" for t in todos), todos

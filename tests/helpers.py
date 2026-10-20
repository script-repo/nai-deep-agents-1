"""Helpers to run an agent graph end to end in mock mode."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from nai_agents.core.models import ModelConfig
from nai_agents.core.registry import AgentSpec
from nai_agents.core.runner import resume_command, run_to_completion, stream_events, thread_snapshot

MOCK = ModelConfig(provider="mock", model="mock-large")


def run_agent(spec: AgentSpec, prompt: str | None = None, auto_approve: bool = True) -> dict[str, Any]:
    graph = spec.build(MOCK, InMemorySaver())
    return asyncio.run(
        run_to_completion(
            graph, prompt or spec.example_prompts[0], str(uuid.uuid4()), auto_approve=auto_approve
        )
    )


def run_and_reject(spec: AgentSpec, prompt: str | None = None) -> dict[str, Any]:
    """Run until the first interrupt, reject every action in it (and any later ones), finish."""

    async def go() -> dict[str, Any]:
        graph = spec.build(MOCK, InMemorySaver())
        thread = str(uuid.uuid4())
        events: list[dict[str, Any]] = []
        graph_input: Any = {"messages": [{"role": "user", "content": prompt or spec.example_prompts[0]}]}
        for _ in range(10):
            pending = None
            async for ev in stream_events(graph, graph_input, thread):
                events.append(ev)
                if ev["type"] == "interrupt":
                    pending = ev
            if not pending:
                break
            graph_input = resume_command(
                [{"type": "reject", "message": "Not approved in test."}] * max(len(pending["actions"]), 1)
            )
        return {
            "events": events,
            "snapshot": await thread_snapshot(graph, thread),
            "final": next((e["final"] for e in reversed(events) if e["type"] == "done"), ""),
        }

    return asyncio.run(go())


def types(result: dict[str, Any]) -> list[str]:
    return [e["type"] for e in result["events"]]


def tool_calls(result: dict[str, Any], agent: str | None = None) -> list[str]:
    return [
        e["name"]
        for e in result["events"]
        if e["type"] == "tool_call" and (agent is None or e["agent"] == agent)
    ]


def interrupted_tools(result: dict[str, Any]) -> list[str]:
    return [a["name"] for e in result["events"] if e["type"] == "interrupt" for a in e["actions"]]

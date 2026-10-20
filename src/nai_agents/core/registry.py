"""The agent spec every agent module exports, and the ordered registry of all ten."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import TodoListMiddleware

from nai_agents.core.mock import MockScript
from nai_agents.core.models import ModelConfig, build_chat_model


@dataclass
class AgentSpec:
    id: str
    name: str
    summary: str
    system_prompt: str
    tools: Sequence[Callable[..., Any]]
    mock_script: Callable[[], MockScript]
    subagents: list[dict[str, Any]] = field(default_factory=list)
    interrupt_on: dict[str, Any] = field(default_factory=dict)
    example_prompts: list[str] = field(default_factory=list)
    icon: str = "bot"
    number: int = 0

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "number": self.number,
            "name": self.name,
            "summary": self.summary,
            "icon": self.icon,
            "example_prompts": self.example_prompts,
            "tools": [getattr(t, "name", getattr(t, "__name__", str(t))) for t in self.tools],
            "subagents": [{"name": s["name"], "description": s["description"]} for s in self.subagents],
            "interrupt_on": sorted(self.interrupt_on),
        }

    def build(self, model_cfg: ModelConfig, checkpointer: Any = None, store: Any = None) -> Any:
        """Compile the deep agent graph for this spec."""
        model = build_chat_model(model_cfg, mock_scripts=self.mock_script())
        subagents = []
        for sub in self.subagents:
            sub = dict(sub)
            sub["system_prompt"] = f"ROLE: {sub['name']}\n{sub.get('system_prompt', '')}"
            subagents.append(sub)
        return create_deep_agent(
            model=model,
            tools=list(self.tools),
            system_prompt=f"ROLE: {self.id}\n{self.system_prompt}",
            subagents=subagents,
            middleware=[TodoListMiddleware()],
            interrupt_on=self.interrupt_on or None,
            checkpointer=checkpointer,
            store=store,
            name=self.id,
        )


AGENT_MODULES = [
    "cost_router",
    "spend_governor",
    "shadow_ai",
    "identity_broker",
    "rogue_monitor",
    "context_curator",
    "fleet_manager",
    "code_review",
    "eval_watchdog",
    "release_scout",
]


def all_agents() -> list[AgentSpec]:
    specs = []
    for number, module in enumerate(AGENT_MODULES, start=1):
        spec: AgentSpec = importlib.import_module(f"nai_agents.agents.{module}").SPEC
        spec.number = number
        specs.append(spec)
    return specs


def get_agent(agent_id: str) -> AgentSpec:
    for spec in all_agents():
        if spec.id == agent_id:
            return spec
    raise KeyError(f"Unknown agent {agent_id!r}")

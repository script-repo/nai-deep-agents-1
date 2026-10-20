"""Offline models, so every agent, the API and the UI run with no API key.

Two models live here:

* `ScriptedChatModel` drives a deep agent. Each agent ships a *mock script*: for every role (the main
  agent and each sub-agent) a list of tool calls to make, then a final answer built from the tool
  results. The role is read from the `ROLE: <name>` marker on the first line of each system prompt.
  Real models ignore that marker; the scripts only exist so the whole graph (planning, sub-agents,
  files, interrupts, streaming) can be exercised deterministically in tests and demos.
* `SimulatedAnswerModel` stands in for a *candidate* model under evaluation (routing, release
  scouting, regression watching). It answers eval prompts correctly with a per-model, per-prompt
  deterministic probability, so price/quality trade-offs look realistic without any network calls.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

ROLE_RE = re.compile(r"ROLE:\s*([\w.-]+)")


@dataclass
class MockContext:
    """What a scripted step can look at to decide its arguments."""

    role: str
    user_text: str
    tool_results: list[tuple[str, str]] = field(default_factory=list)

    def last(self, tool_name: str) -> str:
        for name, content in reversed(self.tool_results):
            if name == tool_name:
                return content
        return ""

    def last_json(self, tool_name: str) -> Any:
        try:
            return json.loads(self.last(tool_name))
        except (json.JSONDecodeError, TypeError):
            return None


ArgsLike = dict[str, Any] | Callable[[MockContext], dict[str, Any] | None]


@dataclass
class Step:
    """One scripted tool call. `args` may be a callable; returning None skips the step."""

    tool: str
    args: ArgsLike = field(default_factory=dict)


@dataclass
class RoleScript:
    steps: list[Step] = field(default_factory=list)
    final: Callable[[MockContext], str] | None = None


MockScript = dict[str, RoleScript]


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


def default_final(ctx: MockContext) -> str:
    lines = [f"**{ctx.role}** finished (mock model).", ""]
    for name, content in ctx.tool_results:
        if name in {"write_todos"}:
            continue
        snippet = content.strip().replace("\n", " ")
        lines.append(f"- `{name}`: {snippet[:240]}{'…' if len(snippet) > 240 else ''}")
    return "\n".join(lines)


class ScriptedChatModel(BaseChatModel):
    """A deterministic chat model that plays back per-role tool-call scripts."""

    scripts: dict[str, Any] = Field(default_factory=dict)
    model_name: str = "mock-large"

    @property
    def _llm_type(self) -> str:
        return "nai-mock-scripted"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self.model_name}

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> ScriptedChatModel:  # type: ignore[override]
        return self

    # -- core ------------------------------------------------------------------------------------
    def _context(self, messages: list[BaseMessage]) -> tuple[MockContext, int]:
        system = next((_text_of(m.content) for m in messages if isinstance(m, SystemMessage)), "")
        match = ROLE_RE.search(system)
        role = match.group(1) if match else "main"
        last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=0)
        turn = messages[last_human:]
        ctx = MockContext(role=role, user_text=_text_of(messages[last_human].content) if messages else "")
        names: dict[str, str] = {}
        ai_turns = 0
        for m in turn:
            if isinstance(m, AIMessage):
                ai_turns += 1
                for tc in m.tool_calls:
                    names[tc["id"]] = tc["name"]
            elif isinstance(m, ToolMessage):
                ctx.tool_results.append((names.get(m.tool_call_id, m.name or "tool"), _text_of(m.content)))
        return ctx, ai_turns

    def _next_message(self, messages: list[BaseMessage]) -> AIMessage:
        ctx, ai_turns = self._context(messages)
        script: RoleScript = self.scripts.get(ctx.role) or RoleScript()
        # Walk the script, skipping steps whose args callable returns None.
        index = 0
        emitted = 0
        while index < len(script.steps):
            step = script.steps[index]
            args = step.args(ctx) if callable(step.args) else step.args
            index += 1
            if args is None:
                continue
            if emitted == ai_turns:
                call_id = "call_" + uuid.uuid4().hex[:12]
                return AIMessage(
                    content="",
                    tool_calls=[{"name": step.tool, "args": args, "id": call_id, "type": "tool_call"}],
                    usage_metadata=_usage(messages, ""),
                )
            emitted += 1
        text = (script.final or default_final)(ctx)
        return AIMessage(content=text, usage_metadata=_usage(messages, text))

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next_message(messages))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        message = self._next_message(messages)
        if message.tool_calls:
            tc = message.tool_calls[0]
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": tc["name"],
                            "args": json.dumps(tc["args"]),
                            "id": tc["id"],
                            "index": 0,
                            "type": "tool_call_chunk",
                        }
                    ],
                    usage_metadata=message.usage_metadata,
                )
            )
            return
        for piece in re.findall(r"\s*\S+", message.content) or [""]:
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=piece))
            if run_manager:
                run_manager.on_llm_new_token(piece, chunk=chunk)
            yield chunk


def _usage(messages: list[BaseMessage], output: str) -> dict[str, int]:
    tokens_in = sum(len(_text_of(m.content)) for m in messages) // 4 + 1
    tokens_out = len(output) // 4 + 1
    return {"input_tokens": tokens_in, "output_tokens": tokens_out, "total_tokens": tokens_in + tokens_out}


def stable_fraction(*parts: str) -> float:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


class SimulatedAnswerModel(BaseChatModel):
    """A candidate model for evals. Knows the expected answers and gets them right `skill` of the time.

    `answers` maps a prompt to its expected answer. `version` changes the dice: bump it to simulate a
    silent provider update (the eval watchdog uses this).
    """

    model_name: str
    skill: float = 0.8
    version: str = "1"
    answers: dict[str, str] = Field(default_factory=dict)
    difficulty: dict[str, float] = Field(default_factory=dict)
    latency_ms: float = 400.0

    @property
    def _llm_type(self) -> str:
        return "nai-mock-candidate"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        prompt = _text_of(messages[-1].content) if messages else ""
        expected = self.answers.get(prompt.strip())
        roll = stable_fraction(self.model_name, self.version, prompt)
        threshold = self.skill - self.difficulty.get(prompt.strip(), 0.0)
        if expected is not None and roll < threshold:
            text = expected
        elif expected is not None:
            text = f"I am not sure. Perhaps {expected[::-1][:12] or 'unknown'}."
        else:
            text = f"[{self.model_name}] response to: {prompt[:80]}"
        msg = AIMessage(
            content=text,
            usage_metadata=_usage(messages, text),
            response_metadata={"simulated_latency_ms": self.latency_ms * (0.75 + roll / 2)},
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])

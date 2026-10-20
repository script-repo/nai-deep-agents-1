"""Run a compiled deep agent and turn LangGraph's stream into a small, UI-friendly event vocabulary.

Events (each a dict with a `type`):

* `token`      {text, agent}            streamed model text (agent = "main" or a sub-agent name)
* `tool_call`  {id, name, args, agent}  the model asked for a tool
* `tool_result`{id, name, content, agent}
* `todos`      {todos}                  the main agent's plan (write_todos)
* `files`      {files: {path: content}} files written in the agent's virtual filesystem
* `subagent`   {status: start|end, id, name, description}
* `interrupt`  {id, actions, review_configs}  waiting for a human decision
* `done`       {final, interrupted}
* `error`      {message}
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, ToolMessage
from langgraph.types import Command

MAX_TOOL_CONTENT = 6000
MAX_FILE_CONTENT = 200_000


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content)


def file_content(data: Any) -> str:
    if isinstance(data, dict):
        content = data.get("content", "")
        if isinstance(content, list):  # older deepagents stored lines
            content = "\n".join(content)
        return str(content)[:MAX_FILE_CONTENT]
    return str(data)[:MAX_FILE_CONTENT]


def interrupt_payload(intr: Any) -> dict[str, Any]:
    value = getattr(intr, "value", intr) or {}
    return {
        "id": getattr(intr, "id", None),
        "actions": value.get("action_requests", []) if isinstance(value, dict) else [],
        "review_configs": value.get("review_configs", []) if isinstance(value, dict) else [],
        "raw": value if not isinstance(value, dict) else None,
    }


def resume_command(decisions: list[dict[str, Any]], interrupt_id: str | None = None) -> Command:
    payload = {"decisions": decisions}
    if interrupt_id:
        return Command(resume={interrupt_id: payload})
    return Command(resume=payload)


async def stream_events(
    graph: Any, graph_input: Any, thread_id: str, recursion_limit: int = 200
) -> AsyncIterator[dict[str, Any]]:
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit}
    pending_tasks: deque[dict[str, Any]] = deque()
    ns_names: dict[str, dict[str, Any]] = {}
    final_parts: list[str] = []
    seen_calls: set[str] = set()

    def agent_of(ns: tuple[str, ...]) -> str:
        if not ns:
            return "main"
        key = ns[0]
        if key not in ns_names:
            task = (
                pending_tasks.popleft()
                if pending_tasks
                else {"id": key, "name": "subagent", "description": ""}
            )
            ns_names[key] = task
        return ns_names[key]["name"]

    try:
        async for ns, mode, data in graph.astream(
            graph_input, config, stream_mode=["messages", "updates"], subgraphs=True
        ):
            if mode == "messages":
                chunk, meta = data
                if isinstance(chunk, AIMessageChunk) and meta.get("langgraph_node") == "model":
                    text = _text(chunk.content)
                    if text:
                        agent = agent_of(ns)
                        if agent == "main":
                            final_parts.append(text)
                        yield {"type": "token", "text": text, "agent": agent}
                continue

            for node, update in (data or {}).items():
                if node == "__interrupt__":
                    for intr in update or ():
                        yield {"type": "interrupt", **interrupt_payload(intr)}
                    continue
                if not isinstance(update, dict):
                    continue
                agent = agent_of(ns)
                for msg in _as_list(update.get("messages")):
                    if isinstance(msg, AIMessage) and msg.tool_calls:
                        if agent == "main":
                            final_parts.clear()
                        for tc in msg.tool_calls:
                            if tc.get("id") in seen_calls:
                                continue
                            seen_calls.add(tc.get("id") or "")
                            yield {
                                "type": "tool_call",
                                "id": tc.get("id"),
                                "name": tc["name"],
                                "args": tc.get("args", {}),
                                "agent": agent,
                            }
                            if tc["name"] == "task":
                                task = {
                                    "id": tc.get("id"),
                                    "name": tc["args"].get("subagent_type", "subagent"),
                                    "description": tc["args"].get("description", ""),
                                }
                                pending_tasks.append(task)
                                yield {"type": "subagent", "status": "start", **task}
                    elif isinstance(msg, ToolMessage):
                        content = _text(msg.content)
                        yield {
                            "type": "tool_result",
                            "id": msg.tool_call_id,
                            "name": msg.name,
                            "content": content[:MAX_TOOL_CONTENT],
                            "agent": agent,
                            "status": getattr(msg, "status", "success"),
                        }
                        if msg.name == "task" and agent == "main":
                            sub = next((t for t in ns_names.values() if t["id"] == msg.tool_call_id), None)
                            yield {
                                "type": "subagent",
                                "status": "end",
                                "id": msg.tool_call_id,
                                "name": sub["name"] if sub else "subagent",
                                "description": "",
                                "result": content[:1000],
                            }
                if agent == "main" and update.get("todos") is not None:
                    yield {"type": "todos", "todos": update["todos"]}
                if update.get("files"):
                    yield {
                        "type": "files",
                        "agent": agent,
                        "files": {p: file_content(v) for p, v in update["files"].items() if v is not None},
                    }
    except Exception as exc:
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
        return

    state = await graph.aget_state(config)
    interrupted = bool(getattr(state, "interrupts", ()) or any(t.interrupts for t in state.tasks))
    yield {"type": "done", "final": "".join(final_parts), "interrupted": interrupted}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    # Overwrite wrappers and similar carry `.value`
    inner = getattr(value, "value", None)
    if isinstance(inner, list):
        return inner
    return [value]


def serialize_message(msg: BaseMessage) -> dict[str, Any]:
    if isinstance(msg, HumanMessage):
        return {"role": "user", "content": _text(msg.content)}
    if isinstance(msg, AIMessage):
        return {
            "role": "assistant",
            "content": _text(msg.content),
            "tool_calls": [
                {"id": tc.get("id"), "name": tc["name"], "args": tc.get("args", {})} for tc in msg.tool_calls
            ],
        }
    if isinstance(msg, ToolMessage):
        return {
            "role": "tool",
            "name": msg.name,
            "tool_call_id": msg.tool_call_id,
            "content": _text(msg.content)[:MAX_TOOL_CONTENT],
        }
    return {"role": msg.type, "content": _text(msg.content)}


async def thread_snapshot(graph: Any, thread_id: str) -> dict[str, Any]:
    config = {"configurable": {"thread_id": thread_id}}
    state = await graph.aget_state(config)
    values = state.values or {}
    interrupts = list(getattr(state, "interrupts", ()) or ())
    for task in state.tasks:
        for intr in task.interrupts:
            if intr not in interrupts:
                interrupts.append(intr)
    return {
        "messages": [serialize_message(m) for m in values.get("messages", [])],
        "todos": values.get("todos", []),
        "files": {p: file_content(v) for p, v in (values.get("files") or {}).items() if v is not None},
        "interrupts": [interrupt_payload(i) for i in interrupts],
    }


async def run_to_completion(
    graph: Any, prompt: str, thread_id: str, auto_approve: bool = False, max_resumes: int = 10
) -> dict[str, Any]:
    """Convenience for tests and the CLI: run, optionally approve every interrupt, collect events."""
    events: list[dict[str, Any]] = []
    graph_input: Any = {"messages": [{"role": "user", "content": prompt}]}
    for _ in range(max_resumes + 1):
        interrupts = []
        async for ev in stream_events(graph, graph_input, thread_id):
            events.append(ev)
            if ev["type"] == "interrupt":
                interrupts.append(ev)
        if not interrupts or not auto_approve:
            break
        n = max(len(interrupts[-1]["actions"]), 1)
        graph_input = resume_command([{"type": "approve"}] * n)
    final = next((e["final"] for e in reversed(events) if e["type"] == "done"), "")
    return {"events": events, "final": final, "snapshot": await thread_snapshot(graph, thread_id)}

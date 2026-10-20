"""Agent 7: coding-agent fleet manager."""

from __future__ import annotations

import re

from nai_agents.agents.fleet_manager import tools as t
from nai_agents.core.mock import MockContext, RoleScript, Step
from nai_agents.core.registry import AgentSpec

SYSTEM_PROMPT = """You are the Coding-Agent Fleet Manager. You turn a coding goal into small tasks and run
each on the right coding agent — Claude Code, Codex, Cursor's CLI, or your own built-in `coder` sub-agent —
while staying inside each agent's daily task, token and dollar limits.

How you work:
1. plan_goal(goal) to split the goal into tasks, then write_todos with one todo per task.
2. detect_coding_clis and fleet_status: what is installed, enabled and how much budget is left today.
3. For each task, assign_task(task_id, task). It checks limits and refuses over-limit work: never work around
   a refusal; report it, or split the task if it was too large.
   - Built-in coder: call the task tool with subagent_type="coder" and a self-contained description (the goal,
     the constraints, the files to write). Then record_result(task_id, status, summary, files).
   - External CLI: dispatch_external(task_id, agent, prompt). A human approves every run. If it comes back
     "blocked" (disabled, not installed, over limit), fall back to the built-in coder.
4. Keep todos current as tasks finish.
5. collect_results and fleet_status, then write /fleet-report.md: a table of tasks (agent, status, files,
   cost), cost per agent, limits remaining, and anything refused, blocked or failed.
Be exact about what ran where. Never claim an external CLI ran when it was blocked or rejected.
"""

CODER_PROMPT = """You are a careful senior engineer. You get one small coding task with its context. Write the
code with write_file (absolute paths under /workspace/), keeping changes minimal and runnable, with type hints
and docstrings. Reply with one line per file you wrote and a one-sentence summary. No preamble."""

# What the mock coder writes for each kind of task in the example goal.
_MOCK_FILES = {
    "healthz": (
        "/workspace/orders/app/health.py",
        '"""Liveness endpoint."""\n\nimport os\n\nfrom fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n'
        '@router.get("/healthz")\ndef healthz() -> dict[str, str]:\n'
        '    return {"status": "ok", "version": os.environ.get("BUILD_VERSION", "dev")}\n',
    ),
    "readyz": (
        "/workspace/orders/app/ready.py",
        '"""Readiness endpoint: checks the database with a 2 second timeout."""\n\nimport asyncio\n\n'
        "from fastapi import APIRouter, Response\n\nfrom app.db import ping\n\nrouter = APIRouter()\n\n\n"
        '@router.get("/readyz")\nasync def readyz(response: Response) -> dict[str, str]:\n    try:\n'
        "        await asyncio.wait_for(ping(), timeout=2)\n    except Exception:\n"
        '        response.status_code = 503\n        return {"status": "unavailable"}\n'
        '    return {"status": "ready"}\n',
    ),
    "test": (
        "/workspace/orders/tests/test_health.py",
        "from fastapi.testclient import TestClient\n\nfrom app.main import app\n\n"
        "client = TestClient(app)\n\n\n"
        'def test_healthz() -> None:\n    r = client.get("/healthz")\n'
        '    assert r.status_code == 200 and r.json()["status"] == "ok"\n\n\n'
        "def test_readyz_db_down(monkeypatch) -> None:\n    async def boom() -> None:\n"
        '        raise ConnectionError("db down")\n\n    monkeypatch.setattr("app.ready.ping", boom)\n'
        '    assert client.get("/readyz").status_code == 503\n',
    ),
    "doc": (
        "/workspace/orders/docs/health.md",
        "# Health checks\n\n| Endpoint | Meaning | Probe |\n|---|---|---|\n"
        "| `/healthz` | process is alive | livenessProbe, period 10s |\n"
        "| `/readyz` | database reachable within 2s | readinessProbe, period 5s, failureThreshold 3 |\n",
    ),
}


def _tasks(ctx: MockContext) -> list[dict]:
    return (ctx.last_json("plan_goal") or {}).get("tasks", [])


def _code_task(i: int):
    """Steps for the i-th non-docs task: assign, run the coder, record."""

    def pick(ctx: MockContext) -> dict | None:
        tasks = [x for x in _tasks(ctx) if x["kind"] != "docs"]
        return tasks[i] if i < len(tasks) else None

    def assign(ctx: MockContext) -> dict | None:
        task = pick(ctx)
        return task and {"task_id": task["id"], "task": task["task"]}

    def run(ctx: MockContext) -> dict | None:
        task = pick(ctx)
        return task and {"subagent_type": "coder", "description": _brief(ctx, task)}

    def record(ctx: MockContext) -> dict | None:
        task = pick(ctx)
        return task and _record_args(ctx, task)

    return [Step("assign_task", assign), Step("task", run), Step("record_result", record)]


def _brief(ctx: MockContext, task: dict) -> str:
    plan = ctx.last_json("plan_goal") or {}
    constraints = "; ".join(plan.get("constraints", []))
    return f"Task {task['id']} of '{plan.get('title', 'goal')}': {task['task']} Constraints: {constraints}"


def _record_args(ctx: MockContext, task: dict) -> dict:
    out = ctx.last("task")
    files = re.findall(r"/workspace/[\w./-]+", out)
    return {
        "task_id": task["id"],
        "status": "done" if files else "failed",
        "summary": out.splitlines()[-1][:200] if out else "no output",
        "files": ",".join(files),
    }


def _docs(ctx: MockContext) -> dict | None:
    return next((x for x in _tasks(ctx) if x["kind"] == "docs"), None)


def _docs_assign(ctx: MockContext) -> dict | None:
    d = _docs(ctx)
    if not d or (ctx.last_json("dispatch_external") or {}).get("status") == "done":
        return None
    return {"task_id": d["id"], "task": d["task"], "agent": t.BUILTIN}


def _docs_assigned(ctx: MockContext) -> dict | None:
    d = _docs(ctx)
    return d if d and (ctx.last_json("assign_task") or {}).get("task_id") == d["id"] else None


def _docs_run(ctx: MockContext) -> dict | None:
    d = _docs_assigned(ctx)
    return {"subagent_type": "coder", "description": _brief(ctx, d)} if d else None


def _docs_record(ctx: MockContext) -> dict | None:
    d = _docs_assigned(ctx)
    return _record_args(ctx, d) if d else None


def _report(ctx: MockContext) -> dict[str, str]:
    res = ctx.last_json("collect_results") or {}
    status = ctx.last_json("fleet_status") or {}
    lines = [
        "# Fleet report",
        "",
        "_Mock mode: the built-in coder wrote files in the virtual filesystem._",
        "",
        "| Task | Agent | Status | Files | Cost | Attempts |",
        "|---|---|---|---|---|---|",
    ]
    for x in res.get("tasks", []):
        lines.append(
            f"| {x['task_id']}: {x['task'][:60]} | {x['agent']} | {x['status']} | "
            f"{', '.join(x.get('files') or []) or '—'} | ${x.get('cost_usd') or 0:.4f} | "
            f"{', '.join(x['attempts'])} |"
        )
    lines += [
        "",
        "## Budget today",
        "",
        "| Agent | Tasks | Tokens | Cost | Tasks left | Tokens left |",
        "|---|---|---|---|---|---|",
    ]
    for name, a in status.get("agents", {}).items():
        lines.append(
            f"| {name} | {a['tasks']} | {a['tokens']} | ${a['cost_usd']:.4f} | {a['tasks_left']} | "
            f"{a['tokens_left']} |"
        )
    lines += ["", f"Total cost today: ${res.get('total_cost_usd', 0):.4f}"]
    return {"file_path": "/fleet-report.md", "content": "\n".join(lines) + "\n"}


def _final(ctx: MockContext) -> str:
    res = ctx.last_json("collect_results") or {}
    ext = ctx.last_json("dispatch_external")
    lines = [
        f"Finished {res.get('done', 0)} of {len(res.get('tasks', []))} tasks "
        f"(total ${res.get('total_cost_usd', 0):.4f}, mock mode)."
    ]
    lines += [
        f"- **{x['task_id']}** → {x['agent']} ({x['status']}): {', '.join(x.get('files') or []) or '—'}"
        for x in res.get("tasks", [])
    ]
    if isinstance(ext, dict) and ext.get("status") == "blocked":
        lines.append(
            f"\nExternal dispatch to {ext['agent']} was blocked: {ext['reason']}. "
            "The built-in coder took the task instead."
        )
    elif not isinstance(ext, dict):
        lines.append("\nExternal dispatch was not approved; the built-in coder took the task instead.")
    lines.append("\nDetails: `/fleet-report.md`.")
    return "\n".join(lines)


def _coder_write(ctx: MockContext) -> dict:
    low = ctx.user_text.lower().split("constraints:")[0]
    key = next((k for k in ("healthz", "readyz", "test", "doc") if k in low), None)
    if key is None:
        return {"file_path": "/workspace/notes/task.md", "content": f"# Task\n\n{ctx.user_text}\n"}
    path, content = _MOCK_FILES[key]
    return {"file_path": path, "content": content}


def _coder_final(ctx: MockContext) -> str:
    args = _coder_write(ctx)
    return f"Wrote {args['file_path']}\nImplemented the task with a minimal, runnable change."


def mock_script() -> dict[str, RoleScript]:
    todos = [
        {"content": "Split the goal into tasks", "status": "in_progress"},
        {"content": "Check installed CLIs and budgets", "status": "pending"},
        {"content": "Run the code and test tasks", "status": "pending"},
        {"content": "Run the docs task", "status": "pending"},
        {"content": "Collect results and write the fleet report", "status": "pending"},
    ]
    done = [dict(x, status="completed") for x in todos]
    steps = [
        Step("write_todos", {"todos": todos}),
        Step("plan_goal", {"goal": t.GOAL_PATH}),
        Step("detect_coding_clis", {}),
        Step("fleet_status", {}),
    ]
    for i in range(6):
        steps += _code_task(i)
    steps += [
        # Try an external CLI for the docs task (approval required); fall back to the coder if blocked.
        Step(
            "dispatch_external",
            lambda c: (d := _docs(c)) and {"task_id": d["id"], "agent": "codex", "prompt": _brief(c, d)},
        ),
        Step("assign_task", _docs_assign),
        Step("task", _docs_run),
        Step("record_result", _docs_record),
        Step("collect_results", {}),
        Step("fleet_status", {}),
        Step("write_file", _report),
        Step("write_todos", {"todos": done}),
    ]
    return {
        "fleet-manager": RoleScript(steps=steps, final=_final),
        "coder": RoleScript(steps=[Step("write_file", _coder_write)], final=_coder_final),
    }


SPEC = AgentSpec(
    id="fleet-manager",
    name="Coding-agent fleet manager",
    summary="Splits a coding goal into tasks and dispatches them to Claude Code, Codex, Cursor or a built-in "
    "coder, within per-agent usage limits and cost.",
    icon="network",
    system_prompt=SYSTEM_PROMPT,
    tools=t.TOOLS,
    subagents=[
        {
            "name": "coder",
            "description": "Built-in coder: implements ONE small, self-contained task by writing files.",
            "system_prompt": CODER_PROMPT,
            "tools": [],
        }
    ],
    interrupt_on={"dispatch_external": {"allowed_decisions": ["approve", "edit", "reject"]}},
    example_prompts=[
        "Work through the goal in fleet_manager/goal.md with the coding agents we have, within budget.",
        "Which coding CLIs are installed, and how much of today's budget is left?",
        "Add input validation and tests to the orders service; use the cheapest agent with room left.",
    ],
    mock_script=mock_script,
)

"""Tools for the coding-agent fleet manager: plan a goal, pick an agent, enforce limits, keep a ledger.

External coding CLIs (Claude Code, Codex, Cursor) only run when `APP_ENABLE_EXTERNAL_CLI=true`, always
non-interactively, with a timeout, no shell, and a working directory confined to a per-task workspace
under the data directory. Otherwise every task goes to the built-in `coder` sub-agent, which writes files
in the agent's virtual filesystem.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from nai_agents.core.data import PathError, load_json_state, read_text, save_json_state, state_path, to_json
from nai_agents.core.settings import get_settings

LIMITS_PATH = "fleet_manager/limits.yaml"
GOAL_PATH = "fleet_manager/goal.md"
LEDGER_FILE = "fleet_manager/ledger.json"
BUILTIN = "builtin-coder"
EXTERNAL_CLIS = ("claude", "codex", "cursor-agent")
MAX_TIMEOUT_S = 3600
TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _now() -> datetime:
    return datetime.now(UTC)


def _limits(limits_path: str = LIMITS_PATH) -> dict[str, Any]:
    data = yaml.safe_load(read_text(limits_path or LIMITS_PATH)) or {}
    if not isinstance(data.get("agents"), dict):
        raise ValueError(f"{limits_path} has no `agents` mapping")
    return data


def _ledger() -> list[dict[str, Any]]:
    return load_json_state(LEDGER_FILE, [])


def _save_ledger(entries: list[dict[str, Any]]) -> None:
    save_json_state(LEDGER_FILE, entries[-5000:])


def _today(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    day = _now().date().isoformat()
    return [e for e in entries if str(e.get("at", "")).startswith(day)]


def _cost(cfg: dict[str, Any], tokens_in: int, tokens_out: int) -> float:
    return round(
        tokens_in / 1e6 * float(cfg.get("input_per_mtok", 0))
        + tokens_out / 1e6 * float(cfg.get("output_per_mtok", 0)),
        6,
    )


def _usage(agent: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    mine = [e for e in _today(entries) if e["agent"] == agent and e["status"] not in {"refused", "blocked"}]
    tokens = sum(int(e.get("input_tokens", 0)) + int(e.get("output_tokens", 0)) for e in mine)
    reserved = sum(int(e.get("estimated_tokens", 0)) for e in mine if e["status"] == "assigned")
    return {
        "tasks": len(mine),
        "tokens": tokens + reserved,
        "cost_usd": round(sum(float(e.get("cost_usd", 0)) for e in mine), 6),
    }


def _check(
    agent: str, estimated_tokens: int, limits: dict[str, Any], entries: list[dict[str, Any]]
) -> str | None:
    """None when the agent may take the task, else why not."""
    cfg = limits["agents"].get(agent)
    if cfg is None:
        return f"unknown agent {agent!r}; known: {', '.join(limits['agents'])}"
    used = _usage(agent, entries)
    if used["tasks"] + 1 > int(cfg.get("daily_tasks", 10**9)):
        return f"{agent} reached its daily task limit ({cfg['daily_tasks']})"
    if used["tokens"] + estimated_tokens > int(cfg.get("daily_tokens", 10**12)):
        return (
            f"{agent} would exceed its daily token limit ({used['tokens']} used + {estimated_tokens} "
            f"> {cfg['daily_tokens']})"
        )
    if estimated_tokens > int(cfg.get("max_tokens_per_task", 10**12)):
        return f"task is larger than {agent}'s per-task limit ({cfg['max_tokens_per_task']} tokens); split it"
    return None


def _kind(task: str) -> str:
    low = task.lower()
    if re.search(r"\b(test|tests|pytest|spec)\b", low):
        return "test"
    if re.search(r"\b(document|docs?|readme|guide)\b", low):
        return "docs"
    return "code"


def _estimate(task: str) -> int:
    # A rough budget: bigger asks read more code. Floor of 6k tokens per task.
    return max(6000, len(task.split()) * 600)


# ---- planning -----------------------------------------------------------------------------------


def plan_goal(goal: str = GOAL_PATH) -> str:
    """Turn a coding goal into tasks. `goal` is a goal file (default: the example goal) or the goal text.

    Takes the bullets under a "Tasks" heading (or all bullets / sentences when there is none) and returns
    tasks with an id, kind (code, test, docs), estimated tokens, plus the goal's constraints.
    """
    try:
        text = read_text(goal) if len(goal) < 300 and "\n" not in goal else goal
    except (PathError, OSError):
        text = goal
    title_m = re.search(r"^#\s+(.+)$", text, flags=re.M)
    sections: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        heading = re.match(r"^#{2,6}\s+(.+)$", line)
        if heading:
            current = heading.group(1).strip().lower()
            continue
        item = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.+)$", line)
        if item:
            sections.setdefault(current, []).append(item.group(1).strip())
    items = next((v for k, v in sections.items() if "task" in k), None)
    if items is None:
        items = [i for k, v in sections.items() if "constraint" not in k for i in v]
    if not items:
        items = [s.strip() for s in re.split(r"(?<=[.;])\s+|\n+", text) if len(s.split()) >= 3]
    constraints = next((v for k, v in sections.items() if "constraint" in k), [])
    tasks = [
        {"id": f"t{i}", "task": task, "kind": _kind(task), "estimated_tokens": _estimate(task)}
        for i, task in enumerate(items, start=1)
    ]
    return to_json(
        {"title": title_m.group(1).strip() if title_m else "Goal", "tasks": tasks, "constraints": constraints}
    )


def detect_coding_clis() -> str:
    """Which coding CLIs (claude, codex, cursor-agent) are installed on this host, and whether running them
    is enabled (APP_ENABLE_EXTERNAL_CLI). Nothing is executed."""
    enabled = get_settings().app_enable_external_cli
    found = {name: shutil.which(name) for name in EXTERNAL_CLIS}
    return to_json(
        {
            "external_enabled": enabled,
            "clis": {name: {"installed": bool(path), "path": path} for name, path in found.items()},
            "usable": [name for name, path in found.items() if path and enabled],
            "note": None
            if enabled
            else "External CLIs are disabled; tasks go to the built-in coder sub-agent.",
        }
    )


def fleet_status(limits_path: str = LIMITS_PATH) -> str:
    """Per-agent usage today (tasks, tokens, $) against its limits, and task counts by status."""
    limits = _limits(limits_path)
    entries = _ledger()
    agents = {}
    for name, cfg in limits["agents"].items():
        used = _usage(name, entries)
        agents[name] = {
            **used,
            "kind": cfg.get("kind", "external"),
            "daily_tasks": cfg.get("daily_tasks"),
            "daily_tokens": cfg.get("daily_tokens"),
            "tasks_left": max(0, int(cfg.get("daily_tasks", 0)) - used["tasks"]),
            "tokens_left": max(0, int(cfg.get("daily_tokens", 0)) - used["tokens"]),
            "installed": True if name == BUILTIN else bool(shutil.which(name)),
        }
    by_status: dict[str, int] = {}
    for e in _today(entries):
        by_status[e["status"]] = by_status.get(e["status"], 0) + 1
    total = round(sum(a["cost_usd"] for a in agents.values()), 6)
    return to_json(
        {
            "date": _now().date().isoformat(),
            "agents": agents,
            "tasks_by_status": by_status,
            "cost_today_usd": total,
        }
    )


def _pick(
    kind: str, estimated: int, limits: dict[str, Any], entries: list[dict[str, Any]]
) -> tuple[str, list[str]]:
    notes = []
    if get_settings().app_enable_external_cli:
        order = sorted(
            (n for n, c in limits["agents"].items() if c.get("kind", "external") == "external"),
            key=lambda n: (
                float(limits["agents"][n].get("output_per_mtok", 0)) if kind == "docs" else 0,
                EXTERNAL_CLIS.index(n) if n in EXTERNAL_CLIS else 99,
            ),
        )
        for name in order:
            if not shutil.which(name):
                notes.append(f"{name}: not installed")
                continue
            why = _check(name, estimated, limits, entries)
            if why is None:
                return name, notes
            notes.append(why)
    else:
        notes.append("external CLIs disabled")
    return BUILTIN, notes


def assign_task(
    task_id: str, task: str, agent: str = "auto", estimated_tokens: int = 0, limits_path: str = LIMITS_PATH
) -> str:
    """Reserve a task for one agent after checking its daily task/token limits. Refuses over-limit work.

    `agent` is "auto" (an installed, enabled external CLI with room, else the built-in coder),
    "builtin-coder", "claude", "codex" or "cursor-agent". The answer says how to run it: the `coder`
    sub-agent (task tool) for the built-in coder, or dispatch_external for a CLI.
    """
    if not TASK_ID_RE.match(task_id):
        raise ValueError("task_id must be 1-40 letters, digits, '-' or '_'")
    limits = _limits(limits_path)
    entries = _ledger()
    estimated = int(estimated_tokens) or _estimate(task)
    notes: list[str] = []
    if agent == "auto":
        agent, notes = _pick(_kind(task), estimated, limits, entries)
    elif agent != BUILTIN and agent in EXTERNAL_CLIS and not get_settings().app_enable_external_cli:
        notes.append(f"{agent} requested but external CLIs are disabled; using {BUILTIN}")
        agent = BUILTIN
    why = _check(agent, estimated, limits, entries)
    entry = {
        "task_id": task_id,
        "task": task,
        "agent": agent,
        "estimated_tokens": estimated,
        "at": _now().isoformat(),
        "status": "refused" if why else "assigned",
    }
    if why:
        entry["reason"] = why
    entries.append(entry)
    _save_ledger(entries)
    if why:
        return to_json(
            {"status": "refused", "task_id": task_id, "agent": agent, "reason": why, "notes": notes}
        )
    how = (
        f"Call the task tool with subagent_type='coder' and the task text, "
        f"then record_result('{task_id}', ...)."
        if agent == BUILTIN
        else f"Call dispatch_external(task_id='{task_id}', agent='{agent}', prompt=...)."
    )
    return to_json(
        {
            "status": "assigned",
            "task_id": task_id,
            "agent": agent,
            "estimated_tokens": estimated,
            "next": how,
            "notes": notes,
        }
    )


def _workspace(task_id: str) -> Path:
    if not TASK_ID_RE.match(task_id):
        raise ValueError("task_id must be 1-40 letters, digits, '-' or '_'")
    path = state_path("fleet_manager", "workspaces", task_id, ".keep").parent
    path.mkdir(parents=True, exist_ok=True)
    return path


def _argv(agent: str, prompt: str) -> list[str]:
    exe = shutil.which(agent) or agent
    return {
        "claude": [exe, "-p", prompt],
        "codex": [exe, "exec", prompt],
        "cursor-agent": [exe, "-p", prompt],
    }[agent]


def _run_cli(argv: list[str], cwd: Path, timeout_s: float) -> tuple[int, str, str]:
    """Run a CLI without a shell. Separate so tests can replace it."""
    proc = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _snapshot(root: Path) -> dict[str, float]:
    return {p.relative_to(root).as_posix(): p.stat().st_mtime for p in root.rglob("*") if p.is_file()}


def dispatch_external(task_id: str, agent: str, prompt: str, timeout_s: int = 0) -> str:
    """Run one task with an external coding CLI (claude -p / codex exec / cursor-agent -p). Needs approval.

    Only when APP_ENABLE_EXTERNAL_CLI is true and the CLI is installed and under its limits. Runs without a
    shell, with a timeout, in a workspace under the data directory; returns the exit code, the output tail,
    the files it changed, tokens (estimated from text) and cost.
    """
    if agent not in EXTERNAL_CLIS:
        raise ValueError(f"agent must be one of {', '.join(EXTERNAL_CLIS)}")
    limits = _limits()
    entries = _ledger()
    cfg = limits["agents"].get(agent, {})
    base = {"task_id": task_id, "task": prompt[:500], "agent": agent, "at": _now().isoformat()}
    blocked = None
    if not get_settings().app_enable_external_cli:
        blocked = "external CLIs are disabled (set APP_ENABLE_EXTERNAL_CLI=true to allow them)"
    elif not shutil.which(agent):
        blocked = f"{agent} is not installed on this host"
    else:
        blocked = _check(agent, _estimate(prompt), limits, entries)
    if blocked:
        entries.append({**base, "status": "blocked", "reason": blocked})
        _save_ledger(entries)
        return to_json(
            {
                "status": "blocked",
                "task_id": task_id,
                "agent": agent,
                "reason": blocked,
                "fallback": f"assign_task('{task_id}', ..., agent='{BUILTIN}') and use the coder sub-agent",
            }
        )
    workspace = _workspace(task_id)
    timeout = min(float(timeout_s or limits.get("default_timeout_s", 900)), MAX_TIMEOUT_S)
    before = _snapshot(workspace)
    try:
        code, out, err = _run_cli(_argv(agent, prompt), workspace, timeout)
        status = "done" if code == 0 else "failed"
    except subprocess.TimeoutExpired:
        code, out, err, status = -1, "", f"timed out after {timeout:.0f}s", "failed"
    after = _snapshot(workspace)
    changed = sorted(p for p, m in after.items() if before.get(p) != m)
    tokens_in, tokens_out = len(prompt) // 4 + 1, len(out) // 4 + 1
    entry = {
        **base,
        "status": status,
        "exit_code": code,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cost_usd": _cost(cfg, tokens_in, tokens_out),
        "files": changed,
        "summary": (out or err)[-500:],
    }
    # Replace the reservation made by assign_task, if any.
    entries = [e for e in entries if not (e["task_id"] == task_id and e["status"] == "assigned")] + [entry]
    _save_ledger(entries)
    return to_json(
        {**entry, "workspace": str(workspace), "output_tail": out[-3000:], "stderr_tail": err[-1000:]}
    )


def record_result(
    task_id: str,
    status: str,
    summary: str,
    files: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    limits_path: str = LIMITS_PATH,
) -> str:
    """Record the outcome of a task run by the built-in coder: status (done/failed), a one-line summary,
    the files it wrote (comma-separated) and tokens used (0 = use the reservation's estimate)."""
    if status not in {"done", "failed"}:
        raise ValueError("status must be 'done' or 'failed'")
    limits = _limits(limits_path)
    entries = _ledger()
    match = next(
        (e for e in reversed(entries) if e["task_id"] == task_id and e["status"] == "assigned"), None
    )
    if match is None:
        raise ValueError(f"No assigned task {task_id!r}; call assign_task first")
    if not input_tokens and not output_tokens:
        est = int(match.get("estimated_tokens", 0))
        input_tokens, output_tokens = int(est * 0.7), int(est * 0.3)
    cfg = limits["agents"].get(match["agent"], {})
    match.update(
        status=status,
        summary=summary[:500],
        files=[f.strip() for f in files.split(",") if f.strip()],
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        cost_usd=_cost(cfg, int(input_tokens), int(output_tokens)),
        finished_at=_now().isoformat(),
    )
    _save_ledger(entries)
    return to_json(match)


def collect_results() -> str:
    """All of today's tasks with agent, status, files, tokens and cost, plus totals per agent."""
    today = _today(_ledger())
    latest: dict[str, dict[str, Any]] = {}
    attempts: dict[str, list[str]] = {}
    for e in today:
        attempts.setdefault(e["task_id"], []).append(f"{e['agent']}:{e['status']}")
        if e["status"] not in {"blocked", "refused"} or e["task_id"] not in latest:
            latest[e["task_id"]] = e
    tasks = [
        {
            k: e.get(k)
            for k in ("task_id", "task", "agent", "status", "files", "summary", "cost_usd", "reason")
        }
        | {"attempts": attempts[tid]}
        for tid, e in latest.items()
    ]
    per_agent: dict[str, float] = {}
    for e in today:
        per_agent[e["agent"]] = round(per_agent.get(e["agent"], 0.0) + float(e.get("cost_usd", 0)), 6)
    statuses = [t["status"] for t in tasks]
    return to_json(
        {
            "tasks": tasks,
            "done": statuses.count("done"),
            "failed": statuses.count("failed"),
            "pending": statuses.count("assigned"),
            "refused_or_blocked": sum(1 for s in statuses if s in {"refused", "blocked"}),
            "cost_by_agent_usd": per_agent,
            "total_cost_usd": round(sum(per_agent.values()), 6),
        }
    )


TOOLS = [
    plan_goal,
    detect_coding_clis,
    fleet_status,
    assign_task,
    dispatch_external,
    record_result,
    collect_results,
]

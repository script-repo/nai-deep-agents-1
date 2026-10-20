"""Command line: `nai-agents serve | list | run | eval`."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

from nai_agents import __version__


def _serve(args: argparse.Namespace) -> None:
    import uvicorn

    from nai_agents.core.settings import get_settings

    s = get_settings()
    uvicorn.run(
        "nai_agents.server.app:get_app",
        factory=True,
        host=args.host or s.app_host,
        port=args.port or s.app_port,
        log_level=s.app_log_level,
        proxy_headers=True,
        forwarded_allow_ips=args.forwarded_allow_ips,
    )


def _list(_: argparse.Namespace) -> None:
    from nai_agents.core.registry import all_agents

    for spec in all_agents():
        print(f"{spec.number:>2}. {spec.id:<18} {spec.name} — {spec.summary}")


async def _run_async(args: argparse.Namespace) -> int:
    from nai_agents.core.checkpoint import open_checkpointer
    from nai_agents.core.models import ModelConfig
    from nai_agents.core.registry import get_agent
    from nai_agents.core.runner import resume_command, stream_events
    from nai_agents.core.settings import get_settings

    settings = get_settings()
    spec = get_agent(args.agent)
    prompt = args.prompt or (spec.example_prompts[0] if spec.example_prompts else "")
    async with open_checkpointer(settings) as saver:
        graph = spec.build(ModelConfig.from_settings(settings), saver)
        thread = args.thread or str(uuid.uuid4())
        graph_input: object = {"messages": [{"role": "user", "content": prompt}]}
        print(f"# {spec.name} · thread {thread}\n> {prompt}\n", file=sys.stderr)
        while True:
            pending = None
            async for ev in stream_events(graph, graph_input, thread):
                t = ev["type"]
                if t == "token" and ev["agent"] == "main":
                    print(ev["text"], end="", flush=True)
                elif t == "tool_call":
                    print(
                        f"\n  · [{ev['agent']}] {ev['name']}({json.dumps(ev['args'])[:160]})", file=sys.stderr
                    )
                elif t == "interrupt":
                    pending = ev
                elif t == "error":
                    print(f"\nERROR: {ev['message']}", file=sys.stderr)
                    return 1
            if not pending:
                print()
                return 0
            decisions = []
            for action in pending["actions"]:
                print(
                    f"\n\n⚠ Approval needed: {action['name']}({json.dumps(action['args'])})", file=sys.stderr
                )
                if args.yes:
                    answer = "y"
                elif args.no:
                    answer = "n"
                else:
                    answer = input("Approve? [y/N] ").strip().lower()
                decisions.append(
                    {"type": "approve"}
                    if answer.startswith("y")
                    else {"type": "reject", "message": "Rejected by the operator."}
                )
            graph_input = resume_command(decisions)


def _eval(args: argparse.Namespace) -> None:
    from nai_agents.core.data import resolve_input
    from nai_agents.core.evals import load_suite, run_suite
    from nai_agents.core.models import find_model, load_catalog

    suite = load_suite(resolve_input(args.suite)).subset(args.category)
    names = args.models.split(",") if args.models else [m.name for m in load_catalog()]
    for name in names:
        report = run_suite(find_model(name), suite)
        print(json.dumps(report.summary()))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="nai-agents", description="NAI Deep Agents")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web UI and API")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--forwarded-allow-ips", default="127.0.0.1")
    s.set_defaults(fn=_serve)
    sub.add_parser("list", help="list the agents").set_defaults(fn=_list)
    r = sub.add_parser("run", help="run one agent in the terminal")
    r.add_argument("agent")
    r.add_argument("prompt", nargs="?")
    r.add_argument("--thread")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--yes", "-y", action="store_true", help="approve every interrupt")
    g.add_argument("--no", action="store_true", help="reject every interrupt")
    r.set_defaults(fn=lambda a: sys.exit(asyncio.run(_run_async(a))))
    e = sub.add_parser("eval", help="run an eval suite against catalog models")
    e.add_argument("--suite", default="evals/general.yaml")
    e.add_argument("--category")
    e.add_argument("--models", help="comma-separated catalog names (default: all)")
    e.set_defaults(fn=_eval)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()

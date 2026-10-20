"""Checkpointers: in-memory (tests), SQLite (single node, default) or Postgres (shared/multi-replica)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from nai_agents.core.settings import Settings


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[Any]:
    kind = settings.app_checkpointer
    if kind == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()
    elif kind == "sqlite":
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        path = settings.state_dir / "checkpoints.sqlite"
        async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
            await saver.setup()
            yield saver
    elif kind == "postgres":
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        except ImportError as exc:  # pragma: no cover - depends on the optional extra
            raise RuntimeError("Install the postgres extra: uv sync --extra postgres") from exc
        if not settings.app_database_url:
            raise RuntimeError("APP_DATABASE_URL is required when APP_CHECKPOINTER=postgres")
        async with AsyncPostgresSaver.from_conn_string(settings.app_database_url) as saver:
            await saver.setup()
            yield saver
    else:  # pragma: no cover
        raise ValueError(f"Unknown checkpointer {kind!r}")

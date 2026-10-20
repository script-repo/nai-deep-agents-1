"""Thread metadata (owner, agent, title). Conversation state itself lives in the LangGraph checkpointer."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class ThreadMeta:
    id: str
    agent_id: str
    owner: str
    title: str
    created_at: str
    updated_at: str
    status: str = "idle"  # idle | running | interrupted | error

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


SCHEMA = """CREATE TABLE IF NOT EXISTS nda_threads (
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, owner TEXT NOT NULL, title TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'idle')"""


class SqliteThreadStore:
    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(SCHEMA)
            self._conn.commit()

    def _q(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock:
            cur = self._conn.execute(sql, args)
            rows = cur.fetchall()
            self._conn.commit()
            return rows

    def create(self, agent_id: str, owner: str, title: str = "New chat") -> ThreadMeta:
        meta = ThreadMeta(str(uuid.uuid4()), agent_id, owner, title, _now(), _now())
        self._q(
            "INSERT INTO nda_threads VALUES (?,?,?,?,?,?,?)",
            (meta.id, meta.agent_id, meta.owner, meta.title, meta.created_at, meta.updated_at, meta.status),
        )
        return meta

    def get(self, thread_id: str) -> ThreadMeta | None:
        rows = self._q("SELECT * FROM nda_threads WHERE id=?", (thread_id,))
        return ThreadMeta(*rows[0]) if rows else None

    def list(self, owner: str, agent_id: str | None = None, limit: int = 200) -> list[ThreadMeta]:
        if agent_id:
            rows = self._q(
                "SELECT * FROM nda_threads WHERE owner=? AND agent_id=? ORDER BY updated_at DESC LIMIT ?",
                (owner, agent_id, limit),
            )
        else:
            rows = self._q(
                "SELECT * FROM nda_threads WHERE owner=? ORDER BY updated_at DESC LIMIT ?", (owner, limit)
            )
        return [ThreadMeta(*r) for r in rows]

    def update(self, thread_id: str, **fields: str) -> None:
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k}=?" for k in fields)
        self._q(f"UPDATE nda_threads SET {cols} WHERE id=?", (*fields.values(), thread_id))

    def delete(self, thread_id: str) -> None:
        self._q("DELETE FROM nda_threads WHERE id=?", (thread_id,))


class PostgresThreadStore(SqliteThreadStore):
    """Same API on Postgres, for multi-replica deployments (uses the checkpointer's database)."""

    def __init__(self, url: str) -> None:  # pragma: no cover - exercised only with a live Postgres
        import psycopg

        self._conn = psycopg.connect(url, autocommit=True)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(SCHEMA)

    def _q(self, sql: str, args: tuple = ()) -> list[tuple]:  # pragma: no cover
        sql = sql.replace("?", "%s")
        with self._lock, self._conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall() if cur.description else []

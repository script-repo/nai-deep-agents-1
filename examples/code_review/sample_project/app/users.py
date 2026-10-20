"""User lookups."""

from __future__ import annotations

import sqlite3


def find_user(conn: sqlite3.Connection, user_id: int) -> tuple | None:
    """Fetch one user row by id."""
    return conn.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,)).fetchone()

"""Lightweight persistent audit log stored in SQLite, capped per guild."""

from __future__ import annotations

from datetime import datetime, timezone

from services.storage import _connect, initialize_database

MAX_EVENTS_PER_GUILD = 500


def initialize_audit_log() -> None:
    initialize_database()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                actor_id INTEGER NOT NULL,
                actor_name TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT,
                details TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_guild_created ON audit_events(guild_id, created_at DESC)")


def record_event(guild_id: int, actor_id: int, actor_name: str, action: str, *, target: str = "", details: str = "") -> None:
    initialize_audit_log()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO audit_events(guild_id,actor_id,actor_name,action,target,details,created_at) VALUES(?,?,?,?,?,?,?)",
            (guild_id, actor_id, actor_name, action, target, details, datetime.now(timezone.utc).isoformat()),
        )
        conn.execute(
            """
            DELETE FROM audit_events
            WHERE guild_id=?
              AND id NOT IN (
                  SELECT id FROM audit_events WHERE guild_id=? ORDER BY id DESC LIMIT ?
              )
            """,
            (guild_id, guild_id, MAX_EVENTS_PER_GUILD),
        )


def recent_events(guild_id: int, limit: int = 10) -> list[dict[str, str]]:
    initialize_audit_log()
    limit = max(1, min(limit, 25))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT actor_name, action, target, details, created_at FROM audit_events WHERE guild_id=? ORDER BY id DESC LIMIT ?",
            (guild_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]

"""Privacy-minimizing command-use metrics and aggregate CSV reports.

Only the guild, command name, outcome, and UTC timestamp are stored. User IDs,
usernames, command arguments, message contents, and target records are not stored.
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from services import storage

LOGGER = logging.getLogger(__name__)
RETENTION_DAYS = 400
VALID_OUTCOMES = {"completed", "raised_error"}
REPORT_MAX_DAYS = 400


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalise_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def initialize_analytics() -> None:
    """Create the command-usage table without modifying existing school records."""
    storage.initialize_database()
    with storage._connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS command_usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                command_name TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK(outcome IN ('completed', 'raised_error')),
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_command_usage_guild_created "
            "ON command_usage_events(guild_id, created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audited_action_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audited_action_guild_created "
            "ON audited_action_events(guild_id, created_at)"
        )


def record_command_usage(
    guild_id: int,
    command_name: str,
    outcome: str,
    *,
    occurred_at: datetime | None = None,
) -> bool:
    """Record one slash-command invocation without recording the invoking user."""
    if not isinstance(guild_id, int) or guild_id <= 0:
        return False
    if not isinstance(command_name, str) or not command_name.strip():
        return False
    command_name = command_name.strip()[:100]
    if command_name.casefold() == "usagereport":
        # Do not inflate usage figures with administrative report exports.
        return False
    if outcome not in VALID_OUTCOMES:
        return False

    timestamp = _normalise_utc(occurred_at or _utc_now())
    timestamp_text = timestamp.isoformat(timespec="seconds")
    retention_cutoff = (_utc_now() - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds")

    try:
        with storage._connect() as conn:
            conn.execute(
                """
                INSERT INTO command_usage_events(guild_id, command_name, outcome, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, command_name, outcome, timestamp_text),
            )
            conn.execute(
                "DELETE FROM command_usage_events WHERE created_at < ?",
                (retention_cutoff,),
            )
        return True
    except (OSError, sqlite3.Error):
        LOGGER.exception("Could not record command-usage analytics; the command itself remains authoritative.")
        return False


def record_audited_action(
    guild_id: int,
    action: str,
    *,
    occurred_at: datetime | None = None,
) -> bool:
    """Mirror an existing audited workflow into aggregate-only analytics."""
    if not isinstance(guild_id, int) or guild_id <= 0:
        return False
    if not isinstance(action, str) or not action.strip():
        return False
    action = action.strip()[:100]
    timestamp = _normalise_utc(occurred_at or _utc_now())
    timestamp_text = timestamp.isoformat(timespec="seconds")
    retention_cutoff = (_utc_now() - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds")
    try:
        with storage._connect() as conn:
            conn.execute(
                "INSERT INTO audited_action_events(guild_id, action, created_at) VALUES (?, ?, ?)",
                (guild_id, action, timestamp_text),
            )
            conn.execute(
                "DELETE FROM audited_action_events WHERE created_at < ?",
                (retention_cutoff,),
            )
        return True
    except (OSError, sqlite3.Error):
        LOGGER.exception("Could not record aggregate audited-action analytics.")
        return False


def export_usage_report(
    guild_id: int,
    days: int = 30,
    *,
    now: datetime | None = None,
    output_directory: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Write a per-day aggregate CSV, with no individual user activity rows."""
    if not isinstance(guild_id, int) or guild_id <= 0:
        raise ValueError("A valid server ID is required.")
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= REPORT_MAX_DAYS:
        raise ValueError(f"days must be between 1 and {REPORT_MAX_DAYS}.")

    initialize_analytics()
    end_at = _normalise_utc(now or _utc_now())
    start_at = end_at.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)

    with storage._connect() as conn:
        rows = conn.execute(
            """
            SELECT date_utc, category, item, outcome, event_count
            FROM (
                SELECT substr(created_at, 1, 10) AS date_utc,
                       'command_invocation' AS category,
                       command_name AS item,
                       outcome,
                       COUNT(*) AS event_count
                FROM command_usage_events
                WHERE guild_id = ? AND created_at >= ? AND created_at <= ?
                GROUP BY substr(created_at, 1, 10), command_name, outcome
                UNION ALL
                SELECT substr(created_at, 1, 10) AS date_utc,
                       'audited_action' AS category,
                       action AS item,
                       'recorded' AS outcome,
                       COUNT(*) AS event_count
                FROM audited_action_events
                WHERE guild_id = ? AND created_at >= ? AND created_at <= ?
                GROUP BY substr(created_at, 1, 10), action
            )
            ORDER BY date_utc ASC, category ASC, item ASC, outcome ASC
            """,
            (
                guild_id,
                start_at.isoformat(timespec="seconds"),
                end_at.isoformat(timespec="seconds"),
                guild_id,
                start_at.isoformat(timespec="seconds"),
                end_at.isoformat(timespec="seconds"),
            ),
        ).fetchall()

    target_directory = output_directory or (storage.DATA_DIR / "reports")
    target_directory.mkdir(parents=True, exist_ok=True)
    filename = (
        f"usage-report-{start_at:%Y%m%d}-{end_at:%Y%m%d}-"
        f"{end_at:%H%M%S}Z.csv"
    )
    report_path = target_directory / filename

    with report_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date_utc", "category", "command_or_action", "outcome", "count"])
        for row in rows:
            writer.writerow(
                [row["date_utc"], row["category"], row["item"], row["outcome"], row["event_count"]]
            )

    completed = sum(
        int(row["event_count"])
        for row in rows
        if row["category"] == "command_invocation" and row["outcome"] == "completed"
    )
    raised_errors = sum(
        int(row["event_count"])
        for row in rows
        if row["category"] == "command_invocation" and row["outcome"] == "raised_error"
    )
    audited_actions = sum(
        int(row["event_count"]) for row in rows if row["category"] == "audited_action"
    )
    summary: dict[str, Any] = {
        "guild_id": guild_id,
        "days": days,
        "start_at": start_at,
        "end_at": end_at,
        "completed": completed,
        "raised_errors": raised_errors,
        "audited_actions": audited_actions,
        "total": completed + raised_errors,
        "groups": len(rows),
        "path": report_path,
    }
    return report_path, summary

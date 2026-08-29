"""Persistence layer for multi-school configuration and academic records."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_FILE = DATA_DIR / "guild_config.json"
DATABASE_FILE = DATA_DIR / "school.db"


def _ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    _ensure_storage()
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _migrate_legacy_enrollments(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "enrollments"):
        return
    columns = _table_columns(conn, "enrollments")
    if "stream_id" in columns or "class_id" not in columns:
        return
    conn.execute("ALTER TABLE enrollments RENAME TO enrollments_legacy")
    conn.execute("""
        CREATE TABLE enrollments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            stream_id INTEGER NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
            FOREIGN KEY(stream_id) REFERENCES streams(id) ON DELETE CASCADE
        )
    """)
    conn.execute("DROP TABLE enrollments_legacy")


def initialize_database() -> None:
    with _connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS academic_years (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(guild_id, name)
        );
        CREATE TABLE IF NOT EXISTS streams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            academic_year_id INTEGER NOT NULL,
            level_name TEXT NOT NULL,
            stream_name TEXT NOT NULL,
            role_name TEXT NOT NULL,
            UNIQUE(guild_id, academic_year_id, level_name, stream_name),
            FOREIGN KEY(academic_year_id) REFERENCES academic_years(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            discord_id INTEGER,
            display_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            UNIQUE(guild_id, discord_id)
        );
        """)
        _migrate_legacy_enrollments(conn)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS enrollments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                stream_id INTEGER NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
                FOREIGN KEY(stream_id) REFERENCES streams(id) ON DELETE CASCADE
            )
        """)
        conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_students_guild_discord ON students(guild_id, discord_id);
        CREATE INDEX IF NOT EXISTS idx_streams_guild_year ON streams(guild_id, academic_year_id);
        CREATE INDEX IF NOT EXISTS idx_enrollments_student ON enrollments(student_id);
        """)


def load_all() -> dict[str, Any]:
    _ensure_storage()
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_all(data: dict[str, Any]) -> None:
    _ensure_storage()
    CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_guild_config(guild_id: int) -> dict[str, Any] | None:
    return load_all().get(str(guild_id))


def save_guild_config(guild_id: int, config: dict[str, Any]) -> None:
    data = load_all()
    data[str(guild_id)] = config
    save_all(data)
    sync_configuration_to_database(guild_id, config)


def delete_guild_config(guild_id: int) -> None:
    data = load_all()
    data.pop(str(guild_id), None)
    save_all(data)


def reset_guild_data(guild_id: int) -> None:
    delete_guild_config(guild_id)
    initialize_database()
    with _connect() as conn:
        conn.execute("DELETE FROM students WHERE guild_id=?", (guild_id,))
        conn.execute("DELETE FROM streams WHERE guild_id=?", (guild_id,))
        conn.execute("DELETE FROM academic_years WHERE guild_id=?", (guild_id,))


def ensure_academic_year(guild_id: int, name: str, *, active: bool = False) -> int:
    initialize_database()
    today = date.today().isoformat()
    with _connect() as conn:
        if active:
            conn.execute("UPDATE academic_years SET is_active=0 WHERE guild_id=?", (guild_id,))
        conn.execute(
            "INSERT OR IGNORE INTO academic_years(guild_id,name,is_active,created_at) VALUES(?,?,?,?)",
            (guild_id, name, int(active), today),
        )
        if active:
            conn.execute(
                "UPDATE academic_years SET is_active=1 WHERE guild_id=? AND name=?",
                (guild_id, name),
            )
        row = conn.execute(
            "SELECT id FROM academic_years WHERE guild_id=? AND name=?",
            (guild_id, name),
        ).fetchone()
        return int(row["id"])


def create_academic_year(guild_id: int, name: str, *, activate: bool = True) -> int:
    return ensure_academic_year(guild_id, name, active=activate)


def get_active_academic_year(guild_id: int) -> sqlite3.Row | None:
    initialize_database()
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM academic_years WHERE guild_id=? AND is_active=1 ORDER BY id DESC LIMIT 1",
            (guild_id,),
        ).fetchone()


def list_academic_years(guild_id: int) -> list[sqlite3.Row]:
    initialize_database()
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM academic_years WHERE guild_id=? ORDER BY name DESC",
            (guild_id,),
        ).fetchall()


def sync_configuration_to_database(guild_id: int, config: dict[str, Any]) -> None:
    initialize_database()
    year_name = config.get("academic_year") or f"{date.today().year}/{date.today().year + 1}"
    year_id = ensure_academic_year(guild_id, year_name, active=True)
    with _connect() as conn:
        for level in config.get("levels", []):
            for stream in level.get("streams", []):
                stream_name = stream["name"]
                role_name = f"Filière - {level['name']} - {stream_name}"
                conn.execute(
                    "INSERT OR IGNORE INTO streams(guild_id,academic_year_id,level_name,stream_name,role_name) VALUES(?,?,?,?,?)",
                    (guild_id, year_id, level["name"], stream_name, role_name),
                )
                conn.execute(
                    "UPDATE streams SET role_name=? WHERE guild_id=? AND academic_year_id=? AND level_name=? AND stream_name=?",
                    (role_name, guild_id, year_id, level["name"], stream_name),
                )


def get_stream(guild_id: int, academic_year_id: int, level_name: str, stream_name: str) -> sqlite3.Row | None:
    initialize_database()
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM streams WHERE guild_id=? AND academic_year_id=? AND level_name=? AND stream_name=? LIMIT 1",
            (guild_id, academic_year_id, level_name, stream_name),
        ).fetchone()


def upsert_student(guild_id: int, discord_id: int | None, display_name: str) -> int:
    initialize_database()
    with _connect() as conn:
        row = (
            conn.execute(
                "SELECT id FROM students WHERE guild_id=? AND discord_id=?",
                (guild_id, discord_id),
            ).fetchone()
            if discord_id is not None
            else None
        )
        if row:
            conn.execute("UPDATE students SET display_name=? WHERE id=?", (display_name, row["id"]))
            return int(row["id"])
        cur = conn.execute(
            "INSERT INTO students(guild_id,discord_id,display_name,created_at) VALUES(?,?,?,?)",
            (guild_id, discord_id, display_name, date.today().isoformat()),
        )
        return int(cur.lastrowid)


def enroll_student(guild_id: int, student_id: int, academic_year_id: int, level_name: str, stream_name: str) -> None:
    stream = get_stream(guild_id, academic_year_id, level_name, stream_name)
    if stream is None:
        raise ValueError("Selected stream is not configured for the active academic year.")
    today = date.today().isoformat()
    with _connect() as conn:
        conn.execute(
            "UPDATE enrollments SET end_date=?, status='transferred' WHERE student_id=? AND status='active'",
            (today, student_id),
        )
        conn.execute(
            "INSERT INTO enrollments(student_id,stream_id,start_date,status) VALUES(?,?,?,'active')",
            (student_id, int(stream["id"]), today),
        )
        conn.execute(
            "UPDATE students SET status='active' WHERE id=? AND guild_id=?",
            (student_id, guild_id),
        )


def mark_student_left(guild_id: int, student_id: int) -> None:
    today = date.today().isoformat()
    with _connect() as conn:
        conn.execute(
            "UPDATE enrollments SET end_date=?, status='left_school' WHERE student_id=? AND status='active'",
            (today, student_id),
        )
        conn.execute(
            "UPDATE students SET status='left_school' WHERE id=? AND guild_id=?",
            (student_id, guild_id),
        )


def get_student(guild_id: int, discord_id: int) -> sqlite3.Row | None:
    initialize_database()
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM students WHERE guild_id=? AND discord_id=?",
            (guild_id, discord_id),
        ).fetchone()


def get_student_history(guild_id: int, discord_id: int) -> list[sqlite3.Row]:
    initialize_database()
    with _connect() as conn:
        return conn.execute(
            """
            SELECT ay.name AS academic_year,
                   s.level_name,
                   s.stream_name,
                   e.start_date,
                   e.end_date,
                   e.status
            FROM students st
            JOIN enrollments e ON e.student_id=st.id
            JOIN streams s ON s.id=e.stream_id
            JOIN academic_years ay ON ay.id=s.academic_year_id
            WHERE st.guild_id=? AND st.discord_id=?
            ORDER BY e.start_date DESC
            """,
            (guild_id, discord_id),
        ).fetchall()


initialize_database()

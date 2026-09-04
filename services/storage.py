"""Persistent JSON configuration and SQLite academic records."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

from config.curriculum import get_stream_abbreviation

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_FILE = DATA_DIR / "guild_config.json"
DATABASE_FILE = DATA_DIR / "school.db"
SQLITE_BUSY_TIMEOUT_MS = 5000


def _ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    _ensure_storage()
    conn = sqlite3.connect(DATABASE_FILE, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _table_columns(conn: sqlite3.Connection, name: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({name})").fetchall()}


def _next_legacy_name(conn: sqlite3.Connection) -> str:
    base = "enrollments_legacy_v1"
    if not _table_exists(conn, base):
        return base
    index = 2
    while _table_exists(conn, f"{base}_{index}"):
        index += 1
    return f"{base}_{index}"


def _create_enrollments_table(conn: sqlite3.Connection) -> None:
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


def _migrate_legacy_enrollments(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "enrollments"):
        _create_enrollments_table(conn)
        return
    columns = _table_columns(conn, "enrollments")
    if "stream_id" in columns:
        return
    if "class_id" not in columns or "student_id" not in columns:
        return
    legacy_name = _next_legacy_name(conn)
    conn.execute(f"ALTER TABLE enrollments RENAME TO {legacy_name}")
    _create_enrollments_table(conn)
    if _table_exists(conn, "classes"):
        class_columns = _table_columns(conn, "classes")
        if {"id", "stream_id"}.issubset(class_columns):
            conn.execute(f"""
                INSERT INTO enrollments(student_id, stream_id, start_date, end_date, status)
                SELECT e.student_id, c.stream_id, COALESCE(e.start_date, DATE('now')), e.end_date,
                       CASE WHEN COALESCE(e.status, 'active') IN ('active','transferred','left_school')
                            THEN COALESCE(e.status, 'active') ELSE 'active' END
                FROM {legacy_name} e
                JOIN classes c ON c.id=e.class_id
                JOIN students st ON st.id=e.student_id
                JOIN streams s ON s.id=c.stream_id
                WHERE st.guild_id=s.guild_id
            """)


def _deduplicate_active_enrollments(conn: sqlite3.Connection) -> None:
    conn.execute("""
        UPDATE enrollments
        SET status='transferred', end_date=COALESCE(end_date, DATE('now'))
        WHERE status='active'
          AND id NOT IN (SELECT MAX(id) FROM enrollments WHERE status='active' GROUP BY student_id)
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_one_active_enrollment_per_student ON enrollments(student_id) WHERE status='active'")


def _deduplicate_active_academic_years(conn: sqlite3.Connection) -> None:
    conn.execute("""
        UPDATE academic_years
        SET is_active=0
        WHERE is_active=1
          AND id NOT IN (
              SELECT MAX(id) FROM academic_years WHERE is_active=1 GROUP BY guild_id
          )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_one_active_academic_year_per_guild ON academic_years(guild_id) WHERE is_active=1")


def initialize_database() -> None:
    with _connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS academic_years (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, name TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, UNIQUE(guild_id, name));
        CREATE TABLE IF NOT EXISTS streams (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, academic_year_id INTEGER NOT NULL, level_name TEXT NOT NULL, stream_name TEXT NOT NULL, role_name TEXT NOT NULL, UNIQUE(guild_id, academic_year_id, level_name, stream_name), FOREIGN KEY(academic_year_id) REFERENCES academic_years(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS students (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, discord_id INTEGER, display_name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, UNIQUE(guild_id, discord_id));
        """)
        _migrate_legacy_enrollments(conn)
        _deduplicate_active_enrollments(conn)
        _deduplicate_active_academic_years(conn)
        conn.executescript("CREATE INDEX IF NOT EXISTS idx_students_guild_discord ON students(guild_id, discord_id); CREATE INDEX IF NOT EXISTS idx_streams_guild_year ON streams(guild_id, academic_year_id); CREATE INDEX IF NOT EXISTS idx_enrollments_student ON enrollments(student_id);")


def load_all() -> dict[str, Any]:
    _ensure_storage()
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_all(data: dict[str, Any]) -> None:
    _ensure_storage()
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    fd, temp_name = tempfile.mkstemp(prefix=f".{CONFIG_FILE.name}.", dir=DATA_DIR, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, CONFIG_FILE)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def get_guild_config(guild_id: int) -> dict[str, Any] | None:
    return load_all().get(str(guild_id))


def save_guild_config(guild_id: int, config: dict[str, Any]) -> None:
    initialize_database()
    old_data = load_all()
    new_data = deepcopy(old_data)
    new_data[str(guild_id)] = deepcopy(config)
    with _connect() as conn:
        try:
            _sync_configuration_to_database_conn(conn, guild_id, config)
            save_all(new_data)
            conn.commit()
        except Exception:
            conn.rollback()
            try:
                save_all(old_data)
            except Exception:
                pass
            raise


def delete_guild_config(guild_id: int) -> None:
    data = load_all()
    data.pop(str(guild_id), None)
    save_all(data)


def _write_json_temp(data: dict[str, Any]) -> str:
    _ensure_storage()
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    fd, temp_name = tempfile.mkstemp(prefix=f".{CONFIG_FILE.name}.transaction.", dir=DATA_DIR, text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return temp_name


def reset_guild_data(guild_id: int) -> None:
    """Reset JSON and SQLite state with coordinated rollback on persistence failure."""
    old_data = load_all()
    new_data = deepcopy(old_data)
    new_data.pop(str(guild_id), None)
    old_config_temp = _write_json_temp(old_data)
    new_config_temp = _write_json_temp(new_data)
    try:
        with _connect() as conn:
            try:
                conn.execute("DELETE FROM students WHERE guild_id=?", (guild_id,))
                conn.execute("DELETE FROM streams WHERE guild_id=?", (guild_id,))
                conn.execute("DELETE FROM academic_years WHERE guild_id=?", (guild_id,))
                if _table_exists(conn, "audit_events"):
                    conn.execute("DELETE FROM audit_events WHERE guild_id=?", (guild_id,))
                os.replace(new_config_temp, CONFIG_FILE)
                new_config_temp = ""
                conn.commit()
            except Exception:
                conn.rollback()
                try:
                    os.replace(old_config_temp, CONFIG_FILE)
                    old_config_temp = ""
                except OSError:
                    pass
                raise
    finally:
        for path in (old_config_temp, new_config_temp):
            if path and os.path.exists(path):
                os.unlink(path)


def ensure_academic_year(guild_id: int, name: str, *, active: bool = False) -> int:
    initialize_database()
    today = date.today().isoformat()
    with _connect() as conn:
        if active:
            conn.execute("UPDATE academic_years SET is_active=0 WHERE guild_id=?", (guild_id,))
        conn.execute("INSERT OR IGNORE INTO academic_years(guild_id,name,is_active,created_at) VALUES(?,?,?,?)", (guild_id, name, int(active), today))
        if active:
            conn.execute("UPDATE academic_years SET is_active=1 WHERE guild_id=? AND name=?", (guild_id, name))
        return int(conn.execute("SELECT id FROM academic_years WHERE guild_id=? AND name=?", (guild_id, name)).fetchone()[0])


def create_academic_year(guild_id: int, name: str, *, activate: bool = True) -> int:
    return ensure_academic_year(guild_id, name, active=activate)


def get_active_academic_year(guild_id: int) -> sqlite3.Row | None:
    initialize_database()
    with _connect() as conn:
        return conn.execute("SELECT * FROM academic_years WHERE guild_id=? AND is_active=1 ORDER BY id DESC LIMIT 1", (guild_id,)).fetchone()


def list_academic_years(guild_id: int) -> list[sqlite3.Row]:
    initialize_database()
    with _connect() as conn:
        return conn.execute("SELECT * FROM academic_years WHERE guild_id=? ORDER BY name DESC", (guild_id,)).fetchall()


def _sync_configuration_to_database_conn(conn: sqlite3.Connection, guild_id: int, config: dict[str, Any]) -> None:
    year_name = config.get("academic_year") or f"{date.today().year}/{date.today().year + 1}"
    today = date.today().isoformat()
    conn.execute("UPDATE academic_years SET is_active=0 WHERE guild_id=?", (guild_id,))
    conn.execute("INSERT OR IGNORE INTO academic_years(guild_id,name,is_active,created_at) VALUES(?,?,1,?)", (guild_id, year_name, today))
    conn.execute("UPDATE academic_years SET is_active=1 WHERE guild_id=? AND name=?", (guild_id, year_name))
    year_id = int(conn.execute("SELECT id FROM academic_years WHERE guild_id=? AND name=?", (guild_id, year_name)).fetchone()[0])
    for level in config.get("levels", []):
        for stream in level.get("streams", []):
            stream_name = str(stream["name"])
            code = stream.get("abbreviation") or get_stream_abbreviation(level["name"], stream_name)
            role_name = f"Filière - {code}"
            conn.execute("INSERT OR IGNORE INTO streams(guild_id,academic_year_id,level_name,stream_name,role_name) VALUES(?,?,?,?,?)", (guild_id, year_id, level["name"], stream_name, role_name))
            conn.execute("UPDATE streams SET role_name=? WHERE guild_id=? AND academic_year_id=? AND level_name=? AND stream_name=?", (role_name, guild_id, year_id, level["name"], stream_name))


def sync_configuration_to_database(guild_id: int, config: dict[str, Any]) -> None:
    initialize_database()
    with _connect() as conn:
        _sync_configuration_to_database_conn(conn, guild_id, config)
        conn.commit()


def get_stream(guild_id: int, academic_year_id: int, level_name: str, stream_name: str) -> sqlite3.Row | None:
    initialize_database()
    with _connect() as conn:
        return conn.execute("SELECT * FROM streams WHERE guild_id=? AND academic_year_id=? AND level_name=? AND stream_name=? LIMIT 1", (guild_id, academic_year_id, level_name, stream_name)).fetchone()


def upsert_student(guild_id: int, discord_id: int | None, display_name: str) -> int:
    initialize_database()
    with _connect() as conn:
        row = conn.execute("SELECT id FROM students WHERE guild_id=? AND discord_id=?", (guild_id, discord_id)).fetchone() if discord_id is not None else None
        if row:
            conn.execute("UPDATE students SET display_name=? WHERE id=?", (display_name, row["id"]))
            return int(row["id"])
        cur = conn.execute("INSERT INTO students(guild_id,discord_id,display_name,created_at) VALUES(?,?,?,?)", (guild_id, discord_id, display_name, date.today().isoformat()))
        return int(cur.lastrowid)


def enroll_student(guild_id: int, student_id: int, academic_year_id: int, level_name: str, stream_name: str) -> None:
    initialize_database()
    with _connect() as conn:
        stream = conn.execute("SELECT * FROM streams WHERE guild_id=? AND academic_year_id=? AND level_name=? AND stream_name=? LIMIT 1", (guild_id, academic_year_id, level_name, stream_name)).fetchone()
        if stream is None:
            raise ValueError("Selected stream is not configured for the active academic year.")
        current = conn.execute("SELECT stream_id FROM enrollments WHERE student_id=? AND status='active' ORDER BY id DESC LIMIT 1", (student_id,)).fetchone()
        if current and int(current["stream_id"]) == int(stream["id"]):
            conn.execute("UPDATE students SET status='active' WHERE id=? AND guild_id=?", (student_id, guild_id))
            return
        today = date.today().isoformat()
        conn.execute("UPDATE enrollments SET end_date=?, status='transferred' WHERE student_id=? AND status='active'", (today, student_id))
        conn.execute("INSERT INTO enrollments(student_id,stream_id,start_date,status) VALUES(?,?,?,'active')", (student_id, int(stream["id"]), today))
        conn.execute("UPDATE students SET status='active' WHERE id=? AND guild_id=?", (student_id, guild_id))


def enroll_student_record(guild_id: int, discord_id: int, display_name: str, academic_year_id: int, level_name: str, stream_name: str) -> int:
    initialize_database()
    with _connect() as conn:
        stream = conn.execute("SELECT * FROM streams WHERE guild_id=? AND academic_year_id=? AND level_name=? AND stream_name=? LIMIT 1", (guild_id, academic_year_id, level_name, stream_name)).fetchone()
        if stream is None:
            raise ValueError("Selected stream is not configured for the active academic year.")
        row = conn.execute("SELECT id FROM students WHERE guild_id=? AND discord_id=?", (guild_id, discord_id)).fetchone()
        today = date.today().isoformat()
        if row:
            student_id = int(row["id"])
            conn.execute("UPDATE students SET display_name=?, status='active' WHERE id=?", (display_name, student_id))
        else:
            cur = conn.execute("INSERT INTO students(guild_id,discord_id,display_name,created_at,status) VALUES(?,?,?,?, 'active')", (guild_id, discord_id, display_name, today))
            student_id = int(cur.lastrowid)
        current = conn.execute("SELECT stream_id FROM enrollments WHERE student_id=? AND status='active' ORDER BY id DESC LIMIT 1", (student_id,)).fetchone()
        if current and int(current["stream_id"]) == int(stream["id"]):
            return student_id
        conn.execute("UPDATE enrollments SET end_date=?, status='transferred' WHERE student_id=? AND status='active'", (today, student_id))
        conn.execute("INSERT INTO enrollments(student_id,stream_id,start_date,status) VALUES(?,?,?,'active')", (student_id, int(stream["id"]), today))
        return student_id


def mark_student_left(guild_id: int, student_id: int) -> None:
    today = date.today().isoformat()
    with _connect() as conn:
        conn.execute("UPDATE enrollments SET end_date=?, status='left_school' WHERE student_id=? AND status='active'", (today, student_id))
        conn.execute("UPDATE students SET status='left_school' WHERE id=? AND guild_id=?", (student_id, guild_id))


def get_student(guild_id: int, discord_id: int) -> sqlite3.Row | None:
    initialize_database()
    with _connect() as conn:
        return conn.execute("SELECT * FROM students WHERE guild_id=? AND discord_id=?", (guild_id, discord_id)).fetchone()


def get_student_history(guild_id: int, discord_id: int) -> list[sqlite3.Row]:
    initialize_database()
    with _connect() as conn:
        return conn.execute("""
            SELECT ay.name AS academic_year, s.level_name, s.stream_name, e.start_date, e.end_date, e.status
            FROM students st JOIN enrollments e ON e.student_id=st.id JOIN streams s ON s.id=e.stream_id JOIN academic_years ay ON ay.id=s.academic_year_id
            WHERE st.guild_id=? AND st.discord_id=? ORDER BY e.start_date DESC, e.id DESC
        """, (guild_id, discord_id)).fetchall()

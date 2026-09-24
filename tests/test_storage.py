import json
import sqlite3

import pytest

from services import storage


def _configure_storage(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "CONFIG_FILE", data_dir / "guild_config.json")
    monkeypatch.setattr(storage, "DATABASE_FILE", data_dir / "school.db")
    return data_dir


def test_json_save_is_atomic_and_round_trips(tmp_path, monkeypatch):
    data_dir = _configure_storage(tmp_path, monkeypatch)
    payload = {"123": {"academic_year": "2026/2027", "levels": []}}
    storage.save_all(payload)
    assert storage.load_all() == payload
    assert not list(data_dir.glob(".guild_config.json.*"))


def test_sqlite_uses_wal_and_busy_timeout(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    conn = storage._connect()
    try:
        assert str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000
    finally:
        conn.close()


def test_duplicate_active_enrollments_are_deduplicated_before_unique_index(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    storage.initialize_database()
    with storage._connect() as conn:
        conn.execute("DROP INDEX uq_one_active_enrollment_per_student")
        year = conn.execute("INSERT INTO academic_years(guild_id,name,is_active,created_at) VALUES(1,'2026/2027',1,'2026-09-01')").lastrowid
        stream = conn.execute("INSERT INTO streams(guild_id,academic_year_id,level_name,stream_name,role_name) VALUES(1,?,?,?,?)", (year, 'TC', 'TCS', 'Filière - TCS')).lastrowid
        student = conn.execute("INSERT INTO students(guild_id,discord_id,display_name,created_at) VALUES(1,99,'Student','2026-09-01')").lastrowid
        conn.execute("INSERT INTO enrollments(student_id,stream_id,start_date,status) VALUES(?,?,?,'active')", (student, stream, '2026-09-01'))
        conn.execute("INSERT INTO enrollments(student_id,stream_id,start_date,status) VALUES(?,?,?,'active')", (student, stream, '2026-09-02'))
    storage.initialize_database()
    with storage._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM enrollments WHERE student_id=? AND status='active'", (student,)).fetchone()[0] == 1
        assert conn.execute("SELECT status FROM enrollments WHERE student_id=? AND id=(SELECT MIN(id) FROM enrollments WHERE student_id=? )", (student, student)).fetchone()[0] == "transferred"
        assert conn.execute("PRAGMA index_list('enrollments')").fetchall()


def test_enrolling_same_stream_twice_is_idempotent(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    storage.initialize_database()
    year_id = storage.ensure_academic_year(1, "2026/2027", active=True)
    storage.sync_configuration_to_database(1, {"academic_year": "2026/2027", "levels": [{"name": "TC", "streams": [{"name": "TCS", "abbreviation": "TCS"}]}]})
    storage.enroll_student_record(1, 99, "Student", year_id, "TC", "TCS")
    storage.enroll_student_record(1, 99, "Student", year_id, "TC", "TCS")
    with storage._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM enrollments WHERE student_id=1 AND status='active'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM enrollments WHERE student_id=1").fetchone()[0] == 1


def test_only_one_academic_year_can_be_active_per_guild(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    first = storage.create_academic_year(1, "2025/2026", activate=True)
    second = storage.create_academic_year(1, "2026/2027", activate=True)
    assert first != second
    active = storage.get_active_academic_year(1)
    assert active is not None
    assert active["name"] == "2026/2027"
    with storage._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM academic_years WHERE guild_id=1 AND is_active=1").fetchone()[0] == 1


def test_active_academic_year_migration_deduplicates_before_unique_index(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    storage.initialize_database()
    with storage._connect() as conn:
        conn.execute("DROP INDEX uq_one_active_academic_year_per_guild")
        old_id = conn.execute("INSERT INTO academic_years(guild_id,name,is_active,created_at) VALUES(1,'2025/2026',1,'2025-09-01')").lastrowid
        new_id = conn.execute("INSERT INTO academic_years(guild_id,name,is_active,created_at) VALUES(1,'2026/2027',1,'2025-09-02')").lastrowid
    storage.initialize_database()
    with storage._connect() as conn:
        rows = conn.execute("SELECT id,name,is_active FROM academic_years WHERE guild_id=1 ORDER BY id").fetchall()
        assert rows[-1]["id"] == new_id
        assert rows[-1]["is_active"] == 1
        assert rows[-2]["id"] == old_id
        assert rows[-2]["is_active"] == 0
        assert conn.execute("SELECT COUNT(*) FROM academic_years WHERE guild_id=1 AND is_active=1").fetchone()[0] == 1



def test_save_guild_config_does_not_switch_active_academic_year(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    first_config = {
        "academic_year": "2025/2026",
        "levels": [{"name": "TC", "streams": [{"name": "TCS", "abbreviation": "TCS"}]}],
    }
    second_config = {
        "academic_year": "2026/2027",
        "levels": [{"name": "TC", "streams": [{"name": "TCS", "abbreviation": "TCS"}]}],
    }

    storage.save_guild_config(1, first_config)
    storage.create_and_activate_academic_year(1, "2026/2027", second_config)
    storage.save_guild_config(1, second_config)

    assert storage.get_guild_config(1)["academic_year"] == "2026/2027"
    active = storage.get_active_academic_year(1)
    assert active is not None
    assert active["name"] == "2026/2027"
    with storage._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM streams WHERE guild_id=1 AND stream_name='TCS'"
        ).fetchone()[0] == 2


def test_save_guild_config_cannot_reactivate_an_older_year(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    storage.save_guild_config(1, {"academic_year": "2027/2028", "levels": []})
    storage.create_and_activate_academic_year(1, "2028/2029", {"academic_year": "2028/2029", "levels": []})

    storage.save_guild_config(1, {"academic_year": "2027/2028", "levels": []})

    active = storage.get_active_academic_year(1)
    assert active is not None
    assert active["name"] == "2028/2029"
    assert storage.get_guild_config(1)["academic_year"] == "2028/2029"


def test_json_cache_failure_after_database_commit_does_not_lose_configuration(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    old_config = {"academic_year": "2025/2026", "levels": []}
    new_config = {"academic_year": "2026/2027", "levels": []}
    storage.save_guild_config(1, old_config)
    storage.create_and_activate_academic_year(1, "2026/2027", new_config)

    monkeypatch.setattr(storage, "save_all", lambda _data: (_ for _ in ()).throw(OSError("simulated JSON cache failure")))
    storage.save_guild_config(1, new_config)

    assert storage.get_guild_config(1) == new_config
    with storage._connect() as conn:
        row = conn.execute("SELECT config_json, is_deleted FROM guild_configs WHERE guild_id=1").fetchone()
        assert row["is_deleted"] == 0
        assert json.loads(row["config_json"]) == new_config


def test_stale_json_cache_is_ignored_after_restart_when_database_is_newer(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    old_config = {"academic_year": "2025/2026", "levels": []}
    new_config = {"academic_year": "2026/2027", "levels": []}
    storage.save_guild_config(1, old_config)

    with storage._connect() as conn:
        storage._upsert_config_conn(conn, 1, new_config)
        conn.commit()

    storage.CONFIG_FILE.write_text('{"1": {"academic_year": "2025/2026", "levels": []}}', encoding="utf-8")
    assert storage.get_guild_config(1) == new_config




def test_archive_guild_database_survives_reset_and_records_active_year(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    storage.create_and_activate_academic_year(
        1,
        "2026/2027",
        {
            "academic_year": "2026/2027",
            "levels": [
                {
                    "name": "TC",
                    "streams": [{"name": "TCS", "abbreviation": "TCS"}],
                }
            ],
        },
    )
    active_year = storage.get_active_academic_year(1)
    assert active_year is not None
    year_id = int(active_year["id"])
    with storage._connect() as conn:
        student_id = conn.execute(
            "INSERT INTO students(guild_id,discord_id,display_name,created_at) VALUES(1,99,'Student','2026-09-01')"
        ).lastrowid
        stream_id = conn.execute(
            "SELECT id FROM streams WHERE guild_id=1 AND academic_year_id=? AND stream_name='TCS'",
            (year_id,),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO enrollments(student_id,stream_id,start_date,status) VALUES(?,?,?,'active')",
            (student_id, stream_id, "2026-09-01"),
        )
        conn.commit()

    archive = storage.archive_guild_database(1, "2026/2027")
    assert archive == tmp_path / "data" / "archives" / "2026-2027.db"
    assert archive.exists()
    assert not archive.with_name(archive.name + "-wal").exists()
    assert not archive.with_name(archive.name + "-shm").exists()

    with sqlite3.connect(archive) as conn:
        conn.row_factory = sqlite3.Row
        metadata = conn.execute("SELECT guild_id,academic_year FROM archive_metadata WHERE id=1").fetchone()
        assert dict(metadata) == {"guild_id": 1, "academic_year": "2026/2027"}
        assert conn.execute("SELECT COUNT(*) FROM students WHERE guild_id=1").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM enrollments").fetchone()[0] == 1
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    storage.reset_guild_data(1)
    assert archive.exists()

def test_reset_tombstone_prevents_old_json_cache_from_resurrecting_config(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    config = {"academic_year": "2026/2027", "levels": []}
    storage.save_guild_config(1, config)
    storage.reset_guild_data(1)

    assert storage.get_guild_config(1) is None
    with storage._connect() as conn:
        row = conn.execute("SELECT is_deleted FROM guild_configs WHERE guild_id=1").fetchone()
        assert row["is_deleted"] == 1

    storage.CONFIG_FILE.write_text('{"1": {"academic_year": "2026/2027", "levels": []}}', encoding="utf-8")
    assert storage.get_guild_config(1) is None


def test_legacy_enrollments_migrate_only_safe_same_guild_rows(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    storage.initialize_database()
    with storage._connect() as conn:
        conn.execute("DROP TABLE enrollments")
        conn.execute("CREATE TABLE enrollments (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL, class_id INTEGER NOT NULL, start_date TEXT, end_date TEXT, status TEXT)")
        conn.execute("CREATE TABLE classes (id INTEGER PRIMARY KEY, stream_id INTEGER NOT NULL)")
        year1 = conn.execute("INSERT INTO academic_years(guild_id,name,is_active,created_at) VALUES(10,'2026/2027',1,'2026-09-01')").lastrowid
        year2 = conn.execute("INSERT INTO academic_years(guild_id,name,is_active,created_at) VALUES(20,'2026/2027',1,'2026-09-01')").lastrowid
        stream1 = conn.execute("INSERT INTO streams(guild_id,academic_year_id,level_name,stream_name,role_name) VALUES(?,?,?,?,?)", (10, year1, 'TC', 'TCS', 'Filière - TCS')).lastrowid
        stream2 = conn.execute("INSERT INTO streams(guild_id,academic_year_id,level_name,stream_name,role_name) VALUES(?,?,?,?,?)", (20, year2, 'TC', 'TCS', 'Filière - TCS')).lastrowid
        student1 = conn.execute("INSERT INTO students(guild_id,discord_id,display_name,created_at) VALUES(10,101,'A','2026-09-01')").lastrowid
        student2 = conn.execute("INSERT INTO students(guild_id,discord_id,display_name,created_at) VALUES(20,202,'B','2026-09-01')").lastrowid
        conn.execute("INSERT INTO classes(id,stream_id) VALUES(1,?)", (stream1,))
        conn.execute("INSERT INTO classes(id,stream_id) VALUES(2,?)", (stream2,))
        conn.execute("INSERT INTO enrollments(student_id,class_id,start_date,status) VALUES(?,?,?,?)", (student1, 1, '2026-09-01', 'active'))
        conn.execute("INSERT INTO enrollments(student_id,class_id,start_date,status) VALUES(?,?,?,?)", (student2, 2, '2026-09-01', 'active'))
        conn.execute("INSERT INTO enrollments(student_id,class_id,start_date,status) VALUES(?,?,?,?)", (student1, 1, '2026-09-02', 'legacy_unknown_status'))
    storage.initialize_database()
    with storage._connect() as conn:
        rows = conn.execute("SELECT student_id,stream_id,start_date,status FROM enrollments ORDER BY id").fetchall()
        assert len(rows) == 3
        assert {(row["student_id"], row["stream_id"]) for row in rows} == {(student1, stream1), (student2, stream2)}
        assert sum(row["status"] == "active" for row in rows if row["student_id"] == student1) == 1
        assert sum(row["status"] == "transferred" for row in rows if row["student_id"] == student1) == 1
        assert all(row["status"] in {"active", "transferred", "left_school"} for row in rows)
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='enrollments_legacy_v1'").fetchone()[0] == 1

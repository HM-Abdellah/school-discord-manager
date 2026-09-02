from pathlib import Path

import pytest

from services import storage


def test_json_save_is_atomic_and_round_trips(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    config_file = data_dir / "guild_config.json"
    db_file = data_dir / "school.db"
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "CONFIG_FILE", config_file)
    monkeypatch.setattr(storage, "DATABASE_FILE", db_file)

    payload = {"123": {"academic_year": "2026/2027", "levels": []}}
    storage.save_all(payload)

    assert storage.load_all() == payload
    assert config_file.exists()
    assert not list(data_dir.glob(".guild_config.json.*"))


def test_sqlite_uses_wal_and_busy_timeout(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "DATABASE_FILE", data_dir / "school.db")

    conn = storage._connect()
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()

    assert str(journal_mode).lower() == "wal"
    assert busy_timeout >= 5000

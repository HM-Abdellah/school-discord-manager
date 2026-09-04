from types import SimpleNamespace

import pytest

from services import storage
from services.server_builder import CATEGORY_GENERAL, CATEGORY_PROFESSORS, CATEGORY_VOICE, ServerBuilder


def test_reset_guild_data_rolls_back_sqlite_when_json_replace_fails(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "CONFIG_FILE", data_dir / "guild_config.json")
    monkeypatch.setattr(storage, "DATABASE_FILE", data_dir / "school.db")

    config = {"academic_year": "2026/2027", "levels": []}
    storage.save_all({"123": config})
    storage.initialize_database()
    with storage._connect() as conn:
        conn.execute("INSERT INTO students(guild_id,discord_id,display_name,created_at) VALUES(123,99,'Student','2026-09-01')")

    original_replace = storage.os.replace

    def fail_transaction_replace(source, destination):
        if ".transaction." in str(source):
            raise OSError("simulated JSON replace failure")
        original_replace(source, destination)

    monkeypatch.setattr(storage.os, "replace", fail_transaction_replace)
    with pytest.raises(OSError, match="simulated JSON replace failure"):
        storage.reset_guild_data(123)

    assert storage.get_guild_config(123) == config
    with storage._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM students WHERE guild_id=123").fetchone()[0] == 1


def test_builder_capacity_counts_only_resources_that_will_actually_be_created():
    category_general = SimpleNamespace(name=CATEGORY_GENERAL, text_channels=[], voice_channels=[])
    category_professors = SimpleNamespace(name=CATEGORY_PROFESSORS, text_channels=[], voice_channels=[])
    category_voice = SimpleNamespace(name=CATEGORY_VOICE, text_channels=[], voice_channels=[])
    guild = SimpleNamespace(channels=[], categories=[category_general, category_professors, category_voice])
    builder = ServerBuilder(guild)
    builder._channel_snapshot = [category_general, category_professors, category_voice]
    selected = {
        "levels": [
            {
                "name": "Tronc Commun",
                "streams": [
                    {
                        "name": "Tronc Commun Scientifique",
                        "abbreviation": "TCS",
                        "subjects": ["Mathématiques"],
                    }
                ],
            }
        ]
    }
    builder._validate_capacity(selected)


def test_builder_capacity_rejects_exact_projected_501_channels():
    existing = [SimpleNamespace(name=f"channel-{index}") for index in range(494)]
    guild = SimpleNamespace(channels=existing, categories=[])
    builder = ServerBuilder(guild)
    builder._channel_snapshot = existing
    selected = {"levels": []}
    with pytest.raises(ValueError, match="501"):
        builder._validate_capacity(selected)

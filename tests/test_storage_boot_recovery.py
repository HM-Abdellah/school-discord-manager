from services import storage


def test_json_cache_is_rebuilt_from_database_after_startup(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "CONFIG_FILE", data_dir / "guild_config.json")
    monkeypatch.setattr(storage, "DATABASE_FILE", data_dir / "school.db")

    config = {"academic_year": "2026/2027", "levels": []}
    storage.save_guild_config(1, config)

    storage.CONFIG_FILE.write_text("{not valid json", encoding="utf-8")
    assert storage.get_guild_config(1) == config

    storage._refresh_json_cache()
    assert storage.CONFIG_FILE.read_text(encoding="utf-8").strip()
    assert storage.get_guild_config(1) == config

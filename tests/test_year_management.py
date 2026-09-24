import pytest

from services import year_management



def test_rollback_switches_logical_year_without_stream_compatibility_check(monkeypatch):
    config = {
        "academic_year": "2026/2027",
        "levels": [
            {
                "name": "Tronc Commun",
                "streams": [
                    {"name": "Tronc Commun Scientifique", "abbreviation": "TCS"}
                ],
            }
        ],
    }
    row = {"id": 7, "name": "2025/2026"}
    calls = {}

    def fake_activate(guild_id, year, *, config=None):
        calls["guild_id"] = guild_id
        calls["year"] = year
        calls["config"] = config

    monkeypatch.setattr(year_management, "get_guild_config", lambda _guild: config.copy())
    monkeypatch.setattr(year_management, "list_academic_years", lambda _guild: [row])
    monkeypatch.setattr(year_management, "activate_academic_year", fake_activate)

    previous, changed = year_management.rollback_guild_config_year(1, "2025/2026")

    assert previous == "2026/2027"
    assert changed is True
    assert calls["guild_id"] == 1
    assert calls["year"] == "2025/2026"
    assert calls["config"]["academic_year"] == "2025/2026"



def test_rollback_rejects_unknown_year(monkeypatch):
    monkeypatch.setattr(year_management, "get_guild_config", lambda _guild: {"academic_year": "2026/2027"})
    monkeypatch.setattr(year_management, "list_academic_years", lambda _guild: [])

    try:
        year_management.rollback_guild_config_year(1, "2024/2025")
    except ValueError as exc:
        assert "n'est pas enregistrée" in str(exc)
    else:
        raise AssertionError("Unknown academic year must be rejected")



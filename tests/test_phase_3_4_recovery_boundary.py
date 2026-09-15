import inspect
from types import SimpleNamespace

import pytest

import cogs.removestream_fix as removestream_fix


def _journal():
    return {
        "version": 1,
        "level": "Tronc Commun",
        "stream": "Tronc Commun Scientifique",
        "code": "TCS",
        "resources": [],
        "completed": [],
        "category": {"id": 500, "name": "📘・TC・🔬 TCS"},
    }


def test_production_path_journals_before_destructive_execution():
    source = inspect.getsource(removestream_fix.SafeRemoveStream.remove_stream.callback)
    install = source.index("install_removal_journal(")
    execute = source.index("await execute_removal_journal(")
    category = source.index("await _finalize_journal_category(guild, journal)")
    finalize = source.index("candidate = _finalize_stream_config(config, journal)")

    assert install < execute < category < finalize


def test_pending_recovery_is_checked_before_new_destructive_resolution():
    source = inspect.getsource(removestream_fix.SafeRemoveStream.remove_stream.callback)
    pending = source.index("get_pending_removal(config)")
    validation = source.index("await validate_managed_registry(guild, config)")
    resolution = source.index("await _resolve_registry(")

    assert pending < validation < resolution


def test_finalize_stream_config_only_removes_exact_journal_owned_mappings():
    config = {
        "levels": [
            {
                "name": "Tronc Commun",
                "streams": [
                    {
                        "name": "Tronc Commun Scientifique",
                        "abbreviation": "TCS",
                    }
                ],
            }
        ],
        "managed": {
            "roles": {"Filière - TCS": 900},
            "categories": {"📘・TC・🔬 TCS": 500},
            "channels": {
                "📌-TCS・informations": 901,
                "📝-TCS・examens": 902,
            },
        },
    }
    journal = _journal()
    journal["resources"] = [
        {"kind": "channel", "id": 901, "name": "📌-TCS・informations"},
        {"kind": "role", "id": 777, "name": "Filière - TCS"},
        {"kind": "channel", "id": 902, "name": "📝-TCS・examens"},
    ]

    candidate = removestream_fix._finalize_stream_config(config, journal)

    assert candidate["levels"][0]["name"] == "Tronc Commun"
    assert candidate["levels"][0]["streams"] == []
    assert "📌-TCS・informations" not in candidate["managed"]["channels"]
    assert "📝-TCS・examens" not in candidate["managed"]["channels"]
    assert candidate["managed"]["roles"]["Filière - TCS"] == 900
    assert candidate["managed"]["categories"] == {}


@pytest.mark.asyncio
async def test_category_is_not_deleted_when_custom_channel_remains(monkeypatch):
    class FakeCategoryChannel:
        pass

    monkeypatch.setattr(removestream_fix.discord, "CategoryChannel", FakeCategoryChannel)

    deleted = []
    category = FakeCategoryChannel()
    category.id = 500
    category.name = "📘・TC・🔬 TCS"

    custom = SimpleNamespace(id=600, name="teacher-custom", category_id=500)

    async def delete(*, reason):
        deleted.append(reason)

    category.delete = delete

    async def fetch_channels():
        return [category, custom]

    guild = SimpleNamespace(fetch_channels=fetch_channels)

    await removestream_fix._finalize_journal_category(guild, _journal())

    assert deleted == []


@pytest.mark.asyncio
async def test_category_is_deleted_only_after_fresh_empty_check(monkeypatch):
    class FakeCategoryChannel:
        pass

    monkeypatch.setattr(removestream_fix.discord, "CategoryChannel", FakeCategoryChannel)

    deleted = []
    category = FakeCategoryChannel()
    category.id = 500
    category.name = "📘・TC・🔬 TCS"

    async def delete(*, reason):
        deleted.append(reason)

    category.delete = delete

    async def fetch_channels():
        return [category]

    guild = SimpleNamespace(fetch_channels=fetch_channels)

    await removestream_fix._finalize_journal_category(guild, _journal())

    assert deleted == ["School Manager scoped empty stream category removal"]

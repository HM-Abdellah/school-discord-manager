import inspect
from types import SimpleNamespace

import pytest

import cogs.removestream_fix as removestream_fix


def test_remove_stream_source_has_no_name_discovery_fallback():
    source = inspect.getsource(removestream_fix._resolve_registry)
    assert "_unique_named" not in source
    assert "matches =" not in source
    assert "_recorded_id(config, \"categories\"" in source
    assert "_recorded_id(config, \"channels\"" in source
    assert "_recorded_id(config, \"roles\"" in source


@pytest.mark.asyncio
async def test_resolver_refuses_missing_managed_ids(monkeypatch):
    class FakeTextChannel:
        pass

    class FakeVoiceChannel:
        pass

    class FakeCategoryChannel:
        pass

    class FakeRole:
        managed = False

    monkeypatch.setattr(removestream_fix.discord, "TextChannel", FakeTextChannel)
    monkeypatch.setattr(removestream_fix.discord, "VoiceChannel", FakeVoiceChannel)
    monkeypatch.setattr(removestream_fix.discord, "CategoryChannel", FakeCategoryChannel)
    monkeypatch.setattr(removestream_fix.discord, "Role", FakeRole)

    async def forbidden_fetch_channels():
        raise AssertionError("destructive resolver must not perform name-discovery fetches")

    guild = SimpleNamespace(
        channels=[],
        roles=[],
        fetch_channels=forbidden_fetch_channels,
        get_channel=lambda _id: None,
        get_role=lambda _id: None,
    )
    config = {
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
        ],
        "managed": {"roles": {}, "channels": {}, "categories": {}},
    }
    monkeypatch.setattr(removestream_fix, "get_stream_abbreviation", lambda *_: "TCS")
    monkeypatch.setattr(removestream_fix, "get_stream_subjects", lambda *_: ["Mathématiques"])
    monkeypatch.setattr(removestream_fix, "_subject_channel_name", lambda code, subject: f"📚-{code}・{subject}")
    monkeypatch.setattr(removestream_fix, "_stream_category_name", lambda *_: "📘・TC・🔬 TCS")

    result = await removestream_fix._resolve_registry(
        guild,
        config,
        "Tronc Commun",
        "Tronc Commun Scientifique",
        config["levels"][0]["streams"][0],
    )

    assert result[2] is None
    assert result[3] is None
    assert result[4] is None
    assert result[5] is not None
    assert "ID géré manquant" in result[5]


def test_remove_stream_validates_managed_registry_before_resolution():
    source = inspect.getsource(removestream_fix.SafeRemoveStream.remove_stream)
    assert "await validate_managed_registry(guild, config)" in source

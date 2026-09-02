from types import SimpleNamespace

import pytest

from services.server_builder import ServerBuilder, _stream_category_name


def test_stream_channel_count_has_no_artificial_title_channel():
    stream = {"subjects": ["MATH", "PC", "SVT"]}
    assert ServerBuilder._stream_channel_count({}, stream) == 6


def test_stream_category_is_a_real_discord_category_name():
    name = _stream_category_name("Tronc Commun", "Tronc Commun Scientifique", "TCS")
    assert name == "📘・TC・🔬 TCS"


def test_planned_channel_names_include_only_real_resources():
    stream = {"abbreviation": "1BACSE", "subjects": ["MATH", "PC"]}
    names = ServerBuilder._planned_channel_names_for_stream(stream)
    assert "📌-1BACSE・informations" in names
    assert "🗓️-1BACSE・emploi-du-temps" in names
    assert "📝-1BACSE・examens" in names
    assert any(name.startswith("📚-1BACSE・") for name in names)
    assert not any(name.startswith("🔹・") for name in names)


def test_validate_capacity_rejects_stream_above_category_limit():
    guild = SimpleNamespace(channels=[], categories=[])
    builder = ServerBuilder(guild)
    selected = {"levels": [{"name": "1ère Année Bac", "streams": [{"name": "Huge", "abbreviation": "HUGE", "subjects": [str(i) for i in range(48)]}]}]}
    with pytest.raises(ValueError, match="dépasse la limite de 50"):
        builder._validate_capacity(selected)

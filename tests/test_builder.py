from types import SimpleNamespace

import pytest

from services.server_builder import ServerBuilder


def test_stream_channel_count_matches_three_fixed_channels_plus_subjects():
    stream = {"subjects": ["MATH", "PC", "SVT"]}
    assert ServerBuilder._stream_channel_count({}, stream) == 6


def test_planned_channel_names_include_all_stream_resources():
    stream = {"abbreviation": "1BACSE", "subjects": ["MATH", "PC"]}
    names = ServerBuilder._planned_channel_names_for_stream(stream)
    assert "📌-1BACSE・informations" in names
    assert "🗓️-1BACSE・emploi-du-temps" in names
    assert "📝-1BACSE・examens" in names
    assert any(name.startswith("📚-1BACSE・") for name in names)


def test_validate_capacity_rejects_single_stream_above_category_limit():
    guild = SimpleNamespace(channels=[], categories=[])
    builder = ServerBuilder(guild)
    builder._level_categories = lambda _level: []
    selected = {
        "levels": [
            {"name": "1ère Année Bac", "streams": [{"name": "Huge", "abbreviation": "HUGE", "subjects": [str(i) for i in range(48)]}]}
        ]
    }
    with pytest.raises(ValueError, match="au-delà de la limite de 50"):
        builder._validate_capacity(selected)

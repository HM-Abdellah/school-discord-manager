from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from services.server_builder import (
    ServerBuilder,
    _stream_category_name,
)


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
    guild = MagicMock()
    guild.channels = []
    guild.categories = []
    builder = ServerBuilder(guild)
    selected = {
        "levels": [
            {
                "name": "1ère Année Bac",
                "streams": [
                    {
                        "name": "Huge",
                        "abbreviation": "HUGE",
                        "subjects": [str(i) for i in range(48)],
                    }
                ],
            }
        ]
    }
    builder._channel_snapshot = []
    with pytest.raises(ValueError, match="dépasse la limite de 50"):
        builder._validate_capacity(selected)


@pytest.mark.asyncio
async def test_existing_main_roles_are_reused_without_create_calls():
    role_names = ("Administration", "Prof", "Prof (F)", "Élève")
    roles = [MagicMock(name=name, id=index + 1) for index, name in enumerate(role_names)]
    guild = SimpleNamespace(roles=roles)
    guild.create_role = AsyncMock()

    builder = ServerBuilder(guild)
    result = await builder._ensure_main_roles()

    assert set(result) == set(role_names)
    assert all(result[name] is roles[index] for index, name in enumerate(role_names))
    guild.create_role.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_category_is_reused_without_create_call():
    category = MagicMock(spec=discord.CategoryChannel)
    category.name = "Test Category"
    category.id = 123
    guild = SimpleNamespace(categories=[category])
    guild.create_category = AsyncMock()

    builder = ServerBuilder(guild)
    builder._channel_snapshot = [category]
    result = await builder._get_or_create_category("Test Category")

    assert result is category
    guild.create_category.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_text_channel_is_reused_without_edit_or_create():
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = "test-channel"
    channel.id = 456
    channel.parent_id = 123
    category = MagicMock(spec=discord.CategoryChannel)
    category.text_channels = [channel]
    category.id = 123
    category.create_text_channel = AsyncMock()
    channel.edit = AsyncMock()

    builder = ServerBuilder(SimpleNamespace())
    builder._channel_snapshot = [channel]

    result = await builder._get_or_create_text(
        category,
        "test-channel",
        topic="topic",
        overwrites={},
    )

    assert result is channel
    channel.edit.assert_not_awaited()
    category.create_text_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_voice_channel_is_reused_without_edit_or_create():
    channel = MagicMock(spec=discord.VoiceChannel)
    channel.name = "test-voice"
    channel.id = 789
    channel.parent_id = 123
    category = MagicMock(spec=discord.CategoryChannel)
    category.voice_channels = [channel]
    category.id = 123
    category.create_voice_channel = AsyncMock()
    channel.edit = AsyncMock()

    builder = ServerBuilder(SimpleNamespace())
    builder._channel_snapshot = [channel]

    result = await builder._get_or_create_voice(category, "test-voice", {})

    assert result is channel
    channel.edit.assert_not_awaited()
    category.create_voice_channel.assert_not_awaited()

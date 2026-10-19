from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from services.discord_registry import resolve_managed_text_channel, resolve_registered_text_channel


@pytest.mark.asyncio
async def test_strict_resolution_requires_persisted_channel_and_category_ids():
    guild = SimpleNamespace(fetch_channel=AsyncMock())
    config = {"managed": {"categories": {"managed": 10}, "channels": {}}}

    result = await resolve_registered_text_channel(
        guild,
        config,
        channel_name="📚-TCS・math",
        category_name="managed",
    )

    assert result is None
    guild.fetch_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_strict_resolution_rejects_channel_name_drift():
    channel = Mock(spec=discord.TextChannel)
    channel.name = "unmanaged-lookalike"
    channel.category_id = 10

    guild = SimpleNamespace(fetch_channel=AsyncMock(return_value=channel))
    config = {
        "managed": {
            "categories": {"managed": 10},
            "channels": {"📚-TCS・math": 20},
        }
    }

    result = await resolve_registered_text_channel(
        guild,
        config,
        channel_name="📚-TCS・math",
        category_name="managed",
    )

    assert result is None


@pytest.mark.asyncio
async def test_strict_resolution_rejects_unmanaged_parent_category():
    channel = Mock(spec=discord.TextChannel)
    channel.name = "📚-TCS・math"
    channel.category_id = 99

    guild = SimpleNamespace(fetch_channel=AsyncMock(return_value=channel))
    config = {
        "managed": {
            "categories": {"managed": 10},
            "channels": {"📚-TCS・math": 20},
        }
    }

    result = await resolve_registered_text_channel(
        guild,
        config,
        channel_name="📚-TCS・math",
        category_name="managed",
    )

    assert result is None


@pytest.mark.asyncio
async def test_managed_resolution_repairs_case_only_channel_name_and_stale_id():
    category_name = "2ème Année Bac Sciences Physiques"
    expected_channel_name = "📚-2BACPC・math"
    actual_channel_name = "📚-2bacpc・math"

    category = Mock(spec=discord.CategoryChannel)
    category.id = 10
    category.name = category_name

    channel = Mock(spec=discord.TextChannel)
    channel.id = 20
    channel.name = actual_channel_name
    channel.category_id = 10

    guild = SimpleNamespace(
        get_channel=lambda channel_id: category if channel_id == 10 else None,
        fetch_channels=AsyncMock(return_value=[category, channel]),
    )
    config = {
        "managed": {
            "categories": {category_name: 10},
            "channels": {expected_channel_name: 999},
        }
    }

    resolved, registry_repaired = await resolve_managed_text_channel(
        guild,
        config,
        channel_name=expected_channel_name,
        category_name=category_name,
    )

    assert resolved is channel
    assert registry_repaired is True
    assert config["managed"]["channels"] == {actual_channel_name: 20}

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from services.discord_registry import resolve_registered_text_channel


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

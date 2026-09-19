from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.discord_ownership import ManagedResourceConflict, validate_managed_registry


@pytest.mark.asyncio
async def test_matching_registered_role_identity_is_accepted():
    config = {"managed": {"roles": {"Administration": 101}}}
    guild = SimpleNamespace(
        roles=[SimpleNamespace(id=101, name="Administration")],
        channels=[],
        fetch_channels=AsyncMock(return_value=[]),
    )

    await validate_managed_registry(guild, config)


@pytest.mark.asyncio
async def test_same_name_different_role_id_is_rejected():
    config = {"managed": {"roles": {"Administration": 101}}}
    guild = SimpleNamespace(
        roles=[SimpleNamespace(id=202, name="Administration")],
        channels=[],
        fetch_channels=AsyncMock(return_value=[]),
    )

    with pytest.raises(ManagedResourceConflict, match="same name exists"):
        await validate_managed_registry(guild, config)


@pytest.mark.asyncio
async def test_missing_registered_role_is_allowed_when_no_same_name_resource_exists():
    config = {"managed": {"roles": {"Administration": 101}}}
    guild = SimpleNamespace(
        roles=[SimpleNamespace(id=202, name="Other")],
        channels=[],
        fetch_channels=AsyncMock(return_value=[]),
    )

    await validate_managed_registry(guild, config)


@pytest.mark.asyncio
async def test_registered_channel_name_on_different_live_id_is_rejected():
    config = {
        "managed": {
            "categories": {"📘・TC・🔬 TCS": 301},
            "channels": {"📌-TCS・informations": 401},
        }
    }
    channels = [
        SimpleNamespace(id=302, name="📘・TC・🔬 TCS"),
        SimpleNamespace(id=402, name="📌-TCS・informations"),
    ]
    guild = SimpleNamespace(roles=[], channels=channels, fetch_channels=AsyncMock(return_value=channels))

    with pytest.raises(ManagedResourceConflict, match="same name exists"):
        await validate_managed_registry(guild, config)


@pytest.mark.asyncio
async def test_duplicate_live_same_name_is_rejected_for_registered_channel():
    config = {"managed": {"channels": {"examens": 401}}}
    channels = [
        SimpleNamespace(id=401, name="examens"),
        SimpleNamespace(id=402, name="examens"),
    ]
    guild = SimpleNamespace(roles=[], channels=channels, fetch_channels=AsyncMock(return_value=channels))

    with pytest.raises(ManagedResourceConflict, match="same name exists"):
        await validate_managed_registry(guild, config)


@pytest.mark.asyncio
async def test_discord_managed_canonical_role_is_rejected():
    config = {
        "managed": {},
        "levels": [
            {
                "name": "Tronc Commun",
                "streams": [
                    {"name": "Tronc Commun Scientifique", "abbreviation": "TCS"}
                ],
            }
        ],
    }
    guild = SimpleNamespace(
        roles=[SimpleNamespace(id=700, name="Filière - TCS", managed=True)],
        channels=[],
        fetch_channels=AsyncMock(return_value=[]),
    )

    with pytest.raises(ManagedResourceConflict, match="Discord-managed"):
        await validate_managed_registry(guild, config)
    with pytest.raises(ManagedResourceConflict, match="Discord-managed"):
        await validate_unmanaged_canonical_collisions(guild, config)

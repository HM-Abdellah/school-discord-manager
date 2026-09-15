import ast
from types import SimpleNamespace
from pathlib import Path

import pytest

from services.discord_ownership import (
    ManagedResourceConflict,
    validate_unmanaged_canonical_collisions,
)


def _guild(*, roles=None, channels=None):
    return SimpleNamespace(
        roles=roles or [],
        channels=channels or [],
    )


def _stream_config():
    return {
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
        "managed": {
            "roles": {},
            "categories": {},
            "channels": {},
        },
    }


@pytest.mark.asyncio
async def test_unmanaged_canonical_role_is_rejected():
    guild = _guild(roles=[SimpleNamespace(id=101, name="Administration", managed=False)])

    with pytest.raises(ManagedResourceConflict, match="refusing silent adoption"):
        await validate_unmanaged_canonical_collisions(guild, _stream_config())


@pytest.mark.asyncio
async def test_unmanaged_canonical_category_is_rejected():
    guild = _guild(
        channels=[
            SimpleNamespace(id=201, name="📘・TC・🔬 TCS"),
        ]
    )

    with pytest.raises(ManagedResourceConflict, match="refusing silent adoption"):
        await validate_unmanaged_canonical_collisions(guild, _stream_config())


@pytest.mark.asyncio
async def test_unmanaged_canonical_channel_is_rejected():
    guild = _guild(
        channels=[
            SimpleNamespace(id=202, name="📌-TCS・informations"),
        ]
    )

    with pytest.raises(ManagedResourceConflict, match="refusing silent adoption"):
        await validate_unmanaged_canonical_collisions(guild, _stream_config())


@pytest.mark.asyncio
async def test_registered_canonical_resource_is_accepted():
    config = _stream_config()
    config["managed"]["channels"]["📌-TCS・informations"] = 202
    guild = _guild(
        channels=[
            SimpleNamespace(id=202, name="📌-TCS・informations"),
        ]
    )

    await validate_unmanaged_canonical_collisions(guild, config)


def test_server_build_path_uses_transactional_service():
    source = Path("cogs/server_v3.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="cogs/server_v3.py")
    imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "services.build_transaction"
        and any(alias.name == "build_and_persist" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert imported
    assert "return await build_and_persist(guild, config)" in source

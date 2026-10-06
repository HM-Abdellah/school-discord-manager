from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.qr_onboarding import (
    _class_key,
    _class_role_name,
    _get_or_create_class_role,
)
from services.qr_invites import (
    DEFAULT_QR_MAX_AGE,
    DEFAULT_QR_MAX_USES,
    create_role_invite,
)


def test_class_identity_is_stream_plus_section():
    assert _class_key("2BACPC", 2) == "2BACPC-2"
    assert _class_role_name("2BACPC", 2) == "Élèves - 2BACPC-2"


@pytest.mark.asyncio
async def test_class_role_is_created_and_registered():
    role = SimpleNamespace(id=900, name="Élèves - 2BACPC-2", managed=False)
    guild = SimpleNamespace(
        roles=[],
        get_role=lambda role_id: None,
        create_role=AsyncMock(return_value=role),
    )
    config = {"class_roles": {}}

    created, was_created = await _get_or_create_class_role(
        guild,
        config,
        level="2ème Année Bac",
        stream="2ème Année Bac Sciences Physiques",
        code="2BACPC",
        section=2,
    )

    assert created is role
    assert was_created is True
    assert config["class_roles"]["2BACPC-2"]["role_id"] == 900


@pytest.mark.asyncio
async def test_class_role_reuses_registered_role():
    role = SimpleNamespace(id=900, name="Élèves - 2BACPC-2", managed=False)
    guild = SimpleNamespace(
        roles=[role],
        get_role=lambda role_id: role if role_id == 900 else None,
        create_role=AsyncMock(),
    )
    config = {
        "class_roles": {
            "2BACPC-2": {
                "role_id": 900,
                "level_name": "2ème Année Bac",
                "stream_name": "2ème Année Bac Sciences Physiques",
                "stream_code": "2BACPC",
                "section": 2,
            }
        }
    }

    created, was_created = await _get_or_create_class_role(
        guild,
        config,
        level="2ème Année Bac",
        stream="2ème Année Bac Sciences Physiques",
        code="2BACPC",
        section=2,
    )

    assert created is role
    assert was_created is False
    guild.create_role.assert_not_awaited()


@pytest.mark.asyncio
async def test_class_role_rejects_same_name_unmanaged_collision():
    collision = SimpleNamespace(id=901, name="Élèves - 2BACPC-2", managed=False)
    guild = SimpleNamespace(
        roles=[collision],
        get_role=lambda role_id: None,
        create_role=AsyncMock(),
    )

    with pytest.raises(RuntimeError, match="collision"):
        await _get_or_create_class_role(
            guild,
            {"class_roles": {}},
            level="2ème Année Bac",
            stream="2ème Année Bac Sciences Physiques",
            code="2BACPC",
            section=2,
        )

    guild.create_role.assert_not_awaited()


@pytest.mark.asyncio
async def test_role_invite_payload_contains_student_and_class_roles():
    request = AsyncMock(return_value={"code": "abc123"})
    bot = SimpleNamespace(http=SimpleNamespace(request=request))
    channel = SimpleNamespace(id=123)
    roles = [SimpleNamespace(id=10), SimpleNamespace(id=20)]

    url = await create_role_invite(bot, channel, roles)

    assert url == "https://discord.gg/abc123"
    payload = request.await_args.kwargs["json"]
    assert payload["max_age"] == DEFAULT_QR_MAX_AGE
    assert payload["max_uses"] == DEFAULT_QR_MAX_USES
    assert payload["unique"] is True
    assert payload["temporary"] is False
    assert payload["role_ids"] == ["10", "20"]


def test_qr_defaults_are_bounded():
    assert 0 < DEFAULT_QR_MAX_AGE <= 604800
    assert 0 < DEFAULT_QR_MAX_USES <= 100

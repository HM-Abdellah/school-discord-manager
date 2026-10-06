from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import storage

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


@pytest.mark.asyncio
async def test_class_role_access_copies_explicit_stream_student_overwrites():
    from cogs.qr_onboarding import _grant_class_role_access

    class FakeChannel:
        def __init__(self, source):
            self.overwrites = {}
            self._source = source
            self.set_permissions = AsyncMock()

        def overwrites_for(self, role):
            if role is stream_role:
                return self._source
            return SimpleNamespace(is_empty=lambda: True)

    class Role:
        def __init__(self, name):
            self.name = name

        def __hash__(self):
            return hash(self.name)

    stream_role = Role("Élèves - 2BACPC")
    class_role = Role("Élèves - 2BACPC-2")
    source = SimpleNamespace(is_empty=lambda: False)

    channel = FakeChannel(source)
    await _grant_class_role_access(
        [channel],
        class_role=class_role,
        student_stream_role=stream_role,
    )

    channel.set_permissions.assert_awaited_once()
    assert channel.set_permissions.await_args.kwargs["overwrite"] is source


def test_class_qr_registry_records_and_filters_active_invites(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_FILE", tmp_path / "guild_config.json")
    monkeypatch.setattr(storage, "DATABASE_FILE", tmp_path / "school.db")

    storage.initialize_database()
    storage.record_class_qr_invite(
        1,
        "abc123",
        "2BACPC-2",
        "2ème Année Bac",
        "2ème Année Bac Sciences Physiques",
        "2BACPC",
        2,
        900,
        42,
        "2026-10-06T10:00:00+00:00",
        "2026-10-06T10:30:00+00:00",
        100,
    )
    rows = storage.get_class_qr_invites(1, "2BACPC-2")
    assert len(rows) == 1
    assert rows[0]["invite_code"] == "abc123"

    storage.mark_class_qr_invite_revoked(1, "abc123")
    assert storage.get_class_qr_invites(1, "2BACPC-2") == []
    assert len(storage.get_class_qr_invites(1, "2BACPC-2", include_revoked=True)) == 1


def test_class_qr_registry_is_cleared_by_guild_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_FILE", tmp_path / "guild_config.json")
    monkeypatch.setattr(storage, "DATABASE_FILE", tmp_path / "school.db")

    storage.initialize_database()
    storage.record_class_qr_invite(
        1,
        "abc123",
        "TCS-1",
        "Tronc Commun",
        "Tronc Commun Scientifique",
        "TCS",
        1,
        901,
        42,
        "2026-10-06T10:00:00+00:00",
        "2026-10-06T10:30:00+00:00",
        100,
    )

    storage.reset_guild_data(1)
    assert storage.get_class_qr_invites(1, include_revoked=True) == []


@pytest.mark.asyncio
async def test_delete_invite_uses_invite_route():
    from services.qr_invites import delete_invite

    request = AsyncMock(return_value=None)
    bot = SimpleNamespace(http=SimpleNamespace(request=request))
    await delete_invite(bot, "abc123")

    route = request.await_args.args[0]
    assert route.method == "DELETE"
    assert route.path == "/invites/{invite_code}"
    assert request.await_args.kwargs["reason"] == "School Manager class QR revoked"

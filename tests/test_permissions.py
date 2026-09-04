from types import SimpleNamespace

import pytest

from services.permissions import ROLE_ADMIN, ROLE_PROFESSOR, _hierarchy_error, get_managed_role, management_check


class FakeRole:
    def __init__(self, name: str, position: int, role_id: int = 1, *, managed: bool = False) -> None:
        self.name = name
        self.position = position
        self.id = role_id
        self.managed = managed

    def is_default(self) -> bool:
        return self.position == 0

    def __ge__(self, other: "FakeRole") -> bool:
        return self.position >= other.position


def role(name: str, position: int, role_id: int):
    return FakeRole(name, position, role_id)


async def noop(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_management_check_requires_configured_role_id(monkeypatch):
    everyone = role("@everyone", 0, 1)
    admin = role(ROLE_ADMIN, 5, 42)
    bot_role = role("Bot", 10, 99)
    guild = SimpleNamespace(owner_id=999, id=123, roles=[everyone, admin, bot_role], default_role=everyone, me=SimpleNamespace(top_role=bot_role), get_role=lambda rid: admin if rid == 42 else None, guild_permissions=SimpleNamespace(manage_channels=True, manage_roles=True))
    response = SimpleNamespace(is_done=lambda: False, send_message=noop)
    user = SimpleNamespace(id=123, roles=[admin])
    interaction = SimpleNamespace(guild=guild, user=user, response=response, command=SimpleNamespace(name="status"))
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {})

    @management_check()
    async def dummy(_interaction):
        return True

    predicate = dummy.__discord_app_commands_checks__[0]
    assert await predicate(interaction) is False


@pytest.mark.asyncio
async def test_configured_admin_role_id_is_accepted(monkeypatch):
    everyone = role("@everyone", 0, 1)
    admin = role(ROLE_ADMIN, 5, 42)
    bot_role = role("Bot", 10, 99)
    guild = SimpleNamespace(owner_id=999, id=123, roles=[everyone, admin, bot_role], default_role=everyone, me=SimpleNamespace(top_role=bot_role), get_role=lambda rid: admin if rid == 42 else None, guild_permissions=SimpleNamespace(manage_channels=True, manage_roles=True))
    response = SimpleNamespace(is_done=lambda: False, send_message=noop)
    user = SimpleNamespace(id=123, roles=[admin])
    interaction = SimpleNamespace(guild=guild, user=user, response=response, command=SimpleNamespace(name="status"))
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {"management_role_id": 42})

    @management_check()
    async def dummy(_interaction):
        return True

    predicate = dummy.__discord_app_commands_checks__[0]
    assert await predicate(interaction) is True


def test_same_name_role_with_different_id_is_not_managed(monkeypatch):
    clone = role(ROLE_ADMIN, 5, 77)
    guild = SimpleNamespace(id=123, get_role=lambda rid: clone if rid == 77 else None)
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {"management_role_id": 42, "managed": {"roles": {ROLE_ADMIN: 42}}})
    assert get_managed_role(guild, ROLE_ADMIN) is None


def test_exact_managed_role_id_is_resolved(monkeypatch):
    managed_admin = role(ROLE_ADMIN, 5, 42)
    guild = SimpleNamespace(id=123, get_role=lambda rid: managed_admin if rid == 42 else None)
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {"management_role_id": 42, "managed": {"roles": {ROLE_ADMIN: 42}}})
    assert get_managed_role(guild, ROLE_ADMIN) is managed_admin


def test_same_name_prof_role_is_not_used_without_managed_id(monkeypatch):
    clone = role(ROLE_PROFESSOR, 5, 77)
    guild = SimpleNamespace(id=123, get_role=lambda rid: clone if rid == 77 else None)
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {"managed": {"roles": {}}})
    assert get_managed_role(guild, ROLE_PROFESSOR) is None


def test_hierarchy_error_uses_recorded_role_ids_only(monkeypatch):
    everyone = role("@everyone", 0, 1)
    unrelated_clone = role(ROLE_ADMIN, 20, 77)
    bot_role = role("Bot", 10, 99)
    guild = SimpleNamespace(roles=[everyone, unrelated_clone, bot_role], id=123, default_role=everyone, me=SimpleNamespace(top_role=bot_role))
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {"managed": {"roles": {}}})
    assert _hierarchy_error(guild) is None


def test_hierarchy_error_identifies_low_bot_role(monkeypatch):
    everyone = role("@everyone", 0, 1)
    school_role = role("Filière - 1BACSE", 12, 55)
    bot_role = role("Bot", 10, 99)
    guild = SimpleNamespace(roles=[everyone, school_role, bot_role], id=123, default_role=everyone, me=SimpleNamespace(top_role=bot_role))
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {"managed": {"roles": {"Filière - 1BACSE": 55}}})
    message = _hierarchy_error(guild)
    assert message is not None
    assert "Filière - 1BACSE" in message

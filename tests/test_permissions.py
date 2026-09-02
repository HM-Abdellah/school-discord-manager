from types import SimpleNamespace

import pytest

from services.permissions import ROLE_ADMIN, _hierarchy_error, management_check


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


def test_hierarchy_error_identifies_low_bot_role():
    everyone = role("@everyone", 0, 1)
    school_role = role("Filière - 1BACSE", 12, 55)
    bot_role = role("Bot", 10, 99)
    guild = SimpleNamespace(roles=[everyone, school_role, bot_role], default_role=everyone, me=SimpleNamespace(top_role=bot_role))
    message = _hierarchy_error(guild)
    assert message is not None
    assert "Filière - 1BACSE" in message

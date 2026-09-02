from types import SimpleNamespace

import pytest

from services.permissions import ROLE_ADMIN, _hierarchy_error, management_check


class FakeRole:
    def __init__(self, name: str, position: int, *, managed: bool = False) -> None:
        self.name = name
        self.position = position
        self.managed = managed

    def is_default(self) -> bool:
        return self.position == 0

    def __ge__(self, other: "FakeRole") -> bool:
        return self.position >= other.position


def _role(name: str, position: int):
    return FakeRole(name, position)


async def _async_noop(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_administrator_permission_is_not_a_management_bypass():
    everyone = _role("@everyone", 0)
    bot_role = _role("Bot", 10)
    guild = SimpleNamespace(
        owner_id=999,
        roles=[everyone, bot_role],
        default_role=everyone,
        me=SimpleNamespace(top_role=bot_role),
    )
    response = SimpleNamespace(is_done=lambda: False, send_message=_async_noop)
    user = SimpleNamespace(id=123, roles=[], guild_permissions=SimpleNamespace(administrator=True))
    interaction = SimpleNamespace(guild=guild, user=user, response=response, command=SimpleNamespace(name="status"))

    @management_check()
    async def dummy(_interaction):
        return True

    predicate = dummy.__discord_app_commands_checks__[0]
    assert await predicate(interaction) is False


@pytest.mark.asyncio
async def test_administration_role_is_accepted():
    everyone = _role("@everyone", 0)
    admin = _role(ROLE_ADMIN, 5)
    bot_role = _role("Bot", 10)
    guild = SimpleNamespace(
        owner_id=999,
        roles=[everyone, admin, bot_role],
        default_role=everyone,
        me=SimpleNamespace(top_role=bot_role),
    )
    response = SimpleNamespace(is_done=lambda: False, send_message=_async_noop)
    user = SimpleNamespace(id=123, roles=[admin], guild_permissions=SimpleNamespace(administrator=False))
    interaction = SimpleNamespace(guild=guild, user=user, response=response, command=SimpleNamespace(name="status"))

    @management_check()
    async def dummy(_interaction):
        return True

    predicate = dummy.__discord_app_commands_checks__[0]
    assert await predicate(interaction) is True


def test_hierarchy_error_identifies_low_bot_role():
    everyone = _role("@everyone", 0)
    school_role = _role("Filière - 1BACSE", 12)
    bot_role = _role("Bot", 10)
    guild = SimpleNamespace(
        owner_id=1,
        roles=[everyone, school_role, bot_role],
        default_role=everyone,
        me=SimpleNamespace(top_role=bot_role),
    )
    message = _hierarchy_error(guild)
    assert message is not None
    assert "Filière - 1BACSE" in message

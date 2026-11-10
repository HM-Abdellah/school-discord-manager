import asyncio

import pytest

from services.build_guard import get_build_lock


@pytest.mark.asyncio
async def test_guild_lock_is_reentrant_for_same_task():
    lock = get_build_lock(901)

    async with lock:
        assert lock.locked() is False
        async with lock:
            assert lock.locked() is False
        assert lock.locked() is False

    assert lock.locked() is False


@pytest.mark.asyncio
async def test_guild_lock_serializes_different_tasks():
    lock = get_build_lock(902)
    entered = []
    release_first = asyncio.Event()
    second_started = asyncio.Event()

    async def first():
        async with lock:
            entered.append("first")
            await release_first.wait()
            entered.append("first-done")

    async def second():
        await asyncio.sleep(0)
        async with lock:
            second_started.set()
            entered.append("second")

    first_task = asyncio.create_task(first())
    await asyncio.sleep(0)
    second_task = asyncio.create_task(second())
    await asyncio.sleep(0)

    assert entered == ["first"]
    assert not second_started.is_set()
    assert lock.locked() is True

    release_first.set()
    await asyncio.gather(first_task, second_task)

    assert entered == ["first", "first-done", "second"]
    assert lock.locked() is False


def test_management_authorized_uses_owner_or_configured_role(monkeypatch):
    from services import permissions

    admin = type("Role", (), {})()
    guild = type("Guild", (), {"owner_id": 100})()
    user = type("User", (), {"id": 200, "roles": [admin]})()
    interaction = type("Interaction", (), {"guild": guild, "user": user})()

    monkeypatch.setattr(permissions, "_management_role", lambda _guild: admin)
    assert permissions.management_authorized(interaction) is True

    user.id = 300
    assert permissions.management_authorized(interaction) is True

    user.roles = []
    assert permissions.management_authorized(interaction) is False

    user.id = 100
    assert permissions.management_authorized(interaction) is True



def test_admin_role_registry_sync_allows_direction_and_surveillance_roles_without_db_registry():
    from services.permissions import (
        ROLE_DIRECTION,
        ROLE_SURVEILLANT_GENERAL,
        admin_role_registry_sync_authorized,
    )

    class Role:
        def __init__(self, role_id, name, *, managed=False, default=False):
            self.id = role_id
            self.name = name
            self.managed = managed
            self._default = default

        def is_default(self):
            return self._default

    for role_name in (ROLE_DIRECTION, ROLE_SURVEILLANT_GENERAL):
        role = Role(501, role_name)
        guild = type("Guild", (), {"owner_id": 100, "roles": [role]})()
        user = type("User", (), {"id": 200, "roles": [role]})()
        interaction = type("Interaction", (), {"guild": guild, "user": user})()
        assert admin_role_registry_sync_authorized(interaction) is True


def test_admin_role_registry_sync_rejects_untrusted_or_ambiguous_roles():
    from services.permissions import ROLE_DIRECTION, admin_role_registry_sync_authorized

    class Role:
        def __init__(self, role_id, name, *, managed=False):
            self.id = role_id
            self.name = name
            self.managed = managed

        def is_default(self):
            return False

    assigned = Role(501, ROLE_DIRECTION)
    guild = type("Guild", (), {"owner_id": 100, "roles": [assigned]})()
    unrelated = Role(700, "Teacher")
    user = type("User", (), {"id": 200, "roles": [unrelated]})()
    interaction = type("Interaction", (), {"guild": guild, "user": user})()
    assert admin_role_registry_sync_authorized(interaction) is False

    user.roles = [assigned]
    guild.roles = [assigned, Role(502, ROLE_DIRECTION)]
    assert admin_role_registry_sync_authorized(interaction) is False

    managed = Role(503, ROLE_DIRECTION, managed=True)
    guild.roles = [managed]
    user.roles = [managed]
    assert admin_role_registry_sync_authorized(interaction) is False


def test_admin_role_registry_sync_always_allows_server_owner():
    from services.permissions import admin_role_registry_sync_authorized

    guild = type("Guild", (), {"owner_id": 100, "roles": []})()
    user = type("User", (), {"id": 100, "roles": []})()
    interaction = type("Interaction", (), {"guild": guild, "user": user})()
    assert admin_role_registry_sync_authorized(interaction) is True

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

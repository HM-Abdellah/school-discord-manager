import asyncio

import pytest

from services.build_guard import get_build_lock


@pytest.mark.asyncio
async def test_different_guilds_do_not_block_each_other():
    first_lock = get_build_lock(1001)
    second_lock = get_build_lock(1002)
    entered = []
    release = asyncio.Event()

    async def first():
        async with first_lock:
            entered.append("guild-1")
            await release.wait()

    async def second():
        async with second_lock:
            entered.append("guild-2")

    first_task = asyncio.create_task(first())
    await asyncio.sleep(0)
    second_task = asyncio.create_task(second())
    await asyncio.sleep(0)

    assert entered == ["guild-1", "guild-2"]
    assert first_lock.locked() is True
    assert second_lock.locked() is False

    release.set()
    await asyncio.gather(first_task, second_task)

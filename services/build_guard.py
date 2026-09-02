"""Per-guild build locks to prevent concurrent Discord reconciliation runs."""

from __future__ import annotations

import asyncio


_LOCKS: dict[int, asyncio.Lock] = {}


def get_build_lock(guild_id: int) -> asyncio.Lock:
    """Return the process-local build lock for one Discord guild."""
    lock = _LOCKS.get(guild_id)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[guild_id] = lock
    return lock

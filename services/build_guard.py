"""Per-guild mutation locks for Discord state-changing operations."""

from __future__ import annotations

import asyncio


class ReentrantAsyncLock:
    """An asyncio lock that can be safely re-entered by its owning task."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None
        self._depth = 0

    def locked(self) -> bool:
        """Return True only when another task currently owns the lock."""
        current = asyncio.current_task()
        return self._lock.locked() and self._owner is not current

    async def __aenter__(self) -> "ReentrantAsyncLock":
        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("Guild mutation locks require an active asyncio task.")
        if self._owner is current:
            self._depth += 1
            return self
        await self._lock.acquire()
        self._owner = current
        self._depth = 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        current = asyncio.current_task()
        if current is not self._owner:
            raise RuntimeError("Guild mutation lock released by a non-owner task.")
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()


_LOCKS: dict[int, ReentrantAsyncLock] = {}


def get_build_lock(guild_id: int) -> ReentrantAsyncLock:
    """Return the process-local mutation lock for one Discord guild.

    This lock serializes all in-process management/owner mutations for a guild.
    Multi-process or multi-replica deployments still require an external
    distributed lock and are intentionally outside this process-local guard.
    """
    lock = _LOCKS.get(guild_id)
    if lock is None:
        lock = ReentrantAsyncLock()
        _LOCKS[guild_id] = lock
    return lock

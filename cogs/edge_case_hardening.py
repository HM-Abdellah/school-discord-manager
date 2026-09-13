"""Isolated compatibility patch for stream-removal config consistency.

The main security cog owns command behavior; this extension only repairs the
persisted stream list around the existing /removestream callback. Keeping the
patch isolated makes the edge-case behavior explicit and easy to remove during
a future full command consolidation.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Awaitable, Callable

from cogs import security_hardening_v2 as hardened

_PATCH_LOCK = asyncio.Lock()


def _repair_removed_stream_config(config: dict[str, Any], level: str, stream: str) -> dict[str, Any]:
    """Return a copy with the requested stream removed from persisted config."""
    candidate = deepcopy(config)
    levels = candidate.get("levels", [])
    if not isinstance(levels, list):
        return candidate

    repaired_levels: list[Any] = []
    for level_item in levels:
        if not isinstance(level_item, dict) or level_item.get("name") != level:
            repaired_levels.append(level_item)
            continue

        streams = level_item.get("streams", [])
        if not isinstance(streams, list):
            repaired_levels.append(level_item)
            continue

        remaining_streams = [
            item
            for item in streams
            if not (isinstance(item, dict) and item.get("name") == stream)
        ]
        updated_level = deepcopy(level_item)
        updated_level["streams"] = remaining_streams
        if remaining_streams:
            repaired_levels.append(updated_level)

    candidate["levels"] = repaired_levels
    return candidate


async def _patched_remove_stream_callback(
    original_callback: Callable[..., Awaitable[Any]],
    interaction: Any,
    level: str,
    stream: str,
) -> None:
    """Serialize the compatibility wrapper and repair config before saving."""
    original_save = hardened.save_guild_config

    def guarded_save(guild_id: int, config: dict[str, Any]) -> None:
        original_save(guild_id, _repair_removed_stream_config(config, level, stream))

    async with _PATCH_LOCK:
        hardened.save_guild_config = guarded_save
        try:
            await original_callback(interaction, level, stream)
        finally:
            hardened.save_guild_config = original_save


async def setup(bot) -> None:
    """Patch the already-registered hardened /removestream command once."""
    command = bot.tree.get_command("removestream")
    if command is None or getattr(command, "_edge_case_hardening_applied", False):
        return

    original_callback = command.callback

    async def guarded_callback(interaction: Any, level: str, stream: str) -> None:
        await _patched_remove_stream_callback(original_callback, interaction, level, stream)

    command.callback = guarded_callback
    command._edge_case_hardening_applied = True

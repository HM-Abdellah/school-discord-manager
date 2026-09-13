"""Targeted edge-case compatibility patches kept isolated for final cleanup."""

from __future__ import annotations

import asyncio
from copy import deepcopy

from cogs import security_hardening_v2 as hardened
from services.storage import get_guild_config

_PATCH_LOCK = asyncio.Lock()
_ORIGINAL_SAVE = hardened.save_guild_config


def _repair_removed_stream_config(config: dict, level: str, stream: str) -> dict:
    """Return a copy with the requested stream removed from persisted config."""
    candidate = deepcopy(config)
    levels = candidate.get("levels", []) if isinstance(candidate, dict) else []
    if not isinstance(levels, list):
        return candidate

    repaired_levels = []
    for level_item in levels:
        if not isinstance(level_item, dict):
            repaired_levels.append(level_item)
            continue
        if level_item.get("name") != level:
            repaired_levels.append(level_item)
            continue
        streams = level_item.get("streams", [])
        if not isinstance(streams, list):
            repaired_levels.append(level_item)
            continue
        remaining_streams = [
            item for item in streams
            if not (isinstance(item, dict) and item.get("name") == stream)
        ]
        updated_level = deepcopy(level_item)
        updated_level["streams"] = remaining_streams
        if remaining_streams:
            repaired_levels.append(updated_level)

    candidate["levels"] = repaired_levels
    return candidate


async def _patched_remove_stream_callback(self, interaction, level: str, stream: str) -> None:
    original_save = hardened.save_guild_config

    def guarded_save(guild_id: int, config: dict) -> None:
        repaired = _repair_removed_stream_config(config, level, stream)
        original_save(guild_id, repaired)

    async with _PATCH_LOCK:
        hardened.save_guild_config = guarded_save
        try:
            await hardened.HardenedServerCommands.remove_stream.callback(self, interaction, level, stream)
        finally:
            hardened.save_guild_config = original_save


_ORIGINAL_REMOVE_STREAM = hardened.HardenedServerCommands.remove_stream.callback
hardened.HardenedServerCommands.remove_stream.callback = _patched_remove_stream_callback


async def setup(bot) -> None:
    """Apply isolated edge-case patch after the hardened cog has loaded."""
    return None

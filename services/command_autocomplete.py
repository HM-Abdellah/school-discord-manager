"""Shared application-command autocomplete helpers.

This module contains reusable UI helpers only. It must not own commands or
import command cogs, so command ownership remains one-way and explicit.
"""

from __future__ import annotations

import discord
from discord import app_commands

from config.curriculum import get_levels, get_streams


def _contains(value: str, current: str) -> bool:
    return current.casefold() in value.casefold()


async def level_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=level, value=level)
        for level in get_levels()
        if _contains(level, current)
    ][:25]


async def stream_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    if level not in get_levels():
        return []
    return [
        app_commands.Choice(name=stream, value=stream)
        for stream in get_streams(level)
        if _contains(stream, current)
    ][:25]

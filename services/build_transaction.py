"""Transactional orchestration for Discord structure builds.

A build mutates Discord first and persists its managed registry second. This
module makes that transition explicit: the working configuration is isolated
from the caller, resources created by the current build are tracked exactly,
and any failure during build or persistence triggers best-effort rollback of
those newly-created Discord resources.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import discord

from services.discord_ownership import (
    validate_managed_registry,
    validate_unmanaged_canonical_collisions,
)
from services.server_builder import ServerBuilder
from services.storage import get_guild_config, save_guild_config


class TransactionalServerBuilder(ServerBuilder):
    """ServerBuilder variant that tracks resources created by one transaction."""

    def __init__(self, guild: discord.Guild) -> None:
        super().__init__(guild)
        self.created_roles: list[discord.Role] = []
        self.created_categories: list[discord.CategoryChannel] = []
        self.created_channels: list[discord.abc.GuildChannel] = []

    @staticmethod
    def _role_ids(guild: discord.Guild) -> set[int]:
        return {
            int(role.id)
            for role in getattr(guild, "roles", [])
            if getattr(role, "id", None) is not None
        }

    @staticmethod
    def _channel_ids(
        guild: discord.Guild,
        snapshot: list[discord.abc.GuildChannel],
    ) -> set[int]:
        result = {
            int(channel.id)
            for channel in snapshot
            if getattr(channel, "id", None) is not None
        }
        result.update(
            int(channel.id)
            for channel in getattr(guild, "channels", [])
            if getattr(channel, "id", None) is not None
        )
        return result

    async def _create_role(self, name: str, **kwargs: Any) -> discord.Role:
        before = self._role_ids(self.guild)
        role = await super()._create_role(name, **kwargs)
        if int(role.id) not in before and all(
            existing.id != role.id for existing in self.created_roles
        ):
            self.created_roles.append(role)
        return role

    async def _get_or_create_category(
        self,
        name: str,
        overwrites=None,
    ) -> discord.CategoryChannel:
        before = self._channel_ids(self.guild, self._channel_snapshot)
        category = await super()._get_or_create_category(name, overwrites)
        if int(category.id) not in before and all(
            existing.id != category.id for existing in self.created_categories
        ):
            self.created_categories.append(category)
        return category

    async def _get_or_create_text(
        self,
        category: discord.CategoryChannel,
        name: str,
        *,
        topic: str,
        overwrites: dict,
    ) -> discord.TextChannel:
        before = self._channel_ids(self.guild, self._channel_snapshot)
        channel = await super()._get_or_create_text(
            category,
            name,
            topic=topic,
            overwrites=overwrites,
        )
        if int(channel.id) not in before and all(
            existing.id != channel.id for existing in self.created_channels
        ):
            self.created_channels.append(channel)
        return channel

    async def _get_or_create_voice(
        self,
        category: discord.CategoryChannel,
        name: str,
        overwrites: dict,
    ) -> discord.VoiceChannel:
        before = self._channel_ids(self.guild, self._channel_snapshot)
        channel = await super()._get_or_create_voice(category, name, overwrites)
        if int(channel.id) not in before and all(
            existing.id != channel.id for existing in self.created_channels
        ):
            self.created_channels.append(channel)
        return channel

    async def rollback(self) -> None:
        """Delete only resources created by this transaction, in dependency order."""
        errors: list[Exception] = []

        for channel in reversed(self.created_channels):
            try:
                await channel.delete(reason="School manager build rollback")
            except (discord.Forbidden, discord.HTTPException) as exc:
                errors.append(exc)

        for category in reversed(self.created_categories):
            try:
                await category.delete(reason="School manager build rollback")
            except (discord.Forbidden, discord.HTTPException) as exc:
                errors.append(exc)

        for role in reversed(self.created_roles):
            try:
                await role.delete(reason="School manager build rollback")
            except (discord.Forbidden, discord.HTTPException) as exc:
                errors.append(exc)

        if errors:
            print(
                f"[BUILD ROLLBACK] Failed to delete {len(errors)} resource(s) after transaction failure.",
                flush=True,
            )


def _configured_stream_keys(config: dict[str, Any] | None) -> set[tuple[str, str]]:
    if not isinstance(config, dict):
        return set()
    keys: set[tuple[str, str]] = set()
    for level in config.get("levels", []) or []:
        if not isinstance(level, dict) or not isinstance(level.get("name"), str):
            continue
        for stream in level.get("streams", []) or []:
            if isinstance(stream, dict) and isinstance(stream.get("name"), str):
                keys.add((level["name"], stream["name"]))
    return keys

async def build_and_persist(
    guild: discord.Guild,
    config: dict[str, Any],
):
    """Run a build against an isolated config and commit it only after success."""
    working_config = deepcopy(config)
    current_config = get_guild_config(guild.id)
    removed_streams = _configured_stream_keys(current_config) - _configured_stream_keys(working_config)
    if removed_streams:
        names = ", ".join(f"{level}/{stream}" for level, stream in sorted(removed_streams))
        raise ValueError(
            "Un build ne peut pas supprimer une filière déjà gérée. "
            f"Utilise /removestream avant de retirer : {names}."
        )
    await validate_managed_registry(guild, working_config)
    await validate_unmanaged_canonical_collisions(guild, working_config)
    builder = TransactionalServerBuilder(guild)

    try:
        stats = await builder.build(working_config)
        save_guild_config(guild.id, working_config)
    except Exception:
        await builder.rollback()
        raise

    config.clear()
    config.update(working_config)
    return stats

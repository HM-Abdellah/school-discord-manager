"""Transactional orchestration for Discord structure builds.

A build mutates Discord first and persists its managed registry second. This
module makes that transition explicit: the working configuration is isolated
from the caller, resources created by the current build are tracked exactly,
and any failure during build or persistence triggers best-effort rollback of
those newly-created Discord resources.
"""

from __future__ import annotations

from copy import copy, deepcopy
from typing import Any

import discord

from services.discord_ownership import (
    validate_managed_registry,
    validate_managed_registry_completeness,
    validate_unmanaged_canonical_collisions,
)
from services.permissions import ADMIN_ROLE_NAMES
from services.server_builder import ServerBuilder
from services.storage import get_guild_config, save_guild_config


class TransactionalServerBuilder(ServerBuilder):
    """ServerBuilder variant that tracks resources created by one transaction."""

    def __init__(self, guild: discord.Guild) -> None:
        super().__init__(guild)
        self.created_roles: list[discord.Role] = []
        self.created_categories: list[discord.CategoryChannel] = []
        self.created_channels: list[discord.abc.GuildChannel] = []
        self.added_role_overwrites: list[tuple[discord.abc.GuildChannel, discord.Role]] = []

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
        try:
            return await super()._create_role(name, **kwargs)
        finally:
            for role in getattr(self.guild, "roles", []):
                if int(role.id) not in before and all(
                    existing.id != role.id for existing in self.created_roles
                ):
                    self.created_roles.append(role)

    async def _get_or_create_category(
        self,
        name: str,
        overwrites=None,
    ) -> discord.CategoryChannel:
        before = self._channel_ids(self.guild, self._channel_snapshot)
        try:
            return await super()._get_or_create_category(name, overwrites)
        finally:
            candidates = list(self._channel_snapshot) + list(getattr(self.guild, "channels", []))
            for category in candidates:
                if (
                    int(getattr(category, "id", 0)) not in before
                    and isinstance(category, discord.CategoryChannel)
                    and all(existing.id != category.id for existing in self.created_categories)
                ):
                    self.created_categories.append(category)

    async def _get_or_create_text(
        self,
        category: discord.CategoryChannel,
        name: str,
        *,
        topic: str,
        overwrites: dict,
    ) -> discord.TextChannel:
        before = self._channel_ids(self.guild, self._channel_snapshot)
        try:
            return await super()._get_or_create_text(
                category,
                name,
                topic=topic,
                overwrites=overwrites,
            )
        finally:
            candidates = list(self._channel_snapshot) + list(getattr(self.guild, "channels", []))
            for channel in candidates:
                if (
                    int(getattr(channel, "id", 0)) not in before
                    and isinstance(channel, discord.TextChannel)
                    and all(existing.id != channel.id for existing in self.created_channels)
                ):
                    self.created_channels.append(channel)

    async def _get_or_create_voice(
        self,
        category: discord.CategoryChannel,
        name: str,
        overwrites: dict,
    ) -> discord.VoiceChannel:
        before = self._channel_ids(self.guild, self._channel_snapshot)
        try:
            return await super()._get_or_create_voice(category, name, overwrites)
        finally:
            candidates = list(self._channel_snapshot) + list(getattr(self.guild, "channels", []))
            for channel in candidates:
                if (
                    int(getattr(channel, "id", 0)) not in before
                    and isinstance(channel, discord.VoiceChannel)
                    and all(existing.id != channel.id for existing in self.created_channels)
                ):
                    self.created_channels.append(channel)

    async def migrate_legacy_admin_overwrites(
        self,
        previous_config: dict[str, Any] | None,
        replacement_roles: list[discord.Role] | tuple[discord.Role, ...],
    ) -> None:
        """Copy exact legacy Administration overwrites to new roles on registered resources.

        This transition never removes the old overwrite or looks up resources by
        name. Only recorded category/channel IDs are considered, and existing
        replacement-role overwrites are left untouched.
        """
        if not isinstance(previous_config, dict):
            return
        managed = previous_config.get("managed", {})
        managed = managed if isinstance(managed, dict) else {}
        role_registry = managed.get("roles", {})
        role_registry = role_registry if isinstance(role_registry, dict) else {}
        legacy_role_id = role_registry.get("Administration")
        if not isinstance(legacy_role_id, int) or legacy_role_id <= 0:
            legacy_role_id = previous_config.get("management_role_id")
        if not isinstance(legacy_role_id, int) or legacy_role_id <= 0:
            return

        role_getter = getattr(self.guild, "get_role", None)
        legacy_role = role_getter(legacy_role_id) if callable(role_getter) else None
        if (
            legacy_role is None
            or getattr(legacy_role, "name", None) != "Administration"
            or getattr(legacy_role, "managed", False)
            or (callable(getattr(legacy_role, "is_default", None)) and legacy_role.is_default())
        ):
            return

        resource_ids: set[int] = set()
        for section in ("categories", "channels"):
            values = managed.get(section, {})
            if not isinstance(values, dict):
                continue
            resource_ids.update(
                value for value in values.values()
                if isinstance(value, int) and value > 0
            )
        channel_getter = getattr(self.guild, "get_channel", None)
        if not callable(channel_getter):
            return

        for resource_id in sorted(resource_ids):
            resource = channel_getter(resource_id)
            if resource is None:
                continue
            current_overwrites = getattr(resource, "overwrites", None)
            if not isinstance(current_overwrites, dict) or legacy_role not in current_overwrites:
                continue
            legacy_overwrite = current_overwrites[legacy_role]
            for replacement_role in replacement_roles:
                if replacement_role in current_overwrites:
                    continue
                # Journal before the HTTP request: if Discord applied a request
                # but the response failed, rollback still attempts to remove it.
                self.added_role_overwrites.append((resource, replacement_role))
                await resource.set_permissions(
                    replacement_role,
                    overwrite=copy(legacy_overwrite),
                    reason="School Manager administrative role migration",
                )
                current_overwrites = getattr(resource, "overwrites", current_overwrites)

    async def rollback(self) -> None:
        """Restore overwrite changes and delete only resources created by this transaction."""
        errors: list[Exception] = []

        for resource, role in reversed(getattr(self, "added_role_overwrites", [])):
            try:
                await resource.set_permissions(
                    role,
                    overwrite=None,
                    reason="School Manager build rollback",
                )
            except (discord.Forbidden, discord.HTTPException) as exc:
                errors.append(exc)

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
    if isinstance(current_config, dict) and current_config.get("pending_reset") is not None:
        raise ValueError(
            "Un reset du serveur est interrompu et doit être repris via /resetserver avant toute nouvelle construction."
        )
    if isinstance(current_config, dict) and current_config.get("pending_removal") is not None:
        raise ValueError(
            "Une suppression de filière est interrompue et doit être reprise via /removestream avant toute nouvelle construction."
        )
    previous_config = current_config if isinstance(current_config, dict) else None
    previous_managed = previous_config.get("managed", {}) if isinstance(previous_config, dict) else {}
    previous_managed = previous_managed if isinstance(previous_managed, dict) else {}
    if previous_managed:
        # Validate the persisted IDs before any are used for an overwrite migration,
        # even if this build was started from a fresh /setup wizard configuration.
        await validate_managed_registry(guild, previous_config)
        previous_roles = previous_managed.get("roles", {})
        previous_roles = previous_roles if isinstance(previous_roles, dict) else {}
        legacy_role_id = previous_roles.get("Administration")
        if not isinstance(legacy_role_id, int) or legacy_role_id <= 0:
            legacy_role_id = previous_config.get("management_role_id")
        role_getter = getattr(guild, "get_role", None)
        legacy_role = role_getter(legacy_role_id) if callable(role_getter) and isinstance(legacy_role_id, int) and legacy_role_id > 0 else None
        if legacy_role is not None and getattr(legacy_role, "name", None) == "Administration" and not getattr(legacy_role, "managed", False):
            working_config.setdefault("managed", {}).setdefault("roles", {}).setdefault("Administration", legacy_role.id)

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
        await builder.migrate_legacy_admin_overwrites(
            previous_config,
            tuple(builder.main_roles[name] for name in ADMIN_ROLE_NAMES),
        )
        validate_managed_registry_completeness(working_config)
        save_guild_config(guild.id, working_config)
    except BaseException:
        # asyncio.CancelledError is not an Exception on supported Python versions.
        # A shutdown/cancellation mid-build must still remove resources created by
        # this transaction before the cancellation propagates to the caller.
        await builder.rollback()
        raise

    config.clear()
    config.update(working_config)
    return stats

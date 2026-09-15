"""Managed-resource ownership validation for live Discord state.

The managed registry identifies resources previously owned by the bot. A live
resource with the same name but a different ID is not silently adopted when a
registered resource has disappeared; that situation is treated as an explicit
ownership conflict and requires reconciliation instead.
"""

from __future__ import annotations

import unicodedata

import discord


class ManagedResourceConflict(RuntimeError):
    """A live Discord resource conflicts with a persisted managed identity."""


def _name_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold().strip()


def _mapping(config: dict, section: str) -> dict[str, int]:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    value = managed.get(section, {}) if isinstance(managed, dict) else {}
    if not isinstance(value, dict):
        return {}
    return {str(name): int(resource_id) for name, resource_id in value.items() if isinstance(resource_id, int) and resource_id > 0}


def _duplicate_by_name(items, expected_name: str):
    key = _name_key(expected_name)
    return [item for item in items if _name_key(getattr(item, "name", "")) == key]


async def validate_managed_registry(guild: discord.Guild, config: dict) -> None:
    """Fail closed when a registered resource identity conflicts with live state.

    Missing registered resources are allowed: a later builder/reconciliation
    pass may recreate them. What is not allowed is silently switching ownership
    to a different live object that merely has the same canonical name.
    """
    role_mappings = _mapping(config, "roles")
    roles = list(getattr(guild, "roles", []))
    for expected_name, registered_id in role_mappings.items():
        by_id = next((role for role in roles if role.id == registered_id), None)
        same_name = _duplicate_by_name(roles, expected_name)
        if by_id is not None and _name_key(by_id.name) != _name_key(expected_name):
            raise ManagedResourceConflict(
                f"Managed role `{expected_name}` points to role ID {registered_id}, "
                f"but that live role is named `{by_id.name}`."
            )
        conflicting = [role for role in same_name if role.id != registered_id]
        if conflicting:
            ids = ", ".join(str(role.id) for role in conflicting)
            raise ManagedResourceConflict(
                f"Managed role `{expected_name}` owns ID {registered_id}, but another live "
                f"role with the same name exists (IDs: {ids})."
            )

    try:
        channels = list(await guild.fetch_channels())
    except (discord.Forbidden, discord.HTTPException):
        channels = list(getattr(guild, "channels", []))

    for section in ("categories", "channels"):
        for expected_name, registered_id in _mapping(config, section).items():
            same_name = _duplicate_by_name(channels, expected_name)
            by_id = next((channel for channel in channels if channel.id == registered_id), None)
            if by_id is not None and _name_key(by_id.name) != _name_key(expected_name):
                raise ManagedResourceConflict(
                    f"Managed {section[:-1]} `{expected_name}` points to channel ID {registered_id}, "
                    f"but that live resource is named `{by_id.name}`."
                )
            conflicting = [channel for channel in same_name if channel.id != registered_id]
            if conflicting:
                ids = ", ".join(str(channel.id) for channel in conflicting)
                raise ManagedResourceConflict(
                    f"Managed {section[:-1]} `{expected_name}` owns ID {registered_id}, but another live "
                    f"resource with the same name exists (IDs: {ids})."
                )

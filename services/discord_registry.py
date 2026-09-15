"""Live Discord resource resolution and managed-registry reconciliation."""

from __future__ import annotations

import unicodedata

import discord

from services.storage import get_guild_config, save_guild_config


def _name_key(value: str) -> str:
    """Return a stable comparison key for Discord names."""
    normalized = unicodedata.normalize("NFKC", value or "")
    return normalized.casefold().strip()


def _managed_mapping(config: dict, section: str) -> dict:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    value = managed.get(section, {}) if isinstance(managed, dict) else {}
    return value if isinstance(value, dict) else {}


def _find_registered_id(
    config: dict,
    section: str,
    expected_name: str,
) -> tuple[str | None, int | None]:
    """Find a managed ID even when the registry key's case differs."""
    mapping = _managed_mapping(config, section)
    exact = mapping.get(expected_name)
    if isinstance(exact, int) and exact > 0:
        return expected_name, exact

    wanted = _name_key(expected_name)
    for name, value in mapping.items():
        if _name_key(str(name)) == wanted and isinstance(value, int) and value > 0:
            return str(name), value
    return None, None


def _set_managed_id(config: dict, section: str, name: str, value: int) -> None:
    managed = config.setdefault("managed", {})
    if not isinstance(managed, dict):
        managed = {}
        config["managed"] = managed

    mapping = managed.setdefault(section, {})
    if not isinstance(mapping, dict):
        mapping = {}
        managed[section] = mapping

    wanted = _name_key(name)
    for key in list(mapping):
        if key != name and _name_key(str(key)) == wanted:
            mapping.pop(key, None)
    mapping[name] = value


def _is_expected_category(
    category: discord.abc.GuildChannel | None,
    expected_name: str,
) -> bool:
    return (
        isinstance(category, discord.CategoryChannel)
        and _name_key(category.name) == _name_key(expected_name)
    )


def _is_expected_text_channel(
    channel: discord.abc.GuildChannel | None,
    expected_name: str,
    category_id: int,
) -> bool:
    return (
        isinstance(channel, discord.TextChannel)
        and _name_key(channel.name) == _name_key(expected_name)
        and channel.category_id == category_id
    )


async def resolve_registered_text_channel(
    guild: discord.Guild,
    config: dict,
    *,
    channel_name: str,
    category_name: str,
) -> discord.TextChannel | None:
    """Resolve a channel only through its persisted managed identity.

    This is the strict path for operations that must never adopt an unmanaged
    same-name channel. Both the persisted category ID and channel ID must
    resolve to objects whose names and parent relationship match exactly.
    Missing IDs, deleted resources, renames, type changes, and category moves
    all fail closed. No live name scan or registry repair is performed.
    """
    _, category_id = _find_registered_id(config, "categories", category_name)
    _, channel_id = _find_registered_id(config, "channels", channel_name)
    if category_id is None or channel_id is None:
        return None

    try:
        category = await guild.fetch_channel(category_id)
        channel = await guild.fetch_channel(channel_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None

    if not _is_expected_category(category, category_name):
        return None
    if not _is_expected_text_channel(channel, channel_name, category.id):
        return None
    return channel


async def resolve_managed_text_channel(
    guild: discord.Guild,
    config: dict,
    *,
    channel_name: str,
    category_name: str,
) -> tuple[discord.TextChannel | None, bool]:
    """Resolve a managed channel with controlled registry reconciliation.

    Resolution order:
    1. Persisted IDs, after strict live-object validation.
    2. An unambiguous live category/channel scan for non-destructive
       publication flows that explicitly support registry repair.

    Callers performing security-sensitive access control or permission changes
    should use :func:`resolve_registered_text_channel` instead.
    """
    category_key, category_id = _find_registered_id(config, "categories", category_name)
    channel_key, channel_id = _find_registered_id(config, "channels", channel_name)
    registry_changed = False

    category = guild.get_channel(category_id) if category_id else None
    channel = guild.get_channel(channel_id) if channel_id else None

    if _is_expected_category(category, category_name) and _is_expected_text_channel(
        channel,
        channel_name,
        category.id,
    ):
        if category.name != category_name or category_key != category.name:
            _set_managed_id(config, "categories", category.name, category.id)
            registry_changed = True
        if channel.name != channel_name or channel_key != channel.name:
            _set_managed_id(config, "channels", channel.name, channel.id)
            registry_changed = True
        return channel, registry_changed

    try:
        channels = list(await guild.fetch_channels())
    except (discord.Forbidden, discord.HTTPException):
        channels = list(guild.channels)

    live_categories = [
        item
        for item in channels
        if isinstance(item, discord.CategoryChannel)
        and _name_key(item.name) == _name_key(category_name)
    ]

    matching_registered_category = next(
        (item for item in live_categories if item.id == category_id),
        None,
    )
    if matching_registered_category is not None:
        category = matching_registered_category
    elif len(live_categories) == 1:
        category = live_categories[0]
    else:
        return None, False

    live_channels = [
        item
        for item in channels
        if _is_expected_text_channel(item, channel_name, category.id)
    ]
    matching_registered_channel = next(
        (item for item in live_channels if item.id == channel_id),
        None,
    )
    if matching_registered_channel is not None:
        live_channel = matching_registered_channel
    elif len(live_channels) == 1:
        live_channel = live_channels[0]
    else:
        return None, False

    current_category_id = _find_registered_id(config, "categories", category_name)[1]
    current_channel_id = _find_registered_id(config, "channels", channel_name)[1]

    if current_category_id != category.id or category_key != category.name:
        _set_managed_id(config, "categories", category.name, category.id)
        registry_changed = True
    if current_channel_id != live_channel.id or channel_key != live_channel.name:
        _set_managed_id(config, "channels", live_channel.name, live_channel.id)
        registry_changed = True

    return live_channel, registry_changed


def persist_registry_repair(guild_id: int, config: dict, changed: bool) -> bool:
    """Persist a reconciled managed registry, failing closed on local I/O errors."""
    if not changed:
        return True
    try:
        save_guild_config(guild_id, config)
        return True
    except OSError:
        return False

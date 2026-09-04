"""Compatibility layer for legacy School Manager Discord resources."""

from __future__ import annotations

import re
from typing import Any

import discord

from config.curriculum import (
    GENERAL_CHANNELS,
    PROFESSOR_CHANNELS,
    get_levels,
    get_stream_abbreviation,
    get_stream_subjects,
    get_streams,
    get_subject_display_name,
    get_subject_internal_code,
)
from services import permissions as permissions_module
from services import storage
from services.server_builder import (
    CATEGORY_GENERAL,
    CATEGORY_PROFESSORS,
    CATEGORY_VOICE,
    _stream_category_name,
    _subject_role_name,
    _stream_role_name,
    _student_stream_role_name,
)

_BOT: Any | None = None


def _token(text: str, value: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])", text, re.I) is not None


def _configured_ids(config: dict[str, Any], kind: str) -> set[int]:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    values = managed.get(kind, {}) if isinstance(managed, dict) else {}
    if not isinstance(values, dict):
        return set()
    return {value for value in values.values() if isinstance(value, int) and value > 0}


def _find_stream_category(guild: discord.Guild, level: str, stream: str) -> discord.CategoryChannel | None:
    code = get_stream_abbreviation(level, stream)
    expected = _stream_category_name(level, stream, code)
    category = discord.utils.get(guild.categories, name=expected)
    if category:
        return category
    return next((c for c in guild.categories if _token(c.name, code)), None)


def get_managed_role_compat(guild: discord.Guild, name: str) -> discord.Role | None:
    config = storage.get_guild_config(guild.id) or {}
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    roles = managed.get("roles", {}) if isinstance(managed, dict) else {}
    role_id = roles.get(name) if isinstance(roles, dict) else None
    if name == permissions_module.ROLE_ADMIN and not isinstance(role_id, int):
        role_id = config.get("management_role_id")
    if isinstance(role_id, int):
        role = guild.get_role(role_id)
        if role is not None and role.name == name and not role.managed:
            return role
    role = discord.utils.get(guild.roles, name=name)
    return role if role is not None and not role.managed else None


def find_subject_channel_compat(guild: discord.Guild, expected_name: str) -> discord.TextChannel | None:
    config = storage.get_guild_config(guild.id) or {}
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    channels = managed.get("channels", {}) if isinstance(managed, dict) else {}
    channel_id = channels.get(expected_name) if isinstance(channels, dict) else None
    if isinstance(channel_id, int):
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
    exact = discord.utils.get(guild.text_channels, name=expected_name)
    if exact:
        return exact

    match = re.match(r"^📚-?([^・\-]+)[・\-](.+)$", expected_name)
    if not match:
        return None
    code, subject_tail = match.group(1).strip(), match.group(2).strip().casefold()
    variants = {subject_tail}
    for level in get_levels():
        for stream in get_streams(level):
            if get_stream_abbreviation(level, stream).casefold() != code.casefold():
                continue
            for subject in get_stream_subjects(level, stream):
                if subject_tail in {
                    subject.casefold(),
                    get_subject_display_name(subject).casefold(),
                    get_subject_internal_code(subject).casefold(),
                }:
                    variants.update({
                        subject.casefold(),
                        get_subject_display_name(subject).casefold(),
                        get_subject_internal_code(subject).casefold(),
                    })
            category = _find_stream_category(guild, level, stream)
            if category:
                for channel in category.text_channels:
                    if any(v and v in channel.name.casefold() for v in variants):
                        return channel
    return next(
        (c for c in guild.text_channels if code.casefold() in c.name.casefold() and subject_tail in c.name.casefold()),
        None,
    )


def find_stream_channel_compat(guild: discord.Guild, level: str, stream: str, kind: str) -> discord.TextChannel | None:
    code = get_stream_abbreviation(level, stream)
    expected = {
        "timetable": f"🗓️-{code}・emploi-du-temps",
        "exams": f"📝-{code}・examens",
    }[kind]
    config = storage.get_guild_config(guild.id) or {}
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    channels = managed.get("channels", {}) if isinstance(managed, dict) else {}
    channel_id = channels.get(expected) if isinstance(channels, dict) else None
    if isinstance(channel_id, int):
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
    exact = discord.utils.get(guild.text_channels, name=expected)
    if exact:
        return exact

    keywords = (
        ("exam", "examen", "épreuve")
        if kind == "exams"
        else ("emploi", "horaire", "planning", "timetable")
    )
    category = _find_stream_category(guild, level, stream)
    if category:
        for channel in category.text_channels:
            name = channel.name.casefold()
            if code.casefold() in name and any(k in name for k in keywords):
                return channel
    return next(
        (
            c
            for c in guild.text_channels
            if code.casefold() in c.name.casefold()
            and any(k in c.name.casefold() for k in keywords)
        ),
        None,
    )


def _resolve_guild_for_config(config: dict[str, Any]) -> discord.Guild | None:
    """Resolve the guild owning a configuration without mutating Discord Command objects."""
    bot = _BOT
    if bot is None:
        return None
    guilds = list(getattr(bot, "guilds", []))
    if len(guilds) == 1:
        return guilds[0]
    for guild in guilds:
        if storage.get_guild_config(guild.id) == config:
            return guild
    return None


def discover_managed_ids(config: dict[str, Any]) -> tuple[set[int], set[int], set[int]]:
    """Return registered IDs plus safely identifiable legacy School Manager IDs."""
    guild = _resolve_guild_for_config(config)
    role_ids = _configured_ids(config, "roles")
    channel_ids = _configured_ids(config, "channels")
    category_ids = _configured_ids(config, "categories")
    if guild is None:
        return role_ids, channel_ids, category_ids

    codes = {
        get_stream_abbreviation(level, stream)
        for level in get_levels()
        for stream in get_streams(level)
    }
    fixed_categories = {CATEGORY_GENERAL, CATEGORY_PROFESSORS, CATEGORY_VOICE}
    for category in guild.categories:
        if category.name in fixed_categories or any(_token(category.name, code) for code in codes):
            category_ids.add(category.id)

    for category in guild.categories:
        if category.id in category_ids:
            channel_ids.update(channel.id for channel in category.channels)

    fixed_channels = set(GENERAL_CHANNELS.values()) | set(PROFESSOR_CHANNELS.values())
    channel_ids.update(
        channel.id
        for channel in guild.channels
        if getattr(channel, "name", None) in fixed_channels
    )

    role_names = {
        permissions_module.ROLE_ADMIN,
        permissions_module.ROLE_PROFESSOR,
        permissions_module.ROLE_PROFESSOR_FEMALE,
        permissions_module.ROLE_STUDENT,
    }
    for level in get_levels():
        for stream in get_streams(level):
            code = get_stream_abbreviation(level, stream)
            role_names.update({
                _stream_role_name(level, stream),
                _student_stream_role_name(level, stream),
                f"{permissions_module.STREAM_ROLE_PREFIX}{code}",
                f"{permissions_module.STUDENT_STREAM_ROLE_PREFIX}{code}",
            })
            role_names.update(
                _subject_role_name(level, stream, subject)
                for subject in get_stream_subjects(level, stream)
            )

    for role in guild.roles:
        if role.name in role_names:
            role_ids.add(role.id)
        elif role.name.startswith(permissions_module.SUBJECT_ROLE_PREFIX) and any(
            _token(role.name, code) for code in codes
        ):
            role_ids.add(role.id)
    return role_ids, channel_ids, category_ids


def apply_runtime_fixes(bot: Any) -> None:
    global _BOT
    _BOT = bot

    import cogs.admin as admin_module
    import cogs.server_v3 as server_module
    import cogs.teachers as teachers_module

    permissions_module.get_managed_role = get_managed_role_compat
    admin_module.get_managed_role = get_managed_role_compat
    teachers_module.get_managed_role = get_managed_role_compat
    teachers_module._find_managed_channel = find_subject_channel_compat
    admin_module._find_stream_channel = find_stream_channel_compat
    server_module._configured_managed_ids = discover_managed_ids

    print("[FIXES] Legacy resource discovery active.", flush=True)

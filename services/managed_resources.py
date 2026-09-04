"""Canonical ownership and legacy-adoption helpers for School Manager resources."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import discord

from config.curriculum import GENERAL_CHANNELS, PROFESSOR_CHANNELS, get_stream_abbreviation, get_stream_subjects
from services.permissions import ROLE_ADMIN, ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, ROLE_STUDENT, STREAM_ROLE_PREFIX, STUDENT_STREAM_ROLE_PREFIX
from services.server_builder import CATEGORY_GENERAL, CATEGORY_PROFESSORS, CATEGORY_VOICE, _stream_category_name, _subject_channel_name, _subject_role_name


def configured_ids(config: dict[str, Any], kind: str) -> set[int]:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    values = managed.get(kind, {}) if isinstance(managed, dict) else {}
    if not isinstance(values, dict):
        return set()
    return {value for value in values.values() if isinstance(value, int) and value > 0}


def configured_streams(config: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    for level in config.get("levels", []) if isinstance(config, dict) else []:
        if not isinstance(level, dict):
            continue
        level_name = str(level.get("name", ""))
        for stream in level.get("streams", []) or []:
            if isinstance(stream, dict):
                yield level_name, stream


def stream_role_names(config: dict[str, Any]) -> set[str]:
    names = {ROLE_ADMIN, ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, ROLE_STUDENT}
    for level_name, stream in configured_streams(config):
        stream_name = str(stream.get("name", ""))
        code = str(stream.get("abbreviation") or get_stream_abbreviation(level_name, stream_name))
        names.update({f"{STREAM_ROLE_PREFIX}{code}", f"{STUDENT_STREAM_ROLE_PREFIX}{code}"})
        subjects = stream.get("subjects", []) or get_stream_subjects(level_name, stream_name)
        names.update(_subject_role_name(level_name, stream_name, subject) for subject in subjects)
    return names


def stream_category_names(config: dict[str, Any]) -> set[str]:
    names = {CATEGORY_GENERAL, CATEGORY_PROFESSORS, CATEGORY_VOICE}
    for level_name, stream in configured_streams(config):
        stream_name = str(stream.get("name", ""))
        code = str(stream.get("abbreviation") or get_stream_abbreviation(level_name, stream_name))
        names.add(_stream_category_name(level_name, stream_name, code))
    return names


def fixed_channel_names() -> set[str]:
    return set(GENERAL_CHANNELS.values()) | set(PROFESSOR_CHANNELS.values())


def discover_managed_ids(guild: discord.Guild, config: dict[str, Any]) -> tuple[set[int], set[int], set[int]]:
    """Find registered resources and exact canonical legacy resources for one guild."""
    role_ids = configured_ids(config, "roles")
    channel_ids = configured_ids(config, "channels")
    category_ids = configured_ids(config, "categories")
    expected_categories = stream_category_names(config)
    for category in guild.categories:
        if category.name in expected_categories:
            category_ids.add(category.id)
    for category in guild.categories:
        if category.id in category_ids:
            channel_ids.update(channel.id for channel in category.channels)
    channel_ids.update(channel.id for channel in guild.channels if getattr(channel, "name", None) in fixed_channel_names())
    expected_roles = stream_role_names(config)
    role_ids.update(role.id for role in guild.roles if role.name in expected_roles and not role.managed)
    return role_ids, channel_ids, category_ids


def resolve_role(guild: discord.Guild, config: dict[str, Any], name: str) -> discord.Role | None:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    roles = managed.get("roles", {}) if isinstance(managed, dict) else {}
    role_id = roles.get(name) if isinstance(roles, dict) else None
    if name == ROLE_ADMIN and not isinstance(role_id, int):
        role_id = config.get("management_role_id")
    if isinstance(role_id, int) and role_id > 0:
        role = guild.get_role(role_id)
        if role is not None and role.name == name and not role.managed:
            return role
    if name in stream_role_names(config):
        role = discord.utils.get(guild.roles, name=name)
        if role is not None and not role.managed:
            return role
    return None


def resolve_channel(guild: discord.Guild, config: dict[str, Any], expected_name: str, *, category: discord.CategoryChannel | None = None) -> discord.abc.GuildChannel | None:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    channels = managed.get("channels", {}) if isinstance(managed, dict) else {}
    channel_id = channels.get(expected_name) if isinstance(channels, dict) else None
    if isinstance(channel_id, int) and channel_id > 0:
        channel = guild.get_channel(channel_id)
        if channel is not None and channel.name == expected_name:
            return channel
    if category is not None:
        return discord.utils.get(category.channels, name=expected_name)
    return discord.utils.get(guild.text_channels, name=expected_name) or discord.utils.get(guild.voice_channels, name=expected_name)


def adopt_stream_registry(config: dict[str, Any], guild: discord.Guild, level_name: str, stream: dict[str, Any]) -> None:
    """Adopt a canonical existing stream category/channels/roles into the managed registry."""
    managed = config.setdefault("managed", {})
    roles = managed.setdefault("roles", {})
    categories = managed.setdefault("categories", {})
    channels = managed.setdefault("channels", {})
    stream_name = str(stream["name"])
    code = str(stream.get("abbreviation") or get_stream_abbreviation(level_name, stream_name))
    category = discord.utils.get(guild.categories, name=_stream_category_name(level_name, stream_name, code))
    if category is not None:
        categories[category.name] = category.id
        for channel in category.channels:
            channels[channel.name] = channel.id
    subjects = stream.get("subjects", []) or get_stream_subjects(level_name, stream_name)
    for role_name in {f"{STREAM_ROLE_PREFIX}{code}", f"{STUDENT_STREAM_ROLE_PREFIX}{code}", *{_subject_role_name(level_name, stream_name, subject) for subject in subjects}}:
        role = discord.utils.get(guild.roles, name=role_name)
        if role is not None and not role.managed:
            roles[role.name] = role.id


def stream_expected_channel_names(level_name: str, stream: dict[str, Any]) -> set[str]:
    stream_name = str(stream["name"])
    code = str(stream.get("abbreviation") or get_stream_abbreviation(level_name, stream_name))
    subjects = stream.get("subjects", []) or get_stream_subjects(level_name, stream_name)
    return {f"📌-{code}・informations", f"🗓️-{code}・emploi-du-temps", f"📝-{code}・examens", *{_subject_channel_name(code, subject) for subject in subjects}}

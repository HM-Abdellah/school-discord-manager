"""Managed-resource ownership validation for live Discord state.

The managed registry identifies resources previously owned by the bot. A live
resource with the same name but a different ID is not silently adopted when a
registered resource has disappeared; that situation is treated as an explicit
ownership conflict and requires reconciliation instead.
"""

from __future__ import annotations

import unicodedata

import discord

from config.curriculum import (
    GENERAL_CHANNELS,
    PROFESSOR_CHANNELS,
    get_stream_abbreviation,
    get_stream_subjects,
)
from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_PROFESSOR_FEMALE,
    ROLE_STUDENT,
    STREAM_ROLE_PREFIX,
    STUDENT_STREAM_ROLE_PREFIX,
)
from services.server_builder import (
    CATEGORY_GENERAL,
    CATEGORY_PROFESSORS,
    CATEGORY_VOICE,
    _safe_name,
    _stream_category_name,
    _subject_channel_name,
)


class ManagedResourceConflict(RuntimeError):
    """A live Discord resource conflicts with a persisted managed identity."""


def _name_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold().strip()


def _mapping(config: dict, section: str) -> dict[str, int]:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    value = managed.get(section, {}) if isinstance(managed, dict) else {}
    if not isinstance(value, dict):
        return {}
    return {
        str(name): int(resource_id)
        for name, resource_id in value.items()
        if isinstance(resource_id, int) and resource_id > 0
    }


def _duplicate_by_name(items, expected_name: str):
    key = _name_key(expected_name)
    return [item for item in items if _name_key(getattr(item, "name", "")) == key]


def _channel_type_error(section: str, resource, expected_name: str) -> str | None:
    """Validate live Discord channel type when inspecting a real Discord object."""
    if resource is None or not hasattr(resource, "type"):
        return None
    if section == "categories" and not isinstance(resource, discord.CategoryChannel):
        return "Managed category %r points to a non-category Discord resource." % expected_name
    if section == "channels":
        expected_voice = expected_name.startswith("🔊-")
        if expected_voice and not isinstance(resource, discord.VoiceChannel):
            return "Managed channel %r points to a non-voice Discord resource." % expected_name
        if not expected_voice and not isinstance(resource, discord.TextChannel):
            return "Managed channel %r points to a non-text Discord resource." % expected_name
    return None


async def _fetch_channels(guild: discord.Guild):
    fetch_channels = getattr(guild, "fetch_channels", None)
    if fetch_channels is None:
        return list(getattr(guild, "channels", []))
    try:
        return list(await fetch_channels())
    except (discord.Forbidden, discord.HTTPException):
        return list(getattr(guild, "channels", []))


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
        if by_id is not None and getattr(by_id, "managed", False):
            raise ManagedResourceConflict(
                f"Managed role `{expected_name}` points to Discord-managed role ID {registered_id}; "
                "that role cannot be owned safely by School Manager."
            )
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

    channels = await _fetch_channels(guild)
    for section in ("categories", "channels"):
        for expected_name, registered_id in _mapping(config, section).items():
            same_name = _duplicate_by_name(channels, expected_name)
            by_id = next((channel for channel in channels if channel.id == registered_id), None)
            type_error = _channel_type_error(section, by_id, expected_name)
            if type_error:
                raise ManagedResourceConflict(type_error)
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


def _expected_canonical_names(config: dict) -> tuple[set[str], set[str], set[str]]:
    """Return canonical role/category/channel names the builder may create/reuse."""
    role_names = {
        ROLE_ADMIN,
        ROLE_PROFESSOR,
        ROLE_PROFESSOR_FEMALE,
        ROLE_STUDENT,
    }
    category_names = {CATEGORY_GENERAL, CATEGORY_PROFESSORS, CATEGORY_VOICE}
    channel_names: set[str] = set(GENERAL_CHANNELS.values()) | {
        PROFESSOR_CHANNELS["discussion"],
        PROFESSOR_CHANNELS["meeting"],
    }

    stream_codes: set[str] = set()
    for level in config.get("levels", []) if isinstance(config, dict) else []:
        if not isinstance(level, dict) or not isinstance(level.get("name"), str):
            continue
        level_name = level["name"]
        for stream in level.get("streams", []) or []:
            if not isinstance(stream, dict) or not isinstance(stream.get("name"), str):
                continue
            stream_name = stream["name"]
            code = str(
                stream.get("abbreviation")
                or get_stream_abbreviation(level_name, stream_name)
            )
            stream_codes.add(code)
            category_names.add(_stream_category_name(level_name, stream_name, code))
            role_names.update(
                {
                    f"{STREAM_ROLE_PREFIX}{code}",
                    f"{STUDENT_STREAM_ROLE_PREFIX}{code}",
                }
            )
            subjects = stream.get("subjects", []) or get_stream_subjects(level_name, stream_name)
            channel_names.update(
                {
                    f"📌-{code}・informations",
                    f"🗓️-{code}・emploi-du-temps",
                    f"📝-{code}・examens",
                    *{
                        _subject_channel_name(code, subject)
                        for subject in subjects
                    },
                }
            )

    channel_names.update(
        f"🔊-{_safe_name(code, 30)}-à-distance"
        for code in stream_codes
    )
    return role_names, category_names, channel_names


def validate_managed_registry_completeness(config: dict) -> None:
    """Fail closed before persistence when the build registry is incomplete.

    The builder is responsible for recording every managed canonical resource
    it creates or reuses. Persisting a partial registry would make future
    destructive operations unable to prove ownership safely.
    """
    role_names, category_names, channel_names = _expected_canonical_names(config)
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    managed = managed if isinstance(managed, dict) else {}

    missing: list[str] = []
    for section, expected_names in (
        ("roles", role_names),
        ("categories", category_names),
        ("channels", channel_names),
    ):
        mapping = managed.get(section)
        mapping = mapping if isinstance(mapping, dict) else {}
        for name in sorted(expected_names):
            value = mapping.get(name)
            if not isinstance(value, int) or value <= 0:
                missing.append(f"{section[:-1]} `{name}`")

    if missing:
        preview = "; ".join(missing[:20])
        suffix = f"; ... ({len(missing)} total)" if len(missing) > 20 else ""
        raise ManagedResourceConflict(
            "Managed registry incomplete after build: " + preview + suffix
        )


async def validate_unmanaged_canonical_collisions(
    guild: discord.Guild,
    config: dict,
) -> None:
    """Reject canonical resources that exist live but are absent from the registry."""
    role_names, category_names, channel_names = _expected_canonical_names(config)
    canonical_role_keys = {_name_key(name) for name in role_names}
    canonical_channel_keys = {
        _name_key(name) for name in category_names | channel_names
    }
    managed_role_ids = set(_mapping(config, "roles").values())
    managed_category_ids = set(_mapping(config, "categories").values())
    managed_channel_ids = set(_mapping(config, "channels").values())

    for role in getattr(guild, "roles", []):
        if role.id in managed_role_ids:
            continue
        if _name_key(role.name) in canonical_role_keys:
            ownership = "Discord-managed" if getattr(role, "managed", False) else "unmanaged"
            raise ManagedResourceConflict(
                f"Canonical role `{role.name}` exists as {ownership} ID {role.id}; refusing silent adoption."
            )

    channels = await _fetch_channels(guild)
    for channel in channels:
        if channel.id in managed_category_ids or channel.id in managed_channel_ids:
            continue
        if _name_key(getattr(channel, "name", "")) in canonical_channel_keys:
            raise ManagedResourceConflict(
                f"Canonical Discord resource `{channel.name}` exists as unmanaged ID {channel.id}; refusing silent adoption."
            )

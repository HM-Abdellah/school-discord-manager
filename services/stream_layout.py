"""Stream visual layout helpers for the compact level-category architecture.

This compatibility layer adds a read-only title channel for each stream while
keeping every level inside one category/suite.  The title channel is counted
as a real Discord resource so capacity checks and placement stay safe.
"""

from __future__ import annotations

import discord

from config.curriculum import get_stream_abbreviation
from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_PROFESSOR_FEMALE,
    ROLE_STUDENT,
    stream_header_overwrites,
)
from services.server_builder import (
    STREAM_EMOJIS,
    ServerBuilder,
    _safe_name,
    _subject_channel_name,
    _stream_channel_prefixes,
)

HEADER_PREFIX = "🔹・"


def stream_header_name(stream_name: str, stream_code: str) -> str:
    return f"{HEADER_PREFIX}{STREAM_EMOJIS.get(stream_name, '🎓')}・{stream_code}"


def _stream_channel_count(level: dict, stream: dict) -> int:
    return 4 + len(list(stream.get("subjects", [])))


def _planned_channel_names_for_stream(stream: dict) -> set[str]:
    code = stream.get("abbreviation") or ""
    return {
        stream_header_name(stream.get("name", ""), code),
        f"📌-{code}・informations",
        f"🗓️-{code}・emploi-du-temps",
        f"📝-{code}・examens",
        *{_subject_channel_name(code, subject) for subject in stream.get("subjects", [])},
    }


def _stream_in_category(category: discord.CategoryChannel, stream_code: str) -> bool:
    prefixes = _stream_channel_prefixes(stream_code)
    return any(
        (channel.name.startswith(HEADER_PREFIX) and channel.name.endswith(f"・{stream_code}"))
        or any(channel.name.startswith(prefix) for prefix in prefixes)
        for channel in category.channels
    )


def _validate_capacity(self: ServerBuilder, selected: dict) -> None:
    projected_missing = 0
    for level in selected.get("levels", []):
        for stream in level.get("streams", []):
            stream_count = _stream_channel_count(level, stream)
            if stream_count > 50:
                code = stream.get("abbreviation", stream.get("name", "stream"))
                raise ValueError(f"La filière `{code}` nécessite {stream_count} salons, au-delà de la limite de 50 par catégorie.")

            code = stream.get("abbreviation") or ""
            existing = 0
            for category in self._level_categories(level["name"]):
                if _stream_in_category(category, code):
                    existing = len(
                        [
                            channel for channel in category.channels
                            if any(channel.name.startswith(prefix) for prefix in _stream_channel_prefixes(code))
                            or (channel.name.startswith(HEADER_PREFIX) and channel.name.endswith(f"・{code}"))
                        ]
                    )
                    break
            projected_missing += max(0, stream_count - existing)

    current_total = len(self.guild.channels)
    if current_total + projected_missing + len(selected.get("levels", [])) + 10 > 500:
        projected_total = current_total + projected_missing + len(selected.get("levels", [])) + 10
        raise ValueError(f"La construction dépasserait la limite Discord de 500 salons ({projected_total}).")


_original_build_level = ServerBuilder._build_level
_original_category_for_stream = ServerBuilder._category_for_stream


async def _category_for_stream_with_headers(self: ServerBuilder, level_name: str, stream_code: str, stream_channel_count: int):
    """Reserve one extra slot for the read-only stream title channel."""
    return await _original_category_for_stream(self, level_name, stream_code, stream_channel_count + 1)


async def _build_level_with_headers(self: ServerBuilder, level, roles, voice_category):
    await _original_build_level(self, level, roles, voice_category)

    level_name = level["name"]
    categories = self._level_categories(level_name)
    for stream in level.get("streams", []):
        stream_name = stream["name"]
        stream_code = stream.get("abbreviation") or get_stream_abbreviation(level_name, stream_name)
        target_category = next(
            (
                category for category in categories
                if _stream_in_category(category, stream_code)
            ),
            None,
        )
        if target_category is None:
            continue

        roles_needed = {
            role_name: discord.utils.get(self.guild.roles, name=role_name)
            for role_name in (ROLE_ADMIN, ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, ROLE_STUDENT)
        }
        teacher_stream_role = discord.utils.get(
            self.guild.roles, name=f"Filière - {stream_code}"
        )
        student_stream_role = discord.utils.get(
            self.guild.roles, name=f"Élèves - {stream_code}"
        )
        if any(role is None for role in roles_needed.values()) or teacher_stream_role is None or student_stream_role is None:
            continue

        overwrites = stream_header_overwrites(
            self.guild.default_role,
            roles_needed[ROLE_ADMIN],
            roles_needed[ROLE_PROFESSOR],
            roles_needed[ROLE_PROFESSOR_FEMALE],
            roles_needed[ROLE_STUDENT],
            teacher_stream_role,
            student_stream_role,
        )
        header = await self._get_or_create_text(
            target_category,
            stream_header_name(stream_name, stream_code),
            topic=f"{stream_code} — {stream_name}",
            overwrites=overwrites,
        )

        stream_channels = [
            channel for channel in target_category.channels
            if channel.id != header.id and any(
                channel.name.startswith(prefix) for prefix in _stream_channel_prefixes(stream_code)
            )
        ]
        if stream_channels:
            first_position = min(channel.position for channel in stream_channels)
            if header.position != first_position:
                await header.edit(position=first_position, reason="School manager stream title placement")


ServerBuilder._stream_channel_count = staticmethod(_stream_channel_count)
ServerBuilder._planned_channel_names_for_stream = staticmethod(_planned_channel_names_for_stream)
ServerBuilder._stream_in_category = staticmethod(_stream_in_category)
ServerBuilder._validate_capacity = _validate_capacity
ServerBuilder._category_for_stream = _category_for_stream_with_headers
ServerBuilder._build_level = _build_level_with_headers


def install() -> None:
    """Compatibility entry point used by the bot startup."""


install()

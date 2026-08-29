"""Centralized permission helpers for the scalable school server structure."""

from __future__ import annotations

import discord


ROLE_ADMIN = "Administration"
ROLE_PROFESSOR = "Professeur"
ROLE_STUDENT = "Élève"
STREAM_ROLE_PREFIX = "Filière - "


def administrator_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        manage_channels=True,
        manage_permissions=True,
        manage_messages=True,
        manage_threads=True,
        connect=True,
        speak=True,
        stream=True,
    )


def professor_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        manage_messages=True,
        manage_threads=True,
        create_public_threads=True,
        create_private_threads=True,
        send_messages_in_threads=True,
        connect=True,
        speak=True,
        stream=True,
    )


def student_overwrite(*, can_send: bool = True) -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=can_send,
        read_message_history=True,
        create_public_threads=True,
        send_messages_in_threads=True,
        connect=True,
        speak=True,
    )


def hidden_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(view_channel=False)


def general_area_overwrites(
    everyone: discord.Role,
    admin_role: discord.Role,
    professor_role: discord.Role,
    student_role: discord.Role,
) -> dict[discord.Role, discord.PermissionOverwrite]:
    return {
        everyone: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
        student_role: student_overwrite(can_send=False),
        professor_role: professor_overwrite(),
        admin_role: administrator_overwrite(),
    }


def teacher_area_overwrites(
    everyone: discord.Role,
    admin_role: discord.Role,
    professor_role: discord.Role,
    student_role: discord.Role,
) -> dict[discord.Role, discord.PermissionOverwrite]:
    return {
        everyone: hidden_overwrite(),
        student_role: hidden_overwrite(),
        professor_role: professor_overwrite(),
        admin_role: administrator_overwrite(),
    }


def stream_area_overwrites(
    everyone: discord.Role,
    admin_role: discord.Role,
    professor_role: discord.Role,
    student_role: discord.Role,
    stream_role: discord.Role,
) -> dict[discord.Role, discord.PermissionOverwrite]:
    """Allow only the selected stream role, professors, and administrators into a stream area."""
    return {
        everyone: hidden_overwrite(),
        student_role: hidden_overwrite(),
        professor_role: professor_overwrite(),
        admin_role: administrator_overwrite(),
        stream_role: student_overwrite(can_send=True),
    }


def stream_announcement_overwrites(
    everyone: discord.Role,
    admin_role: discord.Role,
    professor_role: discord.Role,
    student_role: discord.Role,
    stream_role: discord.Role,
) -> dict[discord.Role, discord.PermissionOverwrite]:
    overwrites = stream_area_overwrites(everyone, admin_role, professor_role, student_role, stream_role)
    overwrites[stream_role] = student_overwrite(can_send=False)
    return overwrites


def public_voice_overwrites(
    everyone: discord.Role,
    admin_role: discord.Role,
    professor_role: discord.Role,
    student_role: discord.Role,
    stream_role: discord.Role,
) -> dict[discord.Role, discord.PermissionOverwrite]:
    return {
        everyone: hidden_overwrite(),
        student_role: hidden_overwrite(),
        professor_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, stream=True),
        admin_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, stream=True),
        stream_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, stream=True),
    }

"""Centralized permission helpers for the scalable school server structure."""

from __future__ import annotations

import discord


ROLE_ADMIN = "Administration"
ROLE_PROFESSOR = "Professeur"
ROLE_STUDENT = "Élève"
STREAM_ROLE_PREFIX = "Filière - "
SUBJECT_TEACHER_ROLE_PREFIX = "Professeur Matière - "


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


def professor_view_overwrite() -> discord.PermissionOverwrite:
    """Teachers can view educational areas but cannot write by default."""
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=False,
        read_message_history=True,
        create_public_threads=False,
        create_private_threads=False,
        send_messages_in_threads=False,
        connect=True,
        speak=True,
        stream=True,
        manage_channels=False,
        manage_permissions=False,
        manage_messages=False,
        manage_threads=False,
    )


def professor_subject_overwrite() -> discord.PermissionOverwrite:
    """A subject-assigned teacher may post in the subject channel, but cannot manage it."""
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        create_public_threads=True,
        create_private_threads=True,
        send_messages_in_threads=True,
        manage_channels=False,
        manage_permissions=False,
        manage_messages=False,
        manage_threads=False,
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
        professor_role: professor_view_overwrite(),
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
        professor_role: professor_view_overwrite(),
        admin_role: administrator_overwrite(),
    }


def stream_area_overwrites(
    everyone: discord.Role,
    admin_role: discord.Role,
    professor_role: discord.Role,
    student_role: discord.Role,
    stream_role: discord.Role,
) -> dict[discord.Role, discord.PermissionOverwrite]:
    """Allow stream students and all teachers to view the whole stream area."""
    return {
        everyone: hidden_overwrite(),
        student_role: hidden_overwrite(),
        professor_role: professor_view_overwrite(),
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


def subject_channel_overwrites(
    everyone: discord.Role,
    admin_role: discord.Role,
    professor_role: discord.Role,
    stream_role: discord.Role,
    subject_teacher_role: discord.Role,
) -> dict[discord.Role, discord.PermissionOverwrite]:
    """Students may participate; all teachers may view; assigned subject teachers may post."""
    return {
        everyone: hidden_overwrite(),
        admin_role: administrator_overwrite(),
        professor_role: professor_view_overwrite(),
        stream_role: student_overwrite(can_send=True),
        subject_teacher_role: professor_subject_overwrite(),
    }


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

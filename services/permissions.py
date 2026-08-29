"""Centralized permission helpers for the scalable school server structure."""

from __future__ import annotations

import discord


ROLE_ADMIN = "Administration"
ROLE_PROFESSOR = "Prof"
ROLE_PROFESSOR_FEMALE = "Prof (F)"
ROLE_STUDENT = "Élève"
STREAM_ROLE_PREFIX = "Filière - "


def administrator_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True,
        manage_channels=True, manage_permissions=True, manage_messages=True,
        manage_threads=True, connect=True, speak=True, stream=True,
    )


def professor_general_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True,
        create_public_threads=True, create_private_threads=True,
        send_messages_in_threads=True, manage_channels=False,
        manage_permissions=False, manage_messages=False, manage_threads=False,
        connect=True, speak=True, stream=True,
    )


def professor_subject_view_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True, send_messages=False, read_message_history=True,
        create_public_threads=False, create_private_threads=False,
        send_messages_in_threads=False, manage_channels=False,
        manage_permissions=False, manage_messages=False, manage_threads=False,
    )


def professor_subject_member_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True,
        create_public_threads=True, create_private_threads=True,
        send_messages_in_threads=True, manage_channels=False,
        manage_permissions=False, manage_messages=False, manage_threads=False,
    )


def student_overwrite(*, can_send: bool = True) -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True, send_messages=can_send, read_message_history=True,
        create_public_threads=True, send_messages_in_threads=True,
        connect=True, speak=True,
    )


def hidden_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(view_channel=False)


def teacher_role_overwrites(professor_role, female_professor_role, permission):
    return {professor_role: permission, female_professor_role: permission}


def general_area_overwrites(everyone, admin_role, professor_role, female_professor_role, student_role):
    return {
        everyone: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
        student_role: student_overwrite(can_send=False),
        professor_role: professor_general_overwrite(),
        female_professor_role: professor_general_overwrite(),
        admin_role: administrator_overwrite(),
    }


def teacher_area_overwrites(everyone, admin_role, professor_role, female_professor_role, student_role):
    return {
        everyone: hidden_overwrite(),
        student_role: hidden_overwrite(),
        professor_role: professor_general_overwrite(),
        female_professor_role: professor_general_overwrite(),
        admin_role: administrator_overwrite(),
    }


def stream_area_overwrites(everyone, admin_role, professor_role, female_professor_role, student_role, stream_role):
    return {
        everyone: hidden_overwrite(),
        student_role: hidden_overwrite(),
        professor_role: professor_subject_view_overwrite(),
        female_professor_role: professor_subject_view_overwrite(),
        admin_role: administrator_overwrite(),
        stream_role: student_overwrite(can_send=True),
    }


def stream_announcement_overwrites(everyone, admin_role, professor_role, female_professor_role, student_role, stream_role):
    overwrites = stream_area_overwrites(
        everyone, admin_role, professor_role, female_professor_role, student_role, stream_role
    )
    overwrites[professor_role] = professor_general_overwrite()
    overwrites[female_professor_role] = professor_general_overwrite()
    overwrites[stream_role] = student_overwrite(can_send=False)
    return overwrites


def subject_channel_overwrites(everyone, admin_role, professor_role, female_professor_role, stream_role):
    return {
        everyone: hidden_overwrite(),
        admin_role: administrator_overwrite(),
        professor_role: professor_subject_view_overwrite(),
        female_professor_role: professor_subject_view_overwrite(),
        stream_role: student_overwrite(can_send=True),
    }


def public_voice_overwrites(everyone, admin_role, professor_role, female_professor_role, student_role, stream_role):
    voice = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, stream=True)
    return {
        everyone: hidden_overwrite(), student_role: hidden_overwrite(),
        professor_role: voice, female_professor_role: voice,
        admin_role: voice, stream_role: voice,
    }

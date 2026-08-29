"""Centralized permission helpers for the school Discord structure."""

from __future__ import annotations

import discord
from discord import app_commands

ROLE_ADMIN = "Administration"
ROLE_PROFESSOR = "Prof"
ROLE_PROFESSOR_FEMALE = "Prof (F)"
ROLE_STUDENT = "Élève"
STREAM_ROLE_PREFIX = "Filière - "


def management_check() -> app_commands.check:
    """Allow the guild owner or members with the Administration role."""
    async def predicate(interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if guild is None:
            return False
        if interaction.user.id == guild.owner_id:
            return True
        admin_role = discord.utils.get(guild.roles, name=ROLE_ADMIN)
        return admin_role is not None and admin_role in getattr(interaction.user, "roles", [])
    return app_commands.check(predicate)


def owner_only_check() -> app_commands.check:
    """Allow only the Discord server owner."""
    async def predicate(interaction: discord.Interaction) -> bool:
        return interaction.guild is not None and interaction.user.id == interaction.guild.owner_id
    return app_commands.check(predicate)


def administrator_overwrite() -> discord.PermissionOverwrite:
    """Administration can manage the school structure, but is not a Discord super-admin."""
    return discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True,
        manage_channels=True, manage_permissions=True, manage_roles=True,
        manage_messages=True, manage_threads=True, connect=True, speak=True, stream=True,
    )


def professor_general_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True,
        create_public_threads=True, create_private_threads=True,
        send_messages_in_threads=True, manage_channels=False,
        manage_permissions=False, manage_messages=False, manage_threads=False,
        manage_roles=False, connect=True, speak=True, stream=True,
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

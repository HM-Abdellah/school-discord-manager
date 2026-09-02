"""Centralized authorization and Discord permission helpers."""

from __future__ import annotations

import discord
from discord import app_commands

from services.storage import get_guild_config

ROLE_ADMIN = "Administration"
ROLE_PROFESSOR = "Prof"
ROLE_PROFESSOR_FEMALE = "Prof (F)"
ROLE_STUDENT = "Élève"
STREAM_ROLE_PREFIX = "Filière - "
STUDENT_STREAM_ROLE_PREFIX = "Élèves - "
SUBJECT_ROLE_PREFIX = "Matière - "

CHANNEL_MANAGEMENT_COMMANDS = {"setup", "build", "addstream", "removestream"}
ROLE_MANAGEMENT_COMMANDS = {"setup", "build", "addstream", "removestream", "assignstudent", "assignteacher", "assignsubjectteachers"}
RESET_COMMANDS = {"resetserver"}


def _bot_member(guild: discord.Guild) -> discord.Member | None:
    return guild.me


def _hierarchy_error(guild: discord.Guild) -> str | None:
    bot = _bot_member(guild)
    if bot is None:
        return "❌ Impossible de vérifier la hiérarchie du rôle du bot."
    if bot.top_role == guild.default_role:
        return "❌ Le bot n'a pas de rôle exploitable. Place son rôle au-dessus des rôles School Manager."
    managed_prefixes = (STREAM_ROLE_PREFIX, STUDENT_STREAM_ROLE_PREFIX, SUBJECT_ROLE_PREFIX)
    for role in guild.roles:
        if role.is_default() or role.managed:
            continue
        if role.name in {ROLE_ADMIN, ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, ROLE_STUDENT} or role.name.startswith(managed_prefixes):
            if role >= bot.top_role:
                return f"❌ Le rôle du bot est trop bas. Place-le au-dessus de `{role.name}`."
    return None


def _management_role(guild: discord.Guild) -> discord.Role | None:
    config = get_guild_config(guild.id) or {}
    role_id = config.get("management_role_id")
    if not isinstance(role_id, int):
        return None
    role = guild.get_role(role_id)
    return role if role is not None and role.name == ROLE_ADMIN and not role.managed else None


def _preflight_message(interaction: discord.Interaction, *, needs_channels: bool = False, needs_roles: bool = False) -> str | None:
    guild = interaction.guild
    if guild is None:
        return "❌ Serveur requis."
    bot = _bot_member(guild)
    if bot is None:
        return "❌ Impossible de trouver le bot dans ce serveur."
    permissions = bot.guild_permissions
    if needs_channels and not permissions.manage_channels:
        return "❌ Le bot doit avoir **Manage Channels**."
    if needs_roles and not permissions.manage_roles:
        return "❌ Le bot doit avoir **Manage Roles**."
    if needs_roles:
        return _hierarchy_error(guild)
    return None


def management_check() -> app_commands.check:
    """Allow the guild owner or the exact Administration role recorded by the bot."""
    async def predicate(interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if guild is None:
            return False
        authorized = interaction.user.id == guild.owner_id
        if not authorized:
            role = _management_role(guild)
            authorized = role is not None and role in getattr(interaction.user, "roles", [])
        if not authorized:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Outil réservé au propriétaire du serveur ou au rôle Administration configuré.", ephemeral=True)
            return False
        command_name = interaction.command.name if interaction.command else ""
        if command_name in RESET_COMMANDS or command_name in CHANNEL_MANAGEMENT_COMMANDS:
            message = _preflight_message(interaction, needs_channels=True, needs_roles=True)
        elif command_name in ROLE_MANAGEMENT_COMMANDS:
            message = _preflight_message(interaction, needs_roles=True)
        else:
            message = None
        if message:
            if not interaction.response.is_done():
                await interaction.response.send_message(message, ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)


def owner_only_check() -> app_commands.check:
    async def predicate(interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if guild is None or interaction.user.id != guild.owner_id:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Cette commande est réservée au propriétaire du serveur.", ephemeral=True)
            return False
        message = _preflight_message(interaction, needs_channels=True, needs_roles=True)
        if message:
            if not interaction.response.is_done():
                await interaction.response.send_message(message, ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)


def administrator_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, manage_permissions=True, manage_messages=True, manage_threads=True, connect=True, speak=True, stream=True)


def professor_general_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, create_public_threads=True, create_private_threads=True, send_messages_in_threads=True, manage_channels=False, manage_permissions=False, manage_messages=False, manage_threads=False, manage_roles=False, connect=True, speak=True, stream=True)


def professor_subject_view_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, create_public_threads=False, create_private_threads=False, send_messages_in_threads=False, manage_channels=False, manage_permissions=False, manage_messages=False, manage_threads=False)


def professor_subject_member_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, create_public_threads=True, create_private_threads=True, send_messages_in_threads=True, manage_channels=False, manage_permissions=False, manage_messages=False, manage_threads=False)


def student_overwrite(*, can_send: bool = True) -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(view_channel=True, send_messages=can_send, read_message_history=True, create_public_threads=can_send, send_messages_in_threads=can_send, connect=True, speak=True)


def hidden_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(view_channel=False)


def stream_area_overwrites(everyone, admin_role, professor_role, female_professor_role, student_role, teacher_stream_role, student_stream_role):
    return {everyone: hidden_overwrite(), student_role: hidden_overwrite(), professor_role: professor_subject_view_overwrite(), female_professor_role: professor_subject_view_overwrite(), admin_role: administrator_overwrite(), teacher_stream_role: professor_subject_view_overwrite(), student_stream_role: student_overwrite(can_send=True)}


def stream_announcement_overwrites(everyone, admin_role, professor_role, female_professor_role, student_role, teacher_stream_role, student_stream_role):
    overwrites = stream_area_overwrites(everyone, admin_role, professor_role, female_professor_role, student_role, teacher_stream_role, student_stream_role)
    overwrites[professor_role] = professor_general_overwrite()
    overwrites[female_professor_role] = professor_general_overwrite()
    overwrites[teacher_stream_role] = professor_general_overwrite()
    overwrites[student_stream_role] = student_overwrite(can_send=False)
    return overwrites


def general_area_overwrites(everyone, admin_role, professor_role, female_professor_role, student_role):
    return {everyone: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True), student_role: student_overwrite(can_send=False), professor_role: professor_general_overwrite(), female_professor_role: professor_general_overwrite(), admin_role: administrator_overwrite()}


def teacher_area_overwrites(everyone, admin_role, professor_role, female_professor_role, student_role):
    return {everyone: hidden_overwrite(), student_role: hidden_overwrite(), professor_role: professor_general_overwrite(), female_professor_role: professor_general_overwrite(), admin_role: administrator_overwrite()}


def subject_channel_overwrites(everyone, admin_role, professor_role, female_professor_role, teacher_stream_role, student_stream_role, subject_role=None):
    overwrites = {
        everyone: hidden_overwrite(),
        admin_role: administrator_overwrite(),
        professor_role: professor_subject_view_overwrite(),
        female_professor_role: professor_subject_view_overwrite(),
        teacher_stream_role: professor_subject_view_overwrite(),
        student_stream_role: student_overwrite(can_send=True),
    }
    if subject_role is not None:
        overwrites[subject_role] = professor_subject_member_overwrite()
    return overwrites


def public_voice_overwrites(everyone, admin_role, professor_role, female_professor_role, student_role, teacher_stream_role, student_stream_role):
    voice = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, stream=True)
    return {everyone: hidden_overwrite(), student_role: hidden_overwrite(), professor_role: voice, female_professor_role: voice, admin_role: voice, teacher_stream_role: voice, student_stream_role: voice}

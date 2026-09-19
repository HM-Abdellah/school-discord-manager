"""Compatibility command module for the full teacher assignment workflow."""

from __future__ import annotations

import unicodedata

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import get_levels, get_stream_abbreviation, get_stream_subjects, get_streams, get_subject_display_name, get_subject_internal_code
from services.audit import record_event
from services.role_transactions import restore_role_presence, snapshot_role_presence
from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_PROFESSOR_FEMALE,
    ROLE_STUDENT,
    SUBJECT_ROLE_PREFIX,
    STREAM_ROLE_PREFIX,
    STUDENT_STREAM_ROLE_PREFIX,
    administrator_overwrite,
    get_managed_role,
    hidden_overwrite,
    management_check,
    professor_subject_member_overwrite,
    professor_subject_view_overwrite,
    student_overwrite,
)
from services.server_builder import _subject_channel_name, _subject_role_name, _stream_role_name
from services.storage import get_guild_config, save_guild_config

OWNED_COMMANDS = {"assignteacherfull"}


def _contains(value: str, current: str) -> bool:
    return current.casefold() in value.casefold()


def _normalize_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().replace("\ufe0f", "")


async def level_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=level, value=level) for level in get_levels() if _contains(level, current)][:25]


async def stream_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    if level not in get_levels():
        return []
    return [app_commands.Choice(name=stream, value=stream) for stream in get_streams(level) if _contains(stream, current)][:25]


async def teacher_subject_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    stream = str(getattr(interaction.namespace, "stream", ""))
    if level not in get_levels():
        return []
    preferred = {subject.casefold() for subject in get_stream_subjects(level, stream)} if stream in get_streams(level) else set()
    subjects: list[str] = []
    seen: set[str] = set()
    config = get_guild_config(interaction.guild.id) if interaction.guild else None
    configured_levels = config.get("levels", []) if isinstance(config, dict) else []
    for configured_level in configured_levels:
        if not isinstance(configured_level, dict):
            continue
        level_name = configured_level.get("name")
        if not isinstance(level_name, str) or level_name not in get_levels():
            continue
        for configured_stream in configured_level.get("streams", []) or []:
            if not isinstance(configured_stream, dict) or not isinstance(configured_stream.get("name"), str):
                continue
            stream_name = configured_stream["name"]
            try:
                candidates = get_stream_subjects(level_name, stream_name)
            except Exception:
                continue
            for subject in candidates:
                key = subject.casefold()
                if key not in seen:
                    seen.add(key)
                    subjects.append(subject)
    if not subjects:
        for candidate_stream in get_streams(level):
            for subject in get_stream_subjects(level, candidate_stream):
                key = subject.casefold()
                if key not in seen:
                    seen.add(key)
                    subjects.append(subject)
    subjects.sort(key=lambda item: (item.casefold() not in preferred, get_subject_display_name(item).casefold()))
    return [
        app_commands.Choice(name=get_subject_display_name(subject)[:100], value=subject)
        for subject in subjects
        if _contains(get_subject_display_name(subject), current) or _contains(subject, current)
    ][:25]


def _global_subject_role_name(subject: str) -> str:
    return f"{SUBJECT_ROLE_PREFIX}{get_subject_display_name(subject)}"[:100]


def _configured_streams(guild: discord.Guild) -> list[tuple[str, str, str]]:
    config = get_guild_config(guild.id) or {}
    result: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for level in config.get("levels", []):
        if not isinstance(level, dict) or not isinstance(level.get("name"), str):
            continue
        level_name = level["name"]
        for stream in level.get("streams", []) or []:
            if not isinstance(stream, dict) or not isinstance(stream.get("name"), str):
                continue
            stream_name = stream["name"]
            code = str(stream.get("abbreviation") or get_stream_abbreviation(level_name, stream_name))
            if code not in seen:
                seen.add(code)
                result.append((level_name, stream_name, code))
    return result


async def _get_or_create_global_subject_role(guild: discord.Guild, config: dict, subject: str) -> discord.Role:
    role_name = _global_subject_role_name(subject)
    role = get_managed_role(guild, role_name)
    if role is not None:
        config.setdefault("managed", {}).setdefault("roles", {})[role_name] = role.id
        return role

    collision = discord.utils.get(guild.roles, name=role_name)
    if collision is not None:
        raise RuntimeError(f"Unmanaged role collision for `{role_name}`.")

    role = await guild.create_role(
        name=role_name,
        permissions=discord.Permissions.none(),
        colour=discord.Colour.dark_blue(),
        mentionable=False,
        reason="School Manager global subject role",
    )
    config.setdefault("managed", {}).setdefault("roles", {})[role_name] = role.id
    return role


async def _migrate_legacy_subject_roles(guild: discord.Guild, member: discord.Member, config: dict) -> list[str]:
    legacy_map: dict[str, str] = {}
    for level_name, stream_name, code in _configured_streams(guild):
        for subject in get_stream_subjects(level_name, stream_name):
            legacy_map[f"{SUBJECT_ROLE_PREFIX}{code} - {get_subject_internal_code(subject)}"] = subject
    old_roles = [role for role in member.roles if not role.managed and role.name in legacy_map]
    if not old_roles:
        return []
    migrated: list[str] = []
    new_roles: list[discord.Role] = []
    for old_role in old_roles:
        subject = legacy_map[old_role.name]
        new_role = await _get_or_create_global_subject_role(guild, config, subject)
        if new_role not in new_roles:
            new_roles.append(new_role)
        if subject not in migrated:
            migrated.append(subject)
        for channel in guild.channels:
            old_overwrite = getattr(channel, "overwrites", {}).get(old_role)
            if old_overwrite is None:
                continue
            try:
                await channel.set_permissions(new_role, overwrite=old_overwrite, reason="School Manager subject role migration")
            except (discord.Forbidden, discord.HTTPException):
                continue
    try:
        if new_roles:
            await member.add_roles(*new_roles, reason="School Manager migrate legacy subject roles")
        await member.remove_roles(*old_roles, reason="School Manager remove legacy subject roles")
    except (discord.Forbidden, discord.HTTPException):
        return []
    return migrated


class CommandFixes(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="assignteacherfull", description="Affecter un professeur à une filière et à une ou plusieurs matières.")
    @app_commands.describe(teacher="Professeur", gender="Type de rôle professeur", level="Niveau scolaire", stream="Filière scolaire", subjects="Matière(s), sélectionne une suggestion ou sépare par des virgules")
    @app_commands.choices(gender=[app_commands.Choice(name="Prof", value="male"), app_commands.Choice(name="Prof (F)", value="female")])
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete, subjects=teacher_subject_autocomplete)
    @management_check()
    async def assign_teacher_full(self, interaction: discord.Interaction, teacher: discord.Member, gender: app_commands.Choice[str], level: str, stream: str, subjects: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.followup.send("❌ Niveau ou filière invalide.", ephemeral=True)
            return
        if any(role.name == ROLE_STUDENT or role.name.startswith(STUDENT_STREAM_ROLE_PREFIX) for role in teacher.roles if not role.managed):
            await interaction.followup.send("❌ Cet utilisateur possède encore un rôle **Élève**. Retire d'abord son rôle élève.", ephemeral=True)
            return
        requested = {item.strip().casefold() for item in subjects.split(",") if item.strip()}
        selected = [subject for subject in get_stream_subjects(level, stream) if subject.casefold() in requested or get_subject_display_name(subject).casefold() in requested]
        if not selected:
            await interaction.followup.send("❌ Aucune matière reconnue pour cette filière. Utilise les suggestions.", ephemeral=True)
            return
        stream_code = get_stream_abbreviation(level, stream)
        stream_role = get_managed_role(guild, f"{STREAM_ROLE_PREFIX}{stream_code}")
        desired_role = get_managed_role(guild, ROLE_PROFESSOR_FEMALE if gender.value == "female" else ROLE_PROFESSOR)
        other_role = get_managed_role(guild, ROLE_PROFESSOR if gender.value == "female" else ROLE_PROFESSOR_FEMALE)
        if stream_role is None or desired_role is None:
            await interaction.followup.send("❌ Les rôles scolaires requis pour cette filière n'existent pas. Vérifie `/build`.", ephemeral=True)
            return
        config = get_guild_config(guild.id) or {}
        working_config = __import__("copy").deepcopy(config)
        tracked_roles = [role for role in (desired_role, other_role, stream_role) if role is not None]
        original_presence = snapshot_role_presence(teacher, tracked_roles)
        created_subject_roles: list[discord.Role] = []
        subject_roles: list[discord.Role] = []
        try:
            migrated = await _migrate_legacy_subject_roles(guild, teacher, working_config)
            for subject in selected:
                before = get_managed_role(guild, _global_subject_role_name(subject))
                role = await _get_or_create_global_subject_role(guild, working_config, subject)
                if before is None:
                    created_subject_roles.append(role)
                subject_roles.append(role)
            tracked_roles.extend(subject_roles)
            original_presence = snapshot_role_presence(teacher, tracked_roles)
            if desired_role not in teacher.roles:
                await teacher.add_roles(desired_role, reason="School Manager full teacher assignment")
            await teacher.add_roles(stream_role, *subject_roles, reason="School Manager full teacher assignment")
            if other_role is not None and other_role in teacher.roles:
                await teacher.remove_roles(other_role, reason="Teacher role normalization")
            save_guild_config(guild.id, working_config)
            config = working_config
        except (discord.Forbidden, discord.HTTPException, OSError, RuntimeError) as exc:
            try:
                await restore_role_presence(
                    teacher, tracked_roles, original_presence, reason="School Manager full teacher assignment rollback"
                )
            except discord.HTTPException:
                pass
            for created_role in reversed(created_subject_roles):
                try:
                    await created_role.delete(reason="School Manager full teacher assignment rollback")
                except discord.HTTPException:
                    pass
            if isinstance(exc, discord.Forbidden):
                message = "❌ Impossible d'attribuer les rôles. Vérifie la hiérarchie du bot."
            elif isinstance(exc, discord.HTTPException):
                message = f"❌ Discord API : `{exc}`"
            elif isinstance(exc, OSError):
                message = f"❌ Stockage local : `{exc}`"
            else:
                message = f"❌ Opération refusée : `{exc}`"
            await interaction.followup.send(message, ephemeral=True)
            return
        subject_names = ", ".join(get_subject_display_name(subject) for subject in selected)
        migration_text = ""
        if migrated:
            migration_text = "\n♻️ Anciens rôles matière migrés : " + ", ".join(get_subject_display_name(subject) for subject in migrated)
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "assignteacherfull", teacher.display_name, f"{stream_code}: {subject_names}")
        await interaction.followup.send(
            f"✅ {teacher.mention} est affecté à **{stream_code}** pour : {subject_names}.\n"
            f"Rôles : `Filière - {stream_code}` + "
            + ", ".join(f"`{_global_subject_role_name(subject)}`" for subject in selected)
            + migration_text,
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CommandFixes(bot))

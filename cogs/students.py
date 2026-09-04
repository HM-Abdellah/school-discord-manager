"""Student management commands for stream-based school servers."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import get_levels, get_stream_abbreviation, get_streams
from services.audit import record_event
from services.permissions import ROLE_ADMIN, ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, ROLE_STUDENT, STUDENT_STREAM_ROLE_PREFIX, STREAM_ROLE_PREFIX, SUBJECT_ROLE_PREFIX, get_managed_role, management_check, student_view_overwrite
from services.storage import enroll_student_record, get_active_academic_year, get_guild_config, get_student, get_student_history, mark_student_left


def _contains(value: str, current: str) -> bool:
    return current.casefold() in value.casefold()


async def level_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=level, value=level) for level in get_levels() if _contains(level, current)][:25]


async def stream_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    if level not in get_levels():
        return []
    return [app_commands.Choice(name=stream, value=stream) for stream in get_streams(level) if _contains(stream, current)][:25]


def _is_canonical_school_role(name: str) -> bool:
    return name in {ROLE_ADMIN, ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, ROLE_STUDENT} or name.startswith((STREAM_ROLE_PREFIX, STUDENT_STREAM_ROLE_PREFIX, SUBJECT_ROLE_PREFIX))


def _school_role_ids(guild: discord.Guild) -> set[int]:
    """Return configured IDs plus exact canonical legacy School Manager role IDs."""
    config = get_guild_config(guild.id) or {}
    managed = config.get("managed", {})
    roles = managed.get("roles", {}) if isinstance(managed, dict) else {}
    ids = {value for value in roles.values() if isinstance(value, int) and value > 0} if isinstance(roles, dict) else set()
    ids.update(role.id for role in getattr(guild, "roles", []) if not role.managed and _is_canonical_school_role(role.name))
    return ids


def _school_roles(member: discord.Member, guild: discord.Guild) -> list[discord.Role]:
    managed_ids = _school_role_ids(guild)
    return [role for role in member.roles if role.id in managed_ids]


def _student_assignment_roles(member: discord.Member, guild: discord.Guild) -> list[discord.Role]:
    """Return only managed student roles, never admin/professor roles."""
    managed_ids = _school_role_ids(guild)
    roles = []
    for role in member.roles:
        if role.id not in managed_ids:
            continue
        if role.name == ROLE_STUDENT or role.name.startswith(STUDENT_STREAM_ROLE_PREFIX):
            roles.append(role)
    return roles


async def _restore_school_roles(member: discord.Member, guild: discord.Guild, original_roles: list[discord.Role]) -> None:
    managed_ids = _school_role_ids(guild)
    current = [role for role in member.roles if role.id in managed_ids]
    desired = [role for role in original_roles if role.id in managed_ids]
    to_remove = [role for role in current if role not in desired and not role.managed]
    to_add = [role for role in desired if role not in current and not role.managed]
    if to_remove:
        await member.remove_roles(*to_remove, reason="School Manager rollback")
    if to_add:
        await member.add_roles(*to_add, reason="School Manager rollback")


async def _grant_student_global_stream_view(guild: discord.Guild, student_role: discord.Role) -> None:
    """Make the base student role read-only in every configured stream channel."""
    config = get_guild_config(guild.id) or {}
    codes: set[str] = set()
    for level in config.get("levels", []):
        if not isinstance(level, dict):
            continue
        level_name = level.get("name")
        if not isinstance(level_name, str):
            continue
        for stream in level.get("streams", []) or []:
            if not isinstance(stream, dict) or not isinstance(stream.get("name"), str):
                continue
            codes.add(str(stream.get("abbreviation") or get_stream_abbreviation(level_name, stream["name"])))
    if not codes:
        return
    try:
        channels = await guild.fetch_channels()
    except (discord.Forbidden, discord.HTTPException):
        channels = guild.channels
    view = student_view_overwrite()
    for channel in channels:
        name = getattr(channel, "name", "")
        is_stream_text = any(name.startswith(prefix) for code in codes for prefix in (f"📌-{code}・", f"🗓️-{code}・", f"📝-{code}・", f"📚-{code}・"))
        is_stream_voice = any(name == f"🔊-{code}-à-distance" for code in codes)
        if not (is_stream_text or is_stream_voice):
            continue
        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
            continue
        overwrites = dict(channel.overwrites)
        overwrites[student_role] = view
        await channel.edit(overwrites=overwrites, reason="School Manager student global stream visibility")


class StudentCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="assignstudent", description="Affecter un élève à une filière.")
    @app_commands.describe(student="Élève", level="Niveau scolaire", stream="Filière scolaire")
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @management_check()
    async def assign_student(self, interaction: discord.Interaction, student: discord.Member, level: str, stream: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.response.send_message("❌ Niveau ou filière invalide.", ephemeral=True)
            return
        student_role = get_managed_role(guild, ROLE_STUDENT)
        student_stream_role = get_managed_role(guild, f"{STUDENT_STREAM_ROLE_PREFIX}{get_stream_abbreviation(level, stream)}")
        if student_role is None or student_stream_role is None:
            await interaction.response.send_message("❌ Les rôles scolaires gérés ne sont pas prêts. Lance `/setup` puis construis le serveur.", ephemeral=True)
            return
        year = get_active_academic_year(guild.id)
        if year is None:
            await interaction.response.send_message("❌ Aucune année scolaire active.", ephemeral=True)
            return
        original_school_roles = _school_roles(student, guild)
        original_student_roles = _student_assignment_roles(student, guild)
        await interaction.response.defer(ephemeral=True)
        try:
            cleanup_roles = [role for role in original_student_roles if role != student_role and role != student_stream_role]
            if cleanup_roles:
                await student.remove_roles(*cleanup_roles, reason="Student role normalization")
            await student.add_roles(student_role, student_stream_role, reason="Student stream assignment")
            enroll_student_record(guild.id, student.id, student.display_name, int(year["id"]), level, stream)
            await _grant_student_global_stream_view(guild, student_role)
        except discord.Forbidden:
            try:
                await _restore_school_roles(student, guild, original_school_roles)
            except discord.HTTPException:
                pass
            await interaction.followup.send("❌ Vérifie que le rôle du bot est assez haut dans la hiérarchie.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            try:
                await _restore_school_roles(student, guild, original_school_roles)
            except discord.HTTPException:
                pass
            await interaction.followup.send(f"❌ Discord API : `{exc}`", ephemeral=True)
            return
        except Exception as exc:
            try:
                await _restore_school_roles(student, guild, original_school_roles)
            except discord.HTTPException:
                pass
            await interaction.followup.send(f"❌ Affectation annulée; les rôles Discord ont été restaurés si possible : `{type(exc).__name__}: {exc}`", ephemeral=True)
            return
        code = get_stream_abbreviation(level, stream)
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "assignstudent", student.display_name, f"{level}: {code}")
        await interaction.followup.send(f"✅ {student.mention} est maintenant dans **{code}** ({level}). Les autres filières restent visibles en lecture seule.", ephemeral=True)

    @app_commands.command(name="studenthistory", description="Voir l'historique scolaire d'un élève.")
    @app_commands.describe(student="Élève")
    @management_check()
    async def student_history(self, interaction: discord.Interaction, student: discord.Member) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        rows = get_student_history(interaction.guild.id, student.id)
        if not rows:
            await interaction.response.send_message("ℹ️ Aucun historique enregistré.", ephemeral=True)
            return
        lines = [f"## 📚 Historique de {student.display_name}", ""]
        for row in rows:
            lines.append(f"• **{row['academic_year']}** — {row['level_name']} / {row['stream_name']} — {row['start_date']} → {row['end_date'] or 'présent'} — `{row['status']}`")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="leave_school", description="Marquer un élève comme ayant quitté l'établissement.")
    @app_commands.describe(student="Élève")
    @management_check()
    async def leave_school(self, interaction: discord.Interaction, student: discord.Member) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        row = get_student(guild.id, student.id)
        if row is None:
            await interaction.response.send_message("❌ Élève non enregistré.", ephemeral=True)
            return
        original_school_roles = _school_roles(student, guild)
        student_assignment_roles = _student_assignment_roles(student, guild)
        await interaction.response.defer(ephemeral=True)
        try:
            if student_assignment_roles:
                await student.remove_roles(*student_assignment_roles, reason="Student left school")
            mark_student_left(guild.id, int(row["id"]))
        except discord.Forbidden:
            try:
                await _restore_school_roles(student, guild, original_school_roles)
            except discord.HTTPException:
                pass
            await interaction.followup.send("❌ Impossible de retirer les rôles. Vérifie la hiérarchie; l'historique n'a pas été modifié.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            try:
                await _restore_school_roles(student, guild, original_school_roles)
            except discord.HTTPException:
                pass
            await interaction.followup.send(f"❌ Discord API : `{exc}`. L'historique n'a pas été modifié.", ephemeral=True)
            return
        except Exception as exc:
            try:
                await _restore_school_roles(student, guild, original_school_roles)
            except discord.HTTPException:
                pass
            await interaction.followup.send(f"❌ Opération annulée; les rôles ont été restaurés si possible et l'historique n'a pas été modifié : `{type(exc).__name__}: {exc}`", ephemeral=True)
            return
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "leave_school", student.display_name, "Student marked left school")
        await interaction.followup.send(f"✅ {student.mention} est marqué **sorti de l'établissement**. Son historique est conservé.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StudentCommands(bot))

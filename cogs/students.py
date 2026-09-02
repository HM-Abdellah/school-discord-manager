"""Student management commands for stream-based school servers."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import get_levels, get_stream_abbreviation, get_streams
from services.audit import record_event
from services.permissions import ROLE_STUDENT, STUDENT_STREAM_ROLE_PREFIX, STREAM_ROLE_PREFIX, SUBJECT_ROLE_PREFIX, management_check
from services.storage import enroll_student_record, get_active_academic_year, get_student, get_student_history, mark_student_left


SCHOOL_ASSIGNMENT_PREFIXES = (STUDENT_STREAM_ROLE_PREFIX, STREAM_ROLE_PREFIX, SUBJECT_ROLE_PREFIX)


def _school_roles(member: discord.Member) -> list[discord.Role]:
    return [role for role in member.roles if role.name == ROLE_STUDENT or role.name.startswith(SCHOOL_ASSIGNMENT_PREFIXES)]


async def _restore_school_roles(member: discord.Member, original_roles: list[discord.Role]) -> None:
    current = _school_roles(member)
    desired = [role for role in original_roles if role.name == ROLE_STUDENT or role.name.startswith(SCHOOL_ASSIGNMENT_PREFIXES)]
    to_remove = [role for role in current if role not in desired and not role.managed]
    to_add = [role for role in desired if role not in current and not role.managed]
    if to_remove:
        await member.remove_roles(*to_remove, reason="School Manager rollback")
    if to_add:
        await member.add_roles(*to_add, reason="School Manager rollback")


class StudentCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="assignstudent", description="Affecter un élève à une filière.")
    @app_commands.describe(student="Élève", level="Niveau scolaire", stream="Filière scolaire")
    @management_check()
    async def assign_student(self, interaction: discord.Interaction, student: discord.Member, level: str, stream: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.response.send_message("❌ Niveau ou filière invalide.", ephemeral=True)
            return
        student_role = discord.utils.get(guild.roles, name=ROLE_STUDENT)
        student_stream_role = discord.utils.get(guild.roles, name=f"{STUDENT_STREAM_ROLE_PREFIX}{get_stream_abbreviation(level, stream)}")
        if student_role is None or student_stream_role is None:
            await interaction.response.send_message("❌ Les rôles scolaires ne sont pas prêts. Lance `/setup` puis construis le serveur.", ephemeral=True)
            return
        year = get_active_academic_year(guild.id)
        if year is None:
            await interaction.response.send_message("❌ Aucune année scolaire active.", ephemeral=True)
            return

        original_school_roles = _school_roles(student)
        try:
            cleanup_roles = [
                role for role in student.roles
                if (role.name.startswith(STUDENT_STREAM_ROLE_PREFIX) and role != student_stream_role)
                or role.name.startswith(STREAM_ROLE_PREFIX)
                or role.name.startswith(SUBJECT_ROLE_PREFIX)
            ]
            if cleanup_roles:
                await student.remove_roles(*cleanup_roles, reason="Student role normalization")
            await student.add_roles(student_role, student_stream_role, reason="Student stream assignment")
            enroll_student_record(guild.id, student.id, student.display_name, int(year["id"]), level, stream)
        except discord.Forbidden:
            try:
                await _restore_school_roles(student, original_school_roles)
            except discord.HTTPException:
                pass
            await interaction.response.send_message("❌ Vérifie que le rôle du bot est assez haut dans la hiérarchie.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            try:
                await _restore_school_roles(student, original_school_roles)
            except discord.HTTPException:
                pass
            await interaction.response.send_message(f"❌ Discord API : `{exc}`", ephemeral=True)
            return
        except Exception as exc:
            try:
                await _restore_school_roles(student, original_school_roles)
            except discord.HTTPException:
                pass
            await interaction.response.send_message(f"❌ Affectation annulée; les rôles Discord ont été restaurés si possible : `{type(exc).__name__}: {exc}`", ephemeral=True)
            return

        code = get_stream_abbreviation(level, stream)
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "assignstudent", student.display_name, f"{level}: {code}")
        await interaction.response.send_message(f"✅ {student.mention} est maintenant dans **{code}** ({level}).", ephemeral=True)

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
        original_school_roles = _school_roles(student)
        school_roles = [role for role in student.roles if role.name.startswith(STUDENT_STREAM_ROLE_PREFIX) or role.name.startswith(STREAM_ROLE_PREFIX) or role.name.startswith(SUBJECT_ROLE_PREFIX)]
        student_role = discord.utils.get(guild.roles, name=ROLE_STUDENT)
        try:
            if school_roles:
                await student.remove_roles(*school_roles, reason="Student left school")
            if student_role and student_role in student.roles:
                await student.remove_roles(student_role, reason="Student left school")
            mark_student_left(guild.id, int(row["id"]))
        except discord.Forbidden:
            try:
                await _restore_school_roles(student, original_school_roles)
            except discord.HTTPException:
                pass
            await interaction.response.send_message("❌ Impossible de retirer les rôles. Vérifie la hiérarchie; l'historique n'a pas été modifié.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            try:
                await _restore_school_roles(student, original_school_roles)
            except discord.HTTPException:
                pass
            await interaction.response.send_message(f"❌ Discord API : `{exc}`. L'historique n'a pas été modifié.", ephemeral=True)
            return
        except Exception as exc:
            try:
                await _restore_school_roles(student, original_school_roles)
            except discord.HTTPException:
                pass
            await interaction.response.send_message(f"❌ Opération annulée; les rôles ont été restaurés si possible et l'historique n'a pas été modifié : `{type(exc).__name__}: {exc}`", ephemeral=True)
            return

        record_event(guild.id, interaction.user.id, interaction.user.display_name, "leave_school", student.display_name, "Student marked left school")
        await interaction.response.send_message(f"✅ {student.mention} est marqué **sorti de l'établissement**. Son historique est conservé.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StudentCommands(bot))

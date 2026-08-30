"""Student management commands for stream-based school servers."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import get_levels, get_stream_abbreviation, get_streams
from services.permissions import ROLE_STUDENT, STUDENT_STREAM_ROLE_PREFIX, STREAM_ROLE_PREFIX, management_check
from services.storage import enroll_student, get_active_academic_year, get_student, get_student_history, mark_student_left, upsert_student


class StudentCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="assignstudent", description="Affecter un élève à une filière.")
    @app_commands.describe(student="Élève", level="Niveau scolaire", stream="Filière scolaire")
    @management_check()
    async def assign_student(self, interaction: discord.Interaction, student: discord.Member, level: str, stream: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.response.send_message("❌ Niveau ou filière invalide.", ephemeral=True)
            return

        student_role = discord.utils.get(interaction.guild.roles, name=ROLE_STUDENT)
        student_stream_role = discord.utils.get(
            interaction.guild.roles,
            name=f"{STUDENT_STREAM_ROLE_PREFIX}{get_stream_abbreviation(level, stream)}",
        )
        if student_role is None or student_stream_role is None:
            await interaction.response.send_message(
                "❌ Les rôles scolaires ne sont pas prêts. Lance `/setup` puis construis le serveur.",
                ephemeral=True,
            )
            return

        year = get_active_academic_year(interaction.guild.id)
        if year is None:
            await interaction.response.send_message("❌ Aucune année scolaire active.", ephemeral=True)
            return

        db_student_id = upsert_student(interaction.guild.id, student.id, student.display_name)
        try:
            old_student_stream_roles = [
                role for role in student.roles
                if role.name.startswith(STUDENT_STREAM_ROLE_PREFIX) and role != student_stream_role
            ]
            if old_student_stream_roles:
                await student.remove_roles(*old_student_stream_roles, reason="Student stream transfer")

            # Remove legacy teacher-style stream roles accidentally attached by older versions.
            old_teacher_stream_roles = [
                role for role in student.roles
                if role.name.startswith(STREAM_ROLE_PREFIX)
            ]
            if old_teacher_stream_roles:
                await student.remove_roles(*old_teacher_stream_roles, reason="Student legacy stream role cleanup")

            await student.add_roles(student_role, student_stream_role, reason="Student stream assignment")
            enroll_student(interaction.guild.id, db_student_id, int(year["id"]), level, stream)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Vérifie que le rôle du bot est assez haut dans la hiérarchie.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"❌ Discord API : `{exc}`", ephemeral=True)
            return

        await interaction.response.send_message(
            f"✅ {student.mention} est maintenant dans **{get_stream_abbreviation(level, stream)}** ({level}).",
            ephemeral=True,
        )

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
            lines.append(
                f"• **{row['academic_year']}** — {row['level_name']} / {row['stream_name']} — "
                f"{row['start_date']} → {row['end_date'] or 'présent'} — `{row['status']}`"
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="leave_school", description="Marquer un élève comme ayant quitté l'établissement.")
    @app_commands.describe(student="Élève")
    @management_check()
    async def leave_school(self, interaction: discord.Interaction, student: discord.Member) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        row = get_student(interaction.guild.id, student.id)
        if row is None:
            await interaction.response.send_message("❌ Élève non enregistré.", ephemeral=True)
            return
        mark_student_left(interaction.guild.id, int(row["id"]))
        school_roles = [
            role for role in student.roles
            if role.name.startswith(STUDENT_STREAM_ROLE_PREFIX)
            or role.name.startswith(STREAM_ROLE_PREFIX)
        ]
        student_role = discord.utils.get(interaction.guild.roles, name=ROLE_STUDENT)
        try:
            if school_roles:
                await student.remove_roles(*school_roles, reason="Student left school")
            if student_role and student_role in student.roles:
                await student.remove_roles(student_role, reason="Student left school")
        except discord.Forbidden:
            await interaction.response.send_message(
                "⚠️ Historique enregistré, mais impossible de retirer les rôles. Vérifie la hiérarchie.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"✅ {student.mention} est marqué **sorti de l'établissement**. Son historique est conservé.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StudentCommands(bot))

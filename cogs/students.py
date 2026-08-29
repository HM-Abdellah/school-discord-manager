"""Student management commands with persistent academic history."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from services.permissions import ROLE_STUDENT
from services.storage import enroll_student, find_class, get_active_academic_year, get_student, get_student_history, mark_student_left, upsert_student

CLASS_ROLE_PREFIX = "Élève - "


class StudentCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="assignstudent", description="Affecter un élève à une classe.")
    @app_commands.describe(student="Élève", class_role="Rôle de classe créé par le bot")
    @app_commands.checks.has_permissions(administrator=True)
    async def assign_student(self, interaction: discord.Interaction, student: discord.Member, class_role: discord.Role) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True); return
        if not class_role.name.startswith(CLASS_ROLE_PREFIX):
            await interaction.response.send_message("❌ Ce n'est pas un rôle de classe School Discord Manager.", ephemeral=True); return
        student_role = discord.utils.get(interaction.guild.roles, name=ROLE_STUDENT)
        if student_role is None:
            await interaction.response.send_message("❌ Lance `/setup` d'abord.", ephemeral=True); return
        year = get_active_academic_year(interaction.guild.id)
        if year is None:
            await interaction.response.send_message("❌ Aucune année scolaire active. Lance `/setup`.", ephemeral=True); return
        db_student_id = upsert_student(interaction.guild.id, student.id, student.display_name)
        school_class = find_class(interaction.guild.id, int(year["id"]), class_role.name)
        if school_class is None:
            await interaction.response.send_message("❌ Ce rôle ne correspond pas à une classe de l'année active.", ephemeral=True); return
        old_roles = [r for r in student.roles if r.name.startswith(CLASS_ROLE_PREFIX) and r != class_role]
        try:
            if old_roles: await student.remove_roles(*old_roles, reason="Student class transfer")
            await student.add_roles(student_role, class_role, reason="Student class assignment")
            enroll_student(interaction.guild.id, db_student_id, int(school_class["id"]))
        except discord.Forbidden:
            await interaction.response.send_message("❌ Vérifie la hiérarchie des rôles du bot.", ephemeral=True); return
        await interaction.response.send_message(f"✅ {student.mention} est maintenant dans **{class_role.name.removeprefix(CLASS_ROLE_PREFIX)}**. Le changement est enregistré.", ephemeral=True)

    @app_commands.command(name="studenthistory", description="Voir l'historique scolaire d'un élève.")
    @app_commands.describe(student="Élève")
    @app_commands.checks.has_permissions(administrator=True)
    async def student_history(self, interaction: discord.Interaction, student: discord.Member) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True); return
        rows = get_student_history(interaction.guild.id, student.id)
        if not rows:
            await interaction.response.send_message("ℹ️ Aucun historique enregistré.", ephemeral=True); return
        lines = [f"## 📚 Historique de {student.display_name}", ""]
        for row in rows:
            lines.append(f"• **{row['academic_year']}** — {row['level_name']} / {row['stream_name']} / **{row['class_name']}** — {row['start_date']} → {row['end_date'] or 'présent'} — `{row['status']}`")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="leavschool", description="Marquer un élève comme ayant quitté l'établissement.")
    @app_commands.describe(student="Élève")
    @app_commands.checks.has_permissions(administrator=True)
    async def leave_school(self, interaction: discord.Interaction, student: discord.Member) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True); return
        row = get_student(interaction.guild.id, student.id)
        if row is None:
            await interaction.response.send_message("❌ Élève non enregistré.", ephemeral=True); return
        mark_student_left(interaction.guild.id, int(row["id"]))
        await interaction.response.send_message(f"✅ {student.mention} est marqué **sorti de l'établissement**. Son historique est conservé.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StudentCommands(bot))

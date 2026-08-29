"""Student management commands."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from services.permissions import ROLE_STUDENT


CLASS_ROLE_PREFIX = "Élève - "


class StudentCommands(commands.Cog):
    """Assign students to one school class role."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="assignstudent",
        description="Assign a student to one class role.",
    )
    @app_commands.describe(
        student="Student to assign",
        class_role="Class role created by the school manager",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def assign_student(
        self,
        interaction: discord.Interaction,
        student: discord.Member,
        class_role: discord.Role,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Cette commande doit être utilisée dans un serveur.",
                ephemeral=True,
            )
            return

        if not class_role.name.startswith(CLASS_ROLE_PREFIX):
            await interaction.response.send_message(
                "❌ Le rôle choisi n'est pas un rôle de classe School Discord Manager.",
                ephemeral=True,
            )
            return

        student_role = discord.utils.get(
            interaction.guild.roles,
            name=ROLE_STUDENT,
        )
        if student_role is None:
            await interaction.response.send_message(
                "❌ Le rôle `Élève` n'existe pas encore. Lance `/setup` d'abord.",
                ephemeral=True,
            )
            return

        # Remove previous class roles so a student belongs to one class only.
        old_class_roles = [
            role
            for role in student.roles
            if role.name.startswith(CLASS_ROLE_PREFIX) and role != class_role
        ]

        try:
            if old_class_roles:
                await student.remove_roles(
                    *old_class_roles,
                    reason="School manager class reassignment",
                )

            await student.add_roles(
                student_role,
                class_role,
                reason="School manager student assignment",
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Impossible de modifier les rôles de cet élève. Vérifie la hiérarchie des rôles du bot.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ {student.mention} a été affecté à **{class_role.name.removeprefix(CLASS_ROLE_PREFIX)}**.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StudentCommands(bot))

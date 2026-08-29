"""Teacher management and absence announcement commands."""

from __future__ import annotations

from datetime import date

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import GENERAL_CHANNELS
from services.permissions import ROLE_PROFESSOR


class TeacherCommands(commands.Cog):
    """Administration tools for teachers and teacher absences."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="assignteacher",
        description="Give the Professeur role to a member.",
    )
    @app_commands.describe(teacher="Member who should receive the teacher role")
    @app_commands.checks.has_permissions(administrator=True)
    async def assign_teacher(
        self,
        interaction: discord.Interaction,
        teacher: discord.Member,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Cette commande doit être utilisée dans un serveur.",
                ephemeral=True,
            )
            return

        role = discord.utils.get(
            interaction.guild.roles,
            name=ROLE_PROFESSOR,
        )
        if role is None:
            await interaction.response.send_message(
                "❌ Le rôle `Professeur` n'existe pas encore. Lance `/setup` d'abord.",
                ephemeral=True,
            )
            return

        try:
            await teacher.add_roles(
                role,
                reason="School manager teacher assignment",
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Impossible d'attribuer le rôle. Vérifie la hiérarchie des rôles.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ {teacher.mention} a reçu le rôle **{ROLE_PROFESSOR}**.",
            ephemeral=True,
        )

    @app_commands.command(
        name="reportabsence",
        description="Publish a teacher absence announcement.",
    )
    @app_commands.describe(
        teacher="Absent teacher",
        duration="Absence duration, for example: 3 jours",
        classes="Affected classes, for example: 1BAC Sciences Physiques C1/C2",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def report_absence(
        self,
        interaction: discord.Interaction,
        teacher: discord.Member,
        duration: str,
        classes: str,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Cette commande doit être utilisée dans un serveur.",
                ephemeral=True,
            )
            return

        channel = discord.utils.get(
            interaction.guild.text_channels,
            name=GENERAL_CHANNELS["absences"],
        )
        if channel is None:
            await interaction.response.send_message(
                "❌ Le salon d'absences n'existe pas. Lance `/build` après `/setup`.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="📢 Absence d'un professeur",
            description=(
                f"**Professeur :** {teacher.mention}\n"
                f"**Durée :** {duration}\n"
                f"**Classes concernées :** {classes}\n"
                f"**Date de publication :** {date.today().isoformat()}"
            ),
            colour=discord.Colour.orange(),
        )

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Le bot ne peut pas publier dans le salon d'absences.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ Absence publiée dans {channel.mention}.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TeacherCommands(bot))

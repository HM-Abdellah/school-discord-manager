"""Teacher management and subject assignment commands."""

from __future__ import annotations

import re
from datetime import date

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import GENERAL_CHANNELS
from services.permissions import ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, professor_subject_member_overwrite

MENTION_RE = re.compile(r"<@!?(\d+)>")


class TeacherCommands(commands.Cog):
    """Administration tools for teachers, subject assignment and absences."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="assignteacher", description="Donner le rôle Prof à un membre.")
    @app_commands.describe(teacher="Membre qui doit recevoir le rôle professeur")
    @app_commands.choices(gender=[
        app_commands.Choice(name="Prof", value="male"),
        app_commands.Choice(name="Prof (F)", value="female"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def assign_teacher(
        self,
        interaction: discord.Interaction,
        teacher: discord.Member,
        gender: app_commands.Choice[str],
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Cette commande doit être utilisée dans un serveur.", ephemeral=True)
            return
        role_name = ROLE_PROFESSOR_FEMALE if gender.value == "female" else ROLE_PROFESSOR
        role = discord.utils.get(interaction.guild.roles, name=role_name)
        if role is None:
            await interaction.response.send_message(
                f"❌ Le rôle `{role_name}` n'existe pas encore. Lance `/setup` d'abord.", ephemeral=True
            )
            return
        try:
            # A teacher may have only one of the two display roles.
            other_name = ROLE_PROFESSOR if gender.value == "female" else ROLE_PROFESSOR_FEMALE
            other_role = discord.utils.get(interaction.guild.roles, name=other_name)
            if other_role is not None and other_role in teacher.roles:
                await teacher.remove_roles(other_role, reason="School manager teacher gender role normalization")
            await teacher.add_roles(role, reason="School manager teacher assignment")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Impossible d'attribuer le rôle. Vérifie la hiérarchie des rôles.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ {teacher.mention} a reçu le rôle **{role_name}**.", ephemeral=True)

    @app_commands.command(
        name="assignsubjectteachers",
        description="Affecter plusieurs professeurs à une matière (sans créer de rôle supplémentaire).",
    )
    @app_commands.describe(
        channel="Le salon de matière, par exemple 📚-Math",
        teachers="Mentions de plusieurs professeurs séparées par des espaces",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def assign_subject_teachers(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        teachers: str,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if not channel.name.startswith("📚-"):
            await interaction.response.send_message("❌ Sélectionne un salon de matière `📚-...`.", ephemeral=True)
            return

        ids = []
        for match in MENTION_RE.finditer(teachers):
            member_id = int(match.group(1))
            if member_id not in ids:
                ids.append(member_id)
        members = [interaction.guild.get_member(member_id) for member_id in ids]
        members = [member for member in members if member is not None]
        members = [member for member in members if member != interaction.guild.me]

        if not members:
            await interaction.response.send_message(
                "❌ Aucun professeur valide détecté. Mentionne un ou plusieurs professeurs.",
                ephemeral=True,
            )
            return

        try:
            overwrites = dict(channel.overwrites)
            teacher_permissions = professor_subject_member_overwrite()
            for member in members:
                overwrites[member] = teacher_permissions
            await channel.edit(
                overwrites=overwrites,
                reason="School manager subject teacher assignment",
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Permission refusée. Vérifie Manage Channels et la hiérarchie du bot.", ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"❌ Discord API : `{exc}`", ephemeral=True)
            return

        mentions = ", ".join(member.mention for member in members)
        await interaction.response.send_message(
            f"✅ **{len(members)} professeur(s)** peuvent maintenant publier dans {channel.mention}.\n"
            f"Professeurs : {mentions}\n\n"
            "Ils peuvent publier ici sans pouvoir modifier les paramètres du salon. "
            "Le même professeur peut être affecté à plusieurs niveaux/matières.",
            ephemeral=True,
        )

    @app_commands.command(name="reportabsence", description="Publier une annonce d'absence d'un professeur.")
    @app_commands.describe(
        teacher="Professeur absent",
        duration="Durée de l'absence, par exemple : 3 jours",
        classes="Classes concernées, par exemple : 1BACSE C1/C2",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def report_absence(self, interaction: discord.Interaction, teacher: discord.Member, duration: str, classes: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Cette commande doit être utilisée dans un serveur.", ephemeral=True)
            return
        channel = discord.utils.get(interaction.guild.text_channels, name=GENERAL_CHANNELS["absences"])
        if channel is None:
            await interaction.response.send_message("❌ Le salon d'absences n'existe pas. Lance `/build` après `/setup`.", ephemeral=True)
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
            await interaction.response.send_message("❌ Le bot ne peut pas publier dans le salon d'absences.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Absence publiée dans {channel.mention}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TeacherCommands(bot))

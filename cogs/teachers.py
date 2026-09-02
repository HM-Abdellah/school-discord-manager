"""Teacher management commands for the school server."""

from __future__ import annotations

import re
from datetime import date

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import GENERAL_CHANNELS, get_levels, get_stream_abbreviation, get_streams, get_stream_subjects
from services.audit import record_event
from services.permissions import ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, STREAM_ROLE_PREFIX, SUBJECT_ROLE_PREFIX, management_check
from services.server_builder import _subject_channel_name, _subject_role_name, _stream_role_name

MENTION_RE = re.compile(r"<@!?(\d+)>")


class TeacherCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="assignteacher", description="Donner le rôle Prof à un membre.")
    @app_commands.describe(teacher="Membre qui doit recevoir le rôle professeur")
    @app_commands.choices(gender=[app_commands.Choice(name="Prof", value="male"), app_commands.Choice(name="Prof (F)", value="female")])
    @management_check()
    async def assign_teacher(self, interaction: discord.Interaction, teacher: discord.Member, gender: app_commands.Choice[str]) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        role_name = ROLE_PROFESSOR_FEMALE if gender.value == "female" else ROLE_PROFESSOR
        role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            await interaction.response.send_message(f"❌ Le rôle `{role_name}` n'existe pas encore. Lance `/setup` puis `/build`.", ephemeral=True)
            return
        try:
            other_name = ROLE_PROFESSOR if gender.value == "female" else ROLE_PROFESSOR_FEMALE
            other_role = discord.utils.get(guild.roles, name=other_name)
            if other_role is not None and other_role in teacher.roles:
                await teacher.remove_roles(other_role, reason="Teacher gender role normalization")
            await teacher.add_roles(role, reason="School manager teacher assignment")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Impossible d'attribuer le rôle. Vérifie la hiérarchie des rôles.", ephemeral=True)
            return
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "assignteacher", teacher.display_name, role_name)
        await interaction.response.send_message(f"✅ {teacher.mention} a reçu le rôle **{role_name}**.", ephemeral=True)

    @app_commands.command(name="assignsubjectteachers", description="Affecter plusieurs professeurs à une matière.")
    @app_commands.describe(channel="Salon de matière", teachers="Mentions de plusieurs professeurs séparées par des espaces")
    @management_check()
    async def assign_subject_teachers(self, interaction: discord.Interaction, channel: discord.TextChannel, teachers: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if not channel.name.startswith("📚-"):
            await interaction.response.send_message("❌ Sélectionne un salon de matière `📚-...`.", ephemeral=True)
            return
        target = None
        for level in get_levels():
            for stream in get_streams(level):
                for subject in get_stream_subjects(level, stream):
                    if _subject_channel_name(get_stream_abbreviation(level, stream), subject) == channel.name:
                        target = (level, stream, subject)
                        break
                if target:
                    break
            if target:
                break
        if target is None:
            await interaction.response.send_message("❌ Ce salon n'est pas un salon de matière reconnu par le curriculum actif.", ephemeral=True)
            return
        level, stream, subject = target
        stream_role_name = _stream_role_name(level, stream)
        subject_role_name = _subject_role_name(level, stream, subject)
        stream_role = discord.utils.get(guild.roles, name=stream_role_name)
        subject_role = discord.utils.get(guild.roles, name=subject_role_name)
        if stream_role is None or subject_role is None:
            await interaction.response.send_message("❌ Les rôles de cette filière/matière n'existent pas encore. Lance `/build`.", ephemeral=True)
            return
        ids: list[int] = []
        for match in MENTION_RE.finditer(teachers):
            member_id = int(match.group(1))
            if member_id not in ids:
                ids.append(member_id)
        members = [guild.get_member(member_id) for member_id in ids]
        members = [member for member in members if member is not None]
        prof_role_ids = {role.id for role in guild.roles if role.name in {ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE}}
        members = [member for member in members if any(role.id in prof_role_ids for role in member.roles)]
        if not members:
            await interaction.response.send_message("❌ Aucun membre valide avec le rôle `Prof` ou `Prof (F)` n'a été détecté.", ephemeral=True)
            return
        try:
            for member in members:
                await member.add_roles(stream_role, subject_role, reason=f"School manager teacher assignment: {get_stream_abbreviation(level, stream)} / {subject}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Permission refusée. Vérifie Manage Roles et la hiérarchie du rôle du bot.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"❌ Discord API : `{exc}`", ephemeral=True)
            return
        mentions = ", ".join(member.mention for member in members)
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "assignsubjectteachers", ", ".join(member.display_name for member in members), f"{get_stream_abbreviation(level, stream)} / {subject}")
        await interaction.response.send_message(f"✅ **{len(members)} professeur(s)** affecté(s) à **{get_stream_abbreviation(level, stream)} / {subject}**.\nRôles ajoutés : `{stream_role_name}` + `{subject_role_name}`\nProfesseurs : {mentions}", ephemeral=True)

    @app_commands.command(name="reportabsence", description="Publier une annonce d'absence d'un professeur.")
    @app_commands.describe(teacher="Professeur absent", duration="Durée de l'absence, par exemple : 3 jours", classes="Classes concernées, par exemple : 1BACSE C1/C2")
    @management_check()
    async def report_absence(self, interaction: discord.Interaction, teacher: discord.Member, duration: str, classes: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        channel = discord.utils.get(guild.text_channels, name=GENERAL_CHANNELS["absences"])
        if channel is None:
            await interaction.response.send_message("❌ Le salon d'absences n'existe pas. Lance `/build` après `/setup`.", ephemeral=True)
            return
        embed = discord.Embed(title="📢 Absence d'un professeur", description=f"**Professeur :** {teacher.mention}\n**Durée :** {duration}\n**Classes concernées :** {classes}\n**Date :** {date.today().isoformat()}", colour=discord.Colour.orange())
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Le bot ne peut pas publier dans le salon d'absences.", ephemeral=True)
            return
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "reportabsence", teacher.display_name, f"{duration} | {classes}")
        await interaction.response.send_message(f"✅ Absence publiée dans {channel.mention}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TeacherCommands(bot))

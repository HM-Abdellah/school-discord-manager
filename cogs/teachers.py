"""Teacher management commands for the school server."""

from __future__ import annotations

import re
from datetime import date

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import (
    GENERAL_CHANNELS,
    get_levels,
    get_stream_abbreviation,
    get_stream_subjects,
    get_streams,
    get_subject_display_name,
)
from services.audit import record_event
from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_PROFESSOR_FEMALE,
    get_managed_role,
    management_check,
    professor_subject_member_overwrite,
    professor_subject_view_overwrite,
    administrator_overwrite,
    hidden_overwrite,
    student_overwrite,
)
from services.server_builder import _subject_channel_name, _subject_role_name, _stream_role_name
from services.storage import get_guild_config, save_guild_config

MENTION_RE = re.compile(r"<@!?(\d+)>")


def _contains(value: str, current: str) -> bool:
    return current.casefold() in value.casefold()


async def level_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=level, value=level)
        for level in get_levels()
        if _contains(level, current)
    ][:25]


async def stream_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    if level not in get_levels():
        return []
    return [
        app_commands.Choice(name=stream, value=stream)
        for stream in get_streams(level)
        if _contains(stream, current)
    ][:25]


async def subject_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    stream = str(getattr(interaction.namespace, "stream", ""))
    if level not in get_levels() or stream not in get_streams(level):
        return []
    choices: list[app_commands.Choice[str]] = []
    for subject in get_stream_subjects(level, stream):
        display = get_subject_display_name(subject)
        if _contains(display, current) or _contains(subject, current):
            choices.append(app_commands.Choice(name=display[:100], value=subject))
    return choices[:25]


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
        role = get_managed_role(guild, role_name)
        if role is None:
            await interaction.response.send_message(f"❌ Le rôle géré `{role_name}` n'existe pas encore. Lance `/setup` puis `/build`.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            other_name = ROLE_PROFESSOR if gender.value == "female" else ROLE_PROFESSOR_FEMALE
            other_role = get_managed_role(guild, other_name)
            if other_role is not None and other_role in teacher.roles:
                await teacher.remove_roles(other_role, reason="Teacher gender role normalization")
            await teacher.add_roles(role, reason="School manager teacher assignment")
        except discord.Forbidden:
            await interaction.followup.send("❌ Impossible d'attribuer le rôle. Vérifie la hiérarchie des rôles.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Discord API : `{exc}`", ephemeral=True)
            return

        record_event(guild.id, interaction.user.id, interaction.user.display_name, "assignteacher", teacher.display_name, role_name)
        await interaction.followup.send(f"✅ {teacher.mention} a reçu le rôle **{role_name}**.", ephemeral=True)

    @app_commands.command(name="assignsubjectteachers", description="Affecter jusqu'à 5 professeurs à une matière.")
    @app_commands.describe(
        level="Niveau scolaire",
        stream="Filière scolaire",
        subject="Matière de la filière",
        teacher1="Professeur 1",
        teacher2="Professeur 2 (optionnel)",
        teacher3="Professeur 3 (optionnel)",
        teacher4="Professeur 4 (optionnel)",
        teacher5="Professeur 5 (optionnel)",
    )
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete, subject=subject_autocomplete)
    @management_check()
    async def assign_subject_teachers(
        self,
        interaction: discord.Interaction,
        level: str,
        stream: str,
        subject: str,
        teacher1: discord.Member,
        teacher2: discord.Member | None = None,
        teacher3: discord.Member | None = None,
        teacher4: discord.Member | None = None,
        teacher5: discord.Member | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.response.send_message("❌ Niveau ou filière invalide.", ephemeral=True)
            return

        curriculum_subject = next(
            (
                candidate
                for candidate in get_stream_subjects(level, stream)
                if candidate == subject
                or get_subject_display_name(candidate).casefold() == subject.casefold()
            ),
            None,
        )
        if curriculum_subject is None:
            await interaction.response.send_message("❌ Matière invalide pour cette filière.", ephemeral=True)
            return

        code = get_stream_abbreviation(level, stream)
        channel_name = _subject_channel_name(code, curriculum_subject)
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if channel is None:
            await interaction.response.send_message("❌ Le salon de matière n'existe pas encore. Lance `/build`.", ephemeral=True)
            return

        stream_role_name = _stream_role_name(level, stream)
        subject_role_name = _subject_role_name(level, stream, curriculum_subject)
        stream_role = get_managed_role(guild, stream_role_name)
        if stream_role is None:
            await interaction.response.send_message("❌ Le rôle géré de cette filière n'existe pas encore. Lance `/build`.", ephemeral=True)
            return

        selected_members: list[discord.Member] = []
        seen_ids: set[int] = set()
        for member in (teacher1, teacher2, teacher3, teacher4, teacher5):
            if member is None or member.id in seen_ids:
                continue
            seen_ids.add(member.id)
            selected_members.append(member)

        prof_role_ids = {
            role.id
            for role in (
                get_managed_role(guild, ROLE_PROFESSOR),
                get_managed_role(guild, ROLE_PROFESSOR_FEMALE),
            )
            if role is not None
        }
        invalid = [member for member in selected_members if not any(role.id in prof_role_ids for role in member.roles)]
        if invalid:
            names = ", ".join(member.display_name for member in invalid)
            await interaction.response.send_message(f"❌ Ces membres n'ont pas le rôle géré `Prof` ou `Prof (F)` : {names}", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        config = get_guild_config(guild.id) or {}
        subject_role = get_managed_role(guild, subject_role_name)
        try:
            if subject_role is None:
                subject_role = await guild.create_role(
                    name=subject_role_name,
                    permissions=discord.Permissions.none(),
                    colour=discord.Colour.dark_blue(),
                    mentionable=False,
                    reason="School manager subject role created on demand",
                )
                managed = config.setdefault("managed", {})
                managed_roles = managed.setdefault("roles", {})
                managed_roles[subject_role_name] = subject_role.id

            overwrites = {
                guild.default_role: hidden_overwrite(),
                stream_role: professor_subject_view_overwrite(),
            }
            admin_role = get_managed_role(guild, ROLE_ADMIN)
            prof_role = get_managed_role(guild, ROLE_PROFESSOR)
            prof_f_role = get_managed_role(guild, ROLE_PROFESSOR_FEMALE)
            student_stream_role = get_managed_role(guild, f"Élèves - {code}")
            if admin_role is not None:
                overwrites[admin_role] = administrator_overwrite()
            if prof_role is not None:
                overwrites[prof_role] = professor_subject_view_overwrite()
            if prof_f_role is not None:
                overwrites[prof_f_role] = professor_subject_view_overwrite()
            if student_stream_role is not None:
                overwrites[student_stream_role] = student_overwrite(can_send=True)
            overwrites[subject_role] = professor_subject_member_overwrite()

            await channel.edit(overwrites=overwrites, reason="School manager subject teacher access")
            for member in selected_members:
                await member.add_roles(stream_role, subject_role, reason=f"School manager teacher assignment: {code} / {curriculum_subject}")
            save_guild_config(guild.id, config)
        except discord.Forbidden:
            await interaction.followup.send("❌ Permission refusée. Vérifie Manage Roles, Manage Channels et la hiérarchie.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Discord API : `{exc}`", ephemeral=True)
            return
        except OSError as exc:
            await interaction.followup.send(f"❌ Stockage local : `{exc}`", ephemeral=True)
            return

        mentions = ", ".join(member.mention for member in selected_members)
        subject_display = get_subject_display_name(curriculum_subject)
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "assignsubjectteachers", ", ".join(member.display_name for member in selected_members), f"{code} / {subject_display}")
        await interaction.followup.send(
            f"✅ **{len(selected_members)} professeur(s)** affecté(s) à **{code} / {subject_display}**.\n"
            f"Salon : {channel.mention}\nRôles ajoutés : `{stream_role_name}` + `{subject_role_name}`\n"
            f"Professeurs : {mentions}",
            ephemeral=True,
        )

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

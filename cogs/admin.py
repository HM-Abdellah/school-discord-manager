"""Lightweight administrative dashboard and academic management tools."""

from __future__ import annotations

from datetime import date

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import get_levels, get_stream_abbreviation, get_streams, get_stream_subjects, get_subject_display_name
from services.permissions import ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, _preflight_message, management_check
from services.server_builder import ServerBuilder, _subject_channel_name
from services.storage import get_active_academic_year, get_guild_config, list_academic_years


async def level_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=level, value=level)
        for level in get_levels()
        if current.lower() in level.lower()
    ][:25]


async def stream_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    if level not in get_levels():
        return []
    return [
        app_commands.Choice(name=stream, value=stream)
        for stream in get_streams(level)
        if current.lower() in stream.lower()
    ][:25]


def _find_stream_channel(guild: discord.Guild, level: str, stream: str, kind: str) -> discord.TextChannel | None:
    code = get_stream_abbreviation(level, stream)
    prefix = {"timetable": f"🗓️-{code}・", "exams": f"📝-{code}・"}[kind]
    return discord.utils.find(
        lambda channel: isinstance(channel, discord.TextChannel) and channel.name.startswith(prefix),
        guild.text_channels,
    )


async def _upsert_bot_embed(channel: discord.TextChannel, *, marker: str, embed: discord.Embed) -> discord.Message:
    async for message in channel.history(limit=50):
        if message.author.id == channel.guild.me.id and message.embeds:
            footer = message.embeds[0].footer.text or ""
            if footer == marker:
                await message.edit(embed=embed)
                return message
    embed.set_footer(text=marker)
    return await channel.send(embed=embed)


class AdminCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="adminpanel", description="Afficher le tableau de bord de l'établissement.")
    @management_check()
    async def admin_panel(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        config = get_guild_config(guild.id) or {}
        years = list_academic_years(guild.id)
        active_year = next((row["name"] for row in years if row["is_active"]), config.get("academic_year", "—"))
        students = sum(1 for member in guild.members if any(role.name == "Élève" for role in member.roles))
        teachers = sum(1 for member in guild.members if any(role.name in {ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE} for role in member.roles))
        streams = sum(len(level.get("streams", [])) for level in config.get("levels", []))
        embed = discord.Embed(title="🏫 SCHOOL MANAGER", description="Tableau de bord administratif", colour=discord.Colour.blurple())
        embed.add_field(name="📅 Année active", value=str(active_year), inline=True)
        embed.add_field(name="👨‍🎓 Élèves", value=str(students), inline=True)
        embed.add_field(name="👨‍🏫 Professeurs", value=str(teachers), inline=True)
        embed.add_field(name="📚 Filières", value=str(streams), inline=True)
        embed.add_field(name="🧩 Channels", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="🎭 Rôles", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="🩺 Health", value="Utilise `/serverhealth` pour le diagnostic détaillé.", inline=False)
        embed.set_footer(text="School Discord Manager")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="serverhealth", description="Vérifier la santé du serveur avant une opération.")
    @management_check()
    async def server_health(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        checks: list[str] = []
        bot = guild.me
        if bot is None:
            checks.append("❌ Bot introuvable dans le serveur.")
        else:
            checks.append("✅ Bot connecté")
            checks.append("✅ Manage Channels" if bot.guild_permissions.manage_channels else "❌ Manage Channels manquant")
            checks.append("✅ Manage Roles" if bot.guild_permissions.manage_roles else "❌ Manage Roles manquant")
            hierarchy = _preflight_message(interaction, needs_channels=False, needs_roles=True)
            checks.append("✅ Role hierarchy" if hierarchy is None else hierarchy)
        total = len(guild.channels)
        checks.append(f"✅ Channel count: **{total}/500**" if total < 500 else f"❌ Channel count: **{total}/500**")
        largest_category = max((len(category.channels) for category in guild.categories), default=0)
        checks.append(f"✅ Category capacity: largest **{largest_category}/50**" if largest_category < 50 else f"❌ Category capacity: largest **{largest_category}/50**")
        config = get_guild_config(guild.id)
        checks.append("✅ Configuration" if config else "⚠️ Aucune configuration enregistrée")
        embed = discord.Embed(title="🩺 SERVER HEALTH", description="\n".join(checks), colour=discord.Colour.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="assignteacherfull", description="Affecter un professeur à une filière et à une ou plusieurs matières.")
    @app_commands.describe(
        teacher="Professeur",
        level="Niveau scolaire",
        stream="Filière scolaire",
        subjects="Matières séparées par des virgules; utilise les noms affichés dans le curriculum",
    )
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @management_check()
    async def assign_teacher_full(
        self,
        interaction: discord.Interaction,
        teacher: discord.Member,
        level: str,
        stream: str,
        subjects: str,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.response.send_message("❌ Niveau ou filière invalide.", ephemeral=True)
            return
        requested = [item.strip().casefold() for item in subjects.split(",") if item.strip()]
        curriculum_subjects = get_stream_subjects(level, stream)
        chosen = [subject for subject in curriculum_subjects if get_subject_display_name(subject).casefold() in requested or subject.casefold() in requested]
        if not chosen:
            await interaction.response.send_message("❌ Aucune matière reconnue. Copie les noms des matières affichés par le curriculum.", ephemeral=True)
            return
        prof_role = discord.utils.get(guild.roles, name=ROLE_PROFESSOR)
        if prof_role is None:
            prof_role = discord.utils.get(guild.roles, name=ROLE_PROFESSOR_FEMALE)
        stream_role_name = f"Filière - {get_stream_abbreviation(level, stream)}"
        stream_role = discord.utils.get(guild.roles, name=stream_role_name)
        if stream_role is None:
            await interaction.response.send_message("❌ Lance `/build` avant d'affecter ce professeur.", ephemeral=True)
            return
        subject_roles = []
        for subject in chosen:
            role_name = f"Matière - {get_stream_abbreviation(level, stream)} - {ServerBuilder._subject_role_name(level, stream, subject).split(' - ', 2)[-1]}"
            role = discord.utils.get(guild.roles, name=role_name)
            if role is None:
                await interaction.response.send_message(f"❌ Rôle matière manquant: `{role_name}`. Lance `/build`.", ephemeral=True)
                return
            subject_roles.append(role)
        try:
            if prof_role is not None and prof_role not in teacher.roles:
                await teacher.add_roles(prof_role, reason="School manager teacher assignment")
            await teacher.add_roles(stream_role, *subject_roles, reason="School manager full teacher assignment")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Impossible d'attribuer les rôles. Vérifie la hiérarchie du bot.", ephemeral=True)
            return
        subject_names = ", ".join(get_subject_display_name(subject) for subject in chosen)
        await interaction.response.send_message(
            f"✅ {teacher.mention} est affecté à **{get_stream_abbreviation(level, stream)}** pour: {subject_names}.",
            ephemeral=True,
        )

    @app_commands.command(name="set_timetable", description="Mettre à jour l'emploi du temps d'une filière sans créer de nouveau salon.")
    @app_commands.describe(level="Niveau", stream="Filière", content="Contenu de l'emploi du temps, avec lignes et horaires")
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @management_check()
    async def set_timetable(self, interaction: discord.Interaction, level: str, stream: str, content: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        channel = _find_stream_channel(guild, level, stream, "timetable")
        if channel is None:
            await interaction.response.send_message("❌ Channel d'emploi du temps introuvable. Lance `/build`.", ephemeral=True)
            return
        code = get_stream_abbreviation(level, stream)
        embed = discord.Embed(title=f"🗓️ Emploi du temps — {code}", description=content[:4000], colour=discord.Colour.blue())
        embed.timestamp = discord.utils.utcnow()
        await _upsert_bot_embed(channel, marker=f"SchoolManager:T:{code}", embed=embed)
        await interaction.response.send_message(f"✅ Emploi du temps mis à jour dans {channel.mention}.", ephemeral=True)

    @app_commands.command(name="setexam", description="Mettre à jour les examens d'une filière sans créer de nouveau salon.")
    @app_commands.describe(level="Niveau", stream="Filière", content="Dates, horaires et consignes des examens")
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @management_check()
    async def set_exam(self, interaction: discord.Interaction, level: str, stream: str, content: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        channel = _find_stream_channel(guild, level, stream, "exams")
        if channel is None:
            await interaction.response.send_message("❌ Channel d'examens introuvable. Lance `/build`.", ephemeral=True)
            return
        code = get_stream_abbreviation(level, stream)
        embed = discord.Embed(title=f"📝 Examens — {code}", description=content[:4000], colour=discord.Colour.orange())
        embed.timestamp = discord.utils.utcnow()
        await _upsert_bot_embed(channel, marker=f"SchoolManager:E:{code}", embed=embed)
        await interaction.response.send_message(f"✅ Examens mis à jour dans {channel.mention}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCommands(bot))

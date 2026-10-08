"""Compatibility command module for the full teacher assignment workflow."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import get_levels, get_stream_abbreviation, get_stream_subjects, get_streams, get_subject_display_name, get_subject_internal_code
from services.permissions import management_check
from services.storage import get_guild_config
from services.teacher_assignment import TeacherAssignmentError, execute_teacher_assignment

OWNED_COMMANDS = {"assignteacherfull"}


def _contains(value: str, current: str) -> bool:
    return current.casefold() in value.casefold()


def _normalize_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().replace("\ufe0f", "")


async def level_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=level, value=level) for level in get_levels() if _contains(level, current)][:25]


async def stream_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    if level not in get_levels():
        return []
    return [app_commands.Choice(name=stream, value=stream) for stream in get_streams(level) if _contains(stream, current)][:25]


async def teacher_subject_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    stream = str(getattr(interaction.namespace, "stream", ""))
    if level not in get_levels():
        return []
    preferred = {subject.casefold() for subject in get_stream_subjects(level, stream)} if stream in get_streams(level) else set()
    subjects: list[str] = []
    seen: set[str] = set()
    config = get_guild_config(interaction.guild.id) if interaction.guild else None
    configured_levels = config.get("levels", []) if isinstance(config, dict) else []
    for configured_level in configured_levels:
        if not isinstance(configured_level, dict):
            continue
        level_name = configured_level.get("name")
        if not isinstance(level_name, str) or level_name not in get_levels():
            continue
        for configured_stream in configured_level.get("streams", []) or []:
            if not isinstance(configured_stream, dict) or not isinstance(configured_stream.get("name"), str):
                continue
            stream_name = configured_stream["name"]
            try:
                candidates = get_stream_subjects(level_name, stream_name)
            except Exception:
                continue
            for subject in candidates:
                key = subject.casefold()
                if key not in seen:
                    seen.add(key)
                    subjects.append(subject)
    if not subjects:
        for candidate_stream in get_streams(level):
            for subject in get_stream_subjects(level, candidate_stream):
                key = subject.casefold()
                if key not in seen:
                    seen.add(key)
                    subjects.append(subject)
    subjects.sort(key=lambda item: (item.casefold() not in preferred, get_subject_display_name(item).casefold()))
    return [
        app_commands.Choice(name=get_subject_display_name(subject)[:100], value=subject)
        for subject in subjects
        if _contains(get_subject_display_name(subject), current) or _contains(subject, current)
    ][:25]


class CommandFixes(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="assignteacherfull", description="Affecter un professeur à une filière et à une ou plusieurs matières.")
    @app_commands.describe(
        gender="Type de rôle professeur",
        level="Niveau scolaire",
        stream="Filière scolaire",
        subjects="Matière(s), sélectionne une suggestion ou sépare par des virgules",
        teacher="Professeur à affecter",
    )
    @app_commands.choices(gender=[app_commands.Choice(name="Prof", value="male"), app_commands.Choice(name="Prof (F)", value="female")])
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete, subjects=teacher_subject_autocomplete)
    @app_commands.default_permissions(manage_roles=True)
    @management_check()
    async def assign_teacher_full(
        self,
        interaction: discord.Interaction,
        gender: app_commands.Choice[str],
        level: str,
        stream: str,
        subjects: str,
        teacher: discord.Member,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await execute_teacher_assignment(
                guild=guild,
                teacher=teacher,
                gender_value=gender.value,
                level=level,
                stream=stream,
                subjects=subjects,
                actor_id=interaction.user.id,
                actor_display_name=interaction.user.display_name,
                self_registration=False,
            )
        except TeacherAssignmentError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        await interaction.followup.send(
            f"✅ {teacher.mention} est maintenant professeur et affecté à {result['stream_code']} pour : {result['subject_names']}.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CommandFixes(bot))
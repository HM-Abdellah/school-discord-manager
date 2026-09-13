"""User-friendly replacements for timetable and exam commands."""

from __future__ import annotations

from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import GENERAL_CHANNELS, get_levels, get_stream_abbreviation, get_stream_subjects, get_streams, get_subject_display_name
from services.audit import record_event
from services.permissions import ROLE_ADMIN, ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, ROLE_STUDENT, STUDENT_STREAM_ROLE_PREFIX, get_managed_role, management_check
from services.server_builder import _stream_category_name
from services.storage import get_guild_config, save_guild_config
from cogs.command_fixes import _find_text_channel, _normalize_name

OVERRIDDEN_COMMANDS = {"set_timetable", "setexam"}


def _contains(value: str, current: str) -> bool:
    return current.casefold() in value.casefold()


async def level_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=level, value=level) for level in get_levels() if _contains(level, current)][:25]


async def stream_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    if level not in get_levels():
        return []
    return [app_commands.Choice(name=stream, value=stream) for stream in get_streams(level) if _contains(stream, current)][:25]


async def subject_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    stream = str(getattr(interaction.namespace, "stream", ""))
    if level not in get_levels() or stream not in get_streams(level):
        return []
    choices: list[app_commands.Choice[str]] = []
    seen: set[str] = set()
    for subject in get_stream_subjects(level, stream):
        key = subject.casefold()
        if key in seen:
            continue
        seen.add(key)
        display = get_subject_display_name(subject)
        if _contains(display, current) or _contains(subject, current):
            choices.append(app_commands.Choice(name=display[:100], value=subject))
    return choices[:25]


def _time_choices() -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=f"{hour:02d}:{minute:02d}", value=f"{hour:02d}:{minute:02d}")
        for hour in range(7, 21)
        for minute in (0, 30)
    ][:25]


def _parse_exam_date(value: str):
    """Accept YYYY-MM-DD and the shorter MM/DD format used in Discord manually."""
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%m/%d", "%m-%d"):
        try:
            parsed = datetime.strptime(value, fmt).date()
            if fmt != "%Y-%m-%d":
                parsed = parsed.replace(year=datetime.now().year)
            return parsed
        except ValueError:
            continue
    return None


class CommandUI(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="set_timetable", description="Publier l'emploi du temps d'une filière sous forme d'image ou de fichier texte.")
    @app_commands.describe(
        level="Niveau scolaire",
        stream="Filière scolaire",
        timetable="Image de l'emploi du temps ou fichier .txt",
    )
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @management_check()
    async def set_timetable(
        self,
        interaction: discord.Interaction,
        level: str,
        stream: str,
        timetable: discord.Attachment,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        if level not in get_levels() or stream not in get_streams(level):
            await interaction.followup.send("❌ Niveau ou filière invalide.", ephemeral=True)
            return

        filename = timetable.filename.lower()
        allowed_images = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        allowed_text = {".txt"}
        extension = "." + filename.rsplit(".", 1)[1] if "." in filename else ""
        if extension not in allowed_images | allowed_text:
            await interaction.followup.send("❌ Format invalide. Envoie une image (`png`, `jpg`, `jpeg`, `webp`, `gif`) ou un fichier `.txt`.", ephemeral=True)
            return
        if timetable.size > 15 * 1024 * 1024:
            await interaction.followup.send("❌ Fichier trop volumineux. Maximum : **15 MB**.", ephemeral=True)
            return

        code = get_stream_abbreviation(level, stream)
        channel_name = f"🗓️-{code}・emploi-du-temps"
        channel = await _find_text_channel(guild, _stream_category_name(level, stream, code), channel_name)
        if channel is None:
            await interaction.followup.send("❌ Channel d'emploi du temps introuvable pour cette filière. Vérifie `/build`.", ephemeral=True)
            return

        config = get_guild_config(guild.id) or {}
        try:
            uploaded = await timetable.to_file(filename=timetable.filename)
            message = await channel.send(
                content=f"📅 **Emploi du temps — {code}**",
                file=uploaded,
            )
            marker_key = f"{code}:timetable_message_id"
            config.setdefault("managed", {}).setdefault("messages", {})[marker_key] = message.id
            save_guild_config(guild.id, config)
        except discord.Forbidden:
            await interaction.followup.send("❌ Le bot ne peut pas publier dans le salon d'emploi du temps.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Discord API : `{exc}`", ephemeral=True)
            return
        except OSError as exc:
            await interaction.followup.send(f"❌ Stockage local : `{exc}`", ephemeral=True)
            return

        record_event(guild.id, interaction.user.id, interaction.user.display_name, "set_timetable", code, timetable.filename)
        await interaction.followup.send(f"✅ Emploi du temps de **{code}** publié dans {channel.mention}.", ephemeral=True)

    @app_commands.command(name="setexam", description="Ajouter un examen avec une date et une plage horaire.")
    @app_commands.describe(
        level="Niveau scolaire",
        stream="Filière scolaire",
        subject="Matière",
        exam_date="Date: YYYY-MM-DD ou MM/DD (ex. 09/17)",
        start_time="Heure de début (suggestions)",
        end_time="Heure de fin (suggestions)",
        details="Détails ou consignes (optionnel)",
    )
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete, subject=subject_autocomplete)
    @app_commands.choices(start_time=_time_choices(), end_time=_time_choices())
    @management_check()
    async def set_exam(
        self,
        interaction: discord.Interaction,
        level: str,
        stream: str,
        subject: str,
        exam_date: str,
        start_time: app_commands.Choice[str],
        end_time: app_commands.Choice[str],
        details: str | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        if level not in get_levels() or stream not in get_streams(level):
            await interaction.followup.send("❌ Niveau ou filière invalide.", ephemeral=True)
            return
        curriculum_subject = _resolve_subject(level, stream, subject)
        if curriculum_subject is None:
            await interaction.followup.send("❌ Matière invalide pour cette filière.", ephemeral=True)
            return

        parsed_date = _parse_exam_date(exam_date)
        if parsed_date is None:
            await interaction.followup.send("❌ Date invalide. Utilise `YYYY-MM-DD` ou `MM/DD` comme `09/17`.", ephemeral=True)
            return

        start_value = start_time.value
        end_value = end_time.value
        if start_value >= end_value:
            await interaction.followup.send("❌ L'heure de début doit être avant l'heure de fin.", ephemeral=True)
            return

        code = get_stream_abbreviation(level, stream)
        channel_name = f"📝-{code}・examens"
        channel = await _find_text_channel(guild, _stream_category_name(level, stream, code), channel_name)
        if channel is None:
            await interaction.followup.send("❌ Channel d'examens introuvable pour cette filière. Vérifie `/build`.", ephemeral=True)
            return

        subject_display = get_subject_display_name(curriculum_subject)
        embed = discord.Embed(
            title=f"📝 Examen — {subject_display}",
            colour=discord.Colour.red(),
            description=(
                f"**Filière :** {code}\n"
                f"**Date :** {parsed_date.strftime('%m/%d/%Y')}\n"
                f"**Horaire :** {start_value} → {end_value}\n"
                f"**Détails :** {details or 'Aucun'}"
            ),
        )
        embed.timestamp = discord.utils.utcnow()
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send("❌ Le bot ne peut pas publier dans le salon d'examens.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Discord API : `{exc}`", ephemeral=True)
            return

        record_event(guild.id, interaction.user.id, interaction.user.display_name, "setexam", code, f"{subject_display} | {parsed_date.isoformat()} | {start_value}-{end_value}")
        await interaction.followup.send(f"✅ Examen de **{subject_display}** publié dans {channel.mention}.", ephemeral=True)


# Keep this helper local because the command uses the same subject autocomplete contract as the original command.
def _resolve_subject(level: str, stream: str, value: str) -> str | None:
    for candidate in get_stream_subjects(level, stream):
        if candidate.casefold() == value.casefold() or get_subject_display_name(candidate).casefold() == value.casefold():
            return candidate
    return None


async def setup(bot: commands.Bot) -> None:
    for name in OVERRIDDEN_COMMANDS:
        bot.tree.remove_command(name)
    await bot.add_cog(CommandUI(bot))

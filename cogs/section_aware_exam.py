"""Section-aware exam publishing override.

A school stream may contain multiple physical classes/sections. The section
number is metadata on the exam, not a separate Discord stream or category.
"""

from __future__ import annotations

from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from cogs.discord_aware_commands import (
    _persist_registry_repair,
    _resolve_discord_managed_text_channel,
    level_autocomplete,
    stream_autocomplete,
)
from config.curriculum import (
    get_levels,
    get_stream_abbreviation,
    get_stream_subjects,
    get_streams,
    get_subject_display_name,
)
from services.audit import record_event
from services.permissions import management_check
from services.server_builder import _stream_category_name
from services.storage import get_guild_config, save_guild_config

OVERRIDDEN_COMMANDS = {"setexam"}
MAX_SECTIONS = 8


def _parse_exam_date(value: str):
    for fmt in ("%Y-%m-%d", "%m/%d", "%m-%d"):
        try:
            parsed = datetime.strptime(value.strip(), fmt).date()
            if fmt != "%Y-%m-%d":
                parsed = parsed.replace(year=datetime.now().year)
            return parsed
        except ValueError:
            continue
    return None


async def subject_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    stream = str(getattr(interaction.namespace, "stream", ""))
    if level not in get_levels() or stream not in get_streams(level):
        return []
    current_key = current.casefold()
    return [
        app_commands.Choice(name=get_subject_display_name(subject)[:100], value=subject)
        for subject in get_stream_subjects(level, stream)
        if current_key in subject.casefold() or current_key in get_subject_display_name(subject).casefold()
    ][:25]


class SectionAwareExamCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="setexam", description="Ajouter un examen pour un niveau, une filière et un numéro de classe.")
    @app_commands.describe(
        level="Niveau scolaire",
        stream="Filière scolaire",
        section="Numéro de classe (1 à 8)",
        subject="Matière",
        exam_date="Date: YYYY-MM-DD ou MM/DD",
        start_time="Heure de début",
        end_time="Heure de fin",
        details="Détails ou consignes",
    )
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete, subject=subject_autocomplete)
    @app_commands.choices(
        start_time=[app_commands.Choice(name=f"{h:02d}:{m:02d}", value=f"{h:02d}:{m:02d}") for h in range(7, 21) for m in (0, 30)][:25],
        end_time=[app_commands.Choice(name=f"{h:02d}:{m:02d}", value=f"{h:02d}:{m:02d}") for h in range(7, 21) for m in (0, 30)][:25],
    )
    @management_check()
    async def set_exam(
        self,
        interaction: discord.Interaction,
        level: str,
        stream: str,
        section: app_commands.Range[int, 1, MAX_SECTIONS],
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

        match_subject = next(
            (
                item for item in get_stream_subjects(level, stream)
                if item.casefold() == subject.casefold()
                or get_subject_display_name(item).casefold() == subject.casefold()
            ),
            None,
        )
        if match_subject is None:
            await interaction.followup.send("❌ Matière invalide pour cette filière.", ephemeral=True)
            return

        parsed = _parse_exam_date(exam_date)
        if parsed is None:
            await interaction.followup.send("❌ Date invalide.", ephemeral=True)
            return
        if start_time.value >= end_time.value:
            await interaction.followup.send("❌ L'heure de début doit être avant l'heure de fin.", ephemeral=True)
            return

        code = get_stream_abbreviation(level, stream)
        category_name = _stream_category_name(level, stream, code)
        channel_name = f"📝-{code}・examens"
        config = get_guild_config(guild.id) or {}

        channel, registry_repaired = await _resolve_discord_managed_text_channel(
            guild,
            config,
            channel_name=channel_name,
            category_name=category_name,
        )
        if channel is None:
            await interaction.followup.send(
                f"❌ Discord ne contient pas le channel géré **{channel_name}** dans la catégorie **{category_name}**. Lance `/build` pour reconstruire la structure.",
                ephemeral=True,
            )
            return
        if not _persist_registry_repair(guild.id, config, registry_repaired):
            await interaction.followup.send(
                "❌ Le channel a bien été détecté sur Discord, mais la synchronisation du registre géré a échoué. Publication annulée pour éviter un état incohérent.",
                ephemeral=True,
            )
            return

        display = get_subject_display_name(match_subject)
        embed = discord.Embed(
            title=f"📝 Examen — {display}",
            colour=discord.Colour.red(),
            description=(
                f"**Filière :** {code}\n"
                f"**Classe :** {code}-{section}\n"
                f"**Date :** {parsed.strftime('%m/%d/%Y')}\n"
                f"**Horaire :** {start_time.value} → {end_time.value}\n"
                f"**Détails :** {details or 'Aucun'}"
            ),
        )
        embed.timestamp = discord.utils.utcnow()

        try:
            message = await channel.send(embed=embed)
            config.setdefault("managed", {}).setdefault("messages", {})[
                f"{code}:section:{section}:exam:{message.id}"
            ] = message.id
            save_guild_config(guild.id, config)
        except (discord.Forbidden, discord.HTTPException, OSError) as exc:
            await interaction.followup.send(f"❌ Publication impossible : `{type(exc).__name__}`", ephemeral=True)
            return

        record_event(
            guild.id,
            interaction.user.id,
            interaction.user.display_name,
            "setexam",
            f"{code}-{section}",
            f"{display} | {parsed.isoformat()} | {start_time.value}-{end_time.value}",
        )
        suffix = " Registre réparé depuis l'état réel de Discord." if registry_repaired else ""
        await interaction.followup.send(
            f"✅ Examen de **{display}** pour la classe **{code}-{section}** publié dans {channel.mention}.{suffix}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    for name in OVERRIDDEN_COMMANDS:
        bot.tree.remove_command(name)
    await bot.add_cog(SectionAwareExamCommands(bot))

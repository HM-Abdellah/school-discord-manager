"""Discord-aware management commands for timetable and exam publishing.

Discord's current channel/category state is authoritative for resource
resolution. The managed registry is reconciled from that state when needed.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from cogs.security_hardening_v2 import (
    level_autocomplete,
    stream_autocomplete,
    subject_autocomplete,
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

OVERRIDDEN_COMMANDS = {"set_timetable", "setexam"}


def _name_key(value: str) -> str:
    """Return a Discord-oriented comparison key tolerant of case/Unicode form."""
    normalized = unicodedata.normalize("NFKC", value or "")
    return normalized.casefold().strip()


def _managed_mapping(config: dict, section: str) -> dict:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    value = managed.get(section, {}) if isinstance(managed, dict) else {}
    return value if isinstance(value, dict) else {}


def _find_registered_id(config: dict, section: str, expected_name: str) -> tuple[str | None, int | None]:
    """Find a managed ID even when Discord normalized the registry key's case."""
    mapping = _managed_mapping(config, section)
    exact = mapping.get(expected_name)
    if isinstance(exact, int) and exact > 0:
        return expected_name, exact
    wanted = _name_key(expected_name)
    for name, value in mapping.items():
        if _name_key(str(name)) == wanted and isinstance(value, int) and value > 0:
            return str(name), value
    return None, None


def _set_managed_id(config: dict, section: str, name: str, value: int) -> None:
    managed = config.setdefault("managed", {})
    if not isinstance(managed, dict):
        managed = {}
        config["managed"] = managed
    mapping = managed.setdefault(section, {})
    if not isinstance(mapping, dict):
        mapping = {}
        managed[section] = mapping

    wanted = _name_key(name)
    for key in list(mapping):
        if key != name and _name_key(str(key)) == wanted:
            mapping.pop(key, None)
    mapping[name] = value


def _is_expected_category(category: discord.abc.GuildChannel | None, expected_name: str) -> bool:
    return isinstance(category, discord.CategoryChannel) and _name_key(category.name) == _name_key(expected_name)


def _is_expected_text_channel(
    channel: discord.abc.GuildChannel | None,
    expected_name: str,
    category_id: int,
) -> bool:
    return (
        isinstance(channel, discord.TextChannel)
        and _name_key(channel.name) == _name_key(expected_name)
        and channel.category_id == category_id
    )


async def _resolve_discord_managed_text_channel(
    guild: discord.Guild,
    config: dict,
    *,
    channel_name: str,
    category_name: str,
) -> tuple[discord.TextChannel | None, bool]:
    """Resolve a managed channel from live Discord state and repair the registry.

    Resolution order:
    1. Registered IDs, but validated against Discord's current object state.
    2. A live Discord category/channel scan using normalized names.

    The second path adopts the live IDs into ``config`` so future commands do
    not depend on stale or case-mismatched registry keys.
    """
    category_key, category_id = _find_registered_id(config, "categories", category_name)
    channel_key, channel_id = _find_registered_id(config, "channels", channel_name)
    registry_changed = False

    category = guild.get_channel(category_id) if category_id else None
    channel = guild.get_channel(channel_id) if channel_id else None

    if _is_expected_category(category, category_name) and _is_expected_text_channel(channel, channel_name, category.id):
        if category.name != category_name or category_key != category.name:
            _set_managed_id(config, "categories", category.name, category.id)
            registry_changed = True
        if channel.name != channel_name or channel_key != channel.name:
            _set_managed_id(config, "channels", channel.name, channel.id)
            registry_changed = True
        return channel, registry_changed

    try:
        channels = list(await guild.fetch_channels())
    except (discord.Forbidden, discord.HTTPException):
        channels = list(guild.channels)

    live_categories = [
        item for item in channels
        if isinstance(item, discord.CategoryChannel) and _name_key(item.name) == _name_key(category_name)
    ]
    category = next((item for item in live_categories if item.id == category_id), None) or (live_categories[0] if live_categories else None)
    if category is None:
        return None, False

    live_channel = next(
        (item for item in channels if _is_expected_text_channel(item, channel_name, category.id)),
        None,
    )
    if live_channel is None:
        return None, False

    current_category_id = _find_registered_id(config, "categories", category_name)[1]
    current_channel_id = _find_registered_id(config, "channels", channel_name)[1]
    if current_category_id != category.id or category_key != category.name:
        _set_managed_id(config, "categories", category.name, category.id)
        registry_changed = True
    if current_channel_id != live_channel.id or channel_key != live_channel.name:
        _set_managed_id(config, "channels", live_channel.name, live_channel.id)
        registry_changed = True

    return live_channel, registry_changed


def _validate_stream(level: str, stream: str) -> bool:
    return level in get_levels() and stream in get_streams(level)


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


def _persist_registry_repair(guild_id: int, config: dict, changed: bool) -> bool:
    if not changed:
        return True
    try:
        save_guild_config(guild_id, config)
        return True
    except OSError:
        return False


class DiscordAwareManagementCommands(commands.Cog):
    """Management commands that reconcile persisted state with Discord."""

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

        if not _validate_stream(level, stream):
            await interaction.followup.send("❌ Niveau ou filière invalide.", ephemeral=True)
            return

        extension = "." + timetable.filename.lower().rsplit(".", 1)[1] if "." in timetable.filename else ""
        if extension not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".txt"}:
            await interaction.followup.send("❌ Format invalide. Utilise une image ou un fichier `.txt`.", ephemeral=True)
            return
        if timetable.size > 15 * 1024 * 1024:
            await interaction.followup.send("❌ Fichier trop volumineux. Maximum : **15 MB**.", ephemeral=True)
            return

        code = get_stream_abbreviation(level, stream)
        category_name = _stream_category_name(level, stream, code)
        channel_name = f"🗓️-{code}・emploi-du-temps"
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

        try:
            message = await channel.send(
                content=f"📅 **Emploi du temps — {code}**",
                file=await timetable.to_file(filename=timetable.filename),
            )
            config.setdefault("managed", {}).setdefault("messages", {})[f"{code}:timetable_message_id"] = message.id
            save_guild_config(guild.id, config)
        except (discord.Forbidden, discord.HTTPException, OSError) as exc:
            await interaction.followup.send(f"❌ Publication impossible : `{type(exc).__name__}`", ephemeral=True)
            return

        record_event(guild.id, interaction.user.id, interaction.user.display_name, "set_timetable", code, timetable.filename)
        suffix = " Registre réparé depuis l'état réel de Discord." if registry_repaired else ""
        await interaction.followup.send(
            f"✅ Emploi du temps de **{code}** publié dans {channel.mention}.{suffix}",
            ephemeral=True,
        )

    @app_commands.command(name="setexam", description="Ajouter un examen avec une date et une plage horaire.")
    @app_commands.describe(
        level="Niveau scolaire",
        stream="Filière scolaire",
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

        if not _validate_stream(level, stream):
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
                f"**Date :** {parsed.strftime('%m/%d/%Y')}\n"
                f"**Horaire :** {start_time.value} → {end_time.value}\n"
                f"**Détails :** {details or 'Aucun'}"
            ),
        )
        embed.timestamp = discord.utils.utcnow()

        try:
            message = await channel.send(embed=embed)
            config.setdefault("managed", {}).setdefault("messages", {})[f"{code}:exam_message_id"] = message.id
            save_guild_config(guild.id, config)
        except (discord.Forbidden, discord.HTTPException, OSError) as exc:
            await interaction.followup.send(f"❌ Publication impossible : `{type(exc).__name__}`", ephemeral=True)
            return

        record_event(
            guild.id,
            interaction.user.id,
            interaction.user.display_name,
            "setexam",
            code,
            f"{display} | {parsed.isoformat()} | {start_time.value}-{end_time.value}",
        )
        suffix = " Registre réparé depuis l'état réel de Discord." if registry_repaired else ""
        await interaction.followup.send(
            f"✅ Examen de **{display}** publié dans {channel.mention}.{suffix}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    for name in OVERRIDDEN_COMMANDS:
        bot.tree.remove_command(name)
    await bot.add_cog(DiscordAwareManagementCommands(bot))

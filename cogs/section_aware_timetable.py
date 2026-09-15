"""Section-aware timetable publishing command.

A timetable belongs to a specific school section while the Discord channel
remains shared by the stream. The section number is metadata on the published
message, not a new channel/category.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import get_levels, get_stream_abbreviation, get_streams
from services.audit import record_event
from services.command_autocomplete import level_autocomplete, stream_autocomplete
from services.discord_registry import persist_registry_repair, resolve_managed_text_channel
from services.permissions import management_check
from services.server_builder import _stream_category_name
from services.storage import get_guild_config, save_guild_config

OWNED_COMMANDS = {"set_timetable"}
MAX_SECTIONS = 8


class SectionAwareTimetableCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="set_timetable",
        description="Publier l'emploi du temps d'une classe d'une filière.",
    )
    @app_commands.describe(
        level="Niveau scolaire",
        stream="Filière scolaire",
        section="Numéro de classe (1 à 8)",
        timetable="Image de l'emploi du temps ou fichier .txt",
    )
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @management_check()
    async def set_timetable(
        self,
        interaction: discord.Interaction,
        level: str,
        stream: str,
        section: app_commands.Range[int, 1, MAX_SECTIONS],
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

        extension = "." + timetable.filename.lower().rsplit(".", 1)[1] if "." in timetable.filename else ""
        if extension not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".txt"}:
            await interaction.followup.send(
                "❌ Format invalide. Utilise une image ou un fichier `.txt`.",
                ephemeral=True,
            )
            return
        if timetable.size > 15 * 1024 * 1024:
            await interaction.followup.send(
                "❌ Fichier trop volumineux. Maximum : **15 MB**.",
                ephemeral=True,
            )
            return

        code = get_stream_abbreviation(level, stream)
        category_name = _stream_category_name(level, stream, code)
        channel_name = f"🗓️-{code}・emploi-du-temps"
        config = get_guild_config(guild.id) or {}

        channel, registry_repaired = await resolve_managed_text_channel(
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

        if not persist_registry_repair(guild.id, config, registry_repaired):
            await interaction.followup.send(
                "❌ Le channel a bien été détecté sur Discord, mais la synchronisation du registre géré a échoué. Publication annulée pour éviter un état incohérent.",
                ephemeral=True,
            )
            return

        try:
            message = await channel.send(
                content=f"📅 **Emploi du temps — {code} — Section {section}**",
                file=await timetable.to_file(filename=timetable.filename),
            )
            config.setdefault("managed", {}).setdefault("messages", {})[
                f"{code}:section:{section}:timetable_message_id"
            ] = message.id
            save_guild_config(guild.id, config)
        except (discord.Forbidden, discord.HTTPException, OSError) as exc:
            await interaction.followup.send(
                f"❌ Publication impossible : `{type(exc).__name__}`",
                ephemeral=True,
            )
            return

        record_event(
            guild.id,
            interaction.user.id,
            interaction.user.display_name,
            "set_timetable",
            f"{code}-{section}",
            timetable.filename,
        )
        suffix = " Registre réparé depuis l'état réel de Discord." if registry_repaired else ""
        await interaction.followup.send(
            f"✅ Emploi du temps de **{code} — Section {section}** publié dans {channel.mention}.{suffix}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SectionAwareTimetableCommands(bot))

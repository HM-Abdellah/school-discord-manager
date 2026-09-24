"""Create official section threads inside a stream subject channel."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import (
    get_levels,
    get_stream_abbreviation,
    get_stream_subjects,
    get_streams,
)
from services.permissions import management_check
from services.server_builder import _stream_category_name, _subject_channel_name

MAX_SECTIONS = 20


def _resolve_subject_channel(channel: discord.abc.GuildChannel) -> tuple[str, str, str] | None:
    """Return (level, stream, subject) when the channel is a managed subject channel."""
    if not isinstance(channel, discord.TextChannel) or channel.category is None:
        return None

    for level in get_levels():
        for stream in get_streams(level):
            code = get_stream_abbreviation(level, stream)
            if channel.category.name != _stream_category_name(level, stream, code):
                continue
            for subject in get_stream_subjects(level, stream):
                if channel.name == _subject_channel_name(code, subject):
                    return level, stream, subject
    return None


async def _all_public_threads(channel: discord.TextChannel) -> list[discord.Thread]:
    """Return active and archived public threads visible to the bot."""
    threads: dict[int, discord.Thread] = {thread.id: thread for thread in channel.threads}

    try:
        async for thread in channel.archived_threads(limit=None):
            threads[thread.id] = thread
    except (discord.Forbidden, discord.HTTPException):
        pass

    return list(threads.values())


class SectionThreads(commands.Cog):
    """Management command for creating official section threads."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="create-section-threads",
        description="Créer les threads officiels des sections dans une matière.",
    )
    @app_commands.describe(sections="Nombre de sections à créer (1 à 20).")
    @management_check()
    async def create_section_threads(
        self,
        interaction: discord.Interaction,
        sections: app_commands.Range[int, 1, MAX_SECTIONS],
    ) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ Cette commande doit être utilisée dans un salon texte de matière.",
                ephemeral=True,
            )
            return

        resolved = _resolve_subject_channel(channel)
        if resolved is None:
            await interaction.response.send_message(
                "❌ Cette commande doit être utilisée directement dans un salon de matière géré par le bot.",
                ephemeral=True,
            )
            return

        level, stream, subject = resolved
        existing = await _all_public_threads(channel)
        existing_names = {thread.name for thread in existing}
        missing_names = [
            f"Section {number}"
            for number in range(1, int(sections) + 1)
            if f"Section {number}" not in existing_names
        ]

        if not missing_names:
            await interaction.response.send_message(
                f"ℹ️ **{subject} — {stream}** possède déjà les {sections} threads de section demandés. Aucun changement.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        created: list[str] = []
        try:
            for name in missing_names:
                await channel.create_thread(
                    name=name,
                    type=discord.ChannelType.public_thread,
                    auto_archive_duration=10080,
                    reason=f"School manager: section thread for {level} / {stream} / {subject}",
                )
                created.append(name)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Permission refusée. Vérifie que le bot peut créer des threads publics dans ce salon.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"❌ Discord API : `{exc}`. Threads créés avant l’erreur : {len(created)}.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ **{subject} — {stream}** : {len(created)} thread(s) créé(s) sur {sections} demandé(s). Les threads existants et les threads non gérés n’ont pas été modifiés.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SectionThreads(bot))
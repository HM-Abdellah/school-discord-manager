"""Create official section threads inside a stream subject channel."""

from __future__ import annotations

import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import (
    get_levels,
    get_stream_abbreviation,
    get_stream_subjects,
    get_streams,
)
from services.build_guard import get_build_lock
from services.permissions import management_check
from services.server_builder import _stream_category_name, _subject_channel_name
from services.storage import get_teacher_assignments
from services.teacher_threads import all_public_threads as _all_public_threads, ensure_teacher_threads

MAX_SECTIONS = 20


def _resolve_subject_channel(channel: discord.abc.GuildChannel) -> tuple[str, str, str] | None:
    """Return (level, stream, subject) when the channel is a managed subject channel."""
    if channel.category is None:
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

        try:
            existing = await _all_public_threads(channel)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "❌ Impossible de vérifier les threads archivés de ce salon. Aucun nouveau thread n'a été créé.",
                ephemeral=True,
            )
            return

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


    @app_commands.command(
        name="sync-teacher-threads",
        description="Réessayer la création des threads des professeurs inscrits.",
    )
    @app_commands.default_permissions(manage_roles=True)
    @management_check()
    async def sync_teacher_threads(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            rows = get_teacher_assignments(guild.id)
        except (OSError, sqlite3.Error) as exc:
            print(
                f"[TEACHER THREAD SYNC STORAGE ERROR] guild={guild.id} "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            await interaction.followup.send(
                "❌ Impossible de lire les affectations des professeurs dans SQLite. Aucun thread n'a été créé. "
                "Vérifiez le stockage puis réessayez.",
                ephemeral=True,
            )
            return

        # Recheck every registered teacher, not only pending/failed rows.
        # A thread can be deleted or moved after SQLite marked it as created;
        # its saved ID must be verified against Discord before declaring it synced.
        teacher_ids = list(dict.fromkeys(int(row["discord_id"]) for row in rows))
        if not teacher_ids:
            await interaction.followup.send(
                "ℹ️ Aucune affectation professeur n'est enregistrée.",
                ephemeral=True,
            )
            return

        totals = {"created": 0, "existing": 0, "failed": 0}
        failed_teachers: list[int] = []
        sync_errors: list[str] = []
        for teacher_id in teacher_ids:
            try:
                async with get_build_lock(guild.id):
                    outcome = await ensure_teacher_threads(guild, teacher_id)
            except Exception as exc:
                # Keep processing other teachers; the failed rows remain available
                # for a later retry because assignment state is persisted in SQLite.
                totals["failed"] += 1
                failed_teachers.append(teacher_id)
                print(
                    f"[TEACHER THREAD SYNC ERROR] guild={guild.id} teacher={teacher_id} "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
                sync_errors.append(
                    f"Erreur interne de synchronisation: {type(exc).__name__}: {exc}"
                )
                continue
            for key in totals:
                totals[key] += int(outcome.get(key, 0))
            outcome_errors = outcome.get("errors", [])
            if isinstance(outcome_errors, list):
                sync_errors.extend(str(error) for error in outcome_errors)
            if int(outcome.get("failed", 0)):
                failed_teachers.append(teacher_id)

        details = (
            f"✅ Threads créés : **{totals['created']}**\n"
            f"♻️ Threads existants réutilisés : **{totals['existing']}**\n"
            f"⚠️ Affectations à réparer : **{totals['failed']}**"
        )
        if failed_teachers:
            details += "\n\nCertains threads nécessitent encore une vérification des permissions ou des salons gérés."
        if sync_errors:
            details += "\n\n**Détails des erreurs :**"
            for error in sync_errors[:4]:
                safe_error = discord.utils.escape_mentions(
                    discord.utils.escape_markdown(str(error), as_needed=False)
                )
                details += f"\n• {safe_error[:280]}"
            if len(sync_errors) > 4:
                details += f"\n• … et {len(sync_errors) - 4} autre(s) erreur(s)."
        await interaction.followup.send(details[:1900], ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SectionThreads(bot))

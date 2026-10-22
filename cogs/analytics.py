"""Administrator-only export for privacy-minimizing usage metrics."""

from __future__ import annotations

import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from services.analytics import export_usage_report
from services.permissions import _pending_removal_message, management_authorized


class UsageAnalytics(commands.Cog):
    """Export aggregate slash-command usage without exposing individual users."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="usagereport",
        description="Exporter les statistiques d'utilisation agrégées du bot.",
    )
    @app_commands.describe(days="Nombre de jours à inclure (1 à 400).")
    @app_commands.default_permissions(manage_roles=True)
    async def usage_report(
        self,
        interaction: discord.Interaction,
        days: app_commands.Range[int, 1, 400] = 30,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "❌ Cette commande doit être utilisée dans le serveur scolaire.",
                ephemeral=True,
            )
            return

        # Acknowledge Discord before any synchronous permission/config/database reads.
        # Slash-command interactions must be acknowledged within Discord's short deadline.
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not management_authorized(interaction):
            await interaction.followup.send(
                "❌ Outil réservé au propriétaire du serveur ou à un rôle administratif configuré.",
                ephemeral=True,
            )
            return

        pending_message = _pending_removal_message(guild, "usagereport")
        if pending_message:
            await interaction.followup.send(pending_message, ephemeral=True)
            return

        try:
            report_path, summary = export_usage_report(guild.id, int(days))
        except (OSError, sqlite3.Error, ValueError):
            await interaction.followup.send(
                "❌ Impossible de générer le rapport. Vérifie l'espace disque et l'accès au stockage local.",
                ephemeral=True,
            )
            return

        note = (
            "Les invocations « terminées » correspondent aux commandes qui se sont achevées "
            "sans erreur d'application non gérée. Cela ne garantit pas, à lui seul, la réussite "
            "métier de chaque opération. Le rapport ne contient pas d'identifiants ni de noms d'utilisateurs."
        )
        content = (
            f"**Rapport d'utilisation — {days} jour(s)**\n"
            f"Période UTC : `{summary['start_at']:%Y-%m-%d}` → `{summary['end_at']:%Y-%m-%d}`\n"
            f"Invocations terminées sans exception non gérée : **{summary['completed']}**\n"
            f"Invocations ayant levé une erreur d'application : **{summary['raised_errors']}**\n"
            f"Total des commandes : **{summary['total']}**\n"
            f"Actions auditées enregistrées : **{summary['audited_actions']}**\n\n"
            f"_{note}_"
        )
        await interaction.followup.send(
            content=content,
            file=discord.File(report_path, filename=report_path.name),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UsageAnalytics(bot))

"""Server-level commands."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from services.server_builder import ServerBuilder
from services.storage import get_guild_config


class ServerCommands(commands.Cog):
    """Commands for inspecting and rebuilding the configured school server."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="build",
        description="Build or reconcile the saved compact school server structure.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def build(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Cette commande doit être utilisée dans un serveur.",
                ephemeral=True,
            )
            return

        config = get_guild_config(interaction.guild.id)
        if not config:
            await interaction.response.send_message(
                "❌ Aucune configuration n'est enregistrée. Utilise d'abord `/setup`.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "🏗️ Reconstruction en cours… La structure est maintenant organisée par niveaux, Forums et rôles.",
            ephemeral=True,
        )

        try:
            stats = await ServerBuilder(interaction.guild).build(config)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Discord a refusé une opération. Vérifie les permissions du bot et sa position dans la hiérarchie des rôles.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"❌ Discord API a retourné une erreur : `{exc}`",
                ephemeral=True,
            )
            return
        except Exception as exc:
            await interaction.followup.send(
                f"❌ Erreur inattendue : `{type(exc).__name__}: {exc}`",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "✅ **Construction terminée.**\n\n"
            f"• Niveaux traités : **{stats.levels_processed}**\n"
            f"• Rôles créés : **{stats.roles_created}**\n"
            f"• Catégories créées : **{stats.categories_created}**\n"
            f"• Salons texte créés : **{stats.text_channels_created}**\n"
            f"• Forums créés : **{stats.forums_created}**\n"
            f"• Salons vocaux créés : **{stats.voice_channels_created}**\n"
            f"• Classes traitées : **{stats.classes_processed}**",
            ephemeral=True,
        )

    @app_commands.command(
        name="status",
        description="Show the saved school configuration and compact channel estimate.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Cette commande doit être utilisée dans un serveur.",
                ephemeral=True,
            )
            return

        config = get_guild_config(interaction.guild.id)
        if not config:
            await interaction.response.send_message(
                "ℹ️ Aucune configuration n'est encore enregistrée. Utilise `/setup`.",
                ephemeral=True,
            )
            return

        lines = ["📋 **Configuration enregistrée**", ""]
        total_classes = 0
        levels = config.get("levels", [])

        for level in levels:
            lines.append(f"**{level['name']}**")
            for stream in level.get("streams", []):
                count = int(stream.get("class_count", len(stream.get("classes", [])) or 1))
                total_classes += count
                lines.append(
                    f"• {stream['name']} — {count} classe(s) — "
                    f"{len(stream.get('subjects', []))} matière(s) via tags"
                )
            lines.append("")

        estimated_channels = 7 * len(levels) + 7
        lines.append(f"**Total classes :** {total_classes}")
        lines.append(f"**Channels structurants estimés :** ~{estimated_channels}")
        lines.append("**Principe :** 3 Forums pédagogiques par niveau + rôles pour les classes")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerCommands(bot))

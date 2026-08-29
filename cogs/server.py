"""Server-level commands."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from services.server_builder import ServerBuilder
from services.storage import get_guild_config


class ServerCommands(commands.Cog):
    """Commands for inspecting and building the configured server."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="build",
        description="Build or reconcile the Discord school structure from the saved setup.",
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
            "🏗️ Construction du serveur en cours... Je vais créer uniquement ce qui manque.",
            ephemeral=True,
        )

        try:
            stats = await ServerBuilder(interaction.guild).build(config)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Discord a refusé une opération. Vérifie que le bot possède `Administrator` "
                "et que son rôle est placé correctement.",
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
            f"• Rôles créés : {stats.roles_created}\n"
            f"• Catégories créées : {stats.categories_created}\n"
            f"• Salons texte créés : {stats.text_channels_created}\n"
            f"• Forums créés : {stats.forums_created}\n"
            f"• Salons vocaux créés : {stats.voice_channels_created}\n"
            f"• Classes traitées : {stats.classes_processed}",
            ephemeral=True,
        )

    @app_commands.command(
        name="status",
        description="Show the saved school configuration.",
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
        total_forums = 0

        for level in config["levels"]:
            lines.append(f"**{level['name']}**")
            for stream in level["streams"]:
                count = stream["class_count"]
                total_classes += count
                total_forums += count * len(stream["subjects"])
                lines.append(
                    f"• {stream['name']} — {count} classe(s) — {len(stream['subjects'])} matière(s)"
                )
            lines.append("")

        lines.append(f"**Total classes :** {total_classes}")
        lines.append(f"**Total forums matières prévus :** {total_forums}")

        await interaction.response.send_message(
            "\n".join(lines),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerCommands(bot))

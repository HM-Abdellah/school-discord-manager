"""Server and academic-year management commands."""

from __future__ import annotations

import re

import discord
from discord import app_commands
from discord.ext import commands

from services.server_builder import ServerBuilder
from services.storage import create_academic_year, get_guild_config, list_academic_years


class ServerCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="build", description="Construire ou mettre à jour la structure scolaire.")
    @app_commands.checks.has_permissions(administrator=True)
    async def build(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        config = get_guild_config(interaction.guild.id)
        if not config:
            await interaction.response.send_message("❌ Utilise d'abord `/setup`.", ephemeral=True)
            return
        await interaction.response.send_message("🏗️ Mise à jour en cours...", ephemeral=True)
        try:
            stats = await ServerBuilder(interaction.guild).build(config)
        except discord.Forbidden:
            await interaction.followup.send("❌ Permission refusée. Vérifie les permissions et la hiérarchie des rôles.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Discord API : `{exc}`", ephemeral=True)
            return
        except Exception as exc:
            await interaction.followup.send(f"❌ Erreur : `{type(exc).__name__}: {exc}`", ephemeral=True)
            return
        await interaction.followup.send(
            "✅ **Structure mise à jour.**\n\n"
            f"• Niveaux : {stats.levels_processed}\n• Classes : {stats.classes_processed}\n"
            f"• Rôles créés : {stats.roles_created}\n• Catégories : {stats.categories_created}\n"
            f"• Texte : {stats.text_channels_created}\n• Forums : {stats.forums_created}\n• Vocaux : {stats.voice_channels_created}",
            ephemeral=True,
        )

    @app_commands.command(name="newyear", description="Créer une nouvelle année scolaire et la rendre active.")
    @app_commands.describe(year="Format : 2026/2027")
    @app_commands.checks.has_permissions(administrator=True)
    async def new_year(self, interaction: discord.Interaction, year: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if not re.fullmatch(r"\d{4}/\d{4}", year):
            await interaction.response.send_message("❌ Format attendu : `2026/2027`.", ephemeral=True)
            return
        create_academic_year(interaction.guild.id, year, activate=True)
        await interaction.response.send_message(
            f"✅ **{year}** est maintenant l'année scolaire active.\n"
            "Configure ses niveaux/classes avec `/setup`, puis `/build`.",
            ephemeral=True,
        )

    @app_commands.command(name="years", description="Afficher les années scolaires enregistrées.")
    @app_commands.checks.has_permissions(administrator=True)
    async def years(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        rows = list_academic_years(interaction.guild.id)
        if not rows:
            await interaction.response.send_message("ℹ️ Aucune année scolaire enregistrée.", ephemeral=True)
            return
        lines = ["## 📅 Années scolaires", ""]
        for row in rows:
            lines.append(f"• **{row['name']}**" + (" 🟢 ACTIVE" if row["is_active"] else ""))
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="status", description="Afficher la configuration scolaire enregistrée.")
    @app_commands.checks.has_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        config = get_guild_config(interaction.guild.id)
        if not config:
            await interaction.response.send_message("ℹ️ Aucune configuration. Utilise `/setup`.", ephemeral=True)
            return
        lines = ["📋 **Configuration enregistrée**", f"📅 Année : **{config.get('academic_year', 'non définie')}**", ""]
        total_classes = 0
        for level in config.get("levels", []):
            lines.append(f"**{level['name']}**")
            for stream in level.get("streams", []):
                count = int(stream.get("class_count", 0))
                total_classes += count
                lines.append(f"• {stream['name']} — {count} classe(s) — {len(stream.get('subjects', []))} matière(s) en tags")
            lines.append("")
        lines.append(f"**Total classes :** {total_classes}")
        lines.append("**Structure :** Forums pédagogiques + rôles de classes, sans channel par matière.")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerCommands(bot))

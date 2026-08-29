"""Server and academic-year management commands."""

from __future__ import annotations

import re

import discord
from discord import app_commands
from discord.ext import commands

from services.server_builder import ServerBuilder
from services.storage import create_academic_year, get_guild_config, list_academic_years, reset_guild_data
from services.permissions import ROLE_ADMIN, ROLE_PROFESSOR, ROLE_STUDENT, STREAM_ROLE_PREFIX, SUBJECT_TEACHER_ROLE_PREFIX


MAIN_ROLE_NAMES = {ROLE_ADMIN, ROLE_PROFESSOR, ROLE_STUDENT}


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
            f"• Niveaux : {stats.levels_processed}\n• Filières : {stats.streams_processed}\n"
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
            "Configure ses niveaux/filières avec `/setup`, puis `/build`.",
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

        lines = [
            "📋 **Configuration enregistrée**",
            f"📅 Année : **{config.get('academic_year', 'non définie')}**",
            "",
            "**Organisation :** une catégorie par filière, sans rôles/channels de classes.",
        ]
        total_streams = 0
        for level in config.get("levels", []):
            lines.append("")
            lines.append(f"**{level['name']}**")
            for stream in level.get("streams", []):
                total_streams += 1
                lines.append(
                    f"• {stream['name']} — {len(stream.get('subjects', []))} matière(s)"
                )
        lines.append("")
        lines.append(f"**Total filières :** {total_streams}")
        lines.append("**Par filière :** informations + emploi du temps + examens + channels par matière + à-distance.")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(
        name="resetserver",
        description="FORMATER complètement le serveur : supprimer tous les channels et la structure du bot.",
    )
    @app_commands.describe(
        confirm="Écris RESET pour confirmer. TOUS les salons seront supprimés (texte, vocal, forum, catégories)."
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def reset_server(self, interaction: discord.Interaction, confirm: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if confirm.strip().upper() != "RESET":
            await interaction.response.send_message("❌ Pour confirmer, utilise exactement `RESET`.", ephemeral=True)
            return

        await interaction.response.send_message(
            "🧨 **FORMATAGE COMPLET EN COURS...**\n"
            "Tous les salons vont être supprimés : texte, vocal, forum et catégories.\n"
            "Les rôles créés par le School Discord Manager seront également supprimés.",
            ephemeral=True,
        )

        guild = interaction.guild
        deleted_channels = 0
        deleted_categories = 0
        deleted_roles = 0

        try:
            for category in list(guild.categories):
                child_count = len(category.channels)
                await category.delete(reason="School Discord Manager FULL SERVER RESET")
                deleted_channels += child_count
                deleted_categories += 1

            for channel in list(guild.channels):
                if isinstance(channel, discord.CategoryChannel):
                    continue
                try:
                    await channel.delete(reason="School Discord Manager FULL SERVER RESET")
                    deleted_channels += 1
                except discord.NotFound:
                    pass

            for role in list(guild.roles):
                if role.is_default() or role.managed:
                    continue
                if (
                    role.name in MAIN_ROLE_NAMES
                    or role.name.startswith(STREAM_ROLE_PREFIX)
                    or role.name.startswith(SUBJECT_TEACHER_ROLE_PREFIX)
                ):
                    try:
                        await role.delete(reason="School Discord Manager FULL SERVER RESET")
                        deleted_roles += 1
                    except (discord.Forbidden, discord.HTTPException):
                        continue

            reset_guild_data(guild.id)

        except discord.Forbidden:
            await interaction.edit_original_response(
                content=(
                    "❌ Discord a refusé une suppression.\n"
                    "Vérifie que le bot possède **Manage Channels** et **Manage Roles**, "
                    "et que son rôle est assez haut dans la hiérarchie."
                )
            )
            return
        except discord.HTTPException as exc:
            await interaction.edit_original_response(content=f"❌ Discord API : `{exc}`")
            return
        except Exception as exc:
            await interaction.edit_original_response(content=f"❌ Erreur : `{type(exc).__name__}: {exc}`")
            return

        await interaction.edit_original_response(
            content=(
                "# ✅ FORMATAGE TERMINÉ\n\n"
                f"• Catégories supprimées : **{deleted_categories}**\n"
                f"• Salons supprimés : **{deleted_channels}**\n"
                f"• Rôles School Manager supprimés : **{deleted_roles}**\n"
                "• Configuration locale supprimée\n"
                "• Données SQLite du serveur supprimées\n\n"
                "⚠️ Le serveur est maintenant vide de salons.\n"
                "Pour relancer `/setup`, crée d'abord manuellement **un salon texte temporaire** (par exemple `#setup`)."
            )
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerCommands(bot))

"""Hardened server and academic-year management commands."""

from __future__ import annotations

import re

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import get_levels, get_stream_abbreviation, get_streams, get_stream_subjects
from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_PROFESSOR_FEMALE,
    ROLE_STUDENT,
    STREAM_ROLE_PREFIX,
    STUDENT_STREAM_ROLE_PREFIX,
    SUBJECT_ROLE_PREFIX,
    management_check,
    owner_only_check,
)
from services.server_builder import ServerBuilder, _level_category_name, _safe_name, _stream_channel_prefixes, _stream_header_name
from services.storage import create_academic_year, get_guild_config, list_academic_years, reset_guild_data, save_guild_config

MAIN_ROLE_NAMES = {ROLE_ADMIN, ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, ROLE_STUDENT}
LEGACY_ROLE_NAMES = {"Professeur", "Professeur (F)"}
LEGACY_SUBJECT_ROLE_PREFIXES = ("Professeur Matière - ", "Professeur Matiere - ")
LEVEL_ABBREVIATIONS = {"Tronc Commun": "TC", "1ère Année Bac": "1BAC", "2ème Année Bac": "2BAC"}


async def level_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=level, value=level) for level in get_levels() if current.lower() in level.lower()][:25]


async def stream_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    if level not in get_levels():
        return []
    return [app_commands.Choice(name=stream, value=stream) for stream in get_streams(level) if current.lower() in stream.lower()][:25]


class ServerCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="build", description="Mettre à jour la structure sans formater le serveur.")
    @management_check()
    async def build(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        config = get_guild_config(interaction.guild.id)
        if not config:
            await interaction.response.send_message("❌ Utilise d'abord `/setup`.", ephemeral=True)
            return
        await interaction.response.send_message("🏗️ Mise à jour sécurisée en cours...", ephemeral=True)
        try:
            stats = await ServerBuilder(interaction.guild).build(config)
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Discord API : `{exc}`", ephemeral=True)
            return
        except Exception as exc:
            await interaction.followup.send(f"❌ Erreur : `{type(exc).__name__}: {exc}`", ephemeral=True)
            return
        await interaction.followup.send(
            "✅ **Structure mise à jour sans formatage.**\n\n"
            f"• Niveaux : {stats.levels_processed}\n"
            f"• Filières : {stats.streams_processed}\n"
            f"• Rôles créés : {stats.roles_created}\n"
            f"• Catégories : {stats.categories_created}\n"
            f"• Texte : {stats.text_channels_created}\n"
            f"• Vocaux : {stats.voice_channels_created}",
            ephemeral=True,
        )

    @app_commands.command(name="addstream", description="Ajouter une filière sans toucher au reste du serveur.")
    @app_commands.describe(level="Niveau", stream="Filière à ajouter")
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @management_check()
    async def add_stream(self, interaction: discord.Interaction, level: str, stream: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.response.send_message("❌ Niveau ou filière invalide.", ephemeral=True)
            return
        config = get_guild_config(interaction.guild.id)
        if not config:
            await interaction.response.send_message("❌ Lance `/setup` d'abord.", ephemeral=True)
            return
        target = next((item for item in config.get("levels", []) if item.get("name") == level), None)
        if target is None:
            target = {"name": level, "abbreviation": LEVEL_ABBREVIATIONS[level], "streams": []}
            config.setdefault("levels", []).append(target)
        if any(item.get("name") == stream for item in target.get("streams", [])):
            await interaction.response.send_message(f"ℹ️ **{get_stream_abbreviation(level, stream)}** existe déjà.", ephemeral=True)
            return
        target.setdefault("streams", []).append({
            "name": stream,
            "abbreviation": get_stream_abbreviation(level, stream),
            "subjects": get_stream_subjects(level, stream),
        })
        try:
            await ServerBuilder(interaction.guild).build(config)
            save_guild_config(interaction.guild.id, config)
        except Exception as exc:
            await interaction.response.send_message(f"❌ Ajout non appliqué : `{type(exc).__name__}: {exc}`", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ **{get_stream_abbreviation(level, stream)}** ajouté sans reset.", ephemeral=True)

    @app_commands.command(name="removestream", description="Supprimer une filière et uniquement ses ressources.")
    @app_commands.describe(level="Niveau", stream="Filière à supprimer")
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @management_check()
    async def remove_stream(self, interaction: discord.Interaction, level: str, stream: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.response.send_message("❌ Niveau ou filière invalide.", ephemeral=True)
            return
        config = get_guild_config(interaction.guild.id)
        target = next((item for item in config.get("levels", []) if item.get("name") == level), None) if config else None
        if target is None or not any(item.get("name") == stream for item in target.get("streams", [])):
            await interaction.response.send_message(f"ℹ️ **{get_stream_abbreviation(level, stream)}** n'est pas configurée.", ephemeral=True)
            return

        code = get_stream_abbreviation(level, stream)
        target["streams"] = [item for item in target.get("streams", []) if item.get("name") != stream]
        config["levels"] = [item for item in config.get("levels", []) if item.get("streams")]

        await interaction.response.send_message(f"🗑️ Suppression de **{code}** en cours...", ephemeral=True)
        try:
            base_name = _level_category_name(level)
            level_categories = [
                category for category in interaction.guild.categories
                if category.name == base_name or re.fullmatch(re.escape(base_name) + r"・SUITE\s+\d+", category.name)
            ]
            prefixes = _stream_channel_prefixes(code)
            legacy_header = _stream_header_name(stream, code)
            for category in level_categories:
                for channel in list(category.channels):
                    if channel.name == legacy_header or any(channel.name.startswith(prefix) for prefix in prefixes):
                        try:
                            await channel.delete(reason="School manager stream removal")
                        except discord.NotFound:
                            pass
                if not category.channels and category.name != base_name:
                    try:
                        await category.delete(reason="School manager empty suite cleanup")
                    except (discord.Forbidden, discord.HTTPException):
                        pass

            voice_category = discord.utils.get(interaction.guild.categories, name="🔊・SALLES VIRTUELLES")
            if voice_category is not None:
                voice = discord.utils.get(voice_category.voice_channels, name=f"🔊-{_safe_name(code, 30)}-à-distance")
                if voice is not None:
                    try:
                        await voice.delete(reason="School manager stream removal")
                    except discord.NotFound:
                        pass

            role_names = {f"{STREAM_ROLE_PREFIX}{code}", f"{STUDENT_STREAM_ROLE_PREFIX}{code}"}
            role_names.update(role.name for role in interaction.guild.roles if role.name.startswith(f"{SUBJECT_ROLE_PREFIX}{code} - "))
            for role in list(interaction.guild.roles):
                if role.name in role_names:
                    try:
                        await role.delete(reason="School manager stream role cleanup")
                    except (discord.Forbidden, discord.HTTPException):
                        pass

            save_guild_config(interaction.guild.id, config)
        except (discord.Forbidden, discord.HTTPException, OSError) as exc:
            await interaction.followup.send(
                f"⚠️ Ressources partiellement supprimées; configuration conservée pour permettre une nouvelle tentative : `{type(exc).__name__}`",
                ephemeral=True,
            )
            return
        await interaction.followup.send(f"✅ **{code}** supprimée sans toucher aux autres filières.", ephemeral=True)

    @app_commands.command(name="newyear", description="Créer une nouvelle année scolaire et la rendre active.")
    @app_commands.describe(year="Format : 2026/2027")
    @management_check()
    async def new_year(self, interaction: discord.Interaction, year: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        match = re.fullmatch(r"(\d{4})/(\d{4})", year)
        if not match or int(match.group(2)) != int(match.group(1)) + 1:
            await interaction.response.send_message("❌ Format attendu : `2026/2027`.", ephemeral=True)
            return
        create_academic_year(interaction.guild.id, year, activate=True)
        await interaction.response.send_message(f"✅ **{year}** est maintenant l'année scolaire active.", ephemeral=True)

    @app_commands.command(name="years", description="Afficher les années scolaires enregistrées.")
    @management_check()
    async def years(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        rows = list_academic_years(interaction.guild.id)
        await interaction.response.send_message(
            "## 📅 Années scolaires\n\n" + ("\n".join(f"• **{r['name']}**" + (" 🟢 ACTIVE" if r['is_active'] else "") for r in rows) or "Aucune année enregistrée."),
            ephemeral=True,
        )

    @app_commands.command(name="status", description="Afficher la configuration scolaire enregistrée.")
    @management_check()
    async def status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        config = get_guild_config(interaction.guild.id)
        if not config:
            await interaction.response.send_message("ℹ️ Aucune configuration. Utilise `/setup`.", ephemeral=True)
            return
        lines = ["📋 **Configuration enregistrée**", f"📅 Année : **{config.get('academic_year', 'non définie')}**", ""]
        total = 0
        for level in config.get("levels", []):
            lines.append(f"**{level['name']}**")
            for stream in level.get("streams", []):
                total += 1
                lines.append(f"• **{stream.get('abbreviation', stream['name'])}** — {stream['name']}")
        lines.extend(["", f"**Total filières :** {total}", "**Discord :** catégorie par niveau; suites automatiques si nécessaire pour respecter la limite de 50 salons; channels par filière et matière."])
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="resetserver", description="FORMATER complètement le serveur : supprimer les salons et la structure School Manager.")
    @app_commands.describe(confirm="Écris RESET pour confirmer. Réservé au propriétaire du serveur.")
    @owner_only_check()
    async def reset_server(self, interaction: discord.Interaction, confirm: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if confirm.strip().upper() != "RESET":
            await interaction.response.send_message("❌ Pour confirmer, utilise exactement `RESET`.", ephemeral=True)
            return
        await interaction.response.send_message("🧹 **FORMATAGE COMPLET EN COURS...**", ephemeral=True)
        guild = interaction.guild
        deleted_channels = deleted_categories = deleted_roles = 0
        try:
            for category in list(guild.categories):
                child_count = len(category.channels)
                await category.delete(reason="School Discord Manager FULL SERVER RESET")
                deleted_channels += child_count
                deleted_categories += 1
            for channel in list(guild.channels):
                if not isinstance(channel, discord.CategoryChannel):
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
                    or role.name in LEGACY_ROLE_NAMES
                    or role.name.startswith((STREAM_ROLE_PREFIX, STUDENT_STREAM_ROLE_PREFIX, SUBJECT_ROLE_PREFIX))
                    or role.name.startswith(LEGACY_SUBJECT_ROLE_PREFIXES)
                ):
                    try:
                        await role.delete(reason="School Discord Manager FULL SERVER RESET")
                        deleted_roles += 1
                    except (discord.Forbidden, discord.HTTPException):
                        pass
            reset_guild_data(guild.id)
        except (discord.Forbidden, discord.HTTPException, OSError) as exc:
            try:
                await interaction.followup.send(f"❌ Reset interrompu : `{type(exc).__name__}: {exc}`", ephemeral=True)
            except discord.HTTPException:
                pass
            return
        try:
            await interaction.followup.send(
                "# ✅ FORMATAGE TERMINÉ\n\n"
                f"• Catégories supprimées : **{deleted_categories}**\n"
                f"• Salons supprimés : **{deleted_channels}**\n"
                f"• Rôles School Manager supprimés : **{deleted_roles}**\n"
                "• Configuration locale supprimée\n"
                "• Données SQLite du serveur supprimées\n\n"
                "⚠️ Le serveur est maintenant vide de salons. Crée un salon texte temporaire pour relancer `/setup`.",
                ephemeral=True,
            )
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerCommands(bot))

"""Server structure and academic-year management commands."""

from __future__ import annotations

import re
from copy import deepcopy

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import get_levels, get_stream_abbreviation, get_streams, get_stream_subjects
from services.build_guard import get_build_lock
from services.permissions import ROLE_ADMIN, ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, ROLE_STUDENT, STREAM_ROLE_PREFIX, STUDENT_STREAM_ROLE_PREFIX, SUBJECT_ROLE_PREFIX, management_check, owner_only_check
from services.server_builder import CATEGORY_GENERAL, CATEGORY_PROFESSORS, CATEGORY_VOICE, ServerBuilder, _safe_name, _stream_category_name
from services.storage import create_academic_year, get_guild_config, list_academic_years, reset_guild_data, save_guild_config

LEVEL_ABBREVIATIONS = {"Tronc Commun": "TC", "1ère Année Bac": "1BAC", "2ème Année Bac": "2BAC"}


async def level_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=level, value=level) for level in get_levels() if current.lower() in level.lower()][:25]


async def stream_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    if level not in get_levels():
        return []
    return [app_commands.Choice(name=stream, value=stream) for stream in get_streams(level) if current.lower() in stream.lower()][:25]


def _configured_managed_ids(config: dict) -> tuple[set[int], set[int], set[int]]:
    """Return only persisted School Manager Discord IDs; names are never treated as ownership proof."""
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    if not isinstance(managed, dict):
        return set(), set(), set()

    def ids_for(kind: str) -> set[int]:
        values = managed.get(kind, {})
        if not isinstance(values, dict):
            return set()
        return {value for value in values.values() if isinstance(value, int) and value > 0}

    return ids_for("roles"), ids_for("channels"), ids_for("categories")


async def _run_build(guild: discord.Guild, config: dict) -> object:
    lock = get_build_lock(guild.id)
    if lock.locked():
        raise RuntimeError("Une construction est déjà en cours sur ce serveur.")
    async with lock:
        stats = await ServerBuilder(guild).build(config)
        save_guild_config(guild.id, config)
        return stats


class ServerCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="build", description="Réconcilier la structure School Manager sans reset.")
    @management_check()
    async def build(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        config = get_guild_config(guild.id)
        if not config:
            await interaction.response.send_message("❌ Utilise d'abord `/setup`.", ephemeral=True)
            return
        await interaction.response.send_message("🏗️ Mise à jour sécurisée en cours...", ephemeral=True)
        try:
            stats = await _run_build(guild, config)
        except discord.Forbidden:
            await interaction.followup.send("❌ Permission refusée. Vérifie Manage Channels, Manage Roles et la hiérarchie.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Discord API : `{exc}`", ephemeral=True)
            return
        except Exception as exc:
            await interaction.followup.send(f"❌ Erreur : `{type(exc).__name__}: {exc}`", ephemeral=True)
            return
        await interaction.followup.send(f"✅ Structure réconciliée. Niveaux: {stats.levels_processed} · Filières: {stats.streams_processed} · Rôles créés: {stats.roles_created} · Catégories créées: {stats.categories_created} · Texte créé: {stats.text_channels_created} · Vocaux créés: {stats.voice_channels_created}", ephemeral=True)

    @app_commands.command(name="addstream", description="Ajouter une filière sans toucher aux autres ressources.")
    @app_commands.describe(level="Niveau", stream="Filière à ajouter")
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @management_check()
    async def add_stream(self, interaction: discord.Interaction, level: str, stream: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.response.send_message("❌ Niveau ou filière invalide.", ephemeral=True)
            return
        config = get_guild_config(guild.id)
        if not config:
            await interaction.response.send_message("❌ Lance `/setup` d'abord.", ephemeral=True)
            return
        candidate = deepcopy(config)
        target = next((item for item in candidate.get("levels", []) if item.get("name") == level), None)
        if target is None:
            target = {"name": level, "abbreviation": LEVEL_ABBREVIATIONS[level], "streams": []}
            candidate.setdefault("levels", []).append(target)
        if any(item.get("name") == stream for item in target.get("streams", [])):
            await interaction.response.send_message(f"ℹ️ **{get_stream_abbreviation(level, stream)}** existe déjà.", ephemeral=True)
            return
        target.setdefault("streams", []).append({"name": stream, "abbreviation": get_stream_abbreviation(level, stream), "subjects": get_stream_subjects(level, stream)})
        await interaction.response.send_message(f"🏗️ Ajout de **{get_stream_abbreviation(level, stream)}** en cours...", ephemeral=True)
        try:
            await _run_build(guild, candidate)
        except Exception as exc:
            await interaction.followup.send(f"❌ Ajout annulé: `{type(exc).__name__}: {exc}`", ephemeral=True)
            return
        await interaction.followup.send(f"✅ **{get_stream_abbreviation(level, stream)}** ajoutée.", ephemeral=True)

    @app_commands.command(name="removestream", description="Supprimer uniquement les ressources d'une filière gérée.")
    @app_commands.describe(level="Niveau", stream="Filière à supprimer")
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @management_check()
    async def remove_stream(self, interaction: discord.Interaction, level: str, stream: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.response.send_message("❌ Niveau ou filière invalide.", ephemeral=True)
            return
        config = get_guild_config(guild.id)
        if not config:
            await interaction.response.send_message("❌ Configuration absente.", ephemeral=True)
            return
        candidate = deepcopy(config)
        target = next((item for item in candidate.get("levels", []) if item.get("name") == level), None)
        if target is None or not any(item.get("name") == stream for item in target.get("streams", [])):
            await interaction.response.send_message(f"ℹ️ **{get_stream_abbreviation(level, stream)}** n'est pas configurée.", ephemeral=True)
            return
        code = get_stream_abbreviation(level, stream)
        target["streams"] = [item for item in target.get("streams", []) if item.get("name") != stream]
        candidate["levels"] = [item for item in candidate.get("levels", []) if item.get("streams")]
        await interaction.response.send_message(f"🗑️ Suppression de **{code}** en cours...", ephemeral=True)
        lock = get_build_lock(guild.id)
        if lock.locked():
            await interaction.followup.send("⏳ Une construction est déjà en cours sur ce serveur.", ephemeral=True)
            return
        try:
            async with lock:
                category_name = _stream_category_name(level, stream, code)
                category = discord.utils.find(lambda c: isinstance(c, discord.CategoryChannel) and c.name == category_name, guild.categories)
                if category is not None:
                    for channel in list(category.channels):
                        await channel.delete(reason="School manager stream removal")
                    await category.delete(reason="School manager stream category removal")
                voice_category = discord.utils.get(guild.categories, name=CATEGORY_VOICE)
                if voice_category is not None:
                    voice = discord.utils.get(voice_category.voice_channels, name=f"🔊-{_safe_name(code, 30)}-à-distance")
                    if voice is not None:
                        await voice.delete(reason="School manager stream removal")
                role_names = {f"Filière - {code}", f"Élèves - {code}"}
                role_names.update(role.name for role in guild.roles if role.name.startswith(f"Professeur Matière - {code} - "))
                for role in list(guild.roles):
                    if role.name in role_names and not role.managed and role < guild.me.top_role:
                        await role.delete(reason="School manager stream role cleanup")
                save_guild_config(guild.id, candidate)
        except discord.NotFound:
            await interaction.followup.send(f"⚠️ Une ressource de **{code}** était déjà absente. Configuration conservée; relance `/build` si nécessaire.", ephemeral=True)
            return
        except (discord.Forbidden, discord.HTTPException, OSError) as exc:
            await interaction.followup.send(f"❌ Suppression interrompue; configuration inchangée : `{type(exc).__name__}: {exc}`", ephemeral=True)
            return
        await interaction.followup.send(f"✅ **{code}** supprimée.", ephemeral=True)

    @app_commands.command(name="newyear", description="Créer une nouvelle année scolaire et la rendre active.")
    @app_commands.describe(year="Format : 2026/2027")
    @management_check()
    async def new_year(self, interaction: discord.Interaction, year: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        match = re.fullmatch(r"(\d{4})/(\d{4})", year)
        if not match or int(match.group(2)) != int(match.group(1)) + 1:
            await interaction.response.send_message("❌ Format attendu : `2026/2027`.", ephemeral=True)
            return
        create_academic_year(guild.id, year, activate=True)
        await interaction.response.send_message(f"✅ **{year}** est maintenant l'année scolaire active.", ephemeral=True)

    @app_commands.command(name="years", description="Afficher les années scolaires enregistrées.")
    @management_check()
    async def years(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        rows = list_academic_years(interaction.guild.id)
        await interaction.response.send_message("## 📅 Années scolaires\n\n" + ("\n".join(f"• **{r['name']}**" + (" 🟢 ACTIVE" if r['is_active'] else "") for r in rows) or "Aucune année enregistrée."), ephemeral=True)

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
        await interaction.response.send_message("\n".join(lines + ["", f"**Total filières :** {total}", "**Architecture :** une vraie catégorie Discord par filière; aucune catégorie-titre artificielle."]), ephemeral=True)

    @app_commands.command(name="resetserver", description="Supprimer uniquement les ressources School Manager gérées.")
    @app_commands.describe(confirm="Écris RESET SCHOOL MANAGER pour confirmer. Réservé au propriétaire.")
    @owner_only_check()
    async def reset_server(self, interaction: discord.Interaction, confirm: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if confirm.strip().upper() != "RESET SCHOOL MANAGER":
            await interaction.response.send_message("❌ Confirmation exacte requise : `RESET SCHOOL MANAGER`.", ephemeral=True)
            return
        lock = get_build_lock(guild.id)
        if lock.locked():
            await interaction.response.send_message("⏳ Une construction est déjà en cours sur ce serveur.", ephemeral=True)
            return
        await interaction.response.send_message("🧹 **RESET SCHOOL MANAGER EN COURS...**", ephemeral=True)
        config = get_guild_config(guild.id) or {}
        role_ids, channel_ids, category_ids = _configured_managed_ids(config)
        if not role_ids and not channel_ids and not category_ids:
            try:
                reset_guild_data(guild.id)
            except (OSError, RuntimeError) as exc:
                await interaction.followup.send(f"❌ Reset des données locales interrompu : `{type(exc).__name__}: {exc}`", ephemeral=True)
                return
            await interaction.followup.send("✅ Aucune ressource Discord gérée enregistrée. Rien n'a été supprimé sur Discord; les données School Manager locales ont été réinitialisées.", ephemeral=True)
            return
        deleted_channels = deleted_categories = deleted_roles = 0
        try:
            async with lock:
                for channel_id in list(channel_ids):
                    channel = guild.get_channel(channel_id)
                    if channel is not None:
                        await channel.delete(reason="School Manager scoped reset")
                        deleted_channels += 1
                for category_id in list(category_ids):
                    category = guild.get_channel(category_id)
                    if isinstance(category, discord.CategoryChannel) and not category.channels:
                        await category.delete(reason="School Manager scoped reset")
                        deleted_categories += 1
                bot_member = guild.me
                top_role = bot_member.top_role if bot_member is not None else None
                for role_id in list(role_ids):
                    role = guild.get_role(role_id)
                    if role is not None and not role.is_default() and not role.managed and (top_role is None or role < top_role):
                        await role.delete(reason="School Manager scoped reset")
                        deleted_roles += 1
                reset_guild_data(guild.id)
        except (discord.Forbidden, discord.HTTPException, OSError) as exc:
            await interaction.followup.send(f"❌ Reset interrompu : `{type(exc).__name__}: {exc}`. Les ressources non supprimées restent intactes.", ephemeral=True)
            return
        await interaction.followup.send(f"✅ Reset School Manager terminé. Channels: **{deleted_channels}** · Catégories: **{deleted_categories}** · Rôles: **{deleted_roles}**. Les autres ressources du serveur n'ont pas été ciblées.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerCommands(bot))

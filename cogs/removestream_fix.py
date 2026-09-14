"""Fail-closed /removestream implementation.

This extension replaces the hardened stream-removal command after all other
command patches have loaded. It refuses to mutate Discord or persisted config
when the managed-resource registry is incomplete or inconsistent.
"""

from __future__ import annotations

from copy import deepcopy

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import get_levels, get_stream_abbreviation, get_stream_subjects, get_streams
from services.build_guard import get_build_lock
from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_PROFESSOR_FEMALE,
    ROLE_STUDENT,
    STREAM_ROLE_PREFIX,
    STUDENT_STREAM_ROLE_PREFIX,
    management_check,
)
from services.server_builder import CATEGORY_VOICE, _safe_name, _stream_category_name, _subject_channel_name, _subject_role_name
from services.storage import get_guild_config, save_guild_config


def _managed_mapping(config: dict, section: str) -> dict[str, object]:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    value = managed.get(section, {}) if isinstance(managed, dict) else {}
    return value if isinstance(value, dict) else {}


def _recorded_id(config: dict, section: str, name: str) -> int | None:
    value = _managed_mapping(config, section).get(name)
    return value if isinstance(value, int) and value > 0 else None


def _stream_role_names(level: str, stream: str) -> set[str]:
    code = get_stream_abbreviation(level, stream)
    return {
        f"{STREAM_ROLE_PREFIX}{code}",
        f"{STUDENT_STREAM_ROLE_PREFIX}{code}",
        *{_subject_role_name(level, stream, subject) for subject in get_stream_subjects(level, stream)},
    }


def _stream_channel_names(level: str, stream: str) -> set[str]:
    code = get_stream_abbreviation(level, stream)
    return {
        f"📌-{code}・informations",
        f"🗓️-{code}・emploi-du-temps",
        f"📝-{code}・examens",
        *{_subject_channel_name(code, subject) for subject in get_stream_subjects(level, stream)},
    }


def _stream_resource_ids(config: dict, level: str, stream: str) -> tuple[dict[str, int], dict[str, int], int | None, int | None]:
    roles = {
        name: value
        for name, value in _managed_mapping(config, "roles").items()
        if name in _stream_role_names(level, stream) and isinstance(value, int) and value > 0
    }
    channels = {
        name: value
        for name, value in _managed_mapping(config, "channels").items()
        if name in _stream_channel_names(level, stream) and isinstance(value, int) and value > 0
    }
    code = get_stream_abbreviation(level, stream)
    category_name = _stream_category_name(level, stream, code)
    voice_name = f"🔊-{_safe_name(code, 30)}-à-distance"
    return roles, channels, _recorded_id(config, "categories", category_name), _recorded_id(config, "channels", voice_name)


def _remove_managed_entries(config: dict, *, role_names: set[str], channel_names: set[str], category_names: set[str]) -> None:
    managed = config.get("managed", {}) if isinstance(config, dict) else None
    if not isinstance(managed, dict):
        return
    for section, names in (("roles", role_names), ("channels", channel_names), ("categories", category_names)):
        mapping = managed.get(section)
        if isinstance(mapping, dict):
            for name in names:
                mapping.pop(name, None)


def _find_level_stream(config: dict, level: str, stream: str) -> tuple[dict | None, dict | None]:
    levels = config.get("levels", []) if isinstance(config, dict) else []
    if not isinstance(levels, list):
        return None, None
    for level_item in levels:
        if not isinstance(level_item, dict) or level_item.get("name") != level:
            continue
        streams = level_item.get("streams", [])
        if not isinstance(streams, list):
            return level_item, None
        for stream_item in streams:
            if isinstance(stream_item, dict) and stream_item.get("name") == stream:
                return level_item, stream_item
        return level_item, None
    return None, None


def _fail(message: str) -> str:
    return f"❌ {message}"


async def level_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=level, value=level)
        for level in get_levels()
        if current.casefold() in level.casefold()
    ][:25]


async def stream_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    if level not in get_levels():
        return []
    return [
        app_commands.Choice(name=stream, value=stream)
        for stream in get_streams(level)
        if current.casefold() in stream.casefold()
    ][:25]


class SafeRemoveStream(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="removestream",
        description="Supprimer une filière uniquement si toutes ses ressources gérées sont enregistrées.",
    )
    @app_commands.describe(level="Niveau", stream="Filière à supprimer")
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @management_check()
    async def remove_stream(self, interaction: discord.Interaction, level: str, stream: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(_fail("Serveur requis."), ephemeral=True)
            return
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.response.send_message(_fail("Niveau ou filière invalide."), ephemeral=True)
            return

        config = get_guild_config(guild.id)
        if not config:
            await interaction.response.send_message(_fail("Configuration absente."), ephemeral=True)
            return

        level_item, stream_item = _find_level_stream(config, level, stream)
        if level_item is None or stream_item is None:
            await interaction.response.send_message(
                f"ℹ️ **{get_stream_abbreviation(level, stream)}** n'est pas configurée.",
                ephemeral=True,
            )
            return

        code = get_stream_abbreviation(level, stream)
        category_name = _stream_category_name(level, stream, code)
        voice_name = f"🔊-{_safe_name(code, 30)}-à-distance"
        expected_role_names = _stream_role_names(level, stream)
        expected_channel_names = _stream_channel_names(level, stream)
        role_ids, channel_ids, category_id, voice_id = _stream_resource_ids(config, level, stream)
        voice_category_id = _recorded_id(config, "categories", CATEGORY_VOICE)

        missing_parts: list[str] = []
        if category_id is None:
            missing_parts.append("category")
        if voice_category_id is None or voice_id is None:
            missing_parts.append("salon vocal")
        missing_roles = expected_role_names - set(role_ids)
        if missing_roles:
            missing_parts.append(f"{len(missing_roles)} rôle(s) géré(s)")
        missing_channels = expected_channel_names - set(channel_ids)
        if missing_channels:
            missing_parts.append(f"{len(missing_channels)} salon(s) géré(s)")
        if missing_parts:
            await interaction.response.send_message(
                _fail(
                    f"Suppression refusée pour **{code}** : registre des ressources gérées incomplet ({', '.join(missing_parts)}). "
                    "Aucune ressource et aucune configuration n'ont été modifiées."
                ),
                ephemeral=True,
            )
            return

        category = guild.get_channel(category_id)
        voice_category = guild.get_channel(voice_category_id)
        voice = guild.get_channel(voice_id)
        if not isinstance(category, discord.CategoryChannel) or category.name != category_name:
            await interaction.response.send_message(
                _fail(f"Suppression refusée pour **{code}** : la catégorie gérée enregistrée est absente ou incohérente. Aucune modification effectuée."),
                ephemeral=True,
            )
            return
        if not isinstance(voice_category, discord.CategoryChannel) or not isinstance(voice, discord.VoiceChannel) or voice.category_id != voice_category.id or voice.name != voice_name:
            await interaction.response.send_message(
                _fail(f"Suppression refusée pour **{code}** : le salon vocal géré enregistré est absent ou incohérent. Aucune modification effectuée."),
                ephemeral=True,
            )
            return

        verified_channels: list[discord.abc.GuildChannel] = []
        for name in sorted(expected_channel_names):
            channel_id = channel_ids.get(name)
            channel = guild.get_channel(channel_id) if channel_id else None
            if (
                channel is None
                or not isinstance(channel, discord.abc.GuildChannel)
                or channel.name != name
                or channel.category_id != category.id
            ):
                await interaction.response.send_message(
                    _fail(f"Suppression refusée pour **{code}** : le salon géré `{name}` est absent ou incohérent. Aucun changement effectué."),
                    ephemeral=True,
                )
                return
            verified_channels.append(channel)

        verified_roles: list[discord.Role] = []
        for name in sorted(expected_role_names):
            role_id = role_ids.get(name)
            role = guild.get_role(role_id) if role_id else None
            if role is None or role.managed or role.is_default() or role.name != name:
                await interaction.response.send_message(
                    _fail(f"Suppression refusée pour **{code}** : le rôle géré `{name}` est absent ou incohérent. Aucun changement effectué."),
                    ephemeral=True,
                )
                return
            verified_roles.append(role)

        top_role = guild.me.top_role if guild.me is not None else None
        blocked_roles = [role.name for role in verified_roles if top_role is not None and role >= top_role]
        if blocked_roles:
            await interaction.response.send_message(
                _fail(
                    f"Suppression refusée pour **{code}** : hiérarchie Discord insuffisante pour supprimer les rôles {', '.join(blocked_roles)}. Aucun changement effectué."
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(f"🗑️ Suppression sécurisée de **{code}** en cours...", ephemeral=True)
        lock = get_build_lock(guild.id)
        if lock.locked():
            await interaction.followup.send("⏳ Une construction est déjà en cours sur ce serveur.", ephemeral=True)
            return

        candidate = deepcopy(config)
        try:
            async with lock:
                for channel in verified_channels:
                    await channel.delete(reason="School Manager scoped stream removal")
                await voice.delete(reason="School Manager scoped stream voice removal")
                for role in verified_roles:
                    await role.delete(reason="School Manager scoped stream role removal")

                target_category = guild.get_channel(category.id)
                if isinstance(target_category, discord.CategoryChannel):
                    remaining = [channel for channel in guild.channels if getattr(channel, "category_id", None) == target_category.id]
                    if not remaining:
                        await target_category.delete(reason="School Manager scoped stream category removal")

                if isinstance(level_item, dict):
                    level_item["streams"] = [
                        item
                        for item in level_item.get("streams", [])
                        if not (isinstance(item, dict) and item.get("name") == stream)
                    ]
                candidate["levels"] = [
                    item
                    for item in candidate.get("levels", [])
                    if not (isinstance(item, dict) and item.get("name") == level and not item.get("streams"))
                ]
                _remove_managed_entries(
                    candidate,
                    role_names=expected_role_names,
                    channel_names=expected_channel_names | {voice_name},
                    category_names={category_name},
                )
                save_guild_config(guild.id, candidate)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound, OSError) as exc:
            await interaction.followup.send(
                f"❌ Suppression interrompue : `{type(exc).__name__}`. La configuration n'a pas été mise à jour; réessaie après vérification de l'état Discord.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ **{code}** supprimée. Les ressources gérées ont été supprimées; les salons non gérés de la catégorie, s'il y en a, ont été conservés.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    # Remove every earlier /removestream implementation/patch before installing this one.
    bot.tree.remove_command("removestream")
    await bot.add_cog(SafeRemoveStream(bot))

"""Fail-closed /removestream implementation.

This extension replaces the hardened stream-removal command after all other
command patches have loaded. It refuses destructive actions unless the exact
stream resources can be verified from the configured stream and Discord state.
"""

from __future__ import annotations

from copy import deepcopy

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import get_levels, get_stream_abbreviation, get_stream_subjects, get_streams
from services.build_guard import get_build_lock
from services.permissions import (
    STREAM_ROLE_PREFIX,
    STUDENT_STREAM_ROLE_PREFIX,
    management_check,
)
from services.server_builder import CATEGORY_VOICE, _safe_name, _stream_category_name, _subject_channel_name
from services.storage import get_guild_config, save_guild_config


def _managed_mapping(config: dict, section: str) -> dict[str, object]:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    value = managed.get(section, {}) if isinstance(managed, dict) else {}
    return value if isinstance(value, dict) else {}


def _recorded_id(config: dict, section: str, name: str) -> int | None:
    value = _managed_mapping(config, section).get(name)
    return value if isinstance(value, int) and value > 0 else None


def _stream_role_names(level: str, stream: str) -> set[str]:
    """Roles actually created by ServerBuilder for a stream."""
    code = get_stream_abbreviation(level, stream)
    return {
        f"{STREAM_ROLE_PREFIX}{code}",
        f"{STUDENT_STREAM_ROLE_PREFIX}{code}",
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


async def _fetch_channels(guild: discord.Guild) -> list[discord.abc.GuildChannel]:
    try:
        return list(await guild.fetch_channels())
    except (discord.Forbidden, discord.HTTPException):
        return list(guild.channels)


async def _resolve_registry(
    guild: discord.Guild,
    config: dict,
    level: str,
    stream: str,
) -> tuple[dict[str, int], dict[str, int], int | None, int | None, str | None]:
    """Resolve recorded resources, with a conservative exact-name recovery path.

    Recovery is limited to the canonical names generated by ServerBuilder and the
    exact stream category. It does not scan arbitrary categories or delete custom
    channels by name alone. Recovered IDs are used only for this invocation and
    are persisted only after the destructive operation succeeds.
    """
    roles, channels, category_id, voice_id = _stream_resource_ids(config, level, stream)
    code = get_stream_abbreviation(level, stream)
    category_name = _stream_category_name(level, stream, code)
    voice_name = f"🔊-{_safe_name(code, 30)}-à-distance"
    expected_channels = _stream_channel_names(level, stream)
    expected_roles = _stream_role_names(level, stream)

    all_channels = await _fetch_channels(guild)
    category = guild.get_channel(category_id) if category_id else None
    if not isinstance(category, discord.CategoryChannel) or category.name != category_name:
        category = next(
            (
                item for item in all_channels
                if isinstance(item, discord.CategoryChannel) and item.name == category_name
            ),
            None,
        )
        if category is not None:
            category_id = category.id

    if isinstance(category, discord.CategoryChannel):
        children = [item for item in all_channels if getattr(item, "category_id", None) == category.id]
        children_by_name: dict[str, list[discord.abc.GuildChannel]] = {}
        for child in children:
            children_by_name.setdefault(child.name, []).append(child)
        for name in expected_channels:
            if name in channels and guild.get_channel(channels[name]) is not None:
                continue
            matches = children_by_name.get(name, [])
            if len(matches) == 1:
                channels[name] = matches[0].id

    voice_category_id = _recorded_id(config, "categories", CATEGORY_VOICE)
    voice_category = guild.get_channel(voice_category_id) if voice_category_id else None
    if not isinstance(voice_category, discord.CategoryChannel):
        voice_category = next(
            (
                item for item in all_channels
                if isinstance(item, discord.CategoryChannel) and item.name == CATEGORY_VOICE
            ),
            None,
        )
        if voice_category is not None:
            voice_category_id = voice_category.id

    if isinstance(voice_category, discord.CategoryChannel):
        if voice_id is None or guild.get_channel(voice_id) is None:
            voice_matches = [
                item for item in all_channels
                if isinstance(item, discord.VoiceChannel)
                and item.category_id == voice_category.id
                and item.name == voice_name
            ]
            if len(voice_matches) == 1:
                voice_id = voice_matches[0].id

    all_roles = list(guild.roles)
    for name in expected_roles:
        if name in roles and guild.get_role(roles[name]) is not None:
            continue
        matches = [role for role in all_roles if not role.managed and role.name == name]
        if len(matches) == 1:
            roles[name] = matches[0].id

    missing = []
    if category_id is None:
        missing.append("category")
    if voice_category_id is None or voice_id is None:
        missing.append("salon vocal")
    if expected_channels - set(channels):
        missing.append(f"{len(expected_channels - set(channels))} salon(s) géré(s)")
    if expected_roles - set(roles):
        missing.append(f"{len(expected_roles - set(roles))} rôle(s) géré(s)")
    return roles, channels, category_id, voice_id, ", ".join(missing) if missing else None


class SafeRemoveStream(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="removestream",
        description="Supprimer une filière uniquement après vérification de ses ressources gérées.",
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
        role_ids, channel_ids, category_id, voice_id, registry_error = await _resolve_registry(guild, config, level, stream)

        if registry_error:
            await interaction.response.send_message(
                _fail(
                    f"Suppression refusée pour **{code}** : ressources gérées absentes ou ambiguës ({registry_error}). "
                    "Aucune ressource et aucune configuration n'ont été modifiées."
                ),
                ephemeral=True,
            )
            return

        category = guild.get_channel(category_id) if category_id else None
        voice_category_id = _recorded_id(config, "categories", CATEGORY_VOICE)
        if voice_category_id is None:
            # _resolve_registry found it from Discord, but keep it local only.
            all_channels = await _fetch_channels(guild)
            voice_categories = [item for item in all_channels if isinstance(item, discord.CategoryChannel) and item.name == CATEGORY_VOICE]
            voice_category_id = voice_categories[0].id if len(voice_categories) == 1 else None
        voice_category = guild.get_channel(voice_category_id) if voice_category_id else None
        voice = guild.get_channel(voice_id) if voice_id else None

        if not isinstance(category, discord.CategoryChannel) or category.name != category_name:
            await interaction.response.send_message(_fail(f"Suppression refusée pour **{code}** : catégorie absente ou incohérente. Aucune modification effectuée."), ephemeral=True)
            return
        if not isinstance(voice_category, discord.CategoryChannel) or not isinstance(voice, discord.VoiceChannel) or voice.category_id != voice_category.id or voice.name != voice_name:
            await interaction.response.send_message(_fail(f"Suppression refusée pour **{code}** : salon vocal absent ou incohérent. Aucune modification effectuée."), ephemeral=True)
            return

        verified_channels: list[discord.abc.GuildChannel] = []
        for name in sorted(expected_channel_names):
            channel_id = channel_ids.get(name)
            channel = guild.get_channel(channel_id) if channel_id else None
            if channel is None or not isinstance(channel, discord.abc.GuildChannel) or channel.name != name or channel.category_id != category.id:
                await interaction.response.send_message(_fail(f"Suppression refusée pour **{code}** : salon géré `{name}` absent ou incohérent. Aucun changement effectué."), ephemeral=True)
                return
            verified_channels.append(channel)

        verified_roles: list[discord.Role] = []
        for name in sorted(expected_role_names):
            role_id = role_ids.get(name)
            role = guild.get_role(role_id) if role_id else None
            if role is None or role.managed or role.is_default() or role.name != name:
                await interaction.response.send_message(_fail(f"Suppression refusée pour **{code}** : rôle géré `{name}` absent ou incohérent. Aucun changement effectué."), ephemeral=True)
                return
            verified_roles.append(role)

        top_role = guild.me.top_role if guild.me is not None else None
        blocked_roles = [role.name for role in verified_roles if top_role is not None and role >= top_role]
        if blocked_roles:
            await interaction.response.send_message(_fail(f"Suppression refusée pour **{code}** : hiérarchie Discord insuffisante pour supprimer les rôles {', '.join(blocked_roles)}. Aucun changement effectué."), ephemeral=True)
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

                level_item["streams"] = [
                    item for item in level_item.get("streams", [])
                    if not (isinstance(item, dict) and item.get("name") == stream)
                ]
                candidate["levels"] = [
                    item for item in candidate.get("levels", [])
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
    bot.tree.remove_command("removestream")
    await bot.add_cog(SafeRemoveStream(bot))

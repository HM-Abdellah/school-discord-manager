"""Fail-closed /removestream implementation.

Destructive stream removal resolves every deletion target from the persisted
managed-resource registry. Resource names are used only to verify that a
registered ID still identifies the expected resource; names are never used to
discover new deletion targets.
"""

from __future__ import annotations

from copy import deepcopy
import unicodedata

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
from services.discord_ownership import validate_managed_registry
from services.permissions import (
    STREAM_ROLE_PREFIX,
    STUDENT_STREAM_ROLE_PREFIX,
    management_check,
)
from services.server_builder import (
    CATEGORY_VOICE,
    _safe_name,
    _stream_category_name,
    _subject_channel_name,
)
from services.storage import get_guild_config, save_guild_config


def _managed_mapping(config: dict, section: str) -> dict[str, object]:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    value = managed.get(section, {}) if isinstance(managed, dict) else {}
    return value if isinstance(value, dict) else {}


def _recorded_id(config: dict, section: str, name: str) -> int | None:
    value = _managed_mapping(config, section).get(name)
    return value if isinstance(value, int) and value > 0 else None


def _norm(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _stream_role_names(level: str, stream: str) -> set[str]:
    code = get_stream_abbreviation(level, stream)
    return {
        f"{STREAM_ROLE_PREFIX}{code}",
        f"{STUDENT_STREAM_ROLE_PREFIX}{code}",
    }


def _stream_channel_names(
    level: str,
    stream: str,
    stream_item: dict | None = None,
) -> set[str]:
    code = get_stream_abbreviation(level, stream)
    subjects = get_stream_subjects(level, stream)
    if isinstance(stream_item, dict) and isinstance(stream_item.get("subjects"), list):
        subjects = [
            subject
            for subject in stream_item["subjects"]
            if isinstance(subject, str)
        ]
    return {
        f"📌-{code}・informations",
        f"🗓️-{code}・emploi-du-temps",
        f"📝-{code}・examens",
        *{_subject_channel_name(code, subject) for subject in subjects},
    }


def _remove_managed_entries(
    config: dict,
    *,
    role_names: set[str],
    channel_names: set[str],
    category_names: set[str],
) -> None:
    managed = config.get("managed") if isinstance(config, dict) else None
    if not isinstance(managed, dict):
        return
    for section, names in (
        ("roles", role_names),
        ("channels", channel_names),
        ("categories", category_names),
    ):
        mapping = managed.get(section)
        if isinstance(mapping, dict):
            for name in names:
                mapping.pop(name, None)


def _find_level_stream(
    config: dict,
    level: str,
    stream: str,
) -> tuple[dict | None, dict | None]:
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


async def level_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=level, value=level)
        for level in get_levels()
        if current.casefold() in level.casefold()
    ][:25]


async def stream_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
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
    stream_item: dict,
):
    """Resolve deletion targets exclusively from persisted managed IDs."""
    code = get_stream_abbreviation(level, stream)
    category_name = _stream_category_name(level, stream, code)
    voice_name = f"🔊-{_safe_name(code, 30)}-à-distance"
    expected_channels = _stream_channel_names(level, stream, stream_item)
    expected_roles = _stream_role_names(level, stream)

    all_channels = await _fetch_channels(guild)

    category_id = _recorded_id(config, "categories", category_name)
    voice_category_id = _recorded_id(config, "categories", CATEGORY_VOICE)
    if category_id is None:
        category = None
    else:
        candidate = guild.get_channel(category_id)
        category = (
            candidate
            if isinstance(candidate, discord.CategoryChannel)
            and _norm(candidate.name) == _norm(category_name)
            else None
        )

    if voice_category_id is None:
        voice_category = None
    else:
        candidate = guild.get_channel(voice_category_id)
        voice_category = (
            candidate
            if isinstance(candidate, discord.CategoryChannel)
            and _norm(candidate.name) == _norm(CATEGORY_VOICE)
            else None
        )

    channels: dict[str, int] = {}
    if category is not None:
        for name in expected_channels:
            recorded = _recorded_id(config, "channels", name)
            if recorded is None:
                continue
            candidate = guild.get_channel(recorded)
            if (
                isinstance(candidate, discord.TextChannel)
                and candidate.category_id == category.id
                and _norm(candidate.name) == _norm(name)
            ):
                channels[name] = candidate.id

    voice = None
    recorded_voice = _recorded_id(config, "channels", voice_name)
    if recorded_voice is not None and voice_category is not None:
        candidate = guild.get_channel(recorded_voice)
        if (
            isinstance(candidate, discord.VoiceChannel)
            and candidate.category_id == voice_category.id
            and _norm(candidate.name) == _norm(voice_name)
        ):
            voice = candidate

    roles: dict[str, int] = {}
    for name in expected_roles:
        recorded = _recorded_id(config, "roles", name)
        if recorded is None:
            continue
        candidate = guild.get_role(recorded)
        if (
            isinstance(candidate, discord.Role)
            and not candidate.managed
            and _norm(candidate.name) == _norm(name)
        ):
            roles[name] = candidate.id

    missing: list[str] = []
    if category is None:
        missing.append(f"catégorie `{category_name}` (ID géré manquant/invalide)")
    if voice_category is None:
        missing.append(f"catégorie vocale `{CATEGORY_VOICE}` (ID géré manquant/invalide)")
    if voice is None:
        missing.append(f"salon vocal `{voice_name}` (ID géré manquant/invalide)")

    missing_channels = expected_channels - set(channels)
    if missing_channels:
        missing.append(f"{len(missing_channels)} salon(s) géré(s) avec ID manquant/invalide")

    missing_roles = expected_roles - set(roles)
    if missing_roles:
        missing.append(f"{len(missing_roles)} rôle(s) géré(s) avec ID manquant/invalide")

    # Keep the live snapshot in this resolver for consistency with the remote API
    # even when guild.get_channel/get_role is backed by Discord.py cache state.
    _ = all_channels

    return (
        roles,
        channels,
        category,
        voice,
        voice_category,
        ", ".join(missing) if missing else None,
    )


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
    async def remove_stream(
        self,
        interaction: discord.Interaction,
        level: str,
        stream: str,
    ) -> None:
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
        expected_channel_names = _stream_channel_names(level, stream, stream_item)

        try:
            await validate_managed_registry(guild, config)
        except RuntimeError as exc:
            await interaction.response.send_message(
                _fail(
                    f"Suppression refusée pour **{code}** : identité gérée incohérente (`{exc}`). "
                    "Aucun changement effectué."
                ),
                ephemeral=True,
            )
            return

        (
            role_ids,
            channel_ids,
            category,
            voice,
            voice_category,
            registry_error,
        ) = await _resolve_registry(guild, config, level, stream, stream_item)

        if registry_error:
            await interaction.response.send_message(
                _fail(
                    f"Suppression refusée pour **{code}** : ressources gérées absentes/incomplètes ({registry_error}). "
                    "Aucune ressource et aucune configuration n'ont été modifiées."
                ),
                ephemeral=True,
            )
            return

        assert category is not None
        assert voice is not None
        assert voice_category is not None

        verified_channels: list[discord.TextChannel] = []
        for name in sorted(expected_channel_names):
            channel = guild.get_channel(channel_ids[name])
            if (
                not isinstance(channel, discord.TextChannel)
                or channel.category_id != category.id
                or _norm(channel.name) != _norm(name)
            ):
                await interaction.response.send_message(
                    _fail(
                        f"Suppression refusée pour **{code}** : salon géré `{name}` absent ou incohérent. "
                        "Aucun changement effectué."
                    ),
                    ephemeral=True,
                )
                return
            verified_channels.append(channel)

        verified_roles: list[discord.Role] = []
        for name in sorted(expected_role_names):
            role = guild.get_role(role_ids[name])
            if (
                role is None
                or role.managed
                or role.is_default()
                or _norm(role.name) != _norm(name)
            ):
                await interaction.response.send_message(
                    _fail(
                        f"Suppression refusée pour **{code}** : rôle géré `{name}` absent ou incohérent. "
                        "Aucun changement effectué."
                    ),
                    ephemeral=True,
                )
                return
            verified_roles.append(role)

        if voice.category_id != voice_category.id or _norm(voice.name) != _norm(voice_name):
            await interaction.response.send_message(
                _fail(
                    f"Suppression refusée pour **{code}** : salon vocal absent ou incohérent. "
                    "Aucun changement effectué."
                ),
                ephemeral=True,
            )
            return

        top_role = guild.me.top_role if guild.me is not None else None
        blocked_roles = [
            role.name
            for role in verified_roles
            if top_role is not None and role >= top_role
        ]
        if blocked_roles:
            await interaction.response.send_message(
                _fail(
                    f"Suppression refusée pour **{code}** : hiérarchie Discord insuffisante pour supprimer les rôles "
                    f"{', '.join(blocked_roles)}. Aucun changement effectué."
                ),
                ephemeral=True,
            )
            return

        lock = get_build_lock(guild.id)
        if lock.locked():
            await interaction.response.send_message(
                "⏳ Une construction est déjà en cours sur ce serveur.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"🗑️ Suppression sécurisée de **{code}** en cours...",
            ephemeral=True,
        )

        candidate = deepcopy(config)
        candidate_level, _candidate_stream = _find_level_stream(candidate, level, stream)
        if candidate_level is None:
            await interaction.followup.send(
                _fail("Configuration devenue incohérente avant suppression. Rien n'a été modifié."),
                ephemeral=True,
            )
            return

        try:
            async with lock:
                for channel in verified_channels:
                    await channel.delete(reason="School Manager scoped stream removal")
                await voice.delete(reason="School Manager scoped stream voice removal")
                for role in verified_roles:
                    await role.delete(reason="School Manager scoped stream role removal")

                target_category = guild.get_channel(category.id)
                if isinstance(target_category, discord.CategoryChannel):
                    remaining = [
                        channel
                        for channel in guild.channels
                        if getattr(channel, "category_id", None) == target_category.id
                    ]
                    if not remaining:
                        await target_category.delete(reason="School Manager scoped stream category removal")

                candidate_level["streams"] = [
                    item
                    for item in candidate_level.get("streams", [])
                    if not (isinstance(item, dict) and item.get("name") == stream)
                ]
                candidate["levels"] = [
                    item
                    for item in candidate.get("levels", [])
                    if not (
                        isinstance(item, dict)
                        and item.get("name") == level
                        and not item.get("streams")
                    )
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
                f"❌ Suppression interrompue : `{type(exc).__name__}`. La configuration n'a pas été mise à jour; vérifie l'état Discord avant de réessayer.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ **{code}** supprimée. Les ressources gérées ont été supprimées; les salons non gérés de la catégorie, s'il y en a, ont été conservés.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SafeRemoveStream(bot))

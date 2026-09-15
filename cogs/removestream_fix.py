"""Fail-closed /removestream implementation.

Destructive stream removal resolves every deletion target from the persisted
managed-resource registry. Resource names are used only to verify that a
registered ID still identifies the expected resource; names are never used to
discover new deletion targets.

The destructive mutation phase is protected by a persisted write-ahead
journal so a process crash or a Discord API failure can be resumed safely
without rediscovering resources by name.
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
from services.removestream_transaction import (
    PENDING_REMOVAL_KEY,
    build_removal_journal,
    clear_removal_journal,
    execute_removal_journal,
    get_pending_removal,
    install_removal_journal,
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


def _checkpoint_factory(guild_id: int):
    def checkpoint(config: dict) -> None:
        save_guild_config(guild_id, config)

    return checkpoint


def _journal_target(guild: discord.Guild, resource: dict):
    kind = resource.get("kind")
    resource_id = resource.get("id")
    if not isinstance(resource_id, int) or resource_id <= 0:
        return None
    if kind == "channel":
        return guild.get_channel(resource_id)
    if kind == "role":
        return guild.get_role(resource_id)
    return None


def _remove_journal_owned_entries(config: dict, journal: dict) -> None:
    """Remove only managed mappings whose name AND ID still match the journal."""
    managed = config.get("managed") if isinstance(config, dict) else None
    if not isinstance(managed, dict):
        return

    for resource in journal.get("resources", []):
        if not isinstance(resource, dict):
            continue
        kind = resource.get("kind")
        name = resource.get("name")
        resource_id = resource.get("id")
        if kind == "channel":
            section = "channels"
        elif kind == "role":
            section = "roles"
        else:
            continue
        mapping = managed.get(section)
        if (
            isinstance(mapping, dict)
            and isinstance(name, str)
            and isinstance(resource_id, int)
            and mapping.get(name) == resource_id
        ):
            mapping.pop(name, None)

    category = journal.get("category")
    if not isinstance(category, dict):
        return
    category_name = category.get("name")
    category_id = category.get("id")
    mapping = managed.get("categories")
    if (
        isinstance(mapping, dict)
        and isinstance(category_name, str)
        and isinstance(category_id, int)
        and mapping.get(category_name) == category_id
    ):
        mapping.pop(category_name, None)


def _finalize_stream_config(config: dict, journal: dict) -> dict:
    """Return a copy with the journaled stream removed, fail-closed on mismatch."""
    level = journal.get("level")
    stream = journal.get("stream")
    if not isinstance(level, str) or not isinstance(stream, str):
        raise RuntimeError("journal de suppression invalide: niveau/filière absents")

    candidate = deepcopy(config)
    candidate_level, _candidate_stream = _find_level_stream(candidate, level, stream)
    if candidate_level is None or _candidate_stream is None:
        raise RuntimeError(
            f"journal de suppression incohérent: filière `{stream}` absente de la configuration"
        )

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
    _remove_journal_owned_entries(candidate, journal)
    return candidate


async def _finalize_journal_category(
    guild: discord.Guild,
    journal: dict,
) -> None:
    """Delete the journaled stream category only after a fresh emptiness check."""
    category = journal.get("category")
    if not isinstance(category, dict):
        raise RuntimeError("journal de suppression invalide: catégorie absente")
    category_id = category.get("id")
    category_name = category.get("name")
    if (
        not isinstance(category_id, int)
        or category_id <= 0
        or not isinstance(category_name, str)
    ):
        raise RuntimeError("journal de suppression invalide: identité de catégorie incorrecte")

    try:
        live_channels = list(await guild.fetch_channels())
    except (discord.Forbidden, discord.HTTPException) as exc:
        raise RuntimeError(
            "impossible de vérifier l'état actuel de la catégorie avant sa suppression"
        ) from exc

    target = next(
        (channel for channel in live_channels if getattr(channel, "id", None) == category_id),
        None,
    )
    if target is None:
        return
    if not isinstance(target, discord.CategoryChannel):
        raise RuntimeError(
            f"l'ID de catégorie géré {category_id} désigne un autre type de ressource"
        )
    if _norm(target.name) != _norm(category_name):
        raise RuntimeError(
            f"l'ID de catégorie géré {category_id} désigne `{target.name}` au lieu de `{category_name}`"
        )

    remaining = [
        channel
        for channel in live_channels
        if getattr(channel, "category_id", None) == category_id
    ]
    if remaining:
        return

    try:
        await target.delete(reason="School Manager scoped empty stream category removal")
    except discord.NotFound:
        return


async def _recover_pending_removal(
    guild: discord.Guild,
    config: dict,
    journal: dict,
) -> None:
    """Finish an interrupted deletion and commit logical config only at the end."""
    checkpoint = _checkpoint_factory(guild.id)
    await execute_removal_journal(
        config=config,
        journal=journal,
        resolve=lambda resource: _journal_target(guild, resource),
        checkpoint=checkpoint,
    )
    await _finalize_journal_category(guild, journal)

    candidate = _finalize_stream_config(config, journal)
    clear_removal_journal(candidate, checkpoint=checkpoint)
    config.clear()
    config.update(candidate)


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

        config = get_guild_config(guild.id)
        if not config:
            await interaction.response.send_message(_fail("Configuration absente."), ephemeral=True)
            return

        pending_raw = config.get(PENDING_REMOVAL_KEY)
        pending = get_pending_removal(config)
        if pending_raw is not None and pending is None:
            await interaction.response.send_message(
                _fail(
                    "Une opération de suppression interrompue possède un journal invalide. "
                    "Aucune nouvelle suppression ne sera exécutée tant que cet état n'est pas réconcilié."
                ),
                ephemeral=True,
            )
            return

        if pending is not None:
            pending_level = pending.get("level")
            pending_stream = pending.get("stream")
            if pending_level != level or pending_stream != stream:
                await interaction.response.send_message(
                    _fail(
                        f"Une suppression interrompue de **{pending.get('code', '?')}** doit d'abord être récupérée "
                        f"avec `{pending_level}` / `{pending_stream}`. Aucune autre suppression n'a été exécutée."
                    ),
                    ephemeral=True,
                )
                return
            lock = get_build_lock(guild.id)
            if lock.locked():
                await interaction.response.send_message(
                    "⏳ Une opération de construction/suppression est déjà en cours sur ce serveur.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                f"♻️ Reprise sécurisée de la suppression interrompue de **{pending.get('code', '?')}**...",
                ephemeral=True,
            )
            try:
                async with lock:
                    await _recover_pending_removal(guild, config, pending)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound, OSError, RuntimeError) as exc:
                await interaction.followup.send(
                    f"❌ Reprise interrompue : `{type(exc).__name__}`. Le journal reste conservé pour une nouvelle reprise sûre.",
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                f"✅ Reprise terminée : **{pending.get('code', '?')}** est maintenant cohérente côté Discord et configuration.",
                ephemeral=True,
            )
            return

        if level not in get_levels() or stream not in get_streams(level):
            await interaction.response.send_message(_fail("Niveau ou filière invalide."), ephemeral=True)
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

        resources = [
            {
                "kind": "channel",
                "id": channel.id,
                "name": name,
            }
            for name, channel in zip(
                sorted(expected_channel_names),
                verified_channels,
                strict=True,
            )
        ]
        resources.append(
            {
                "kind": "channel",
                "id": voice.id,
                "name": voice_name,
            }
        )
        resources.extend(
            {
                "kind": "role",
                "id": role.id,
                "name": role.name,
            }
            for role in verified_roles
        )
        journal = build_removal_journal(
            level=level,
            stream=stream,
            code=code,
            resources=resources,
            category={
                "id": category.id,
                "name": category_name,
            },
        )
        checkpoint = _checkpoint_factory(guild.id)

        try:
            async with lock:
                install_removal_journal(config, journal, checkpoint)
                await execute_removal_journal(
                    config=config,
                    journal=journal,
                    resolve=lambda resource: _journal_target(guild, resource),
                    checkpoint=checkpoint,
                )
                await _finalize_journal_category(guild, journal)

                candidate = _finalize_stream_config(config, journal)
                clear_removal_journal(candidate, checkpoint=checkpoint)
                config.clear()
                config.update(candidate)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound, OSError, RuntimeError) as exc:
            await interaction.followup.send(
                f"❌ Suppression interrompue : `{type(exc).__name__}`. Le journal de reprise a été conservé; aucun nouveau resource target ne sera découvert par nom.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ **{code}** supprimée. Les ressources gérées ont été supprimées; les salons non gérés de la catégorie, s'il y en a, ont été conservés.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SafeRemoveStream(bot))

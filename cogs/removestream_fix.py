"""Fail-closed /removestream implementation.

Destructive stream removal resolves deletion targets from the persisted managed
-resource registry. A registered resource that is already absent from Discord
is treated as already deleted; the bot still refuses to adopt a different live
resource with the same name.

The destructive mutation phase is protected by a persisted write-ahead journal
so a process crash or a Discord API failure can be resumed safely without
rediscovering resources by name.
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


def _stream_channel_names(level: str, stream: str, stream_item: dict | None = None) -> set[str]:
    code = get_stream_abbreviation(level, stream)
    subjects = get_stream_subjects(level, stream)
    if isinstance(stream_item, dict) and isinstance(stream_item.get("subjects"), list):
        subjects = [subject for subject in stream_item["subjects"] if isinstance(subject, str)]
    return {
        f"📌-{code}・informations",
        f"🗓️-{code}・emploi-du-temps",
        f"📝-{code}・examens",
        *{_subject_channel_name(code, subject) for subject in subjects},
    }


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


def _checkpoint_factory(guild_id: int):
    def checkpoint(config: dict) -> None:
        save_guild_config(guild_id, config)
    return checkpoint


def _registry_removal_journal(config: dict, *, level: str, stream: str, stream_item: dict) -> tuple[dict | None, list[str]]:
    """Create a deletion plan from persisted IDs; live absence is not an error."""
    code = get_stream_abbreviation(level, stream)
    category_name = _stream_category_name(level, stream, code)
    voice_name = f"🔊-{_safe_name(code, 30)}-à-distance"
    expected_channels = _stream_channel_names(level, stream, stream_item)
    expected_roles = _stream_role_names(level, stream)

    category_id = _recorded_id(config, "categories", category_name)
    voice_category_id = _recorded_id(config, "categories", CATEGORY_VOICE)
    missing: list[str] = []
    if category_id is None:
        missing.append(f"catégorie `{category_name}`: ID géré manquant")
    if voice_category_id is None:
        missing.append(f"catégorie vocale `{CATEGORY_VOICE}`: ID géré manquant")

    resources: list[dict] = []
    if category_id is not None:
        for name in sorted(expected_channels):
            resource_id = _recorded_id(config, "channels", name)
            if resource_id is None:
                missing.append(f"salon `{name}`: ID géré manquant")
                continue
            resources.append({
                "kind": "channel",
                "id": resource_id,
                "name": name,
                "channel_type": "text",
                "category_id": category_id,
            })

    recorded_voice = _recorded_id(config, "channels", voice_name)
    if recorded_voice is None:
        missing.append(f"salon vocal `{voice_name}`: ID géré manquant")
    elif voice_category_id is not None:
        resources.append({
            "kind": "channel",
            "id": recorded_voice,
            "name": voice_name,
            "channel_type": "voice",
            "category_id": voice_category_id,
        })

    for name in sorted(expected_roles):
        resource_id = _recorded_id(config, "roles", name)
        if resource_id is None:
            missing.append(f"rôle `{name}`: ID géré manquant")
            continue
        resources.append({"kind": "role", "id": resource_id, "name": name})

    if missing:
        return None, missing

    return build_removal_journal(
        level=level,
        stream=stream,
        code=code,
        resources=resources,
        category={"id": category_id, "name": category_name},
    ), []


def _journal_identity_error(guild: discord.Guild, journal: dict) -> str | None:
    """Validate existing targets without treating already-deleted IDs as conflicts."""
    category = journal.get("category")
    if not isinstance(category, dict):
        return "journal de suppression invalide: catégorie absente"
    category_id = category.get("id")
    category_name = category.get("name")
    if not isinstance(category_id, int) or category_id <= 0 or not isinstance(category_name, str):
        return "journal de suppression invalide: identité de catégorie incorrecte"
    live_category = guild.get_channel(category_id)
    if live_category is not None:
        if not isinstance(live_category, discord.CategoryChannel):
            return f"l'ID de catégorie géré {category_id} désigne un autre type de ressource"
        if _norm(live_category.name) != _norm(category_name):
            return f"l'ID de catégorie géré {category_id} désigne `{live_category.name}` au lieu de `{category_name}`"

    for resource in journal.get("resources", []):
        try:
            _journal_target(guild, resource)
        except RuntimeError as exc:
            return str(exc)
    return None


def _journal_target(guild: discord.Guild, resource: dict):
    """Resolve a persisted target; an absent registered ID is already deleted."""
    kind = resource.get("kind")
    resource_id = resource.get("id")
    name = resource.get("name")
    if not isinstance(resource_id, int) or resource_id <= 0:
        raise RuntimeError("journal resource ID invalide")
    if not isinstance(name, str) or not name:
        raise RuntimeError("journal resource name invalide")

    if kind == "channel":
        target = guild.get_channel(resource_id)
        if target is None:
            return None
        channel_type = resource.get("channel_type")
        expected_category_id = resource.get("category_id")
        if channel_type == "text":
            if not isinstance(target, discord.TextChannel):
                raise RuntimeError(f"l'ID de salon géré {resource_id} désigne un type différent")
        elif channel_type == "voice":
            if not isinstance(target, discord.VoiceChannel):
                raise RuntimeError(f"l'ID de salon vocal géré {resource_id} désigne un type différent")
        else:
            raise RuntimeError(f"type de salon journalisé invalide pour {resource_id}")
        if not isinstance(expected_category_id, int) or expected_category_id <= 0:
            raise RuntimeError(f"catégorie journalisée invalide pour le salon {resource_id}")
        if getattr(target, "category_id", None) != expected_category_id:
            raise RuntimeError(f"l'ID de salon géré {resource_id} n'appartient plus à la catégorie attendue")
        if _norm(target.name) != _norm(name):
            raise RuntimeError(f"l'ID de salon géré {resource_id} désigne `{target.name}` au lieu de `{name}`")
        return target

    if kind == "role":
        target = guild.get_role(resource_id)
        if target is None:
            return None
        if not isinstance(target, discord.Role):
            raise RuntimeError(f"l'ID de rôle géré {resource_id} désigne un type différent")
        if target.managed or target.is_default():
            raise RuntimeError(f"l'ID de rôle géré {resource_id} désigne un rôle non supprimable")
        if _norm(target.name) != _norm(name):
            raise RuntimeError(f"l'ID de rôle géré {resource_id} désigne `{target.name}` au lieu de `{name}`")
        return target

    raise RuntimeError(f"type de ressource journalisé invalide: {kind!r}")


async def _resolve_registry(
    guild: discord.Guild,
    config: dict,
    level: str,
    stream: str,
    stream_item: dict,
):
    """Legacy fail-closed resolver retained for the destructive-boundary tests.

    It resolves only persisted IDs and never discovers deletion targets by name.
    The active command uses ``_registry_removal_journal`` so missing live IDs are
    handled idempotently by the transaction journal.
    """
    code = get_stream_abbreviation(level, stream)
    category_name = _stream_category_name(level, stream, code)
    voice_name = f"🔊-{_safe_name(code, 30)}-à-distance"
    expected_channels = _stream_channel_names(level, stream, stream_item)
    expected_roles = _stream_role_names(level, stream)

    category_id = _recorded_id(config, "categories", category_name)
    voice_category_id = _recorded_id(config, "categories", CATEGORY_VOICE)
    category = guild.get_channel(category_id) if category_id is not None else None
    if not isinstance(category, discord.CategoryChannel) or _norm(category.name) != _norm(category_name):
        category = None
    voice_category = guild.get_channel(voice_category_id) if voice_category_id is not None else None
    if not isinstance(voice_category, discord.CategoryChannel) or _norm(voice_category.name) != _norm(CATEGORY_VOICE):
        voice_category = None

    channels: dict[str, int] = {}
    if category is not None:
        for name in expected_channels:
            recorded = _recorded_id(config, "channels", name)
            if recorded is None:
                continue
            candidate = guild.get_channel(recorded)
            if isinstance(candidate, discord.TextChannel) and candidate.category_id == category.id and _norm(candidate.name) == _norm(name):
                channels[name] = candidate.id

    voice = None
    recorded_voice = _recorded_id(config, "channels", voice_name)
    if recorded_voice is not None and voice_category is not None:
        candidate = guild.get_channel(recorded_voice)
        if isinstance(candidate, discord.VoiceChannel) and candidate.category_id == voice_category.id and _norm(candidate.name) == _norm(voice_name):
            voice = candidate

    roles: dict[str, int] = {}
    for name in expected_roles:
        recorded = _recorded_id(config, "roles", name)
        if recorded is None:
            continue
        candidate = guild.get_role(recorded)
        if isinstance(candidate, discord.Role) and not candidate.managed and _norm(candidate.name) == _norm(name):
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

    return roles, channels, category, voice, voice_category, ", ".join(missing) if missing else None


async def _finalize_journal_category(guild: discord.Guild, journal: dict) -> None:
    category = journal.get("category")
    if not isinstance(category, dict):
        raise RuntimeError("journal de suppression invalide: catégorie absente")
    category_id = category.get("id")
    category_name = category.get("name")
    if not isinstance(category_id, int) or category_id <= 0 or not isinstance(category_name, str):
        raise RuntimeError("journal de suppression invalide: identité de catégorie incorrecte")
    try:
        live_channels = list(await guild.fetch_channels())
    except (discord.Forbidden, discord.HTTPException) as exc:
        raise RuntimeError("impossible de vérifier l'état actuel de la catégorie avant sa suppression") from exc
    target = next((channel for channel in live_channels if getattr(channel, "id", None) == category_id), None)
    if target is None:
        return
    if not isinstance(target, discord.CategoryChannel):
        raise RuntimeError(f"l'ID de catégorie géré {category_id} désigne un autre type de ressource")
    if _norm(target.name) != _norm(category_name):
        raise RuntimeError(f"l'ID de catégorie géré {category_id} désigne `{target.name}` au lieu de `{category_name}`")
    remaining = [channel for channel in live_channels if getattr(channel, "category_id", None) == category_id]
    if remaining:
        return
    try:
        await target.delete(reason="School Manager scoped empty stream category removal")
    except discord.NotFound:
        return


async def _recover_pending_removal(guild: discord.Guild, config: dict, journal: dict) -> None:
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


def _remove_journal_owned_entries(config: dict, journal: dict) -> None:
    managed = config.get("managed") if isinstance(config, dict) else None
    if not isinstance(managed, dict):
        return
    for resource in journal.get("resources", []):
        if not isinstance(resource, dict):
            continue
        kind = resource.get("kind")
        name = resource.get("name")
        resource_id = resource.get("id")
        section = "channels" if kind == "channel" else "roles" if kind == "role" else None
        mapping = managed.get(section) if section else None
        if isinstance(mapping, dict) and isinstance(name, str) and isinstance(resource_id, int) and mapping.get(name) == resource_id:
            mapping.pop(name, None)
    category = journal.get("category")
    if isinstance(category, dict):
        category_name = category.get("name")
        category_id = category.get("id")
        mapping = managed.get("categories")
        if isinstance(mapping, dict) and isinstance(category_name, str) and isinstance(category_id, int) and mapping.get(category_name) == category_id:
            mapping.pop(category_name, None)


def _finalize_stream_config(config: dict, journal: dict) -> dict:
    level = journal.get("level")
    stream = journal.get("stream")
    if not isinstance(level, str) or not isinstance(stream, str):
        raise RuntimeError("journal de suppression invalide: niveau/filière absents")
    candidate = deepcopy(config)
    candidate_level, candidate_stream = _find_level_stream(candidate, level, stream)
    if candidate_level is None or candidate_stream is None:
        raise RuntimeError(f"journal de suppression incohérent: filière `{stream}` absente de la configuration")
    candidate_level["streams"] = [
        item for item in candidate_level.get("streams", [])
        if not (isinstance(item, dict) and item.get("name") == stream)
    ]
    candidate["levels"] = [
        item for item in candidate.get("levels", [])
        if not (isinstance(item, dict) and item.get("name") == level and not item.get("streams"))
    ]
    _remove_journal_owned_entries(candidate, journal)
    return candidate


async def level_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=level, value=level) for level in get_levels() if current.casefold() in level.casefold()][:25]


async def stream_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    if level not in get_levels():
        return []
    return [app_commands.Choice(name=stream, value=stream) for stream in get_streams(level) if current.casefold() in stream.casefold()][:25]


class SafeRemoveStream(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="removestream", description="Supprimer une filière uniquement après vérification de ses ressources gérées.")
    @app_commands.describe(level="Niveau", stream="Filière à supprimer")
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @management_check()
    async def remove_stream(self, interaction: discord.Interaction, level: str, stream: str) -> None:
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
            await interaction.response.send_message(_fail("Une opération de suppression interrompue possède un journal invalide. Aucune nouvelle suppression ne sera exécutée tant que cet état n'est pas réconcilié."), ephemeral=True)
            return

        if pending is not None:
            if pending.get("level") != level or pending.get("stream") != stream:
                await interaction.response.send_message(_fail(f"Une suppression interrompue de **{pending.get('code', '?')}** doit d'abord être récupérée avec `{pending.get('level', '?')}` / `{pending.get('stream', '?')}`."), ephemeral=True)
                return
            lock = get_build_lock(guild.id)
            if lock.locked():
                await interaction.response.send_message("⏳ Une opération de construction/suppression est déjà en cours sur ce serveur.", ephemeral=True)
                return
            await interaction.response.send_message(f"♻️ Reprise sécurisée de la suppression interrompue de **{pending.get('code', '?')}**...", ephemeral=True)
            try:
                async with lock:
                    await _recover_pending_removal(guild, config, pending)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound, OSError, RuntimeError) as exc:
                await interaction.followup.send(f"❌ Reprise interrompue : `{type(exc).__name__}`. Le journal reste conservé pour une nouvelle reprise sûre.", ephemeral=True)
                return
            await interaction.followup.send(f"✅ Reprise terminée : **{pending.get('code', '?')}** est maintenant cohérente côté Discord et configuration.", ephemeral=True)
            return

        if level not in get_levels() or stream not in get_streams(level):
            await interaction.response.send_message(_fail("Niveau ou filière invalide."), ephemeral=True)
            return

        level_item, stream_item = _find_level_stream(config, level, stream)
        if level_item is None or stream_item is None:
            await interaction.response.send_message(f"ℹ️ **{get_stream_abbreviation(level, stream)}** n'est pas configurée.", ephemeral=True)
            return

        # Keep the established managed-registry validation contract, but do not
        # let a same-name unmanaged target stream block its own scoped removal.
        original_config = config
        validation_config = deepcopy(config)
        code = get_stream_abbreviation(level, stream)
        target_channel_names = _stream_channel_names(level, stream, stream_item)
        target_role_names = _stream_role_names(level, stream)
        target_category_name = _stream_category_name(level, stream, code)
        validation_managed = validation_config.get("managed")
        if isinstance(validation_managed, dict):
            for section, names in (("channels", target_channel_names), ("roles", target_role_names), ("categories", {target_category_name})):
                mapping = validation_managed.get(section)
                if isinstance(mapping, dict):
                    for name in names:
                        mapping.pop(name, None)
        config = validation_config
        try:
            await validate_managed_registry(guild, config)
        except RuntimeError as exc:
            await interaction.response.send_message(_fail(f"Suppression refusée pour **{code}** : identité gérée incohérente (`{exc}`). Aucun changement effectué."), ephemeral=True)
            return
        finally:
            config = original_config

        journal, missing_registry = _registry_removal_journal(config, level=level, stream=stream, stream_item=stream_item)
        if journal is None:
            await interaction.response.send_message(_fail(f"Suppression refusée pour **{code}** : identité(s) gérée(s) manquante(s) dans la configuration ({'; '.join(missing_registry)}). Aucun changement effectué."), ephemeral=True)
            return

        identity_error = _journal_identity_error(guild, journal)
        if identity_error:
            await interaction.response.send_message(_fail(f"Suppression refusée pour **{code}** : identité gérée incohérente (`{identity_error}`). Aucun changement effectué."), ephemeral=True)
            return

        blocked_roles: list[str] = []
        top_role = guild.me.top_role if guild.me is not None else None
        if top_role is not None:
            for resource in journal.get("resources", []):
                if resource.get("kind") != "role":
                    continue
                role = guild.get_role(resource.get("id"))
                if role is not None and role >= top_role:
                    blocked_roles.append(role.name)
        if blocked_roles:
            await interaction.response.send_message(_fail(f"Suppression refusée pour **{code}** : hiérarchie Discord insuffisante pour supprimer les rôles {', '.join(sorted(set(blocked_roles)))}. Aucun changement effectué."), ephemeral=True)
            return

        lock = get_build_lock(guild.id)
        if lock.locked():
            await interaction.response.send_message("⏳ Une construction est déjà en cours sur ce serveur.", ephemeral=True)
            return

        await interaction.response.send_message(f"🗑️ Suppression sécurisée de **{code}** en cours...", ephemeral=True)
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
            await interaction.followup.send(f"❌ Suppression interrompue : `{type(exc).__name__}`. Le journal de reprise a été conservé; aucun nouveau resource target ne sera découvert par nom.", ephemeral=True)
            return

        await interaction.followup.send(f"✅ **{code}** supprimée. Les ressources gérées encore présentes ont été supprimées et les ressources déjà absentes ont été considérées comme déjà supprimées; les salons non gérés ont été conservés.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SafeRemoveStream(bot))

"""Server structure and academic-year management commands."""

from __future__ import annotations

import re
from copy import deepcopy

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import (
    GENERAL_CHANNELS,
    PROFESSOR_CHANNELS,
    get_levels,
    get_stream_abbreviation,
    get_streams,
    get_stream_subjects,
)
from services.build_guard import get_build_lock
from services.build_transaction import build_and_persist
from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_PROFESSOR_FEMALE,
    ROLE_STUDENT,
    STREAM_ROLE_PREFIX,
    STUDENT_STREAM_ROLE_PREFIX,
    management_check,
)
from services.server_builder import (
    CATEGORY_GENERAL,
    CATEGORY_PROFESSORS,
    CATEGORY_VOICE,
    _safe_name,
    _stream_category_name,
    _subject_channel_name,
)
from services.storage import (
    create_and_activate_academic_year,
    get_active_academic_year,
    get_guild_config,
    list_academic_years,
    save_guild_config,
)

LEVEL_ABBREVIATIONS = {
    "Tronc Commun": "TC",
    "1ère Année Bac": "1BAC",
    "2ème Année Bac": "2BAC",
}


def _contains(value: str, current: str) -> bool:
    return current.casefold() in value.casefold()


async def level_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=level, value=level)
        for level in get_levels()
        if _contains(level, current)
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
        if _contains(stream, current)
    ][:25]


def _configured_managed_ids(
    config: dict,
    guild: discord.Guild | None = None,
) -> tuple[set[int], set[int], set[int]]:
    """Return managed resource IDs, expanding canonical resources from live guild state."""
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    managed = managed if isinstance(managed, dict) else {}

    def ids_for(kind: str) -> set[int]:
        values = managed.get(kind, {})
        if not isinstance(values, dict):
            return set()
        return {
            value
            for value in values.values()
            if isinstance(value, int) and value > 0
        }

    role_ids = ids_for("roles")
    channel_ids = ids_for("channels")
    category_ids = ids_for("categories")
    if guild is None:
        return role_ids, channel_ids, category_ids

    expected_categories = {CATEGORY_GENERAL, CATEGORY_PROFESSORS, CATEGORY_VOICE}
    expected_roles = {
        ROLE_ADMIN,
        ROLE_PROFESSOR,
        ROLE_PROFESSOR_FEMALE,
        ROLE_STUDENT,
    }
    for level in config.get("levels", []):
        if not isinstance(level, dict):
            continue
        level_name = level.get("name")
        if not isinstance(level_name, str):
            continue
        for stream in level.get("streams", []) or []:
            if not isinstance(stream, dict) or not isinstance(stream.get("name"), str):
                continue
            stream_name = stream["name"]
            code = str(
                stream.get("abbreviation")
                or get_stream_abbreviation(level_name, stream_name)
            )
            expected_categories.add(
                _stream_category_name(level_name, stream_name, code)
            )
            expected_roles.update(
                {
                    f"{STREAM_ROLE_PREFIX}{code}",
                    f"{STUDENT_STREAM_ROLE_PREFIX}{code}",
                }
            )

    for category in guild.categories:
        if category.name in expected_categories:
            category_ids.add(category.id)
            channel_ids.update(channel.id for channel in category.channels)

    role_ids.update(
        role.id
        for role in guild.roles
        if not role.managed and role.name in expected_roles
    )
    return role_ids, channel_ids, category_ids


def _stream_configured(config: dict, level: str, stream: str) -> bool:
    for configured_level in config.get("levels", []):
        if not isinstance(configured_level, dict):
            continue
        if configured_level.get("name") == level:
            return any(
                isinstance(item, dict) and item.get("name") == stream
                for item in configured_level.get("streams", []) or []
            )
    return False


def _expected_structure_names(
    config: dict,
) -> tuple[set[str], set[str], dict[str, set[str]]]:
    expected_roles = {
        ROLE_ADMIN,
        ROLE_PROFESSOR,
        ROLE_PROFESSOR_FEMALE,
        ROLE_STUDENT,
    }
    expected_categories = {
        CATEGORY_GENERAL,
        CATEGORY_PROFESSORS,
        CATEGORY_VOICE,
    }
    expected_channels_by_category: dict[str, set[str]] = {
        CATEGORY_GENERAL: set(GENERAL_CHANNELS.values()),
        CATEGORY_PROFESSORS: {
            PROFESSOR_CHANNELS["discussion"],
            PROFESSOR_CHANNELS["meeting"],
        },
        CATEGORY_VOICE: set(),
    }
    stream_codes: set[str] = set()
    for level in config.get("levels", []):
        if not isinstance(level, dict):
            continue
        level_name = level.get("name")
        if not isinstance(level_name, str):
            continue
        for stream in level.get("streams", []) or []:
            if not isinstance(stream, dict) or not isinstance(stream.get("name"), str):
                continue
            stream_name = stream["name"]
            code = str(
                stream.get("abbreviation")
                or get_stream_abbreviation(level_name, stream_name)
            )
            stream_codes.add(code)
            category_name = _stream_category_name(
                level_name,
                stream_name,
                code,
            )
            expected_categories.add(category_name)
            expected_roles.update(
                {
                    f"{STREAM_ROLE_PREFIX}{code}",
                    f"{STUDENT_STREAM_ROLE_PREFIX}{code}",
                }
            )
            subjects = stream.get("subjects", []) or get_stream_subjects(
                level_name,
                stream_name,
            )
            expected_channels_by_category[category_name] = {
                f"📌-{code}・informations",
                f"🗓️-{code}・emploi-du-temps",
                f"📝-{code}・examens",
                *{
                    _subject_channel_name(code, subject)
                    for subject in subjects
                },
            }

    expected_channels_by_category[CATEGORY_VOICE] = {
        f"🔊-{_safe_name(code, 30)}-à-distance"
        for code in stream_codes
    }
    return expected_roles, expected_categories, expected_channels_by_category


async def _managed_resource_state(
    guild: discord.Guild,
    config: dict,
) -> tuple[bool, int, int, int]:
    """Inspect live Discord state using the same canonical names as the builder."""
    expected_roles, expected_categories, expected_channels_by_category = (
        _expected_structure_names(config)
    )
    try:
        channels = list(await guild.fetch_channels())
    except (discord.Forbidden, discord.HTTPException):
        channels = list(guild.channels)

    categories_by_name = {
        channel.name: channel
        for channel in channels
        if isinstance(channel, discord.CategoryChannel)
    }
    if not categories_by_name:
        categories_by_name = {
            category.name: category
            for category in guild.categories
        }

    existing_roles = sum(
        1
        for name in expected_roles
        if any(
            role.name == name and not role.managed
            for role in guild.roles
        )
    )
    existing_categories = sum(
        1
        for name in expected_categories
        if name in categories_by_name
    )
    existing_channels = 0
    expected_channel_count = sum(
        len(names)
        for names in expected_channels_by_category.values()
    )
    for category_name, expected_names in expected_channels_by_category.items():
        category = categories_by_name.get(category_name)
        if category is None:
            continue
        existing_names = {
            channel.name
            for channel in (
                list(getattr(category, "text_channels", []))
                + list(getattr(category, "voice_channels", []))
                + list(getattr(category, "forums", []))
            )
        }
        existing_channels += len(expected_names & existing_names)

    complete = (
        existing_roles == len(expected_roles)
        and existing_categories == len(expected_categories)
        and existing_channels == expected_channel_count
    )
    return complete, existing_roles, existing_channels, existing_categories


async def _run_build(guild: discord.Guild, config: dict) -> object:
    lock = get_build_lock(guild.id)
    if lock.locked():
        raise RuntimeError("Une construction est déjà en cours sur ce serveur.")
    async with lock:
        return await build_and_persist(guild, config)


def _valid_academic_year(value: str) -> bool:
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r"(\d{4})/(\d{4})", value)
    if match is None:
        return False
    start, end = int(match.group(1)), int(match.group(2))
    return 2000 <= start <= 2100 and end == start + 1


class ServerCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="build",
        description="Synchroniser la structure sans recréer les ressources existantes.",
    )
    @management_check(lock=False)
    async def build(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "❌ Serveur requis.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        config = get_guild_config(guild.id)
        if not config:
            await interaction.followup.send(
                "❌ Utilise d'abord `/setup`.",
                ephemeral=True,
            )
            return

        complete, existing_roles, existing_channels, existing_categories = (
            await _managed_resource_state(guild, config)
        )
        await interaction.followup.send(
            "🏗️ Synchronisation sécurisée en cours...",
            ephemeral=True,
        )
        try:
            stats = await _run_build(guild, config)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Permission refusée. Vérifie Manage Channels, Manage Roles et la hiérarchie.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"❌ Discord API : `{exc}`",
                ephemeral=True,
            )
            return
        except Exception as exc:
            await interaction.followup.send(
                f"❌ Erreur : `{type(exc).__name__}: {exc}`",
                ephemeral=True,
            )
            return

        created_total = (
            stats.roles_created
            + stats.categories_created
            + stats.text_channels_created
            + stats.voice_channels_created
            + stats.forums_created
        )
        if created_total == 0:
            await interaction.followup.send(
                f"✅ **Déjà construit.** Rien à recréer : {existing_roles} rôles · {existing_categories} catégories · {existing_channels} channels gérés sont déjà présents.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"✅ Structure synchronisée. Niveaux: {stats.levels_processed} · Filières: {stats.streams_processed} · Rôles créés: {stats.roles_created} · Catégories créées: {stats.categories_created} · Texte créé: {stats.text_channels_created} · Vocaux créés: {stats.voice_channels_created}",
            ephemeral=True,
        )

    @app_commands.command(
        name="addstream",
        description="Ajouter une seule filière sans reconstruire les filières existantes.",
    )
    @app_commands.describe(level="Niveau", stream="Filière à ajouter")
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @management_check(lock=False)
    async def add_stream(
        self,
        interaction: discord.Interaction,
        level: str,
        stream: str,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.response.send_message(
                "❌ Niveau ou filière invalide.",
                ephemeral=True,
            )
            return
        config = get_guild_config(guild.id)
        if not config:
            await interaction.response.send_message(
                "❌ Lance `/setup` d'abord.",
                ephemeral=True,
            )
            return
        if _stream_configured(config, level, stream):
            code = get_stream_abbreviation(level, stream)
            await interaction.response.send_message(
                f"ℹ️ **{code} — {stream}** est déjà configurée. Aucun build ne sera lancé.",
                ephemeral=True,
            )
            return

        code = get_stream_abbreviation(level, stream)
        category_name = _stream_category_name(level, stream, code)
        existing_category = discord.utils.get(
            guild.categories,
            name=category_name,
        )
        conflict_note = (
            " Une catégorie existante non enregistrée sera refusée par le preflight d'ownership."
            if existing_category is not None
            else ""
        )
        candidate = deepcopy(config)
        target = next(
            (
                item
                for item in candidate.get("levels", [])
                if isinstance(item, dict) and item.get("name") == level
            ),
            None,
        )
        if target is None:
            target = {
                "name": level,
                "abbreviation": LEVEL_ABBREVIATIONS[level],
                "streams": [],
            }
            candidate.setdefault("levels", []).append(target)
        target.setdefault("streams", []).append(
            {
                "name": stream,
                "abbreviation": code,
                "subjects": get_stream_subjects(level, stream),
            }
        )

        await interaction.response.send_message(
            f"🏗️ Ajout de **{code} — {stream}** en cours...{conflict_note}",
            ephemeral=True,
        )
        try:
            await _run_build(guild, candidate)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Permission refusée. Vérifie Manage Channels, Manage Roles et la hiérarchie.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"❌ Discord API : `{exc}`",
                ephemeral=True,
            )
            return
        except OSError as exc:
            await interaction.followup.send(
                f"❌ Stockage local : `{exc}`",
                ephemeral=True,
            )
            return
        except Exception as exc:
            await interaction.followup.send(
                f"❌ Ajout annulé : `{type(exc).__name__}: {exc}`",
                ephemeral=True,
            )
            return

        category = discord.utils.get(guild.categories, name=category_name)
        if category is None:
            await interaction.followup.send(
                f"❌ Sécurité : **{code} — {stream}** a été demandée mais sa catégorie attendue `{category_name}` n'a pas été trouvée après construction. La configuration n'est pas considérée comme validée.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"✅ **{code} — {stream}** ajoutée. Catégorie créée : {category.mention}",
            ephemeral=True,
        )


    @app_commands.command(
        name="newyear",
        description="Créer une nouvelle année scolaire et la rendre active.",
    )
    @app_commands.describe(year="Format : 2026/2027")
    @management_check()
    async def new_year(
        self,
        interaction: discord.Interaction,
        year: str,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis", ephemeral=True)
            return
        if not _valid_academic_year(year):
            await interaction.response.send_message(
                "❌ Format invalide. Utilise `YYYY/YYYY` avec une année entre 2000 et 2100.",
                ephemeral=True,
            )
            return

        active = get_active_academic_year(guild.id)
        if active is not None:
            active_name = str(active["name"])
            if active_name == year:
                await interaction.response.send_message(
                    f"ℹ️ **{year}** est déjà l'année scolaire active.",
                    ephemeral=True,
                )
                return
            active_parts = tuple(int(part) for part in active_name.split("/"))
            requested_parts = tuple(int(part) for part in year.split("/"))
            if requested_parts <= active_parts:
                await interaction.response.send_message(
                    f"❌ **{year}** n'est pas une nouvelle année scolaire. Utilise `/rollbackyear` pour revenir vers une année antérieure.",
                    ephemeral=True,
                )
                return

        if any(str(row["name"]) == year for row in list_academic_years(guild.id)):
            await interaction.response.send_message(
                f"❌ **{year}** est déjà enregistrée comme année scolaire. Utilise `/rollbackyear` si tu veux l'activer.",
                ephemeral=True,
            )
            return

        config = deepcopy(get_guild_config(guild.id) or {"levels": []})
        config["academic_year"] = year

        await interaction.response.defer(ephemeral=True)
        try:
            create_and_activate_academic_year(guild.id, year, config)
        except (ValueError, OSError) as exc:
            await interaction.followup.send(
                f"❌ Impossible d'enregistrer l'année scolaire : `{exc}`",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ **{year}** est maintenant l'année scolaire active. La structure Discord existante reste inchangée.",
            ephemeral=True,
        )

    @app_commands.command(
        name="years",
        description="Afficher les années scolaires enregistrées.",
    )
    @management_check()
    async def years(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        rows = list_academic_years(interaction.guild.id)
        lines = [
            "## 📅 Années scolaires",
            "",
        ]
        lines.extend(
            f"• **{row['name']}**" + (" 🟢 ACTIVE" if row["is_active"] else "")
            for row in rows
        )
        if not rows:
            lines.append("Aucune année enregistrée.")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @app_commands.command(
        name="status",
        description="Afficher la configuration scolaire enregistrée.",
    )
    @management_check()
    async def status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        config = get_guild_config(interaction.guild.id)
        if not config:
            await interaction.response.send_message(
                "ℹ️ Aucune configuration. Utilise `/setup`.",
                ephemeral=True,
            )
            return
        lines = [
            "📋 **Configuration enregistrée**",
            f"📅 Année : **{config.get('academic_year', 'non définie')}**",
            "",
        ]
        total = 0
        for level in config.get("levels", []):
            if not isinstance(level, dict):
                continue
            lines.append(f"**{level.get('name', 'Niveau inconnu')}**")
            for stream in level.get("streams", []) or []:
                if not isinstance(stream, dict):
                    continue
                total += 1
                lines.append(
                    f"• **{stream.get('abbreviation', stream.get('name', ''))}** — {stream.get('name', '')}"
                )
        lines.extend(
            [
                "",
                f"**Total filières :** {total}",
                "**Architecture :** une vraie catégorie Discord par filière; aucune catégorie-titre artificielle.",
            ]
        )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerCommands(bot))

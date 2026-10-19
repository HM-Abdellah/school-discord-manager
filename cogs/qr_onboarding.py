"""Class QR onboarding command for School Discord Manager."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import (
    get_levels,
    get_stream_abbreviation,
    get_stream_subjects,
    get_streams,
)
from services.audit import record_event
from services.build_guard import get_build_lock
from services.permissions import (
    ROLE_STUDENT,
    STUDENT_STREAM_ROLE_PREFIX,
    get_managed_role,
    management_check,
)
from services.role_conflicts import student_staff_conflict, student_transfer_conflict
from services.qr_invites import (
    DEFAULT_QR_MAX_AGE,
    DEFAULT_QR_MAX_USES,
    create_role_invite,
    delete_invite,
    qr_file,
)
from services.server_builder import CATEGORY_VOICE, _safe_name, _stream_category_name, _subject_channel_name
from services.storage import (
    cancel_class_transfer_request,
    cleanup_class_transfer_requests,
    complete_class_transfer_request,
    create_class_transfer_request,
    enroll_student_record,
    expire_class_transfer_requests,
    get_active_academic_year,
    get_class_qr_invites,
    get_class_transfer_request,
    get_guild_config,
    list_pending_class_transfer_requests,
    mark_class_qr_invite_revoked,
    record_class_qr_invite,
    save_guild_config,
    set_class_transfer_message,
)


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


def _class_key(stream_code: str, section: int) -> str:
    return stream_code + "-" + str(section)


def _class_role_name(stream_code: str, section: int) -> str:
    return STUDENT_STREAM_ROLE_PREFIX + _class_key(stream_code, section)


def _audit_class_section(
    guild: discord.Guild,
    config: dict[str, Any],
    *,
    level: str,
    stream: str,
    code: str,
    section: int,
) -> str:
    """Explain why a section is or is not eligible for teacher onboarding.

    This helper is deliberately read-only: it never adopts roles or changes the
    class-role registry because an unregistered same-named role may be manual.
    """
    key = _class_key(code, section)
    expected_name = _class_role_name(code, section)
    registry = config.get("class_roles", {}) if isinstance(config, dict) else {}
    if not isinstance(registry, dict):
        return f"❌ Section {section}: le registre `class_roles` est invalide."

    entry = registry.get(key)
    if entry is None:
        alternate_key = next(
            (str(candidate) for candidate in registry if str(candidate).casefold() == key.casefold()),
            None,
        )
        if alternate_key is not None:
            return (
                f"⚠️ Section {section}: clé `class_roles` non canonique `{alternate_key}`; "
                f"la clé attendue est `{key}`."
            )
        matching_roles = [
            role for role in getattr(guild, "roles", [])
            if not getattr(role, "managed", False)
            and str(getattr(role, "name", "")).casefold() == expected_name.casefold()
        ]
        if matching_roles:
            names = ", ".join(
                f"`{getattr(role, 'name', expected_name)}` (ID `{getattr(role, 'id', '?')}`)"
                for role in matching_roles[:3]
            )
            return (
                f"⚠️ Section {section}: rôle présent ({names}), mais aucune entrée `{key}` dans `class_roles`. "
                "Il n'a pas été adopté automatiquement."
            )
        return (
            f"❌ Section {section}: aucune entrée `{key}` dans `class_roles` "
            f"et aucun rôle `{expected_name}` trouvé."
        )

    if not isinstance(entry, dict):
        return f"❌ Section {section}: l'entrée `{key}` du registre n'est pas un objet valide."
    if entry.get("level_name") != level or entry.get("stream_name") != stream:
        return f"❌ Section {section}: niveau ou filière enregistré incorrect dans `{key}`."
    if type(entry.get("section")) is not int or entry.get("section") != section:
        return f"❌ Section {section}: numéro de section enregistré incorrect dans `{key}`."
    if entry.get("stream_code") != code:
        return f"❌ Section {section}: code de filière enregistré incorrect dans `{key}`."
    role_id = entry.get("role_id")
    if type(role_id) is not int or role_id <= 0:
        return f"❌ Section {section}: `role_id` invalide dans `{key}`."
    role = guild.get_role(role_id)
    if role is None:
        return f"❌ Section {section}: rôle ID `{role_id}` enregistré mais absent du cache Discord."
    if getattr(role, "managed", False):
        return f"❌ Section {section}: le rôle ID `{role_id}` est un rôle géré par une intégration."
    if role.name != expected_name:
        return (
            f"❌ Section {section}: le rôle ID `{role_id}` porte le nom `{role.name}`, "
            f"au lieu de `{expected_name}`."
        )
    return f"✅ Section {section}: valide — `{expected_name}` (ID `{role_id}`)."


def _class_role_registry(config: dict[str, Any]) -> dict[str, Any]:
    registry = config.get("class_roles")
    if registry is None:
        registry = {}
        config["class_roles"] = registry
    if not isinstance(registry, dict):
        raise RuntimeError("Le registre des rôles de classe est invalide.")
    return registry


async def _get_or_create_class_role(
    guild: discord.Guild,
    config: dict[str, Any],
    *,
    level: str,
    stream: str,
    code: str,
    section: int,
) -> tuple[discord.Role, bool]:
    key = _class_key(code, section)
    name = _class_role_name(code, section)
    registry = _class_role_registry(config)
    entry = registry.get(key)

    if entry is not None:
        if not isinstance(entry, dict) or not isinstance(entry.get("role_id"), int):
            raise RuntimeError("Le registre du rôle de classe " + name + " est invalide.")
        role = guild.get_role(int(entry["role_id"]))
        if role is not None:
            if role.managed or role.name != name:
                raise RuntimeError(
                    "L'identité du rôle de classe " + name + " ne correspond plus à son registre."
                )
            return role, False

    collision = discord.utils.get(guild.roles, name=name)
    if collision is not None:
        raise RuntimeError(
            "Un rôle non géré porte déjà le nom " + name + "; création refusée pour éviter une collision."
        )

    role = await guild.create_role(
        name=name,
        permissions=discord.Permissions.none(),
        colour=discord.Colour.green(),
        hoist=False,
        mentionable=False,
        reason="School Manager class role: " + level + " / " + stream + " / section " + str(section),
    )
    registry[key] = {
        "role_id": role.id,
        "level_name": level,
        "stream_name": stream,
        "stream_code": code,
        "section": section,
    }
    return role, True


def _managed_registry_id(
    mapping: dict[str, Any],
    expected_name: str,
) -> int | None:
    exact = mapping.get(expected_name)
    if isinstance(exact, int) and exact > 0:
        return exact

    wanted = expected_name.casefold()
    matches = [
        value
        for name, value in mapping.items()
        if isinstance(value, int)
        and value > 0
        and str(name).casefold() == wanted
    ]
    if len(matches) > 1:
        raise RuntimeError(
            "Le registre contient plusieurs ressources gérées ambiguës pour "
            + expected_name
            + "."
        )
    return matches[0] if matches else None


def _same_managed_name(actual: object, expected: str) -> bool:
    return isinstance(actual, str) and actual.casefold() == expected.casefold()


def _managed_stream_channels(
    guild: discord.Guild,
    config: dict[str, Any],
    *,
    level: str,
    stream: str,
    code: str,
) -> list[discord.abc.GuildChannel]:
    managed = config.get("managed", {})
    if not isinstance(managed, dict):
        raise RuntimeError("La configuration des ressources gérées est absente.")

    categories = managed.get("categories", {})
    channels = managed.get("channels", {})
    if not isinstance(categories, dict) or not isinstance(channels, dict):
        raise RuntimeError("La configuration des ressources Discord gérées est invalide.")

    category_name = _stream_category_name(level, stream, code)
    category_id = _managed_registry_id(categories, category_name)
    if category_id is None:
        raise RuntimeError("La catégorie gérée " + category_name + " est introuvable dans le registre.")

    configured_subjects: list[str] | None = None
    for configured_level in config.get("levels", []) or []:
        if not isinstance(configured_level, dict) or configured_level.get("name") != level:
            continue
        for configured_stream in configured_level.get("streams", []) or []:
            if not isinstance(configured_stream, dict) or configured_stream.get("name") != stream:
                continue
            raw_subjects = configured_stream.get("subjects")
            if isinstance(raw_subjects, list):
                configured_subjects = [subject for subject in raw_subjects if isinstance(subject, str)]
            break
        break

    subjects = configured_subjects or get_stream_subjects(level, stream)
    expected_names = {
        "📌-" + code + "・informations",
        "🗓️-" + code + "・emploi-du-temps",
        "📝-" + code + "・examens",
        *{_subject_channel_name(code, subject) for subject in subjects},
    }

    voice_category_id = _managed_registry_id(categories, CATEGORY_VOICE)
    voice_name = "🔊-" + _safe_name(code, 30) + "-à-distance"
    if isinstance(voice_category_id, int) and voice_category_id > 0:
        expected_names.add(voice_name)

    result: list[discord.abc.GuildChannel] = []
    seen_ids: set[int] = set()

    for name in expected_names:
        resource_id = _managed_registry_id(channels, name)
        if resource_id is None:
            raise RuntimeError("Le salon géré " + name + " est absent du registre.")

        channel = guild.get_channel(resource_id)
        if channel is None:
            raise RuntimeError("Le salon géré " + name + " n'est plus présent sur Discord.")

        expected_category_id = (
            voice_category_id
            if name == voice_name and isinstance(voice_category_id, int)
            else category_id
        )
        if (
            not _same_managed_name(getattr(channel, "name", None), name)
            or getattr(channel, "category_id", None) != expected_category_id
        ):
            raise RuntimeError("L'identité du salon géré " + name + " ne correspond pas au registre.")

        if channel.id not in seen_ids:
            seen_ids.add(channel.id)
            result.append(channel)

    return result


async def _grant_class_role_access(
    channels: list[discord.abc.GuildChannel],
    *,
    class_role: discord.Role,
    student_stream_role: discord.Role,
) -> None:
    """Copy explicit student-stream access rules to the class role."""

    backups: list[tuple[discord.abc.GuildChannel, bool, discord.PermissionOverwrite]] = []
    try:
        for channel in channels:
            source = channel.overwrites_for(student_stream_role)
            if source.is_empty():
                raise RuntimeError(
                    "Le rôle " + student_stream_role.name +
                    " n'a pas de permission explicite dans " + channel.name + "."
                )

            current = channel.overwrites_for(class_role)
            had_existing = class_role in getattr(channel, "overwrites", {})
            if current == source:
                continue

            backups.append((channel, had_existing, current))
            await channel.set_permissions(
                class_role,
                overwrite=source,
                reason="School Manager class role access",
            )
    except Exception:
        for channel, had_existing, previous in reversed(backups):
            try:
                await channel.set_permissions(
                    class_role,
                    overwrite=previous if had_existing else None,
                    reason="School Manager class role access rollback",
                )
            except discord.HTTPException:
                pass
        raise



def _class_keys(config: dict[str, Any]) -> list[str]:
    registry = config.get("class_roles", {})
    if not isinstance(registry, dict):
        return []
    return sorted(str(key) for key in registry)


async def class_key_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    config = get_guild_config(interaction.guild.id) if interaction.guild else {}
    return [
        app_commands.Choice(name=key[:100], value=key)
        for key in _class_keys(config or {})
        if _contains(key, current)
    ][:25]


async def _revoke_class_qrs(bot: discord.Client, guild_id: int, class_key: str) -> int:
    rows = get_class_qr_invites(guild_id, class_key, include_revoked=False)
    revoked = 0
    for row in rows:
        code = str(row["invite_code"])
        try:
            await delete_invite(bot, code)
        except discord.HTTPException as exc:
            if exc.status != 404:
                raise
        mark_class_qr_invite_revoked(guild_id, code)
        revoked += 1
    return revoked


class ClassQROnboarding(commands.Cog):
    """Management command for temporary class QR invitations."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        """Restore pending student-transfer buttons after a bot restart."""
        now = discord.utils.utcnow().isoformat()
        cleanup_class_transfer_requests(now)
        for row in list_pending_class_transfer_requests(now):
            try:
                self.bot.add_view(
                    ClassTransferConfirmationView(self.bot, int(row["id"])),
                    message_id=int(row["dm_message_id"]),
                )
            except (ValueError, TypeError, RuntimeError) as exc:
                print(
                    "[QR] Could not restore class-transfer view id="
                    + str(row["id"]) + ": " + str(exc),
                    flush=True,
                )

    @app_commands.command(
        name="auditclasssections",
        description="Diagnostiquer les sections disponibles pour l'inscription des professeurs.",
    )
    @app_commands.describe(
        level="Niveau scolaire",
        stream="Filière scolaire",
    )
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @app_commands.default_permissions(manage_roles=True)
    @management_check(lock=False)
    async def audit_class_sections(
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
            await interaction.response.send_message("❌ Niveau ou filière invalide.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        config = get_guild_config(guild.id) or {}
        code = get_stream_abbreviation(level, stream)
        lines = [
            _audit_class_section(
                guild, config, level=level, stream=stream, code=code, section=section
            )
            for section in range(1, 9)
        ]
        await interaction.followup.send(
            f"## Audit des sections — {code}\n"
            "**Lecture seule :** aucun rôle, QR ou réglage n'a été modifié.\n\n"
            + "\n".join(lines),
            ephemeral=True,
        )

    @app_commands.command(
        name="createclassqr",
        description="Créer un QR temporaire pour inscrire une classe.",
    )
    @app_commands.describe(
        level="Niveau scolaire",
        stream="Filière scolaire",
        section="Numéro de section (1 à 8)",
        max_uses="Nombre de personnes autorisées à entrer (1 à 42)",
    )
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @app_commands.default_permissions(manage_roles=True)
    @management_check()
    async def create_class_qr(
        self,
        interaction: discord.Interaction,
        level: str,
        stream: str,
        section: app_commands.Range[int, 1, 8],
        max_uses: app_commands.Range[int, 1, 42],
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return

        bot_member = guild.me
        if bot_member is None or not bot_member.guild_permissions.create_instant_invite:
            await interaction.response.send_message(
                "❌ Le bot doit avoir Create Instant Invite pour générer le QR.",
                ephemeral=True,
            )
            return

        if level not in get_levels() or stream not in get_streams(level):
            await interaction.response.send_message(
                "❌ Niveau ou filière invalide.",
                ephemeral=True,
            )
            return

        code = get_stream_abbreviation(level, stream)
        class_key = _class_key(code, int(section))
        student_role = get_managed_role(guild, ROLE_STUDENT)
        student_stream_role = get_managed_role(
            guild,
            STUDENT_STREAM_ROLE_PREFIX + code,
        )
        if student_role is None or student_stream_role is None:
            await interaction.response.send_message(
                "❌ Les rôles scolaires requis n'existent pas. Lance /setup puis /build.",
                ephemeral=True,
            )
            return

        config = get_guild_config(guild.id) or {}
        working_config = deepcopy(config)
        await interaction.response.defer(ephemeral=True)
        class_role: discord.Role | None = None
        created_role = False

        revoked_before = 0
        try:
            revoked_before = await _revoke_class_qrs(self.bot, guild.id, class_key)
            class_role, created_role = await _get_or_create_class_role(
                guild,
                working_config,
                level=level,
                stream=stream,
                code=code,
                section=int(section),
            )
            channels = _managed_stream_channels(
                guild,
                working_config,
                level=level,
                stream=stream,
                code=code,
            )
            await _grant_class_role_access(
                channels,
                class_role=class_role,
                student_stream_role=student_stream_role,
            )
            save_guild_config(guild.id, working_config)
        except discord.Forbidden:
            if created_role and class_role is not None:
                try:
                    await class_role.delete(reason="School Manager QR setup rollback")
                except discord.HTTPException:
                    pass
            await interaction.followup.send(
                "❌ Permission refusée. Vérifie Manage Roles et la hiérarchie du bot.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            if created_role and class_role is not None:
                try:
                    await class_role.delete(reason="School Manager QR setup rollback")
                except discord.HTTPException:
                    pass
            await interaction.followup.send(
                "❌ Discord API : " + str(exc),
                ephemeral=True,
            )
            return
        except (OSError, RuntimeError, ValueError) as exc:
            if created_role and class_role is not None:
                try:
                    await class_role.delete(reason="School Manager QR setup rollback")
                except discord.HTTPException:
                    pass
            await interaction.followup.send(
                "❌ " + str(exc),
                ephemeral=True,
            )
            return

        invite_code = None
        try:
            source_channel = next(
                (
                    channel
                    for channel in channels
                    if _same_managed_name(channel.name, "📌-" + code + "・informations")
                ),
                None,
            )
            if source_channel is None:
                raise RuntimeError("Le salon d'informations de la filière est introuvable.")

            invite_url = await create_role_invite(
                self.bot,
                source_channel,
                (student_role, class_role),
                max_uses=int(max_uses),
            )
            invite_code = invite_url.rsplit("/", 1)[-1]
            now = discord.utils.utcnow()
            expires_at = now + timedelta(seconds=DEFAULT_QR_MAX_AGE)
            record_class_qr_invite(
                guild.id,
                invite_code,
                class_key,
                level,
                stream,
                code,
                int(section),
                class_role.id,
                interaction.user.id,
                now.isoformat(),
                expires_at.isoformat(),
                int(max_uses),
            )
            qr = qr_file(
                invite_url,
                "school-manager-" + code + "-" + str(int(section)) + ".png",
            )
        except (discord.Forbidden, discord.HTTPException, OSError, RuntimeError, ValueError) as exc:
            if invite_code:
                try:
                    await delete_invite(self.bot, invite_code)
                except discord.HTTPException:
                    pass
            await interaction.followup.send(
                "❌ QR non créé : " + type(exc).__name__ + ": " + str(exc),
                ephemeral=True,
            )
            return

        class_key = _class_key(code, int(section))
        record_event(
            guild.id,
            interaction.user.id,
            interaction.user.display_name,
            "createclassqr",
            class_key,
            "expires=" + str(DEFAULT_QR_MAX_AGE) + "s;max_uses=" + str(int(max_uses)),
        )

        await interaction.followup.send(
            "## ✅ QR de classe prêt\n\n"
            "**Classe :** " + class_key + "\n"
            "**Rôles attribués :** " + ROLE_STUDENT + " + " + class_role.name + "\n"
            "**Expiration :** 30 minutes\n"
            "**Utilisations max :** " + str(int(max_uses)) + "\n\n"
            "Chaque élève de cette classe peut scanner **le même QR**. "
            "Le QR est temporaire et doit rester destiné uniquement à cette classe.\n\n"
            "🔗 " + invite_url,
            file=qr,
            ephemeral=True,
        )


    @app_commands.command(
        name="createclasstransferqr",
        description="Créer un QR privé pour transférer un élève vers une autre classe.",
    )
    @app_commands.describe(
        student="Élève déjà présent sur le serveur",
        level="Niveau scolaire de destination",
        stream="Filière de destination",
        section="Numéro de la nouvelle section (1 à 8)",
    )
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @app_commands.default_permissions(manage_roles=True)
    @management_check()
    async def create_class_transfer_qr(
        self,
        interaction: discord.Interaction,
        student: discord.Member,
        level: str,
        stream: str,
        section: app_commands.Range[int, 1, 8],
    ) -> None:
        """Create a one-student transfer confirmation through a private DM."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if student.bot:
            await interaction.response.send_message("❌ Un compte bot ne peut pas être transféré comme élève.", ephemeral=True)
            return

        conflict = student_staff_conflict(student, guild)
        if conflict:
            await interaction.response.send_message(conflict, ephemeral=True)
            return
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.response.send_message("❌ Niveau ou filière invalide.", ephemeral=True)
            return

        year = get_active_academic_year(guild.id)
        if year is None:
            await interaction.response.send_message("❌ Aucune année scolaire active.", ephemeral=True)
            return

        code = get_stream_abbreviation(level, stream)
        class_key = _class_key(code, int(section))
        student_role = get_managed_role(guild, ROLE_STUDENT)
        student_stream_role = get_managed_role(guild, STUDENT_STREAM_ROLE_PREFIX + code)
        if student_role is None or student_stream_role is None:
            await interaction.response.send_message(
                "❌ Les rôles de base de cette filière ne sont pas prêts. Lance /setup puis /build.",
                ephemeral=True,
            )
            return
        if student_role.id not in {role.id for role in student.roles}:
            await interaction.response.send_message(
                "❌ Le membre sélectionné ne possède pas le rôle élève géré par le bot. "
                "Choisis un élève déjà inscrit, ou vérifie son inscription avec l'administration.",
                ephemeral=True,
            )
            return

        now = discord.utils.utcnow()
        expire_class_transfer_requests(now.isoformat())
        existing = [
            row for row in list_pending_class_transfer_requests(now.isoformat())
            if int(row["guild_id"]) == guild.id
            and int(row["student_discord_id"]) == student.id
        ]
        if existing:
            await interaction.response.send_message(
                "❌ Cet élève a déjà un transfert en attente. Annule-le avec "
                "/cancelclasstransferqr avant de créer un autre QR.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        config = get_guild_config(guild.id) or {}
        working_config = deepcopy(config)
        class_role: discord.Role | None = None
        created_role = False
        try:
            class_role, created_role = await _get_or_create_class_role(
                guild, working_config, level=level, stream=stream, code=code,
                section=int(section),
            )
            channels = _managed_stream_channels(
                guild, working_config, level=level, stream=stream, code=code,
            )
            await _grant_class_role_access(
                channels, class_role=class_role, student_stream_role=student_stream_role,
            )
            save_guild_config(guild.id, working_config)
        except (discord.Forbidden, discord.HTTPException, OSError, RuntimeError, ValueError) as exc:
            if created_role and class_role is not None:
                try:
                    await class_role.delete(reason="School Manager transfer QR setup rollback")
                except discord.HTTPException:
                    pass
            await interaction.followup.send(
                "❌ Impossible de préparer le rôle de destination : "
                + type(exc).__name__ + ": " + str(exc),
                ephemeral=True,
            )
            return

        assigned_ids = {role.id for role in student.roles}
        registry = working_config.get("class_roles", {})
        current_keys = sorted(
            str(key) for key, entry in registry.items()
            if isinstance(entry, dict)
            and isinstance(entry.get("role_id"), int)
            and int(entry["role_id"]) in assigned_ids
        ) if isinstance(registry, dict) else []
        current_label = ", ".join(current_keys) if current_keys else "غير محدد"
        created_at = discord.utils.utcnow()
        expires_at = created_at + timedelta(seconds=DEFAULT_QR_MAX_AGE)

        try:
            request_id = create_class_transfer_request(
                guild.id, student.id, interaction.user.id, int(year["id"]),
                level, stream, code, int(section), class_key, class_role.id,
                student_stream_role.id, created_at.isoformat(), expires_at.isoformat(),
            )
        except Exception as exc:
            await interaction.followup.send(
                "❌ ما قدرناش نحفظو طلب الانتقال، ما تبدل حتى دور. "
                + type(exc).__name__ + ": " + str(exc),
                ephemeral=True,
            )
            return

        dm_message: discord.Message | None = None
        view = ClassTransferConfirmationView(self.bot, request_id)
        try:
            dm_message = await student.send(
                "## 🏫 طلب تغيير القسم\n\n"
                "الإدارة طلبات نقلك لقسم آخر داخل المدرسة.\n"
                "**القسم الحالي حسب أدوار السيرفر:** " + current_label + "\n"
                "**القسم الجديد:** " + class_key + "\n"
                "**الصلاحية:** 30 دقيقة\n\n"
                "أكد غير إلا كان هاد التغيير صحيح. الأدوار وتاريخ التسجيل الدراسي "
                "غادي يتبدلو من بعد الضغط على الزر لتحت."
            )
            set_class_transfer_message(request_id, dm_message.channel.id, dm_message.id)
            await dm_message.edit(content=dm_message.content, view=view)
        except (discord.Forbidden, discord.HTTPException, OSError, RuntimeError, ValueError) as exc:
            cancel_class_transfer_request(request_id)
            if dm_message is not None:
                try:
                    await dm_message.edit(
                        content="❌ طلب الانتقال ما تكملش. تواصل مع الإدارة إذا كنت محتاج تغيير القسم.",
                        view=None,
                    )
                except discord.HTTPException:
                    pass
            await interaction.followup.send(
                "❌ ما قدرناش نرسل/نجهزو رسالة التأكيد الخاصة بالتلميذ. "
                "تأكد أن رسائله الخاصة (DMs) مفتوحة. التفاصيل: "
                + type(exc).__name__ + ": " + str(exc),
                ephemeral=True,
            )
            return

        deep_link = "https://discord.com/channels/@me/" + str(dm_message.channel.id) + "/" + str(dm_message.id)
        try:
            qr = qr_file(deep_link, "school-manager-transfer-" + str(request_id) + ".png")
        except (OSError, ValueError, RuntimeError) as exc:
            cancel_class_transfer_request(request_id)
            try:
                await dm_message.edit(content="❌ تعذر تجهيز QR؛ طلب الانتقال أُلغي. تواصل مع الإدارة.", view=None)
            except discord.HTTPException:
                pass
            await interaction.followup.send(
                "❌ فشل إنشاء صورة QR. الطلب أُلغي بأمان: " + str(exc),
                ephemeral=True,
            )
            return

        record_event(
            guild.id, interaction.user.id, interaction.user.display_name,
            "createclasstransferqr", student.display_name,
            current_label + " -> " + class_key + ";request_id=" + str(request_id),
        )
        try:
            await interaction.followup.send(
                "## ✅ QR ديال تغيير القسم واجد\n\n"
                "**التلميذ:** " + student.mention + "\n"
                "**من:** " + current_label + "\n"
                "**إلى:** " + class_key + "\n"
                "**الصلاحية:** 30 دقيقة، واستعمال واحد فقط\n\n"
                "هاد QR كيفتح رسالة خاصة بهاد التلميذ فـDiscord؛ خاص التلميذ يكون داخل للحساب "
                "ويضغط على زر تأكيد تغيير القسم. الدعوة ما كتزيدوش للسيرفر من جديد، "
                "وما كيتبدل حتى دور قبل التأكيد. ما تشاركش QR مع تلميذ آخر.",
                file=qr, ephemeral=True,
            )
        except discord.HTTPException as exc:
            cancel_class_transfer_request(request_id)
            try:
                await dm_message.edit(content="❌ تعذر تسليم QR للإدارة، لذلك أُلغي طلب تغيير القسم.", view=None)
            except discord.HTTPException:
                pass
            print("[QR] Could not return transfer QR request=" + str(request_id) + ": " + str(exc), flush=True)


    @app_commands.command(
        name="cancelclasstransferqr",
        description="Annuler le QR de transfert en attente d'un élève.",
    )
    @app_commands.describe(student="Élève dont le transfert doit être annulé")
    @app_commands.default_permissions(manage_roles=True)
    @management_check()
    async def cancel_class_transfer_qr(
        self,
        interaction: discord.Interaction,
        student: discord.Member,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        now = discord.utils.utcnow().isoformat()
        expire_class_transfer_requests(now)
        rows = [
            row for row in list_pending_class_transfer_requests(now)
            if int(row["guild_id"]) == guild.id
            and int(row["student_discord_id"]) == student.id
        ]
        if not rows:
            await interaction.followup.send(
                "ℹ️ Aucun transfert en attente trouvé pour " + student.mention + ".",
                ephemeral=True,
            )
            return

        cancelled = 0
        for row in rows:
            if not cancel_class_transfer_request(int(row["id"])):
                continue
            cancelled += 1
            try:
                channel = self.bot.get_channel(int(row["dm_channel_id"]))
                if channel is None:
                    channel = await self.bot.fetch_channel(int(row["dm_channel_id"]))
                message = await channel.fetch_message(int(row["dm_message_id"]))
                await message.edit(
                    content="ℹ️ طلب تغيير القسم تلغى من طرف الإدارة. هاد QR ما بقاش صالح.",
                    view=None,
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
                pass
            record_event(
                guild.id, interaction.user.id, interaction.user.display_name,
                "cancelclasstransferqr", student.display_name,
                "request_id=" + str(row["id"]),
            )

        await interaction.followup.send(
            "✅ تلغى " + str(cancelled) + " طلب(ات) انتقال ديال " + student.mention + ".",
            ephemeral=True,
        )


    @app_commands.command(
        name="revokeclassqr",
        description="Révoquer les QR actifs d'une classe.",
    )
    @app_commands.describe(class_key="Classe, par exemple 2BACPC-2")
    @app_commands.autocomplete(class_key=class_key_autocomplete)
    @app_commands.default_permissions(manage_roles=True)
    @management_check()
    async def revoke_class_qr(
        self,
        interaction: discord.Interaction,
        class_key: str,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            count = await _revoke_class_qrs(self.bot, interaction.guild.id, class_key)
        except (discord.Forbidden, discord.HTTPException, OSError) as exc:
            await interaction.followup.send(
                "❌ Révocation impossible : " + type(exc).__name__ + ": " + str(exc),
                ephemeral=True,
            )
            return

        record_event(
            interaction.guild.id,
            interaction.user.id,
            interaction.user.display_name,
            "revokeclassqr",
            class_key,
            "count=" + str(count),
        )
        if count:
            message = "✅ " + str(count) + " QR actif(s) révoqué(s) pour " + class_key + "."
        else:
            message = "ℹ️ Aucun QR actif trouvé pour " + class_key + "."
        await interaction.followup.send(message, ephemeral=True)


    @app_commands.command(
        name="listclassqr",
        description="Afficher les QR de classes encore actifs.",
    )
    @app_commands.describe(class_key="Filtrer par classe, par exemple 2BACPC-2")
    @app_commands.autocomplete(class_key=class_key_autocomplete)
    @app_commands.default_permissions(manage_roles=True)
    @management_check()
    async def list_class_qr(
        self,
        interaction: discord.Interaction,
        class_key: str | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        rows = get_class_qr_invites(guild.id, class_key, include_revoked=False)
        now = discord.utils.utcnow()
        lines: list[str] = []

        for row in rows:
            try:
                expires_at = discord.utils.parse_time(str(row["expires_at"]))
            except (TypeError, ValueError):
                continue
            if expires_at <= now:
                continue
            lines.append(
                "• " + str(row["class_key"]) + " — " + str(row["invite_code"]) + " — "
                + discord.utils.format_dt(expires_at, "R")
            )

        if not lines:
            await interaction.followup.send(
                "ℹ️ Aucun QR actif non expiré."
                + ("" if class_key is None else " pour " + class_key + "."),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "## 🔐 QR de classes actifs\\n\\n" + "\\n".join(lines),
            ephemeral=True,
        )


    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Persist a member that entered through a registered class QR role."""
        if member.bot:
            return

        config = get_guild_config(member.guild.id) or {}
        registry = config.get("class_roles", {})
        if not isinstance(registry, dict):
            return

        role_ids = {role.id for role in member.roles}
        matches = []
        for key, entry in registry.items():
            if not isinstance(entry, dict):
                continue
            role_id = entry.get("role_id")
            if not isinstance(role_id, int) or role_id not in role_ids:
                continue
            level = entry.get("level_name")
            stream = entry.get("stream_name")
            code = entry.get("stream_code")
            section = entry.get("section")
            if (
                not isinstance(level, str)
                or not isinstance(stream, str)
                or not isinstance(code, str)
                or not isinstance(section, int)
            ):
                continue
            matches.append((str(key), level, stream, code, section))

        if len(matches) != 1:
            if len(matches) > 1:
                print(
                    "[QR] Ambiguous class roles for member="
                    + str(member.id)
                    + " guild="
                    + str(member.guild.id),
                    flush=True,
                )
            return

        class_key, level, stream, code, section = matches[0]
        year = get_active_academic_year(member.guild.id)
        if year is None:
            print(
                "[QR] Cannot persist "
                + class_key
                + ": no active academic year for guild="
                + str(member.guild.id),
                flush=True,
            )
            return

        student_role = get_managed_role(member.guild, ROLE_STUDENT)
        try:
            if student_role is not None and student_role not in member.roles:
                await member.add_roles(
                    student_role,
                    reason="School Manager class QR onboarding",
                )

            enroll_student_record(
                member.guild.id,
                member.id,
                member.display_name,
                int(year["id"]),
                level,
                stream,
                section=section,
            )
            record_event(
                member.guild.id,
                self.bot.user.id if self.bot.user else 0,
                self.bot.user.display_name if self.bot.user else "School Manager",
                "class_qr_join",
                member.display_name,
                class_key,
            )
        except (discord.Forbidden, discord.HTTPException, OSError, ValueError) as exc:
            print(
                "[QR] Failed to persist class onboarding for member="
                + str(member.id)
                + ": "
                + str(exc),
                flush=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ClassQROnboarding(bot))


class ClassTransferConfirmationView(discord.ui.View):
    """Persistent, target-user-only confirmation for a class transfer request."""

    def __init__(self, bot: discord.Client, request_id: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.request_id = int(request_id)
        button = discord.ui.Button(
            label="تأكيد تغيير القسم",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id="school-manager:class-transfer:confirm:" + str(self.request_id),
        )
        button.callback = self._confirm
        self.add_item(button)

    async def _followup(self, interaction: discord.Interaction, content: str) -> None:
        await interaction.followup.send(content, ephemeral=True)

    async def _disable_message(self, interaction: discord.Interaction, content: str) -> None:
        message = getattr(interaction, "message", None)
        if message is None:
            return
        try:
            await message.edit(content=content, view=None)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    async def _restore_roles(
        self,
        guild: discord.Guild,
        member: discord.Member,
        relevant_role_ids: set[int],
        original_roles: list[discord.Role],
    ) -> None:
        try:
            current_member = await guild.fetch_member(member.id)
        except discord.HTTPException:
            current_member = member
        original_by_id = {role.id: role for role in original_roles if not role.managed}
        current_roles = {
            role.id: role for role in current_member.roles
            if role.id in relevant_role_ids and not role.managed
        }
        to_remove = [
            role for role_id, role in current_roles.items()
            if role_id not in original_by_id
        ]
        to_add = [
            role for role_id, role in original_by_id.items()
            if role_id not in current_roles
        ]
        if to_remove:
            await current_member.remove_roles(
                *to_remove,
                reason="School Manager class-transfer rollback",
            )
        if to_add:
            await current_member.add_roles(
                *to_add,
                reason="School Manager class-transfer rollback",
            )

    async def _confirm(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        request = get_class_transfer_request(self.request_id)
        if request is None:
            await self._followup(interaction, "❌ طلب تغيير القسم غير موجود.")
            return
        if int(request["student_discord_id"]) != interaction.user.id:
            await self._followup(interaction, "❌ هاد التأكيد خاص بالتلميذ المعني فقط.")
            return
        if request["status"] != "pending":
            await self._followup(interaction, "ℹ️ هاد الطلب تلغى أو تكمل من قبل.")
            await self._disable_message(interaction, "ℹ️ طلب تغيير القسم ما بقاش نشط.")
            return

        guild = self.bot.get_guild(int(request["guild_id"]))
        if guild is None:
            await self._followup(interaction, "❌ السيرفر ما بقاش متاح للبوت. تواصل مع الإدارة.")
            return

        async with get_build_lock(guild.id):
            request = get_class_transfer_request(self.request_id)
            if request is None or request["status"] != "pending":
                await self._followup(interaction, "ℹ️ هاد الطلب تلغى أو تكمل من قبل.")
                await self._disable_message(interaction, "ℹ️ طلب تغيير القسم ما بقاش نشط.")
                return
            if int(request["student_discord_id"]) != interaction.user.id:
                await self._followup(interaction, "❌ هاد التأكيد خاص بالتلميذ المعني فقط.")
                return

            now = discord.utils.utcnow()
            try:
                expires_at = datetime.fromisoformat(str(request["expires_at"]))
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                cancel_class_transfer_request(self.request_id, expired=True)
                await self._followup(interaction, "❌ تاريخ صلاحية الطلب غير صالح؛ تواصل مع الإدارة.")
                await self._disable_message(interaction, "❌ طلب تغيير القسم غير صالح.")
                return
            if expires_at <= now:
                cancel_class_transfer_request(self.request_id, expired=True)
                await self._followup(interaction, "⌛ سالات صلاحية QR. طلب من الإدارة تصاوب QR جديد.")
                await self._disable_message(interaction, "⌛ سالات صلاحية طلب تغيير القسم.")
                return

            year = get_active_academic_year(guild.id)
            if year is None or int(year["id"]) != int(request["academic_year_id"]):
                cancel_class_transfer_request(self.request_id)
                await self._followup(
                    interaction,
                    "❌ تبدلات السنة الدراسية منذ إنشاء QR. خاص الإدارة تصاوب طلب جديد.",
                )
                await self._disable_message(interaction, "❌ طلب الانتقال تلغى بسبب تغيير السنة الدراسية.")
                return

            try:
                # Role assignments may change while a QR is pending. Use fresh Discord state,
                # not a potentially stale guild cache, for the transfer eligibility check.
                member = await guild.fetch_member(interaction.user.id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                cancel_class_transfer_request(self.request_id)
                await self._followup(interaction, "❌ ما لقيناش حسابك فالسيرفر. تواصل مع الإدارة.")
                await self._disable_message(interaction, "❌ الطلب ما بقاش صالح.")
                return

            conflict = student_transfer_conflict(member, guild)
            if conflict:
                cancel_class_transfer_request(self.request_id)
                await self._followup(interaction, conflict)
                await self._disable_message(interaction, "❌ الطلب تلغى بسبب تعارض الأدوار.")
                return

            config = get_guild_config(guild.id) or {}
            class_registry = config.get("class_roles", {})
            key = str(request["class_key"])
            entry = class_registry.get(key) if isinstance(class_registry, dict) else None
            class_role = guild.get_role(int(request["class_role_id"]))
            expected_class_name = _class_role_name(str(request["stream_code"]), int(request["section"]))
            if (
                not isinstance(entry, dict)
                or entry.get("role_id") != int(request["class_role_id"])
                or entry.get("level_name") != str(request["level_name"])
                or entry.get("stream_name") != str(request["stream_name"])
                or entry.get("stream_code") != str(request["stream_code"])
                or entry.get("section") != int(request["section"])
                or class_role is None
                or class_role.managed
                or class_role.name != expected_class_name
            ):
                cancel_class_transfer_request(self.request_id)
                await self._followup(
                    interaction,
                    "❌ دور القسم المستهدف تبدل أو ما بقاش مسجّل بشكل صحيح. طلب من الإدارة تصاوب QR جديد.",
                )
                await self._disable_message(interaction, "❌ الدور المستهدف ما بقاش صالح.")
                return

            stream_role = guild.get_role(int(request["student_stream_role_id"]))
            expected_stream_role = get_managed_role(
                guild,
                STUDENT_STREAM_ROLE_PREFIX + str(request["stream_code"]),
            )
            student_role = get_managed_role(guild, ROLE_STUDENT)
            if (
                stream_role is None
                or expected_stream_role is None
                or stream_role.id != expected_stream_role.id
                or stream_role.name != STUDENT_STREAM_ROLE_PREFIX + str(request["stream_code"])
                or student_role is None
                or stream_role.managed
            ):
                cancel_class_transfer_request(self.request_id)
                await self._followup(
                    interaction,
                    "❌ أدوار القسم المستهدف ما بقاتش صالحة. تواصل مع الإدارة.",
                )
                await self._disable_message(interaction, "❌ أدوار القسم المستهدف ما بقاتش صالحة.")
                return

            managed = config.get("managed", {})
            managed_roles = managed.get("roles", {}) if isinstance(managed, dict) else {}
            managed_ids = {
                int(value) for value in managed_roles.values()
                if isinstance(value, int) and value > 0
            } if isinstance(managed_roles, dict) else set()
            class_role_ids = {
                int(item["role_id"])
                for item in class_registry.values()
                if isinstance(item, dict)
                and isinstance(item.get("role_id"), int)
                and int(item["role_id"]) > 0
            } if isinstance(class_registry, dict) else set()
            relevant_role_ids = {
                student_role.id, stream_role.id, class_role.id,
                *class_role_ids,
                *{
                    role.id for role in guild.roles
                    if role.id in managed_ids
                    and role.name.startswith(STUDENT_STREAM_ROLE_PREFIX)
                },
            }
            original_roles = [
                role for role in member.roles
                if role.id in relevant_role_ids and not role.managed
            ]
            desired_roles = [student_role, stream_role, class_role]
            original_ids = {role.id for role in member.roles}
            to_add = [role for role in desired_roles if role.id not in original_ids]
            desired_ids = {role.id for role in desired_roles}
            to_remove = [
                role for role in member.roles
                if role.id in relevant_role_ids
                and role.id not in desired_ids
                and not role.managed
            ]

            try:
                if to_add:
                    await member.add_roles(
                        *to_add,
                        reason="School Manager student class transfer",
                    )
                if to_remove:
                    await member.remove_roles(
                        *to_remove,
                        reason="School Manager student class transfer",
                    )
                completed_at = discord.utils.utcnow().isoformat()
                complete_class_transfer_request(
                    self.request_id,
                    guild.id,
                    member.id,
                    member.display_name,
                    completed_at,
                )
            except Exception as exc:
                rollback_ok = True
                try:
                    await self._restore_roles(guild, member, relevant_role_ids, original_roles)
                except Exception as rollback_exc:
                    rollback_ok = False
                    print(
                        "[QR] Class-transfer role rollback failed request="
                        + str(self.request_id) + ": " + str(rollback_exc),
                        flush=True,
                    )
                if not rollback_ok:
                    cancel_class_transfer_request(self.request_id)
                print(
                    "[QR] Class-transfer failed request="
                    + str(self.request_id) + ": " + type(exc).__name__ + ": " + str(exc),
                    flush=True,
                )
                await self._followup(
                    interaction,
                    "❌ ما تكملش تغيير القسم. "
                    + ("خاص الإدارة تراجع الأدوار يدويًا بسبب فشل الاسترجاع." if not rollback_ok
                       else "رجعنا الأدوار القديمة قدر الإمكان؛ عاود تواصل مع الإدارة.")
                )
                current_request = get_class_transfer_request(self.request_id)
                if current_request is None or current_request["status"] != "pending":
                    await self._disable_message(interaction, "❌ طلب تغيير القسم ما بقاش صالح.")
                return

            record_event(
                guild.id,
                member.id,
                member.display_name,
                "class_transfer_confirmed",
                member.display_name,
                str(request["class_key"]) + ";request_id=" + str(self.request_id),
            )
            await self._disable_message(
                interaction,
                "✅ تأكد تغيير القسم بنجاح. القسم الجديد هو **" + str(request["class_key"]) + "**.",
            )
            await self._followup(
                interaction,
                "✅ تأكد تغيير القسم بنجاح. القسم الجديد ديالك هو **"
                + str(request["class_key"]) + "**.",
            )

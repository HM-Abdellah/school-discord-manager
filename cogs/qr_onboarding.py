"""Class QR onboarding command for School Discord Manager."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
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
from services.permissions import (
    ROLE_STUDENT,
    STUDENT_STREAM_ROLE_PREFIX,
    get_managed_role,
    management_check,
)
from services.qr_invites import (
    DEFAULT_QR_MAX_AGE,
    DEFAULT_QR_MAX_USES,
    create_role_invite,
    delete_invite,
    qr_file,
)
from services.server_builder import CATEGORY_VOICE, _safe_name, _stream_category_name, _subject_channel_name
from services.storage import (
    enroll_student_record,
    get_active_academic_year,
    get_class_qr_invites,
    get_guild_config,
    mark_class_qr_invite_revoked,
    record_class_qr_invite,
    save_guild_config,
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
    category_id = categories.get(category_name)
    if not isinstance(category_id, int) or category_id <= 0:
        raise RuntimeError("La catégorie gérée " + category_name + " est introuvable dans le registre.")

    subjects = get_stream_subjects(level, stream)
    expected_names = {
        "📌-" + code + "・informations",
        "🗓️-" + code + "・emploi-du-temps",
        "📝-" + code + "・examens",
        *{_subject_channel_name(code, subject) for subject in subjects},
    }

    voice_category_id = categories.get(CATEGORY_VOICE)
    voice_name = "🔊-" + _safe_name(code, 30) + "-à-distance"
    if isinstance(voice_category_id, int) and voice_category_id > 0:
        expected_names.add(voice_name)

    result: list[discord.abc.GuildChannel] = []
    seen_ids: set[int] = set()

    for name in expected_names:
        resource_id = channels.get(name)
        if not isinstance(resource_id, int) or resource_id <= 0:
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
            getattr(channel, "name", None) != name
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
                    if channel.name == "📌-" + code + "・informations"
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

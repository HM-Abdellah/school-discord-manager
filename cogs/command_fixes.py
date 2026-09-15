"""Teacher-assignment and absence commands with dynamic school lookups."""

from __future__ import annotations

import unicodedata
from datetime import date

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import (
    GENERAL_CHANNELS,
    get_levels,
    get_stream_abbreviation,
    get_stream_subjects,
    get_streams,
    get_subject_display_name,
    get_subject_internal_code,
)
from services.audit import record_event
from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_PROFESSOR_FEMALE,
    ROLE_STUDENT,
    SUBJECT_ROLE_PREFIX,
    STREAM_ROLE_PREFIX,
    STUDENT_STREAM_ROLE_PREFIX,
    administrator_overwrite,
    get_managed_role,
    hidden_overwrite,
    management_check,
    professor_subject_member_overwrite,
    professor_subject_view_overwrite,
    student_overwrite,
)
from services.server_builder import _stream_category_name, _subject_channel_name
from services.storage import get_guild_config, save_guild_config

# This module owns only the three commands below. Timetable and exam commands
# live in dedicated section-aware cogs; they must never be redefined here.
OWNED_COMMANDS = {
    "assignteacherfull",
    "assignsubjectteachers",
    "reportabsence",
}


def _contains(value: str, current: str) -> bool:
    return current.casefold() in value.casefold()


def _normalize_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().replace("\ufe0f", "")


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


async def subject_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    stream = str(getattr(interaction.namespace, "stream", ""))
    if level not in get_levels() or stream not in get_streams(level):
        return []
    choices: list[app_commands.Choice[str]] = []
    seen: set[str] = set()
    for subject in get_stream_subjects(level, stream):
        key = subject.casefold()
        if key in seen:
            continue
        seen.add(key)
        display = get_subject_display_name(subject)
        if _contains(display, current) or _contains(subject, current):
            choices.append(app_commands.Choice(name=display[:100], value=subject))
    return choices[:25]


async def teacher_subject_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Suggest configured subjects once, prioritizing the selected stream."""
    level = str(getattr(interaction.namespace, "level", ""))
    stream = str(getattr(interaction.namespace, "stream", ""))
    if level not in get_levels():
        return []

    preferred = (
        {subject.casefold() for subject in get_stream_subjects(level, stream)}
        if stream in get_streams(level)
        else set()
    )
    subjects: list[str] = []
    seen: set[str] = set()

    config = get_guild_config(interaction.guild.id) if interaction.guild else None
    configured_levels = config.get("levels", []) if isinstance(config, dict) else []
    for configured_level in configured_levels:
        if not isinstance(configured_level, dict):
            continue
        level_name = configured_level.get("name")
        if not isinstance(level_name, str) or level_name not in get_levels():
            continue
        for configured_stream in configured_level.get("streams", []) or []:
            if (
                not isinstance(configured_stream, dict)
                or not isinstance(configured_stream.get("name"), str)
            ):
                continue
            stream_name = configured_stream["name"]
            try:
                candidates = get_stream_subjects(level_name, stream_name)
            except Exception:
                continue
            for subject in candidates:
                key = subject.casefold()
                if key not in seen:
                    seen.add(key)
                    subjects.append(subject)

    if not subjects:
        for candidate_stream in get_streams(level):
            for subject in get_stream_subjects(level, candidate_stream):
                key = subject.casefold()
                if key not in seen:
                    seen.add(key)
                    subjects.append(subject)

    subjects.sort(
        key=lambda item: (
            item.casefold() not in preferred,
            get_subject_display_name(item).casefold(),
        )
    )
    choices: list[app_commands.Choice[str]] = []
    for subject in subjects:
        display = get_subject_display_name(subject)
        if _contains(display, current) or _contains(subject, current):
            choices.append(app_commands.Choice(name=display[:100], value=subject))
    return choices[:25]


async def class_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    guild = interaction.guild
    if guild is None:
        return []
    config = get_guild_config(guild.id) or {}
    choices: list[app_commands.Choice[str]] = []
    seen_codes: set[str] = set()
    for level in config.get("levels", []):
        if not isinstance(level, dict):
            continue
        level_name = level.get("name")
        if not isinstance(level_name, str) or level_name not in get_levels():
            continue
        for stream in level.get("streams", []) or []:
            if not isinstance(stream, dict) or not isinstance(stream.get("name"), str):
                continue
            stream_name = stream["name"]
            code = str(
                stream.get("abbreviation")
                or get_stream_abbreviation(level_name, stream_name)
            )
            if code in seen_codes:
                continue
            seen_codes.add(code)
            label = f"{code} — {stream_name}"[:100]
            if (
                _contains(label, current)
                or _contains(code, current)
                or _contains(stream_name, current)
            ):
                choices.append(app_commands.Choice(name=label, value=code))
    return choices[:25]


async def _find_text_channel(
    guild: discord.Guild,
    category_name: str,
    expected_name: str,
) -> discord.TextChannel | None:
    """Resolve a text channel by managed ID, then normalized name in scope."""
    expected_norm = _normalize_name(expected_name)
    config = get_guild_config(guild.id) or {}
    managed = config.get("managed", {})
    channels = managed.get("channels", {}) if isinstance(managed, dict) else {}
    channel_id = channels.get(expected_name) if isinstance(channels, dict) else None
    if isinstance(channel_id, int):
        try:
            channel = await guild.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            channel = None
        if (
            isinstance(channel, discord.TextChannel)
            and _normalize_name(channel.name) == expected_norm
        ):
            return channel

    try:
        channels_now = list(await guild.fetch_channels())
    except (discord.Forbidden, discord.HTTPException):
        channels_now = list(guild.channels)

    category = next(
        (
            channel
            for channel in channels_now
            if isinstance(channel, discord.CategoryChannel)
            and _normalize_name(channel.name) == _normalize_name(category_name)
        ),
        None,
    )
    if category is not None:
        matches = [
            channel
            for channel in list(getattr(category, "text_channels", []))
            if _normalize_name(channel.name) == expected_norm
        ]
        if len(matches) == 1:
            return matches[0]

    matches = [
        channel
        for channel in channels_now
        if isinstance(channel, discord.TextChannel)
        and _normalize_name(channel.name) == expected_norm
    ]
    return matches[0] if len(matches) == 1 else None


def _global_subject_role_name(subject: str) -> str:
    return f"{SUBJECT_ROLE_PREFIX}{get_subject_display_name(subject)}"[:100]


def _member_is_professor(
    member: discord.Member,
    guild: discord.Guild,
) -> bool:
    professor_role_ids = {
        role.id
        for role in (
            get_managed_role(guild, ROLE_PROFESSOR),
            get_managed_role(guild, ROLE_PROFESSOR_FEMALE),
        )
        if role is not None
    }
    return any(
        role.id in professor_role_ids
        for role in member.roles
        if not role.managed
    )


def _configured_streams(
    guild: discord.Guild,
) -> list[tuple[str, str, str]]:
    config = get_guild_config(guild.id) or {}
    result: list[tuple[str, str, str]] = []
    seen: set[str] = set()
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
            if code not in seen:
                seen.add(code)
                result.append((level_name, stream_name, code))
    return result


def _legacy_subject_map(guild: discord.Guild) -> dict[str, str]:
    """Map legacy stream-specific subject roles to curriculum subjects."""
    result: dict[str, str] = {}
    for level_name, stream_name, code in _configured_streams(guild):
        for subject in get_stream_subjects(level_name, stream_name):
            legacy_name = (
                f"{SUBJECT_ROLE_PREFIX}{code} - "
                f"{get_subject_internal_code(subject)}"
            )
            result[legacy_name] = subject
    return result


async def _get_or_create_global_subject_role(
    guild: discord.Guild,
    config: dict,
    subject: str,
) -> discord.Role:
    """Resolve or create the shared subject role."""
    role_name = _global_subject_role_name(subject)
    role = get_managed_role(guild, role_name)
    if role is None:
        role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        role = await guild.create_role(
            name=role_name,
            permissions=discord.Permissions.none(),
            colour=discord.Colour.dark_blue(),
            mentionable=False,
            reason="School Manager global subject role",
        )
    config.setdefault("managed", {}).setdefault("roles", {})[role_name] = role.id
    return role


async def _migrate_legacy_subject_roles(
    guild: discord.Guild,
    member: discord.Member,
    config: dict,
) -> list[str]:
    """Migrate legacy subject roles while preserving channel access."""
    legacy_map = _legacy_subject_map(guild)
    if not legacy_map:
        return []

    old_roles = [
        role
        for role in member.roles
        if not role.managed and role.name in legacy_map
    ]
    if not old_roles:
        return []

    global_roles: list[discord.Role] = []
    migrated_subjects: list[str] = []
    for old_role in old_roles:
        subject = legacy_map[old_role.name]
        new_role = await _get_or_create_global_subject_role(
            guild,
            config,
            subject,
        )
        if new_role not in global_roles:
            global_roles.append(new_role)
        if subject not in migrated_subjects:
            migrated_subjects.append(subject)

        for channel in guild.channels:
            try:
                overwrites = getattr(channel, "overwrites", {})
                old_overwrite = overwrites.get(old_role)
                if old_overwrite is None or new_role in overwrites:
                    continue
                await channel.set_permissions(
                    new_role,
                    overwrite=old_overwrite,
                    reason="School Manager subject role migration",
                )
            except (discord.Forbidden, discord.HTTPException):
                continue

    try:
        await member.add_roles(
            *global_roles,
            reason="School Manager migrate legacy subject roles",
        )
        await member.remove_roles(
            *old_roles,
            reason="School Manager remove legacy stream-specific subject roles",
        )
    except (discord.Forbidden, discord.HTTPException):
        return []

    return migrated_subjects


def _resolve_class_codes(
    guild: discord.Guild,
    value: str,
) -> list[str]:
    available = {
        code.casefold(): code
        for _, _, code in _configured_streams(guild)
    }
    resolved: list[str] = []
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        code = available.get(token.casefold())
        if code is None:
            raise ValueError(
                f"Classe/filière inconnue : `{token}`. Utilise l'autocomplétion."
            )
        if code not in resolved:
            resolved.append(code)
    if not resolved:
        raise ValueError("Sélectionne au moins une classe/filière.")
    return resolved


class CommandFixes(commands.Cog):
    """Commands with responsibilities not owned by specialized cogs."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="assignteacherfull",
        description="Affecter un professeur à une filière et à une ou plusieurs matières.",
    )
    @app_commands.describe(
        teacher="Professeur",
        gender="Type de rôle professeur",
        level="Niveau scolaire",
        stream="Filière scolaire",
        subjects="Matière(s), sélectionne une suggestion ou sépare par des virgules",
    )
    @app_commands.choices(
        gender=[
            app_commands.Choice(name="Prof", value="male"),
            app_commands.Choice(name="Prof (F)", value="female"),
        ]
    )
    @app_commands.autocomplete(
        level=level_autocomplete,
        stream=stream_autocomplete,
        subjects=teacher_subject_autocomplete,
    )
    @management_check()
    async def assign_teacher_full(
        self,
        interaction: discord.Interaction,
        teacher: discord.Member,
        gender: app_commands.Choice[str],
        level: str,
        stream: str,
        subjects: str,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        if level not in get_levels() or stream not in get_streams(level):
            await interaction.followup.send("❌ Niveau ou filière invalide.", ephemeral=True)
            return
        if any(
            role.name == ROLE_STUDENT
            or role.name.startswith(STUDENT_STREAM_ROLE_PREFIX)
            for role in teacher.roles
            if not role.managed
        ):
            await interaction.followup.send(
                "❌ Cet utilisateur possède encore un rôle **Élève**. Retire d'abord son rôle élève.",
                ephemeral=True,
            )
            return

        requested = {
            item.strip().casefold()
            for item in subjects.split(",")
            if item.strip()
        }
        selected = [
            subject
            for subject in get_stream_subjects(level, stream)
            if subject.casefold() in requested
            or get_subject_display_name(subject).casefold() in requested
        ]
        if not selected:
            await interaction.followup.send(
                "❌ Aucune matière reconnue pour cette filière. Utilise les suggestions.",
                ephemeral=True,
            )
            return

        stream_code = get_stream_abbreviation(level, stream)
        stream_role = get_managed_role(
            guild,
            f"{STREAM_ROLE_PREFIX}{stream_code}",
        )
        desired_role = get_managed_role(
            guild,
            ROLE_PROFESSOR_FEMALE
            if gender.value == "female"
            else ROLE_PROFESSOR,
        )
        other_role = get_managed_role(
            guild,
            ROLE_PROFESSOR
            if gender.value == "female"
            else ROLE_PROFESSOR_FEMALE,
        )
        if stream_role is None or desired_role is None:
            await interaction.followup.send(
                "❌ Les rôles scolaires requis pour cette filière n'existent pas. Vérifie `/build`.",
                ephemeral=True,
            )
            return

        config = get_guild_config(guild.id) or {}
        try:
            migrated = await _migrate_legacy_subject_roles(
                guild,
                teacher,
                config,
            )
            subject_roles = [
                await _get_or_create_global_subject_role(
                    guild,
                    config,
                    subject,
                )
                for subject in selected
            ]
            if other_role is not None and other_role in teacher.roles:
                await teacher.remove_roles(
                    other_role,
                    reason="Teacher role normalization",
                )
            await teacher.add_roles(
                desired_role,
                stream_role,
                *subject_roles,
                reason="School Manager full teacher assignment",
            )
            save_guild_config(guild.id, config)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Impossible d'attribuer les rôles. Vérifie la hiérarchie du bot.",
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

        subject_names = ", ".join(
            get_subject_display_name(subject)
            for subject in selected
        )
        migration_text = ""
        if migrated:
            migration_text = (
                "\n♻️ Anciens rôles matière migrés : "
                + ", ".join(
                    get_subject_display_name(subject)
                    for subject in migrated
                )
            )
        record_event(
            guild.id,
            interaction.user.id,
            interaction.user.display_name,
            "assignteacherfull",
            teacher.display_name,
            f"{stream_code}: {subject_names}",
        )
        await interaction.followup.send(
            f"✅ {teacher.mention} est affecté à **{stream_code}** pour : {subject_names}.\n"
            f"Rôles : `Filière - {stream_code}` + "
            + ", ".join(
                f"`{_global_subject_role_name(subject)}`"
                for subject in selected
            )
            + migration_text,
            ephemeral=True,
        )

    @app_commands.command(
        name="assignsubjectteachers",
        description="Affecter jusqu'à 5 professeurs à une matière.",
    )
    @app_commands.describe(
        level="Niveau scolaire",
        stream="Filière scolaire",
        subject="Matière de la filière",
        teacher1="Professeur 1",
        teacher2="Professeur 2 (optionnel)",
        teacher3="Professeur 3 (optionnel)",
        teacher4="Professeur 4 (optionnel)",
        teacher5="Professeur 5 (optionnel)",
    )
    @app_commands.autocomplete(
        level=level_autocomplete,
        stream=stream_autocomplete,
        subject=subject_autocomplete,
    )
    @management_check()
    async def assign_subject_teachers(
        self,
        interaction: discord.Interaction,
        level: str,
        stream: str,
        subject: str,
        teacher1: discord.Member,
        teacher2: discord.Member | None = None,
        teacher3: discord.Member | None = None,
        teacher4: discord.Member | None = None,
        teacher5: discord.Member | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.followup.send("❌ Niveau ou filière invalide.", ephemeral=True)
            return

        subject_match = next(
            (
                item
                for item in get_stream_subjects(level, stream)
                if item.casefold() == subject.casefold()
                or get_subject_display_name(item).casefold() == subject.casefold()
            ),
            None,
        )
        if subject_match is None:
            await interaction.followup.send(
                "❌ Matière invalide pour cette filière.",
                ephemeral=True,
            )
            return

        code = get_stream_abbreviation(level, stream)
        channel_name = _subject_channel_name(code, subject_match)
        channel = await _find_text_channel(
            guild,
            _stream_category_name(level, stream, code),
            channel_name,
        )
        if channel is None:
            await interaction.followup.send(
                f"❌ Le salon de matière **{channel_name}** est introuvable dans la catégorie de **{code}**. Vérifie `/build`.",
                ephemeral=True,
            )
            return

        stream_role_name = f"{STREAM_ROLE_PREFIX}{code}"
        stream_role = get_managed_role(guild, stream_role_name)
        subject_role_name = _global_subject_role_name(subject_match)
        if stream_role is None:
            await interaction.followup.send(
                "❌ Le rôle géré de cette filière n'existe pas. Vérifie `/build`.",
                ephemeral=True,
            )
            return

        selected_members: list[discord.Member] = []
        seen_ids: set[int] = set()
        for member in (
            teacher1,
            teacher2,
            teacher3,
            teacher4,
            teacher5,
        ):
            if member is not None and member.id not in seen_ids:
                seen_ids.add(member.id)
                selected_members.append(member)

        invalid = [
            member
            for member in selected_members
            if any(
                role.name == ROLE_STUDENT
                or role.name.startswith(STUDENT_STREAM_ROLE_PREFIX)
                for role in member.roles
                if not role.managed
            )
            or not _member_is_professor(member, guild)
        ]
        if invalid:
            await interaction.followup.send(
                "❌ Un ou plusieurs membres sélectionnés ne sont pas des professeurs valides.",
                ephemeral=True,
            )
            return

        config = get_guild_config(guild.id) or {}
        try:
            subject_role = await _get_or_create_global_subject_role(
                guild,
                config,
                subject_match,
            )
            for member in selected_members:
                await _migrate_legacy_subject_roles(
                    guild,
                    member,
                    config,
                )

            overwrites = {
                guild.default_role: hidden_overwrite(),
                stream_role: professor_subject_view_overwrite(),
                subject_role: professor_subject_member_overwrite(),
            }
            admin_role = get_managed_role(guild, ROLE_ADMIN)
            prof_role = get_managed_role(guild, ROLE_PROFESSOR)
            prof_f_role = get_managed_role(guild, ROLE_PROFESSOR_FEMALE)
            student_stream_role = get_managed_role(
                guild,
                f"{STUDENT_STREAM_ROLE_PREFIX}{code}",
            )
            if admin_role is not None:
                overwrites[admin_role] = administrator_overwrite()
            if prof_role is not None:
                overwrites[prof_role] = professor_subject_view_overwrite()
            if prof_f_role is not None:
                overwrites[prof_f_role] = professor_subject_view_overwrite()
            if student_stream_role is not None:
                overwrites[student_stream_role] = student_overwrite(can_send=True)
            await channel.edit(
                overwrites=overwrites,
                reason="School Manager subject teacher access",
            )
            for member in selected_members:
                await member.add_roles(
                    stream_role,
                    subject_role,
                    reason=(
                        f"School Manager subject assignment: "
                        f"{code} / {subject_match}"
                    ),
                )
            save_guild_config(guild.id, config)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Permission refusée. Vérifie Manage Roles, Manage Channels et la hiérarchie.",
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

        record_event(
            guild.id,
            interaction.user.id,
            interaction.user.display_name,
            "assignsubjectteachers",
            ", ".join(
                member.display_name for member in selected_members
            ),
            f"{code} / {get_subject_display_name(subject_match)}",
        )
        await interaction.followup.send(
            f"✅ **{len(selected_members)} professeur(s)** affecté(s) à **{code} / {get_subject_display_name(subject_match)}**.\n"
            f"Salon : {channel.mention}\n"
            f"Rôle matière partagé : `{subject_role_name}`",
            ephemeral=True,
        )

    @app_commands.command(
        name="reportabsence",
        description="Publier une annonce d'absence d'un professeur.",
    )
    @app_commands.describe(
        teacher="Professeur absent",
        duration="Durée en jours",
        classes="Classe(s)/filière(s) concernée(s)",
    )
    @app_commands.choices(
        duration=[
            app_commands.Choice(name="1 jour", value=1),
            app_commands.Choice(name="2 jours", value=2),
            app_commands.Choice(name="3 jours", value=3),
            app_commands.Choice(name="5 jours", value=5),
            app_commands.Choice(name="7 jours", value=7),
            app_commands.Choice(name="10 jours", value=10),
            app_commands.Choice(name="14 jours", value=14),
            app_commands.Choice(name="30 jours", value=30),
        ]
    )
    @app_commands.autocomplete(classes=class_autocomplete)
    @management_check()
    async def report_absence(
        self,
        interaction: discord.Interaction,
        teacher: discord.Member,
        duration: app_commands.Choice[int],
        classes: str,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if not _member_is_professor(teacher, guild):
            await interaction.followup.send(
                "❌ La personne sélectionnée n'est pas enregistrée comme **professeur**. Attribue d'abord `Prof` ou `Prof (F)` avec `/assignteacher`.",
                ephemeral=True,
            )
            return
        try:
            class_codes = _resolve_class_codes(guild, classes)
        except ValueError as exc:
            await interaction.followup.send(
                f"❌ {exc}",
                ephemeral=True,
            )
            return

        channel = await _find_text_channel(
            guild,
            "🏢・INFORMATIONS & ADMINISTRATION",
            GENERAL_CHANNELS["absences"],
        )
        if channel is None:
            await interaction.followup.send(
                "❌ Le salon d'absences n'existe pas. Lance `/build` après `/setup`.",
                ephemeral=True,
            )
            return

        days = duration.value
        duration_text = f"{days} jour" if days == 1 else f"{days} jours"
        class_text = ", ".join(class_codes)
        embed = discord.Embed(
            title="📢 Absence d'un professeur",
            description=(
                f"**Professeur :** {teacher.mention}\n"
                f"**Durée :** {duration_text}\n"
                f"**Classes concernées :** {class_text}\n"
                f"**Date :** {date.today().isoformat()}"
            ),
            colour=discord.Colour.orange(),
        )
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Le bot ne peut pas publier dans le salon d'absences.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"❌ Discord API : `{exc}`",
                ephemeral=True,
            )
            return
        record_event(
            guild.id,
            interaction.user.id,
            interaction.user.display_name,
            "reportabsence",
            teacher.display_name,
            f"{duration_text} | {class_text}",
        )
        await interaction.followup.send(
            f"✅ Absence publiée dans {channel.mention}.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    for name in OWNED_COMMANDS:
        bot.tree.remove_command(name)
    await bot.add_cog(CommandFixes(bot))

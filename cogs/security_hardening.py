"""Security hardening overrides for high-impact School Manager commands.

This cog is intentionally fail-closed.  It keeps the existing feature set while
ensuring destructive operations target only resources recorded as managed by the
bot, and that mutable academic commands resolve targets from recorded IDs.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime

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
)
from services.audit import record_event
from services.build_guard import get_build_lock
from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_PROFESSOR_FEMALE,
    ROLE_STUDENT,
    STREAM_ROLE_PREFIX,
    STUDENT_STREAM_ROLE_PREFIX,
    SUBJECT_ROLE_PREFIX,
    administrator_overwrite,
    get_managed_role,
    hidden_overwrite,
    management_check,
    owner_only_check,
    professor_subject_member_overwrite,
    professor_subject_view_overwrite,
    student_overwrite,
)
from services.server_builder import (
    CATEGORY_GENERAL,
    CATEGORY_VOICE,
    ServerBuilder,
    _safe_name,
    _stream_category_name,
    _subject_channel_name,
    _subject_role_name,
)
from services.storage import (
    get_active_academic_year,
    get_guild_config,
    list_academic_years,
    reset_guild_data,
    save_guild_config,
)

OVERRIDDEN_COMMANDS = {
    "removestream",
    "resetserver",
    "assignstudent",
    "assignteacher",
    "set_timetable",
    "setexam",
    "newyear",
}


# ---------------------------------------------------------------------------
# Shared security helpers
# ---------------------------------------------------------------------------


def _management_authorized(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    if guild is None:
        return False
    if interaction.user.id == guild.owner_id:
        return True
    role = get_managed_role(guild, ROLE_ADMIN)
    return role is not None and role in getattr(interaction.user, "roles", [])


def _student_or_staff_conflict(member: discord.Member, guild: discord.Guild) -> str | None:
    if member.bot:
        return "❌ Un bot ne peut pas recevoir un rôle scolaire."

    admin_role = get_managed_role(guild, ROLE_ADMIN)
    if admin_role is not None and admin_role in member.roles:
        return "❌ Cet utilisateur possède le rôle **Administration**. Retire d'abord ce rôle avant de l'affecter comme élève/professeur."

    student_role = get_managed_role(guild, ROLE_STUDENT)
    student_stream_roles = {
        role
        for role in guild.roles
        if not role.managed and role.name.startswith(STUDENT_STREAM_ROLE_PREFIX)
    }
    if (student_role is not None and student_role in member.roles) or any(role in member.roles for role in student_stream_roles):
        return "❌ Cet utilisateur possède encore un rôle **Élève**."
    return None


def _recorded_role_id(config: dict, name: str) -> int | None:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    roles = managed.get("roles", {}) if isinstance(managed, dict) else {}
    value = roles.get(name) if isinstance(roles, dict) else None
    return value if isinstance(value, int) and value > 0 else None


def _recorded_category_id(config: dict, name: str) -> int | None:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    categories = managed.get("categories", {}) if isinstance(managed, dict) else {}
    value = categories.get(name) if isinstance(categories, dict) else None
    return value if isinstance(value, int) and value > 0 else None


def _recorded_channel_id(config: dict, name: str) -> int | None:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    channels = managed.get("channels", {}) if isinstance(managed, dict) else {}
    value = channels.get(name) if isinstance(channels, dict) else None
    return value if isinstance(value, int) and value > 0 else None


def _configured_role_names_for_stream(level: str, stream: str) -> set[str]:
    code = get_stream_abbreviation(level, stream)
    return {
        f"{STREAM_ROLE_PREFIX}{code}",
        f"{STUDENT_STREAM_ROLE_PREFIX}{code}",
        *{
            _subject_role_name(level, stream, subject)
            for subject in get_stream_subjects(level, stream)
        },
    }


def _configured_channel_names_for_stream(level: str, stream: str) -> set[str]:
    code = get_stream_abbreviation(level, stream)
    return {
        f"📌-{code}・informations",
        f"🗓️-{code}・emploi-du-temps",
        f"📝-{code}・examens",
        *{_subject_channel_name(code, subject) for subject in get_stream_subjects(level, stream)},
    }


def _stream_managed_ids(config: dict, level: str, stream: str) -> tuple[set[int], set[int], int | None, int | None]:
    """Return only IDs explicitly recorded as managed for a stream.

    Crucially, this function never discovers IDs by scanning names.  That prevents
    destructive commands from adopting or deleting user-created resources.
    """
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    managed = managed if isinstance(managed, dict) else {}
    roles = managed.get("roles", {}) if isinstance(managed.get("roles", {}), dict) else {}
    channels = managed.get("channels", {}) if isinstance(managed.get("channels", {}), dict) else {}
    categories = managed.get("categories", {}) if isinstance(managed.get("categories", {}), dict) else {}

    role_ids = {
        value
        for name, value in roles.items()
        if name in _configured_role_names_for_stream(level, stream)
        and isinstance(value, int)
        and value > 0
    }
    channel_ids = {
        value
        for name, value in channels.items()
        if name in _configured_channel_names_for_stream(level, stream)
        and isinstance(value, int)
        and value > 0
    }

    code = get_stream_abbreviation(level, stream)
    category_name = _stream_category_name(level, stream, code)
    voice_name = f"🔊-{_safe_name(code, 30)}-à-distance"
    category_id = categories.get(category_name)
    voice_id = channels.get(voice_name)
    if not isinstance(category_id, int) or category_id <= 0:
        category_id = None
    if not isinstance(voice_id, int) or voice_id <= 0:
        voice_id = None
    return role_ids, channel_ids, category_id, voice_id


def _remove_managed_entries(config: dict, *, role_names: set[str], channel_names: set[str], category_names: set[str]) -> None:
    managed = config.get("managed", {})
    if not isinstance(managed, dict):
        return
    for section, names in (("roles", role_names), ("channels", channel_names), ("categories", category_names)):
        mapping = managed.get(section)
        if not isinstance(mapping, dict):
            continue
        for name in names:
            mapping.pop(name, None)


def _strict_managed_text_channel(guild: discord.Guild, config: dict, *, channel_name: str, category_name: str) -> discord.TextChannel | None:
    """Resolve a text channel strictly from managed IDs and its managed category."""
    category_id = _recorded_category_id(config, category_name)
    channel_id = _recorded_channel_id(config, channel_name)
    if category_id is None or channel_id is None:
        return None

    category = guild.get_channel(category_id)
    if not isinstance(category, discord.CategoryChannel) or category.name != category_name:
        return None
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel) or channel.name != channel_name:
        return None
    if channel.category_id != category.id:
        return None
    return channel


def _valid_academic_year(value: str) -> bool:
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r"(\d{4})/(\d{4})", value)
    if match is None:
        return False
    start, end = int(match.group(1)), int(match.group(2))
    return 2000 <= start <= 2100 and end == start + 1


def _academic_year_near_today() -> str:
    now = datetime.now()
    start = now.year if now.month >= 8 else now.year - 1
    return f"{start}/{start + 1}"


# ---------------------------------------------------------------------------
# Safe destructive commands
# ---------------------------------------------------------------------------


class HardenedServerCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="removestream", description="Supprimer uniquement les ressources School Manager enregistrées d'une filière.")
    @app_commands.describe(level="Niveau", stream="Filière à supprimer")
    @app_commands.autocomplete(
        level=lambda i, c: [],
        stream=lambda i, c: [],
    )
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

        configured = next(
            (
                item
                for item in config.get("levels", [])
                if isinstance(item, dict) and item.get("name") == level
            ),
            None,
        )
        if configured is None or not any(
            isinstance(item, dict) and item.get("name") == stream
            for item in configured.get("streams", []) or []
        ):
            await interaction.response.send_message(
                f"ℹ️ **{get_stream_abbreviation(level, stream)}** n'est pas configurée.",
                ephemeral=True,
            )
            return

        code = get_stream_abbreviation(level, stream)
        category_name = _stream_category_name(level, stream, code)
        role_names = _configured_role_names_for_stream(level, stream)
        channel_names = _configured_channel_names_for_stream(level, stream)
        voice_name = f"🔊-{_safe_name(code, 30)}-à-distance"
        channel_names_with_voice = channel_names | {voice_name}

        role_ids, channel_ids, category_id, voice_id = _stream_managed_ids(config, level, stream)
        await interaction.response.send_message(f"🗑️ Suppression sécurisée de **{code}** en cours...", ephemeral=True)

        lock = get_build_lock(guild.id)
        if lock.locked():
            await interaction.followup.send("⏳ Une construction est déjà en cours sur ce serveur.", ephemeral=True)
            return

        candidate = deepcopy(config)
        try:
            async with lock:
                # Delete only explicitly recorded channels that still belong to the
                # recorded stream category. Anything else is intentionally preserved.
                category = guild.get_channel(category_id) if category_id else None
                if isinstance(category, discord.CategoryChannel) and category.name == category_name:
                    for channel_id in sorted(channel_ids):
                        channel = guild.get_channel(channel_id)
                        if channel is None:
                            continue
                        if not isinstance(channel, discord.abc.GuildChannel):
                            continue
                        if channel.category_id != category.id:
                            continue
                        await channel.delete(reason="School Manager scoped stream removal")

                    # Only remove the category when it is empty. A custom channel
                    # deliberately blocks category deletion, so user content survives.
                    if not category.channels:
                        await category.delete(reason="School Manager scoped stream category removal")

                voice_category_id = _recorded_category_id(config, CATEGORY_VOICE)
                voice_category = guild.get_channel(voice_category_id) if voice_category_id else None
                if isinstance(voice_category, discord.CategoryChannel) and voice_id:
                    voice = guild.get_channel(voice_id)
                    if isinstance(voice, discord.VoiceChannel) and voice.category_id == voice_category.id and voice.name == voice_name:
                        await voice.delete(reason="School Manager scoped stream voice removal")

                top_role = guild.me.top_role if guild.me is not None else None
                for role_id in sorted(role_ids):
                    role = guild.get_role(role_id)
                    if role is None or role.managed or role.is_default():
                        continue
                    if top_role is not None and role >= top_role:
                        continue
                    await role.delete(reason="School Manager scoped stream role removal")

                target["streams"] = [
                    item
                    for item in target.get("streams", [])
                    if not isinstance(item, dict) or item.get("name") != stream
                ]
                candidate["levels"] = [
                    item for item in candidate.get("levels", [])
                    if not isinstance(item, dict) or item.get("streams")
                ]
                _remove_managed_entries(
                    candidate,
                    role_names=role_names,
                    channel_names=channel_names_with_voice,
                    category_names={category_name},
                )
                save_guild_config(guild.id, candidate)
        except discord.NotFound:
            await interaction.followup.send("⚠️ Une ressource avait déjà disparu. Configuration non modifiée; vérifie `/status` puis relance la suppression.", ephemeral=True)
            return
        except (discord.Forbidden, discord.HTTPException, OSError) as exc:
            await interaction.followup.send(f"❌ Suppression interrompue; configuration non modifiée : `{type(exc).__name__}: {exc}`", ephemeral=True)
            return

        remaining_custom = False
        if category_id:
            remaining_category = guild.get_channel(category_id)
            remaining_custom = isinstance(remaining_category, discord.CategoryChannel) and bool(remaining_category.channels)
        note = " Des salons non gérés étaient présents et ont été conservés." if remaining_custom else ""
        await interaction.followup.send(f"✅ **{code}** supprimée. Les ressources non enregistrées comme gérées ont été conservées.{note}", ephemeral=True)

    @app_commands.command(name="resetserver", description="Supprimer uniquement les ressources School Manager enregistrées.")
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

        config = get_guild_config(guild.id) or {}
        managed = config.get("managed", {}) if isinstance(config, dict) else {}
        managed = managed if isinstance(managed, dict) else {}
        role_map = managed.get("roles", {}) if isinstance(managed.get("roles", {}), dict) else {}
        channel_map = managed.get("channels", {}) if isinstance(managed.get("channels", {}), dict) else {}
        category_map = managed.get("categories", {}) if isinstance(managed.get("categories", {}), dict) else {}

        role_ids = {value for value in role_map.values() if isinstance(value, int) and value > 0}
        channel_ids = {value for value in channel_map.values() if isinstance(value, int) and value > 0}
        category_ids = {value for value in category_map.values() if isinstance(value, int) and value > 0}

        await interaction.response.send_message("🧹 **RESET SCHOOL MANAGER EN COURS...**", ephemeral=True)
        deleted_channels = deleted_categories = deleted_roles = 0
        retained_categories = 0

        try:
            async with lock:
                for channel_id in sorted(channel_ids):
                    channel = guild.get_channel(channel_id)
                    if channel is None or not isinstance(channel, discord.abc.GuildChannel):
                        continue
                    await channel.delete(reason="School Manager scoped reset")
                    deleted_channels += 1

                for category_id in sorted(category_ids):
                    category = guild.get_channel(category_id)
                    if not isinstance(category, discord.CategoryChannel):
                        continue
                    if category.channels:
                        retained_categories += 1
                        continue
                    await category.delete(reason="School Manager scoped reset")
                    deleted_categories += 1

                bot_member = guild.me
                top_role = bot_member.top_role if bot_member is not None else None
                for role_id in sorted(role_ids):
                    role = guild.get_role(role_id)
                    if role is None or role.managed or role.is_default():
                        continue
                    if top_role is not None and role >= top_role:
                        continue
                    await role.delete(reason="School Manager scoped reset")
                    deleted_roles += 1

                reset_guild_data(guild.id)
        except (discord.Forbidden, discord.HTTPException, OSError) as exc:
            await interaction.followup.send(
                f"❌ Reset interrompu : `{type(exc).__name__}: {exc}`. Les ressources restantes n'ont pas été ciblées par nom.",
                ephemeral=True,
            )
            return

        suffix = f" Catégories conservées car elles contiennent des ressources non gérées : **{retained_categories}**." if retained_categories else ""
        await interaction.followup.send(
            f"✅ Reset School Manager terminé. Channels: **{deleted_channels}** · Catégories: **{deleted_categories}** · Rôles: **{deleted_roles}**. Seuls les IDs enregistrés comme gérés ont été ciblés.{suffix}",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Safe student/teacher/content commands
# ---------------------------------------------------------------------------


async def level_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=level, value=level) for level in get_levels() if current.casefold() in level.casefold()][:25]


async def stream_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    if level not in get_levels():
        return []
    return [app_commands.Choice(name=stream, value=stream) for stream in get_streams(level) if current.casefold() in stream.casefold()][:25]


async def subject_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    stream = str(getattr(interaction.namespace, "stream", ""))
    if level not in get_levels() or stream not in get_streams(level):
        return []
    out: list[app_commands.Choice[str]] = []
    for subject in get_stream_subjects(level, stream):
        display = get_subject_display_name(subject)
        if current.casefold() in display.casefold() or current.casefold() in subject.casefold():
            out.append(app_commands.Choice(name=display[:100], value=subject))
    return out[:25]


class HardenedManagementCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="assignstudent", description="Affecter un élève à une filière.")
    @app_commands.describe(student="Élève", level="Niveau scolaire", stream="Filière scolaire")
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @management_check()
    async def assign_student(self, interaction: discord.Interaction, student: discord.Member, level: str, stream: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.response.send_message("❌ Niveau ou filière invalide.", ephemeral=True)
            return
        conflict = _student_or_staff_conflict(student, guild)
        if conflict:
            await interaction.response.send_message(conflict, ephemeral=True)
            return

        student_role = get_managed_role(guild, ROLE_STUDENT)
        student_stream_role = get_managed_role(guild, f"{STUDENT_STREAM_ROLE_PREFIX}{get_stream_abbreviation(level, stream)}")
        if student_role is None or student_stream_role is None:
            await interaction.response.send_message("❌ Les rôles scolaires gérés ne sont pas prêts. Lance `/setup` puis `/build`.", ephemeral=True)
            return
        year = get_active_academic_year(guild.id)
        if year is None:
            await interaction.response.send_message("❌ Aucune année scolaire active.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        original_roles = list(student.roles)
        try:
            managed_student_roles = [
                role for role in student.roles
                if not role.managed and (role.id == student_role.id or role.name.startswith(STUDENT_STREAM_ROLE_PREFIX))
            ]
            cleanup = [role for role in managed_student_roles if role != student_role and role != student_stream_role]
            if cleanup:
                await student.remove_roles(*cleanup, reason="School Manager student role normalization")
            await student.add_roles(student_role, student_stream_role, reason="School Manager student stream assignment")
            from services.storage import enroll_student_record
            enroll_student_record(guild.id, student.id, student.display_name, int(year["id"]), level, stream)
        except discord.Forbidden:
            try:
                await student.edit(roles=original_roles, reason="School Manager student assignment rollback")
            except discord.HTTPException:
                pass
            await interaction.followup.send("❌ Vérifie la hiérarchie des rôles du bot.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            try:
                await student.edit(roles=original_roles, reason="School Manager student assignment rollback")
            except discord.HTTPException:
                pass
            await interaction.followup.send(f"❌ Discord API : `{exc}`", ephemeral=True)
            return
        except Exception as exc:
            try:
                await student.edit(roles=original_roles, reason="School Manager student assignment rollback")
            except discord.HTTPException:
                pass
            await interaction.followup.send(f"❌ Affectation annulée : `{type(exc).__name__}: {exc}`", ephemeral=True)
            return

        record_event(guild.id, interaction.user.id, interaction.user.display_name, "assignstudent", student.display_name, f"{level}: {get_stream_abbreviation(level, stream)}")
        await interaction.followup.send(f"✅ {student.mention} est maintenant dans **{get_stream_abbreviation(level, stream)}** ({level}).", ephemeral=True)

    @app_commands.command(name="assignteacher", description="Donner le rôle Prof à un membre.")
    @app_commands.describe(teacher="Membre qui doit recevoir le rôle professeur")
    @app_commands.choices(gender=[app_commands.Choice(name="Prof", value="male"), app_commands.Choice(name="Prof (F)", value="female")])
    @management_check()
    async def assign_teacher(self, interaction: discord.Interaction, teacher: discord.Member, gender: app_commands.Choice[str]) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        conflict = _student_or_staff_conflict(teacher, guild)
        if conflict:
            await interaction.response.send_message(conflict, ephemeral=True)
            return
        role_name = ROLE_PROFESSOR_FEMALE if gender.value == "female" else ROLE_PROFESSOR
        role = get_managed_role(guild, role_name)
        if role is None:
            await interaction.response.send_message(f"❌ Le rôle géré `{role_name}` n'existe pas encore. Lance `/setup` puis `/build`.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        other_role = get_managed_role(guild, ROLE_PROFESSOR if gender.value == "female" else ROLE_PROFESSOR_FEMALE)
        try:
            if other_role is not None and other_role in teacher.roles:
                await teacher.remove_roles(other_role, reason="School Manager teacher gender normalization")
            await teacher.add_roles(role, reason="School Manager teacher assignment")
        except discord.Forbidden:
            await interaction.followup.send("❌ Impossible d'attribuer le rôle. Vérifie la hiérarchie.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Discord API : `{exc}`", ephemeral=True)
            return
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "assignteacher", teacher.display_name, role_name)
        await interaction.followup.send(f"✅ {teacher.mention} a reçu le rôle **{role_name}**.", ephemeral=True)

    @app_commands.command(name="set_timetable", description="Publier l'emploi du temps d'une filière sous forme d'image ou de fichier texte.")
    @app_commands.describe(level="Niveau scolaire", stream="Filière scolaire", timetable="Image de l'emploi du temps ou fichier .txt")
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @management_check()
    async def set_timetable(self, interaction: discord.Interaction, level: str, stream: str, timetable: discord.Attachment) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.followup.send("❌ Niveau ou filière invalide.", ephemeral=True)
            return

        filename = timetable.filename.lower()
        allowed = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".txt"}
        extension = "." + filename.rsplit(".", 1)[1] if "." in filename else ""
        if extension not in allowed:
            await interaction.followup.send("❌ Format invalide. Envoie une image ou un fichier `.txt`.", ephemeral=True)
            return
        if timetable.size > 15 * 1024 * 1024:
            await interaction.followup.send("❌ Fichier trop volumineux. Maximum : **15 MB**.", ephemeral=True)
            return

        code = get_stream_abbreviation(level, stream)
        channel_name = f"🗓️-{code}・emploi-du-temps"
        category_name = _stream_category_name(level, stream, code)
        config = get_guild_config(guild.id) or {}
        channel = _strict_managed_text_channel(guild, config, channel_name=channel_name, category_name=category_name)
        if channel is None:
            await interaction.followup.send("❌ Le channel géré d'emploi du temps est introuvable ou sa catégorie ne correspond plus à la configuration.", ephemeral=True)
            return

        try:
            uploaded = await timetable.to_file(filename=timetable.filename)
            message = await channel.send(content=f"📅 **Emploi du temps — {code}**", file=uploaded)
            config.setdefault("managed", {}).setdefault("messages", {})[f"{code}:timetable_message_id"] = message.id
            save_guild_config(guild.id, config)
        except discord.Forbidden:
            await interaction.followup.send("❌ Le bot ne peut pas publier dans le salon d'emploi du temps.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Discord API : `{exc}`", ephemeral=True)
            return
        except OSError as exc:
            await interaction.followup.send(f"❌ Stockage local : `{exc}`", ephemeral=True)
            return
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "set_timetable", code, timetable.filename)
        await interaction.followup.send(f"✅ Emploi du temps de **{code}** publié dans {channel.mention}.", ephemeral=True)

    @app_commands.command(name="setexam", description="Ajouter un examen avec une date et une plage horaire.")
    @app_commands.describe(level="Niveau scolaire", stream="Filière scolaire", subject="Matière", exam_date="Date: YYYY-MM-DD ou MM/DD", start_time="Heure de début", end_time="Heure de fin", details="Détails ou consignes")
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete, subject=subject_autocomplete)
    @app_commands.choices(
        start_time=[app_commands.Choice(name=f"{hour:02d}:{minute:02d}", value=f"{hour:02d}:{minute:02d}") for hour in range(7, 21) for minute in (0, 30)][:25],
        end_time=[app_commands.Choice(name=f"{hour:02d}:{minute:02d}", value=f"{hour:02d}:{minute:02d}") for hour in range(7, 21) for minute in (0, 30)][:25],
    )
    @management_check()
    async def set_exam(self, interaction: discord.Interaction, level: str, stream: str, subject: str, exam_date: str, start_time: app_commands.Choice[str], end_time: app_commands.Choice[str], details: str | None = None) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.followup.send("❌ Niveau ou filière invalide.", ephemeral=True)
            return
        curriculum_subject = next((candidate for candidate in get_stream_subjects(level, stream) if candidate.casefold() == subject.casefold() or get_subject_display_name(candidate).casefold() == subject.casefold()), None)
        if curriculum_subject is None:
            await interaction.followup.send("❌ Matière invalide pour cette filière.", ephemeral=True)
            return
        parsed = None
        for fmt in ("%Y-%m-%d", "%m/%d", "%m-%d"):
            try:
                parsed = datetime.strptime(exam_date.strip(), fmt).date()
                if fmt != "%Y-%m-%d":
                    parsed = parsed.replace(year=datetime.now().year)
                break
            except ValueError:
                continue
        if parsed is None:
            await interaction.followup.send("❌ Date invalide. Utilise `YYYY-MM-DD` ou `MM/DD`.", ephemeral=True)
            return
        if start_time.value >= end_time.value:
            await interaction.followup.send("❌ L'heure de début doit être avant l'heure de fin.", ephemeral=True)
            return

        code = get_stream_abbreviation(level, stream)
        channel_name = f"📝-{code}・examens"
        category_name = _stream_category_name(level, stream, code)
        config = get_guild_config(guild.id) or {}
        channel = _strict_managed_text_channel(guild, config, channel_name=channel_name, category_name=category_name)
        if channel is None:
            await interaction.followup.send("❌ Le channel géré d'examens est introuvable ou sa catégorie ne correspond plus à la configuration.", ephemeral=True)
            return

        subject_display = get_subject_display_name(curriculum_subject)
        embed = discord.Embed(
            title=f"📝 Examen — {subject_display}",
            colour=discord.Colour.red(),
            description=(
                f"**Filière :** {code}\n"
                f"**Date :** {parsed.strftime('%m/%d/%Y')}\n"
                f"**Horaire :** {start_time.value} → {end_time.value}\n"
                f"**Détails :** {details or 'Aucun'}"
            ),
        )
        embed.timestamp = discord.utils.utcnow()
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send("❌ Le bot ne peut pas publier dans le salon d'examens.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Discord API : `{exc}`", ephemeral=True)
            return
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "setexam", code, f"{subject_display} | {parsed.isoformat()} | {start_time.value}-{end_time.value}")
        await interaction.followup.send(f"✅ Examen de **{subject_display}** publié dans {channel.mention}.", ephemeral=True)

    @app_commands.command(name="newyear", description="Créer une nouvelle année scolaire et la rendre active.")
    @app_commands.describe(year="Format : 2026/2027")
    @management_check()
    async def new_year(self, interaction: discord.Interaction, year: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if not _valid_academic_year(year):
            await interaction.response.send_message("❌ Format invalide. Utilise `YYYY/YYYY` avec une année entre 2000 et 2100, par exemple `2026/2027`.", ephemeral=True)
            return

        config = deepcopy(get_guild_config(guild.id) or {"levels": []})
        active = get_active_academic_year(guild.id)
        if active is not None and str(active["name"]) == year:
            await interaction.response.send_message(f"ℹ️ **{year}** est déjà l'année scolaire active.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        config["academic_year"] = year
        try:
            save_guild_config(guild.id, config)
        except OSError as exc:
            await interaction.followup.send(f"❌ Impossible d'enregistrer l'année scolaire : `{exc}`", ephemeral=True)
            return
        await interaction.followup.send(f"✅ **{year}** est maintenant l'année scolaire active.", ephemeral=True)


# ---------------------------------------------------------------------------
# Setup UI authorization re-check
# ---------------------------------------------------------------------------


def _patch_setup_build_callback() -> None:
    from cogs.setup import SummaryView

    if getattr(SummaryView, "_security_hardening_applied", False):
        return
    original = SummaryView.build_callback

    async def guarded_build(self, interaction: discord.Interaction) -> None:
        if not _management_authorized(interaction):
            await interaction.response.send_message("❌ Tes droits d'administration ne sont plus valides pour cette configuration.", ephemeral=True)
            return
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        bot = guild.me
        if bot is None or not bot.guild_permissions.manage_channels or not bot.guild_permissions.manage_roles:
            await interaction.response.send_message("❌ Le bot n'a plus les permissions nécessaires pour construire le serveur.", ephemeral=True)
            return
        async def _check_hierarchy() -> str | None:
            from services.permissions import _preflight_message
            return _preflight_message(interaction, needs_channels=True, needs_roles=True)
        message = await _check_hierarchy()
        if message:
            await interaction.response.send_message(message, ephemeral=True)
            return
        await original(self, interaction)

    SummaryView.build_callback = guarded_build
    SummaryView._security_hardening_applied = True


async def setup(bot: commands.Bot) -> None:
    # Remove all high-impact command implementations registered by legacy cogs,
    # then install the fail-closed replacements from this cog.
    for name in OVERRIDDEN_COMMANDS:
        bot.tree.remove_command(name)
    _patch_setup_build_callback()
    await bot.add_cog(HardenedServerCommands(bot))
    await bot.add_cog(HardenedManagementCommands(bot))

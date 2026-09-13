"""Fail-closed security overrides for high-impact School Manager commands."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import get_levels, get_stream_abbreviation, get_stream_subjects, get_streams, get_subject_display_name
from services.audit import record_event
from services.build_guard import get_build_lock
from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_PROFESSOR_FEMALE,
    ROLE_STUDENT,
    STREAM_ROLE_PREFIX,
    STUDENT_STREAM_ROLE_PREFIX,
    administrator_overwrite,
    get_managed_role,
    hidden_overwrite,
    management_check,
    owner_only_check,
    professor_subject_member_overwrite,
    professor_subject_view_overwrite,
    student_overwrite,
)
from services.server_builder import CATEGORY_VOICE, _safe_name, _stream_category_name, _subject_channel_name, _subject_role_name
from services.storage import enroll_student_record, get_active_academic_year, get_guild_config, reset_guild_data, save_guild_config

OVERRIDDEN_COMMANDS = {"removestream", "resetserver", "assignstudent", "assignteacher", "set_timetable", "setexam", "newyear"}


def _management_authorized(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    if guild is None:
        return False
    if interaction.user.id == guild.owner_id:
        return True
    role = get_managed_role(guild, ROLE_ADMIN)
    return role is not None and role in getattr(interaction.user, "roles", [])


def _student_staff_conflict(member: discord.Member, guild: discord.Guild) -> str | None:
    if member.bot:
        return "❌ Un bot ne peut pas recevoir un rôle scolaire."
    admin_role = get_managed_role(guild, ROLE_ADMIN)
    if admin_role is not None and admin_role in member.roles:
        return "❌ Cet utilisateur possède le rôle **Administration**. Retire d'abord ce rôle avant une affectation scolaire."
    professor_roles = {
        role for role in (get_managed_role(guild, ROLE_PROFESSOR), get_managed_role(guild, ROLE_PROFESSOR_FEMALE)) if role is not None
    }
    if any(role in member.roles for role in professor_roles):
        return "❌ Cet utilisateur possède encore un rôle **Prof**. Retire d'abord son rôle professeur avant de l'affecter comme élève."
    return None


def _teacher_target_conflict(member: discord.Member, guild: discord.Guild) -> str | None:
    if member.bot:
        return "❌ Un bot ne peut pas être enregistré comme professeur."
    admin_role = get_managed_role(guild, ROLE_ADMIN)
    if admin_role is not None and admin_role in member.roles:
        return "❌ Un membre du rôle **Administration** ne peut pas recevoir un rôle professeur."
    student_role = get_managed_role(guild, ROLE_STUDENT)
    if student_role is not None and student_role in member.roles:
        return "❌ Cet utilisateur possède encore le rôle **Élève**. Retire-le d'abord."
    if any(not role.managed and role.name.startswith(STUDENT_STREAM_ROLE_PREFIX) for role in member.roles):
        return "❌ Cet utilisateur possède encore un rôle de filière **Élève**. Retire-le d'abord."
    return None


def _managed_mapping(config: dict, section: str) -> dict:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    value = managed.get(section, {}) if isinstance(managed, dict) else {}
    return value if isinstance(value, dict) else {}


def _recorded_id(config: dict, section: str, name: str) -> int | None:
    value = _managed_mapping(config, section).get(name)
    return value if isinstance(value, int) and value > 0 else None


def _stream_role_names(level: str, stream: str) -> set[str]:
    code = get_stream_abbreviation(level, stream)
    return {f"{STREAM_ROLE_PREFIX}{code}", f"{STUDENT_STREAM_ROLE_PREFIX}{code}", *{_subject_role_name(level, stream, subject) for subject in get_stream_subjects(level, stream)}}


def _stream_channel_names(level: str, stream: str) -> set[str]:
    code = get_stream_abbreviation(level, stream)
    return {f"📌-{code}・informations", f"🗓️-{code}・emploi-du-temps", f"📝-{code}・examens", *{_subject_channel_name(code, subject) for subject in get_stream_subjects(level, stream)}}


def _stream_managed_ids(config: dict, level: str, stream: str) -> tuple[set[int], set[int], int | None, int | None]:
    role_ids = {value for name, value in _managed_mapping(config, "roles").items() if name in _stream_role_names(level, stream) and isinstance(value, int) and value > 0}
    channel_ids = {value for name, value in _managed_mapping(config, "channels").items() if name in _stream_channel_names(level, stream) and isinstance(value, int) and value > 0}
    code = get_stream_abbreviation(level, stream)
    category_name = _stream_category_name(level, stream, code)
    voice_name = f"🔊-{_safe_name(code, 30)}-à-distance"
    return role_ids, channel_ids, _recorded_id(config, "categories", category_name), _recorded_id(config, "channels", voice_name)


def _remove_managed_entries(config: dict, *, role_names: set[str], channel_names: set[str], category_names: set[str]) -> None:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    if not isinstance(managed, dict):
        return
    for section, names in (("roles", role_names), ("channels", channel_names), ("categories", category_names)):
        mapping = managed.get(section)
        if isinstance(mapping, dict):
            for name in names:
                mapping.pop(name, None)


def _strict_managed_text_channel(guild: discord.Guild, config: dict, *, channel_name: str, category_name: str) -> discord.TextChannel | None:
    category_id = _recorded_id(config, "categories", category_name)
    channel_id = _recorded_id(config, "channels", channel_name)
    if category_id is None or channel_id is None:
        return None
    category = guild.get_channel(category_id)
    channel = guild.get_channel(channel_id)
    if not isinstance(category, discord.CategoryChannel) or category.name != category_name:
        return None
    if not isinstance(channel, discord.TextChannel) or channel.name != channel_name or channel.category_id != category.id:
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


def _contains(value: str, current: str) -> bool:
    return current.casefold() in value.casefold()


async def level_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=level, value=level) for level in get_levels() if _contains(level, current)][:25]


async def stream_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    if level not in get_levels():
        return []
    return [app_commands.Choice(name=stream, value=stream) for stream in get_streams(level) if _contains(stream, current)][:25]


async def subject_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    stream = str(getattr(interaction.namespace, "stream", ""))
    if level not in get_levels() or stream not in get_streams(level):
        return []
    return [
        app_commands.Choice(name=get_subject_display_name(subject)[:100], value=subject)
        for subject in get_stream_subjects(level, stream)
        if _contains(get_subject_display_name(subject), current) or _contains(subject, current)
    ][:25]


class HardenedServerCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="removestream", description="Supprimer uniquement les ressources School Manager enregistrées d'une filière.")
    @app_commands.describe(level="Niveau", stream="Filière à supprimer")
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
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
        target = next((item for item in config.get("levels", []) if isinstance(item, dict) and item.get("name") == level), None)
        if target is None or not any(isinstance(item, dict) and item.get("name") == stream for item in target.get("streams", []) or []):
            await interaction.response.send_message(f"ℹ️ **{get_stream_abbreviation(level, stream)}** n'est pas configurée.", ephemeral=True)
            return
        code = get_stream_abbreviation(level, stream)
        category_name = _stream_category_name(level, stream, code)
        voice_name = f"🔊-{_safe_name(code, 30)}-à-distance"
        role_names = _stream_role_names(level, stream)
        channel_names = _stream_channel_names(level, stream)
        role_ids, channel_ids, category_id, voice_id = _stream_managed_ids(config, level, stream)
        await interaction.response.send_message(f"🗑️ Suppression sécurisée de **{code}** en cours...", ephemeral=True)
        lock = get_build_lock(guild.id)
        if lock.locked():
            await interaction.followup.send("⏳ Une construction est déjà en cours sur ce serveur.", ephemeral=True)
            return
        candidate = deepcopy(config)
        try:
            async with lock:
                category = guild.get_channel(category_id) if category_id else None
                if isinstance(category, discord.CategoryChannel) and category.name == category_name:
                    for channel_id in sorted(channel_ids):
                        channel = guild.get_channel(channel_id)
                        if channel is None or not isinstance(channel, discord.abc.GuildChannel) or channel.category_id != category.id:
                            continue
                        await channel.delete(reason="School Manager scoped stream removal")
                    remaining = [channel for channel in guild.channels if getattr(channel, "category_id", None) == category.id]
                    if not remaining:
                        await category.delete(reason="School Manager scoped stream category removal")
                voice_category_id = _recorded_id(config, "categories", CATEGORY_VOICE)
                voice_category = guild.get_channel(voice_category_id) if voice_category_id else None
                if isinstance(voice_category, discord.CategoryChannel) and voice_id:
                    voice = guild.get_channel(voice_id)
                    if isinstance(voice, discord.VoiceChannel) and voice.category_id == voice_category.id and voice.name == voice_name:
                        await voice.delete(reason="School Manager scoped stream voice removal")
                top_role = guild.me.top_role if guild.me is not None else None
                for role_id in sorted(role_ids):
                    role = guild.get_role(role_id)
                    if role is None or role.managed or role.is_default() or (top_role is not None and role >= top_role):
                        continue
                    await role.delete(reason="School Manager scoped stream role removal")
                target["streams"] = [item for item in target.get("streams", []) if not isinstance(item, dict) or item.get("name") != stream]
                candidate["levels"] = [item for item in candidate.get("levels", []) if not isinstance(item, dict) or item.get("streams")]
                _remove_managed_entries(candidate, role_names=role_names, channel_names=channel_names | {voice_name}, category_names={category_name})
                save_guild_config(guild.id, candidate)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound, OSError) as exc:
            await interaction.followup.send(f"❌ Suppression interrompue; configuration non modifiée : `{type(exc).__name__}`", ephemeral=True)
            return
        remaining_category = guild.get_channel(category_id) if category_id else None
        note = " Les salons non gérés présents dans la catégorie ont été conservés." if isinstance(remaining_category, discord.CategoryChannel) and remaining_category.channels else ""
        await interaction.followup.send(f"✅ **{code}** supprimée. Seules les ressources explicitement enregistrées comme gérées ont été ciblées.{note}", ephemeral=True)

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
        role_ids = {value for value in _managed_mapping(config, "roles").values() if isinstance(value, int) and value > 0}
        channel_ids = {value for value in _managed_mapping(config, "channels").values() if isinstance(value, int) and value > 0}
        category_ids = {value for value in _managed_mapping(config, "categories").values() if isinstance(value, int) and value > 0}
        await interaction.response.send_message("🧹 **RESET SCHOOL MANAGER EN COURS...**", ephemeral=True)
        deleted_channels = deleted_categories = deleted_roles = retained_categories = 0
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
                    remaining = [channel for channel in guild.channels if getattr(channel, "category_id", None) == category.id]
                    if remaining:
                        retained_categories += 1
                        continue
                    await category.delete(reason="School Manager scoped reset")
                    deleted_categories += 1
                top_role = guild.me.top_role if guild.me is not None else None
                for role_id in sorted(role_ids):
                    role = guild.get_role(role_id)
                    if role is None or role.managed or role.is_default() or (top_role is not None and role >= top_role):
                        continue
                    await role.delete(reason="School Manager scoped reset")
                    deleted_roles += 1
                reset_guild_data(guild.id)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound, OSError) as exc:
            await interaction.followup.send(f"❌ Reset interrompu : `{type(exc).__name__}`. Aucun nom n'a été utilisé pour élargir le scope.", ephemeral=True)
            return
        suffix = f" Catégories conservées car elles contiennent des ressources non gérées : **{retained_categories}**." if retained_categories else ""
        await interaction.followup.send(f"✅ Reset terminé. Channels: **{deleted_channels}** · Catégories: **{deleted_categories}** · Rôles: **{deleted_roles}**. Seuls les IDs gérés enregistrés ont été ciblés.{suffix}", ephemeral=True)


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
        conflict = _student_staff_conflict(student, guild)
        if conflict:
            await interaction.response.send_message(conflict, ephemeral=True)
            return
        code = get_stream_abbreviation(level, stream)
        student_role = get_managed_role(guild, ROLE_STUDENT)
        student_stream_role = get_managed_role(guild, f"{STUDENT_STREAM_ROLE_PREFIX}{code}")
        if student_role is None or student_stream_role is None:
            await interaction.response.send_message("❌ Les rôles scolaires gérés ne sont pas prêts. Lance `/setup` puis `/build`.", ephemeral=True)
            return
        year = get_active_academic_year(guild.id)
        if year is None:
            await interaction.response.send_message("❌ Aucune année scolaire active.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        original_roles = list(student.roles)
        original_stream_roles = [role for role in student.roles if not role.managed and role.name.startswith(STUDENT_STREAM_ROLE_PREFIX)]
        try:
            remove = [role for role in original_stream_roles if role != student_stream_role]
            if remove:
                await student.remove_roles(*remove, reason="School Manager student stream normalization")
            await student.add_roles(student_role, student_stream_role, reason="School Manager student stream assignment")
            enroll_student_record(guild.id, student.id, student.display_name, int(year["id"]), level, stream)
        except (discord.Forbidden, discord.HTTPException, OSError) as exc:
            try:
                current_stream = [role for role in student.roles if role not in original_roles and not role.managed]
                if current_stream:
                    await student.remove_roles(*current_stream, reason="School Manager assignment rollback")
                restored = [role for role in original_roles if role not in student.roles and not role.managed]
                if restored:
                    await student.add_roles(*restored, reason="School Manager assignment rollback")
            except discord.HTTPException:
                pass
            await interaction.followup.send(f"❌ Affectation annulée : `{type(exc).__name__}`", ephemeral=True)
            return
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "assignstudent", student.display_name, f"{level}: {code}")
        await interaction.followup.send(f"✅ {student.mention} est maintenant dans **{code}** ({level}).", ephemeral=True)

    @app_commands.command(name="assignteacher", description="Donner le rôle Prof à un membre.")
    @app_commands.describe(teacher="Membre qui doit recevoir le rôle professeur")
    @app_commands.choices(gender=[app_commands.Choice(name="Prof", value="male"), app_commands.Choice(name="Prof (F)", value="female")])
    @management_check()
    async def assign_teacher(self, interaction: discord.Interaction, teacher: discord.Member, gender: app_commands.Choice[str]) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        conflict = _teacher_target_conflict(teacher, guild)
        if conflict:
            await interaction.response.send_message(conflict, ephemeral=True)
            return
        role_name = ROLE_PROFESSOR_FEMALE if gender.value == "female" else ROLE_PROFESSOR
        role = get_managed_role(guild, role_name)
        if role is None:
            await interaction.response.send_message(f"❌ Le rôle géré `{role_name}` n'existe pas encore. Lance `/setup` puis `/build`.", ephemeral=True)
            return
        other_role = get_managed_role(guild, ROLE_PROFESSOR if gender.value == "female" else ROLE_PROFESSOR_FEMALE)
        await interaction.response.defer(ephemeral=True)
        try:
            if other_role is not None and other_role in teacher.roles:
                await teacher.remove_roles(other_role, reason="School Manager teacher gender normalization")
            await teacher.add_roles(role, reason="School Manager teacher assignment")
        except (discord.Forbidden, discord.HTTPException) as exc:
            await interaction.followup.send(f"❌ Impossible d'attribuer le rôle : `{type(exc).__name__}`", ephemeral=True)
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
        extension = "." + timetable.filename.lower().rsplit(".", 1)[1] if "." in timetable.filename else ""
        if extension not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".txt"}:
            await interaction.followup.send("❌ Format invalide.", ephemeral=True)
            return
        if timetable.size > 15 * 1024 * 1024:
            await interaction.followup.send("❌ Fichier trop volumineux. Maximum : **15 MB**.", ephemeral=True)
            return
        code = get_stream_abbreviation(level, stream)
        config = get_guild_config(guild.id) or {}
        channel = _strict_managed_text_channel(guild, config, channel_name=f"🗓️-{code}・emploi-du-temps", category_name=_stream_category_name(level, stream, code))
        if channel is None:
            await interaction.followup.send("❌ Le channel géré d'emploi du temps est introuvable ou sa catégorie ne correspond plus à la configuration.", ephemeral=True)
            return
        try:
            message = await channel.send(content=f"📅 **Emploi du temps — {code}**", file=await timetable.to_file(filename=timetable.filename))
            config.setdefault("managed", {}).setdefault("messages", {})[f"{code}:timetable_message_id"] = message.id
            save_guild_config(guild.id, config)
        except (discord.Forbidden, discord.HTTPException, OSError) as exc:
            await interaction.followup.send(f"❌ Publication impossible : `{type(exc).__name__}`", ephemeral=True)
            return
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "set_timetable", code, timetable.filename)
        await interaction.followup.send(f"✅ Emploi du temps de **{code}** publié dans {channel.mention}.", ephemeral=True)

    @app_commands.command(name="setexam", description="Ajouter un examen avec une date et une plage horaire.")
    @app_commands.describe(level="Niveau scolaire", stream="Filière scolaire", subject="Matière", exam_date="Date: YYYY-MM-DD ou MM/DD", start_time="Heure de début", end_time="Heure de fin", details="Détails ou consignes")
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete, subject=subject_autocomplete)
    @app_commands.choices(
        start_time=[app_commands.Choice(name=f"{h:02d}:{m:02d}", value=f"{h:02d}:{m:02d}") for h in range(7, 21) for m in (0, 30)][:25],
        end_time=[app_commands.Choice(name=f"{h:02d}:{m:02d}", value=f"{h:02d}:{m:02d}") for h in range(7, 21) for m in (0, 30)][:25],
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
        match_subject = next((item for item in get_stream_subjects(level, stream) if item.casefold() == subject.casefold() or get_subject_display_name(item).casefold() == subject.casefold()), None)
        if match_subject is None:
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
            await interaction.followup.send("❌ Date invalide.", ephemeral=True)
            return
        if start_time.value >= end_time.value:
            await interaction.followup.send("❌ L'heure de début doit être avant l'heure de fin.", ephemeral=True)
            return
        code = get_stream_abbreviation(level, stream)
        config = get_guild_config(guild.id) or {}
        channel = _strict_managed_text_channel(guild, config, channel_name=f"📝-{code}・examens", category_name=_stream_category_name(level, stream, code))
        if channel is None:
            await interaction.followup.send("❌ Le channel géré d'examens est introuvable ou sa catégorie ne correspond plus à la configuration.", ephemeral=True)
            return
        display = get_subject_display_name(match_subject)
        embed = discord.Embed(title=f"📝 Examen — {display}", colour=discord.Colour.red(), description=f"**Filière :** {code}\n**Date :** {parsed.strftime('%m/%d/%Y')}\n**Horaire :** {start_time.value} → {end_time.value}\n**Détails :** {details or 'Aucun'}")
        embed.timestamp = discord.utils.utcnow()
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as exc:
            await interaction.followup.send(f"❌ Publication impossible : `{type(exc).__name__}`", ephemeral=True)
            return
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "setexam", code, f"{display} | {parsed.isoformat()} | {start_time.value}-{end_time.value}")
        await interaction.followup.send(f"✅ Examen de **{display}** publié dans {channel.mention}.", ephemeral=True)

    @app_commands.command(name="newyear", description="Créer une nouvelle année scolaire et la rendre active.")
    @app_commands.describe(year="Format : 2026/2027")
    @management_check()
    async def new_year(self, interaction: discord.Interaction, year: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if not _valid_academic_year(year):
            await interaction.response.send_message("❌ Format invalide. Utilise `YYYY/YYYY` avec une année entre 2000 et 2100.", ephemeral=True)
            return
        active = get_active_academic_year(guild.id)
        if active is not None and str(active["name"]) == year:
            await interaction.response.send_message(f"ℹ️ **{year}** est déjà l'année scolaire active.", ephemeral=True)
            return
        config = deepcopy(get_guild_config(guild.id) or {"levels": []})
        config["academic_year"] = year
        await interaction.response.defer(ephemeral=True)
        try:
            save_guild_config(guild.id, config)
        except OSError as exc:
            await interaction.followup.send(f"❌ Impossible d'enregistrer l'année scolaire : `{exc}`", ephemeral=True)
            return
        await interaction.followup.send(f"✅ **{year}** est maintenant l'année scolaire active.", ephemeral=True)


def _patch_setup_build_callback() -> None:
    from cogs.setup import SummaryView
    if getattr(SummaryView, "_security_hardening_applied", False):
        return
    original = SummaryView.build_callback

    async def guarded_build(self, interaction: discord.Interaction) -> None:
        if not _management_authorized(interaction):
            await interaction.response.send_message("❌ Tes droits d'administration ne sont plus valides pour cette configuration.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        from services.permissions import _preflight_message
        message = _preflight_message(interaction, needs_channels=True, needs_roles=True)
        if message:
            await interaction.response.send_message(message, ephemeral=True)
            return
        await original(self, interaction)

    SummaryView.build_callback = guarded_build
    SummaryView._security_hardening_applied = True


async def setup(bot: commands.Bot) -> None:
    for name in OVERRIDDEN_COMMANDS:
        bot.tree.remove_command(name)
    _patch_setup_build_callback()
    await bot.add_cog(HardenedServerCommands(bot))
    await bot.add_cog(HardenedManagementCommands(bot))

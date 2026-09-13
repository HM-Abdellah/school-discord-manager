"""Hardened replacements for Discord commands with dynamic school lookups."""

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
)
from services.audit import record_event
from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_PROFESSOR_FEMALE,
    ROLE_STUDENT,
    SUBJECT_ROLE_PREFIX,
    STUDENT_STREAM_ROLE_PREFIX,
    get_managed_role,
    hidden_overwrite,
    management_check,
    administrator_overwrite,
    professor_subject_member_overwrite,
    professor_subject_view_overwrite,
    student_overwrite,
)
from services.server_builder import _subject_channel_name, _stream_category_name
from services.storage import get_guild_config, save_guild_config

OVERRIDDEN_COMMANDS = {
    "assignteacherfull",
    "assignsubjectteachers",
    "set_timetable",
    "setexam",
    "reportabsence",
}


def _contains(value: str, current: str) -> bool:
    return current.casefold() in value.casefold()


def _normalize_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().replace("\ufe0f", "")


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
    choices: list[app_commands.Choice[str]] = []
    for subject in get_stream_subjects(level, stream):
        display = get_subject_display_name(subject)
        if _contains(display, current) or _contains(subject, current):
            choices.append(app_commands.Choice(name=display[:100], value=subject))
    return choices[:25]


async def teacher_subject_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    stream = str(getattr(interaction.namespace, "stream", ""))
    if level not in get_levels():
        return []
    preferred = set(get_stream_subjects(level, stream)) if stream in get_streams(level) else set()
    seen: set[str] = set()
    subjects: list[str] = []
    for candidate_stream in get_streams(level):
        for subject in get_stream_subjects(level, candidate_stream):
            key = subject.casefold()
            if key not in seen:
                seen.add(key)
                subjects.append(subject)
    subjects.sort(key=lambda item: (item not in preferred, get_subject_display_name(item).casefold()))
    choices: list[app_commands.Choice[str]] = []
    for subject in subjects:
        display = get_subject_display_name(subject)
        if _contains(display, current) or _contains(subject, current):
            choices.append(app_commands.Choice(name=display[:100], value=subject))
    return choices[:25]


async def class_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
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
            code = str(stream.get("abbreviation") or get_stream_abbreviation(level_name, stream_name))
            if code in seen_codes:
                continue
            seen_codes.add(code)
            label = f"{code} — {stream_name}"[:100]
            if _contains(label, current) or _contains(code, current) or _contains(stream_name, current):
                choices.append(app_commands.Choice(name=label, value=code))
    return choices[:25]


async def _find_text_channel(guild: discord.Guild, category_name: str, expected_name: str) -> discord.TextChannel | None:
    """Find a managed text channel by ID, then normalized name inside its expected category."""
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
        if isinstance(channel, discord.TextChannel) and _normalize_name(channel.name) == expected_norm:
            return channel
    try:
        channels_now = list(await guild.fetch_channels())
    except (discord.Forbidden, discord.HTTPException):
        channels_now = list(guild.channels)
    category = next((channel for channel in channels_now if isinstance(channel, discord.CategoryChannel) and _normalize_name(channel.name) == _normalize_name(category_name)), None)
    if category is not None:
        for channel in list(getattr(category, "text_channels", [])):
            if _normalize_name(channel.name) == expected_norm:
                return channel
    for channel in channels_now:
        if isinstance(channel, discord.TextChannel) and _normalize_name(channel.name) == expected_norm:
            return channel
    return None


def _global_subject_role_name(subject: str) -> str:
    return f"{SUBJECT_ROLE_PREFIX}{get_subject_display_name(subject)}"[:100]


def _member_is_professor(member: discord.Member, guild: discord.Guild) -> bool:
    professor_roles = {role.id for role in (get_managed_role(guild, ROLE_PROFESSOR), get_managed_role(guild, ROLE_PROFESSOR_FEMALE)) if role is not None}
    return any(role.id in professor_roles for role in member.roles if not role.managed)


def _configured_streams(guild: discord.Guild) -> list[tuple[str, str, str]]:
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
            code = str(stream.get("abbreviation") or get_stream_abbreviation(level_name, stream_name))
            if code not in seen:
                seen.add(code)
                result.append((level_name, stream_name, code))
    return result


def _resolve_class_codes(guild: discord.Guild, value: str) -> list[str]:
    available = {code.casefold(): code for _, _, code in _configured_streams(guild)}
    resolved: list[str] = []
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        code = available.get(token.casefold())
        if code is None:
            raise ValueError(f"Classe/filière inconnue : `{token}`. Utilise l'autocomplétion.")
        if code not in resolved:
            resolved.append(code)
    if not resolved:
        raise ValueError("Sélectionne au moins une classe/filière.")
    return resolved


async def _upsert_bot_embed(channel: discord.TextChannel, *, marker: str, embed: discord.Embed) -> discord.Message:
    bot_user = channel.guild.me
    async for message in channel.history(limit=50):
        if bot_user is not None and message.author.id == bot_user.id and message.embeds:
            footer = message.embeds[0].footer.text or ""
            if footer == marker:
                await message.edit(embed=embed)
                return message
    embed.set_footer(text=marker)
    return await channel.send(embed=embed)


class CommandFixes(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="assignteacherfull", description="Affecter un professeur à une filière et à une ou plusieurs matières.")
    @app_commands.describe(teacher="Professeur", gender="Type de rôle professeur", level="Niveau scolaire", stream="Filière scolaire", subjects="Matière(s), sélectionne une suggestion ou sépare par des virgules")
    @app_commands.choices(gender=[app_commands.Choice(name="Prof", value="male"), app_commands.Choice(name="Prof (F)", value="female")])
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete, subjects=teacher_subject_autocomplete)
    @management_check()
    async def assign_teacher_full(self, interaction: discord.Interaction, teacher: discord.Member, gender: app_commands.Choice[str], level: str, stream: str, subjects: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.followup.send("❌ Niveau ou filière invalide.", ephemeral=True)
            return
        if any(role.name == ROLE_STUDENT or role.name.startswith(STUDENT_STREAM_ROLE_PREFIX) for role in teacher.roles if not role.managed):
            await interaction.followup.send("❌ Cet utilisateur possède encore un rôle **Élève**. Retire d'abord son rôle élève.", ephemeral=True)
            return
        requested = {item.strip().casefold() for item in subjects.split(",") if item.strip()}
        selected = [subject for subject in get_stream_subjects(level, stream) if subject.casefold() in requested or get_subject_display_name(subject).casefold() in requested]
        if not selected:
            await interaction.followup.send("❌ Aucune matière reconnue pour cette filière. Utilise les suggestions.", ephemeral=True)
            return
        stream_code = get_stream_abbreviation(level, stream)
        stream_role = get_managed_role(guild, f"Filière - {stream_code}")
        desired_role = get_managed_role(guild, ROLE_PROFESSOR_FEMALE if gender.value == "female" else ROLE_PROFESSOR)
        other_role = get_managed_role(guild, ROLE_PROFESSOR if gender.value == "female" else ROLE_PROFESSOR_FEMALE)
        if stream_role is None or desired_role is None:
            await interaction.followup.send("❌ Les rôles scolaires requis pour cette filière n'existent pas. Vérifie `/build`.", ephemeral=True)
            return
        config = get_guild_config(guild.id) or {}
        try:
            subject_roles: list[discord.Role] = []
            for subject in selected:
                role_name = _global_subject_role_name(subject)
                role = get_managed_role(guild, role_name)
                if role is None:
                    role = await guild.create_role(name=role_name, permissions=discord.Permissions.none(), colour=discord.Colour.dark_blue(), mentionable=False, reason="School Manager global subject role")
                    config.setdefault("managed", {}).setdefault("roles", {})[role_name] = role.id
                subject_roles.append(role)
            if other_role is not None and other_role in teacher.roles:
                await teacher.remove_roles(other_role, reason="Teacher role normalization")
            await teacher.add_roles(desired_role, stream_role, *subject_roles, reason="School Manager full teacher assignment")
            save_guild_config(guild.id, config)
        except discord.Forbidden:
            await interaction.followup.send("❌ Impossible d'attribuer les rôles. Vérifie la hiérarchie du bot.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Discord API : `{exc}`", ephemeral=True)
            return
        except OSError as exc:
            await interaction.followup.send(f"❌ Stockage local : `{exc}`", ephemeral=True)
            return
        subject_names = ", ".join(get_subject_display_name(subject) for subject in selected)
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "assignteacherfull", teacher.display_name, f"{stream_code}: {subject_names}")
        await interaction.followup.send(f"✅ {teacher.mention} est affecté à **{stream_code}** pour : {subject_names}.\nRôles : `Filière - {stream_code}` + {', '.join(f'`{_global_subject_role_name(subject)}`' for subject in selected)}", ephemeral=True)

    @app_commands.command(name="assignsubjectteachers", description="Affecter jusqu'à 5 professeurs à une matière.")
    @app_commands.describe(level="Niveau scolaire", stream="Filière scolaire", subject="Matière de la filière", teacher1="Professeur 1", teacher2="Professeur 2 (optionnel)", teacher3="Professeur 3 (optionnel)", teacher4="Professeur 4 (optionnel)", teacher5="Professeur 5 (optionnel)")
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete, subject=subject_autocomplete)
    @management_check()
    async def assign_subject_teachers(self, interaction: discord.Interaction, level: str, stream: str, subject: str, teacher1: discord.Member, teacher2: discord.Member | None = None, teacher3: discord.Member | None = None, teacher4: discord.Member | None = None, teacher5: discord.Member | None = None) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.followup.send("❌ Niveau ou filière invalide.", ephemeral=True)
            return
        subject_match = next((item for item in get_stream_subjects(level, stream) if item.casefold() == subject.casefold() or get_subject_display_name(item).casefold() == subject.casefold()), None)
        if subject_match is None:
            await interaction.followup.send("❌ Matière invalide pour cette filière.", ephemeral=True)
            return
        code = get_stream_abbreviation(level, stream)
        channel_name = _subject_channel_name(code, subject_match)
        channel = await _find_text_channel(guild, _stream_category_name(level, stream, code), channel_name)
        if channel is None:
            await interaction.followup.send(f"❌ Le salon de matière **{channel_name}** est introuvable dans la catégorie de **{code}**. Vérifie `/build`.", ephemeral=True)
            return
        stream_role_name = f"Filière - {code}"
        stream_role = get_managed_role(guild, stream_role_name)
        subject_role_name = _global_subject_role_name(subject_match)
        subject_role = get_managed_role(guild, subject_role_name)
        if stream_role is None:
            await interaction.followup.send("❌ Le rôle géré de cette filière n'existe pas. Vérifie `/build`.", ephemeral=True)
            return
        selected_members: list[discord.Member] = []
        seen_ids: set[int] = set()
        for member in (teacher1, teacher2, teacher3, teacher4, teacher5):
            if member is not None and member.id not in seen_ids:
                seen_ids.add(member.id)
                selected_members.append(member)
        invalid = [member for member in selected_members if any(role.name == ROLE_STUDENT or role.name.startswith(STUDENT_STREAM_ROLE_PREFIX) for role in member.roles if not role.managed) or not _member_is_professor(member, guild)]
        if invalid:
            await interaction.followup.send("❌ Un ou plusieurs membres sélectionnés ne sont pas des professeurs valides.", ephemeral=True)
            return
        config = get_guild_config(guild.id) or {}
        try:
            if subject_role is None:
                subject_role = await guild.create_role(name=subject_role_name, permissions=discord.Permissions.none(), colour=discord.Colour.dark_blue(), mentionable=False, reason="School Manager global subject role")
                config.setdefault("managed", {}).setdefault("roles", {})[subject_role_name] = subject_role.id
            overwrites = {guild.default_role: hidden_overwrite(), stream_role: professor_subject_view_overwrite(), subject_role: professor_subject_member_overwrite()}
            admin_role = get_managed_role(guild, ROLE_ADMIN)
            prof_role = get_managed_role(guild, ROLE_PROFESSOR)
            prof_f_role = get_managed_role(guild, ROLE_PROFESSOR_FEMALE)
            student_stream_role = get_managed_role(guild, f"{STUDENT_STREAM_ROLE_PREFIX}{code}")
            if admin_role is not None:
                overwrites[admin_role] = administrator_overwrite()
            if prof_role is not None:
                overwrites[prof_role] = professor_subject_view_overwrite()
            if prof_f_role is not None:
                overwrites[prof_f_role] = professor_subject_view_overwrite()
            if student_stream_role is not None:
                overwrites[student_stream_role] = student_overwrite(can_send=True)
            await channel.edit(overwrites=overwrites, reason="School Manager subject teacher access")
            for member in selected_members:
                await member.add_roles(stream_role, subject_role, reason=f"School Manager subject assignment: {code} / {subject_match}")
            save_guild_config(guild.id, config)
        except discord.Forbidden:
            await interaction.followup.send("❌ Permission refusée. Vérifie Manage Roles, Manage Channels et la hiérarchie.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Discord API : `{exc}`", ephemeral=True)
            return
        except OSError as exc:
            await interaction.followup.send(f"❌ Stockage local : `{exc}`", ephemeral=True)
            return
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "assignsubjectteachers", ", ".join(member.display_name for member in selected_members), f"{code} / {get_subject_display_name(subject_match)}")
        await interaction.followup.send(f"✅ **{len(selected_members)} professeur(s)** affecté(s) à **{code} / {get_subject_display_name(subject_match)}**.\nSalon : {channel.mention}\nRôle matière partagé : `{subject_role_name}`", ephemeral=True)

    @app_commands.command(name="set_timetable", description="Mettre à jour l'emploi du temps d'une filière sans créer de nouveau salon.")
    @app_commands.describe(level="Niveau", stream="Filière", subject="Matière concernée", content="Horaire et détails de la séance")
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete, subject=subject_autocomplete)
    @management_check()
    async def set_timetable(self, interaction: discord.Interaction, level: str, stream: str, subject: str, content: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.followup.send("❌ Niveau ou filière invalide.", ephemeral=True)
            return
        curriculum_subject = next((candidate for candidate in get_stream_subjects(level, stream) if candidate == subject or get_subject_display_name(candidate).casefold() == subject.casefold()), None)
        if curriculum_subject is None:
            await interaction.followup.send("❌ Matière invalide pour cette filière.", ephemeral=True)
            return
        code = get_stream_abbreviation(level, stream)
        channel_name = f"🗓️-{code}・emploi-du-temps"
        channel = await _find_text_channel(guild, _stream_category_name(level, stream, code), channel_name)
        if channel is None:
            await interaction.followup.send("❌ Channel d'emploi du temps introuvable pour cette filière. Vérifie `/build`.", ephemeral=True)
            return
        subject_display = get_subject_display_name(curriculum_subject)
        embed = discord.Embed(title=f"🗓️ Emploi du temps — {code} / {subject_display}", description=content[:4000], colour=discord.Colour.blue())
        embed.timestamp = discord.utils.utcnow()
        try:
            await _upsert_bot_embed(channel, marker=f"SchoolManager:T:{code}:{curriculum_subject}", embed=embed)
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Discord API : `{exc}`", ephemeral=True)
            return
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "set_timetable", code, f"{subject_display} timetable updated")
        await interaction.followup.send(f"✅ Emploi du temps de **{subject_display}** mis à jour dans {channel.mention}.", ephemeral=True)

    @app_commands.command(name="setexam", description="Mettre à jour les examens d'une filière sans créer de nouveau salon.")
    @app_commands.describe(level="Niveau", stream="Filière", subject="Matière concernée", content="Dates, horaires et consignes des examens")
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete, subject=subject_autocomplete)
    @management_check()
    async def set_exam(self, interaction: discord.Interaction, level: str, stream: str, subject: str, content: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.followup.send("❌ Niveau ou filière invalide.", ephemeral=True)
            return
        curriculum_subject = next((candidate for candidate in get_stream_subjects(level, stream) if candidate == subject or get_subject_display_name(candidate).casefold() == subject.casefold()), None)
        if curriculum_subject is None:
            await interaction.followup.send("❌ Matière invalide pour cette filière.", ephemeral=True)
            return
        code = get_stream_abbreviation(level, stream)
        channel_name = f"📝-{code}・examens"
        channel = await _find_text_channel(guild, _stream_category_name(level, stream, code), channel_name)
        if channel is None:
            await interaction.followup.send("❌ Channel d'examens introuvable pour cette filière. Vérifie `/build`.", ephemeral=True)
            return
        subject_display = get_subject_display_name(curriculum_subject)
        embed = discord.Embed(title=f"📝 Examens — {code} / {subject_display}", description=content[:4000], colour=discord.Colour.red())
        embed.timestamp = discord.utils.utcnow()
        try:
            await _upsert_bot_embed(channel, marker=f"SchoolManager:E:{code}:{curriculum_subject}", embed=embed)
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Discord API : `{exc}`", ephemeral=True)
            return
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "setexam", code, f"{subject_display} exam content updated")
        await interaction.followup.send(f"✅ Examens de **{subject_display}** mis à jour dans {channel.mention}.", ephemeral=True)

    @app_commands.command(name="reportabsence", description="Publier une annonce d'absence d'un professeur.")
    @app_commands.describe(teacher="Professeur absent", duration="Durée en jours", classes="Classe(s)/filière(s) concernée(s)")
    @app_commands.choices(duration=[app_commands.Choice(name="1 jour", value=1), app_commands.Choice(name="2 jours", value=2), app_commands.Choice(name="3 jours", value=3), app_commands.Choice(name="5 jours", value=5), app_commands.Choice(name="7 jours", value=7), app_commands.Choice(name="10 jours", value=10), app_commands.Choice(name="14 jours", value=14), app_commands.Choice(name="30 jours", value=30)])
    @app_commands.autocomplete(classes=class_autocomplete)
    @management_check()
    async def report_absence(self, interaction: discord.Interaction, teacher: discord.Member, duration: app_commands.Choice[int], classes: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if not _member_is_professor(teacher, guild):
            await interaction.followup.send("❌ La personne sélectionnée n'est pas enregistrée comme **professeur**. Attribue d'abord `Prof` ou `Prof (F)` avec `/assignteacher`.", ephemeral=True)
            return
        try:
            class_codes = _resolve_class_codes(guild, classes)
        except ValueError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        channel = await _find_text_channel(guild, "🏢・INFORMATIONS & ADMINISTRATION", GENERAL_CHANNELS["absences"])
        if channel is None:
            await interaction.followup.send("❌ Le salon d'absences n'existe pas. Lance `/build` après `/setup`.", ephemeral=True)
            return
        days = duration.value
        duration_text = f"{days} jour" if days == 1 else f"{days} jours"
        class_text = ", ".join(class_codes)
        embed = discord.Embed(title="📢 Absence d'un professeur", description=f"**Professeur :** {teacher.mention}\n**Durée :** {duration_text}\n**Classes concernées :** {class_text}\n**Date :** {date.today().isoformat()}", colour=discord.Colour.orange())
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send("❌ Le bot ne peut pas publier dans le salon d'absences.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Discord API : `{exc}`", ephemeral=True)
            return
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "reportabsence", teacher.display_name, f"{duration_text} | {class_text}")
        await interaction.followup.send(f"✅ Absence publiée dans {channel.mention}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    for name in OVERRIDDEN_COMMANDS:
        bot.tree.remove_command(name)
    await bot.add_cog(CommandFixes(bot))

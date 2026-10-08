
from __future__ import annotations

from copy import deepcopy
import sqlite3

import discord

from config.curriculum import get_stream_abbreviation, get_stream_subjects, get_streams, get_subject_display_name, get_subject_internal_code
from services.audit import record_event
from services.permissions import (
    ROLE_PROFESSOR,
    ROLE_PROFESSOR_FEMALE,
    ROLE_TEACHER_PENDING,
    SUBJECT_ROLE_PREFIX,
    STREAM_ROLE_PREFIX,
    get_managed_role,
    hidden_overwrite,
    professor_subject_member_overwrite,
    professor_subject_view_overwrite,
    student_overwrite,
)
from services.role_conflicts import teacher_target_conflict
from services.role_transactions import restore_role_presence, snapshot_role_presence
from services.storage import delete_teacher_registration, get_guild_config, record_teacher_registration, save_guild_config


def _global_subject_role_name(subject: str) -> str:
    return f"{SUBJECT_ROLE_PREFIX}{get_subject_display_name(subject)}"[:100]


def _configured_streams(guild: discord.Guild) -> list[tuple[str, str, str]]:
    config = get_guild_config(guild.id) or {}
    result: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for level in config.get("levels", []):
        if not isinstance(level, dict) or not isinstance(level.get("name"), str):
            continue
        level_name = level["name"]
        for stream in level.get("streams", []) or []:
            if not isinstance(stream, dict) or not isinstance(stream.get("name"), str):
                continue
            stream_name = stream["name"]
            code = str(stream.get("abbreviation") or get_stream_abbreviation(level_name, stream_name))
            if code not in seen:
                seen.add(code)
                result.append((level_name, stream_name, code))
    return result


async def _get_or_create_global_subject_role(guild: discord.Guild, config: dict, subject: str) -> discord.Role:
    role_name = _global_subject_role_name(subject)
    managed = config.setdefault("managed", {}).setdefault("roles", {})
    recorded_id = managed.get(role_name)
    if isinstance(recorded_id, int) and recorded_id > 0:
        role = guild.get_role(recorded_id)
        if role is None:
            raise RuntimeError(f"Managed role ID {recorded_id} for {role_name} is missing from Discord.")
        if role.managed or role.name != role_name:
            raise RuntimeError(f"Managed role ID {recorded_id} does not identify {role_name}.")
        return role

    collision = discord.utils.get(guild.roles, name=role_name)
    if collision is not None:
        raise RuntimeError(f"Unmanaged role collision for {role_name}.")

    role = await guild.create_role(
        name=role_name,
        permissions=discord.Permissions.none(),
        colour=discord.Colour.dark_blue(),
        mentionable=False,
        reason="School Manager global subject role",
    )
    managed[role_name] = role.id
    return role


async def _migrate_legacy_subject_roles(
    guild: discord.Guild,
    member: discord.Member,
    config: dict,
) -> tuple[
    list[str],
    list[discord.Role],
    list[discord.Role],
    list[tuple[discord.abc.GuildChannel, discord.Role, discord.PermissionOverwrite | None]],
]:
    legacy_map: dict[str, str] = {}
    for level_name, stream_name, code in _configured_streams(guild):
        for subject in get_stream_subjects(level_name, stream_name):
            legacy_map[f"{SUBJECT_ROLE_PREFIX}{code} - {get_subject_internal_code(subject)}"] = subject

    old_roles = [role for role in member.roles if not role.managed and role.name in legacy_map]
    if not old_roles:
        return [], [], [], []

    migrated: list[str] = []
    new_roles: list[discord.Role] = []
    created_roles: list[discord.Role] = []
    original_presence = snapshot_role_presence(member, list(old_roles))
    original_permissions: list[tuple[discord.abc.GuildChannel, discord.Role, discord.PermissionOverwrite | None]] = []
    seen_permission_keys: set[tuple[int, int]] = set()

    try:
        for old_role in old_roles:
            subject = legacy_map[old_role.name]
            role_name = _global_subject_role_name(subject)
            managed_roles = config.get("managed", {}).get("roles", {}) if isinstance(config.get("managed"), dict) else {}
            before_id = managed_roles.get(role_name) if isinstance(managed_roles, dict) else None
            new_role = await _get_or_create_global_subject_role(guild, config, subject)
            if not isinstance(before_id, int) or before_id <= 0:
                created_roles.append(new_role)
            if new_role not in new_roles:
                new_roles.append(new_role)
            if subject not in migrated:
                migrated.append(subject)

            managed = config.get("managed", {}) if isinstance(config, dict) else {}
            managed_channels = managed.get("channels", {}) if isinstance(managed, dict) else {}
            managed_channel_ids = {
                value for value in managed_channels.values()
                if isinstance(value, int) and value > 0
            } if isinstance(managed_channels, dict) else set()
            for channel in guild.channels:
                if getattr(channel, "id", None) not in managed_channel_ids:
                    continue
                old_overwrite = getattr(channel, "overwrites", {}).get(old_role)
                if old_overwrite is None:
                    continue
                key = (getattr(channel, "id", 0), new_role.id)
                if key not in seen_permission_keys:
                    seen_permission_keys.add(key)
                    original_permissions.append((
                        channel,
                        new_role,
                        getattr(channel, "overwrites", {}).get(new_role),
                    ))
                await channel.set_permissions(
                    new_role,
                    overwrite=old_overwrite,
                    reason="School Manager subject role migration",
                )

        tracked_roles = list(old_roles) + [role for role in new_roles if role not in old_roles]
        original_presence = snapshot_role_presence(member, tracked_roles)
        await member.add_roles(*new_roles, reason="School Manager migrate legacy subject roles")
        await member.remove_roles(*old_roles, reason="School Manager remove legacy subject roles")
    except (discord.Forbidden, discord.HTTPException, OSError, RuntimeError):
        tracked_roles = list(old_roles) + [role for role in new_roles if role not in old_roles]
        try:
            await restore_role_presence(
                member,
                tracked_roles,
                original_presence,
                reason="School Manager legacy role migration rollback",
            )
        except discord.HTTPException:
            pass
        for channel, role, overwrite in reversed(original_permissions):
            try:
                await channel.set_permissions(
                    role,
                    overwrite=overwrite,
                    reason="School Manager subject role migration rollback",
                )
            except discord.HTTPException:
                pass
        for role in reversed(created_roles):
            try:
                await role.delete(reason="School Manager subject role migration rollback")
            except discord.HTTPException:
                pass
        raise
    tracked_roles = list(old_roles) + [role for role in new_roles if role not in old_roles]
    return migrated, created_roles, tracked_roles, original_permissions


class TeacherAssignmentError(RuntimeError):
    pass


async def execute_teacher_assignment(
    *,
    guild: discord.Guild,
    teacher: discord.Member,
    gender_value: str,
    level: str,
    stream: str,
    subjects: str,
    actor_id: int,
    actor_display_name: str,
    self_registration: bool,
) -> dict:
    conflict = teacher_target_conflict(teacher, guild)
    if conflict:
        raise TeacherAssignmentError(conflict)
    if level not in get_levels() or stream not in get_streams(level):
        raise TeacherAssignmentError("❌ Niveau ou filière invalide.")

    requested = {item.strip().casefold() for item in subjects.split(",") if item.strip()}
    selected = [
        subject
        for subject in get_stream_subjects(level, stream)
        if subject.casefold() in requested or get_subject_display_name(subject).casefold() in requested
    ]
    if not selected:
        raise TeacherAssignmentError("❌ Aucune matière reconnue pour cette filière. Vérifie les noms des matières.")

    if gender_value not in {"male", "female"}:
        raise TeacherAssignmentError("❌ Type de professeur invalide.")

    stream_code = get_stream_abbreviation(level, stream)
    stream_role = get_managed_role(guild, f"{STREAM_ROLE_PREFIX}{stream_code}")
    desired_role = get_managed_role(guild, ROLE_PROFESSOR_FEMALE if gender_value == "female" else ROLE_PROFESSOR)
    other_role = get_managed_role(guild, ROLE_PROFESSOR if gender_value == "female" else ROLE_PROFESSOR_FEMALE)
    pending_role = get_managed_role(guild, ROLE_TEACHER_PENDING) if self_registration else None

    if stream_role is None or desired_role is None:
        raise TeacherAssignmentError("❌ Les rôles scolaires requis pour cette filière n'existent pas. Vérifie /build.")
    if self_registration and pending_role is None:
        raise TeacherAssignmentError("❌ Le rôle d'inscription professeur est introuvable. Génère un nouveau QR professeur.")

    config = get_guild_config(guild.id) or {}
    working_config = deepcopy(config)
    tracked_roles = [role for role in (desired_role, other_role, stream_role, pending_role) if role is not None]
    initial_role_ids = {role.id for role in teacher.roles}
    created_subject_roles: list[discord.Role] = []
    subject_roles: list[discord.Role] = []
    migration_permission_backups: list[tuple[discord.abc.GuildChannel, discord.Role, discord.PermissionOverwrite | None]] = []
    registration_created = False
    migrated: list[str] = []

    try:
        migrated, migration_created_roles, migration_tracked_roles, migration_permission_backups = await _migrate_legacy_subject_roles(
            guild, teacher, working_config
        )
        for role in migration_tracked_roles:
            if role not in tracked_roles:
                tracked_roles.append(role)
        for role in migration_created_roles:
            if role not in created_subject_roles:
                created_subject_roles.append(role)

        for subject in selected:
            role_name = _global_subject_role_name(subject)
            managed_roles = working_config.get("managed", {}).get("roles", {}) if isinstance(working_config.get("managed"), dict) else {}
            before_id = managed_roles.get(role_name) if isinstance(managed_roles, dict) else None
            role = await _get_or_create_global_subject_role(guild, working_config, subject)
            if not isinstance(before_id, int) or before_id <= 0:
                created_subject_roles.append(role)
            subject_roles.append(role)
        for role in subject_roles:
            if role not in tracked_roles:
                tracked_roles.append(role)

        if desired_role not in teacher.roles:
            await teacher.add_roles(desired_role, reason="School Manager teacher assignment")
        await teacher.add_roles(stream_role, *subject_roles, reason="School Manager full teacher assignment")
        if other_role is not None and other_role in teacher.roles:
            await teacher.remove_roles(other_role, reason="Teacher role normalization")

        if self_registration:
            record_teacher_registration(
                guild.id,
                teacher.id,
                teacher.display_name,
                gender_value,
                level,
                stream,
                ", ".join(get_subject_display_name(subject) for subject in selected),
            )
            registration_created = True
            if pending_role is not None and pending_role in teacher.roles:
                await teacher.remove_roles(pending_role, reason="School Manager teacher self-registration completed")

        save_guild_config(guild.id, working_config)
    except sqlite3.Error as exc:
        try:
            await restore_role_presence(
                teacher,
                tracked_roles,
                initial_role_ids,
                reason="School Manager full teacher assignment rollback",
            )
        except discord.HTTPException:
            pass
        if registration_created and self_registration:
            try:
                delete_teacher_registration(guild.id, teacher.id)
            except OSError:
                pass
        for channel, role, overwrite in reversed(migration_permission_backups):
            try:
                await channel.set_permissions(
                    role,
                    overwrite=overwrite,
                    reason="School Manager full teacher assignment rollback",
                )
            except discord.HTTPException:
                pass
        for created_role in reversed(created_subject_roles):
            try:
                await created_role.delete(reason="School Manager full teacher assignment rollback")
            except discord.HTTPException:
                pass
        raise TeacherAssignmentError(f"❌ Erreur de stockage SQLite : {exc}") from exc

    except (discord.Forbidden, discord.HTTPException, OSError, RuntimeError, ValueError) as exc:
        try:
            await restore_role_presence(
                teacher,
                tracked_roles,
                initial_role_ids,
                reason="School Manager full teacher assignment rollback",
            )
        except discord.HTTPException:
            pass
        if registration_created and self_registration:
            try:
                delete_teacher_registration(guild.id, teacher.id)
            except OSError:
                pass
        for channel, role, overwrite in reversed(migration_permission_backups):
            try:
                await channel.set_permissions(
                    role,
                    overwrite=overwrite,
                    reason="School Manager legacy role migration rollback",
                )
            except discord.HTTPException:
                pass
        for created_role in reversed(created_subject_roles):
            try:
                await created_role.delete(reason="School Manager full teacher assignment rollback")
            except discord.HTTPException:
                pass
        if isinstance(exc, discord.Forbidden):
            raise TeacherAssignmentError("❌ Impossible d'attribuer les rôles. Vérifie la hiérarchie du bot.") from exc
        if isinstance(exc, discord.HTTPException):
            raise TeacherAssignmentError(f"❌ Discord API : {exc}") from exc
        if isinstance(exc, OSError):
            raise TeacherAssignmentError(f"❌ Stockage local : {exc}") from exc
        if isinstance(exc, ValueError):
            raise TeacherAssignmentError(f"❌ Données invalides : {exc}") from exc
        raise TeacherAssignmentError(f"❌ Opération refusée : {exc}") from exc

    subject_names = ", ".join(get_subject_display_name(subject) for subject in selected)
    record_event(
        guild.id,
        actor_id,
        actor_display_name,
        "assignteacherfull",
        teacher.display_name,
        f"{stream_code}: {subject_names}" + (";self-registration" if self_registration else ""),
    )
    return {
        "stream_code": stream_code,
        "subject_names": subject_names,
        "subject_role_names": [_global_subject_role_name(subject) for subject in selected],
        "migration_text": (
            "\n♻️ Anciens rôles matière migrés : "
            + ", ".join(get_subject_display_name(subject) for subject in migrated)
            if migrated
            else ""
        ),
    }

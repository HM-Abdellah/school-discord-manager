"""Idempotent public-thread provisioning for registered teacher assignments."""

from __future__ import annotations

import discord

from config.curriculum import get_stream_abbreviation, get_subject_display_name
from services.discord_registry import persist_registry_repair, resolve_managed_text_channel
from services.server_builder import _stream_category_name, _subject_channel_name
from services.storage import (
    get_guild_config,
    get_teacher_assignments,
    update_teacher_assignment_thread,
)


async def all_public_threads(channel: discord.TextChannel) -> list[discord.Thread]:
    """Return active and archived public threads, failing closed on archive errors."""
    threads = {
        thread.id: thread
        for thread in channel.threads
        if thread.type == discord.ChannelType.public_thread
    }
    async for thread in channel.archived_threads(limit=None):
        if thread.type == discord.ChannelType.public_thread:
            threads[thread.id] = thread
    return list(threads.values())


def _thread_name(full_name: str, section: int) -> str:
    # Discord display names are short; truncation is defensive for imported data.
    return f"{' '.join(str(full_name).split())} • {section}"[:100]


def _error_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]


_THREAD_CHANNEL_REQUIRED_PERMISSIONS = (
    "view_channel",
    "read_message_history",
    "send_messages",
    "create_public_threads",
    "manage_threads",
)


async def _ensure_bot_thread_channel_access(
    guild: discord.Guild, channel: discord.TextChannel
) -> bool:
    """Repair only the bot member's access to a trusted managed subject channel.

    Managed subject channels intentionally deny @everyone visibility. Without a
    bot-specific overwrite, the bot can resolve the channel from guild state but
    Discord rejects thread operations with 50001 Missing Access. A member-level
    overwrite is narrowly scoped to this bot and leaves all student/teacher
    visibility rules intact.
    """
    bot_member = getattr(guild, "me", None)
    if bot_member is None:
        # Lightweight test doubles predating this helper have no ``me`` field.
        # A real Guild exposes the property but can return None if the bot member
        # is not cached; fail clearly instead of silently skipping the repair.
        if not hasattr(guild, "me"):
            return False
        raise RuntimeError(
            "Impossible de réparer les permissions du salon géré : le membre du bot "
            "(guild.me) n'est pas disponible dans le cache Discord. Vérifiez que le bot "
            "est bien présent dans ce serveur puis reconnectez-le avant de relancer la synchronisation."
        )

    effective = channel.permissions_for(bot_member)
    missing = [
        permission
        for permission in _THREAD_CHANNEL_REQUIRED_PERMISSIONS
        if not getattr(effective, permission, False)
    ]
    if not missing:
        return False

    guild_permissions = getattr(bot_member, "guild_permissions", None)
    if not getattr(guild_permissions, "manage_roles", False):
        raise RuntimeError(
            f"Le bot n'a pas les permissions effectives {', '.join(missing)} dans le salon géré "
            f"{channel.name!r} et ne peut pas corriger les overwrites sans la permission serveur Manage Roles."
        )

    try:
        overwrite = channel.overwrites_for(bot_member)
        for permission in _THREAD_CHANNEL_REQUIRED_PERMISSIONS:
            setattr(overwrite, permission, True)
        await channel.set_permissions(
            bot_member,
            overwrite=overwrite,
            reason="School Manager: restore bot access to managed subject channel for teacher threads",
        )
    except (discord.Forbidden, discord.HTTPException) as exc:
        raise RuntimeError(
            f"Le bot ne possède pas les permissions {', '.join(missing)} dans le salon géré "
            f"{channel.name!r}; Discord a refusé la réparation ciblée de ses permissions: "
            f"{_error_text(exc)}"
        ) from exc

    return True


async def _activate_thread(thread: discord.Thread) -> None:
    """Reopen a matching historical thread when it becomes a current assignment again."""
    if getattr(thread, "archived", False) or getattr(thread, "locked", False):
        await thread.edit(
            archived=False,
            locked=False,
            reason="School Manager: restore current teacher assignment thread",
        )


async def ensure_teacher_threads(guild: discord.Guild, teacher_id: int) -> dict[str, object]:
    """Ensure one public thread exists per registered section/subject assignment.

    Assignment rows are persisted before this function is called. Existing
    active/archived threads are adopted by ID or exact name before anything is
    created, so repeated calls and interrupted previous runs are safe to retry.
    """
    all_rows = get_teacher_assignments(guild.id)
    rows = [row for row in all_rows if int(row["discord_id"]) == teacher_id]
    result: dict[str, object] = {"created": 0, "existing": 0, "failed": 0, "errors": []}
    if not rows:
        return result

    # Avoid accidentally attaching a same-named public thread to the wrong
    # teacher when two staff members share a display name and section number.
    thread_owners = {
        int(row["thread_id"]): (int(row["discord_id"]), int(row["id"]))
        for row in all_rows
        if isinstance(row["thread_id"], int) and row["thread_id"] > 0
    }
    config = get_guild_config(guild.id) or {}
    grouped: dict[tuple[str, str, str], list[object]] = {}
    for row in rows:
        key = (str(row["level_name"]), str(row["stream_name"]), str(row["subject_name"]))
        grouped.setdefault(key, []).append(row)

    errors: list[str] = result["errors"]  # type: ignore[assignment]
    for (level, stream, subject), group_rows in grouped.items():
        code = get_stream_abbreviation(level, stream)
        category_name = _stream_category_name(level, stream, code)
        channel_name = _subject_channel_name(code, subject)
        try:
            channel, registry_repaired = await resolve_managed_text_channel(
                guild,
                config,
                channel_name=channel_name,
                category_name=category_name,
            )
            if channel is None:
                raise RuntimeError(
                    f"Salon de matière géré introuvable : {channel_name} "
                    f"dans la catégorie {category_name} (aucune correspondance unique dans Discord)."
                )
            if not persist_registry_repair(guild.id, config, registry_repaired):
                raise RuntimeError(
                    f"Le salon {channel_name} existe, mais la réparation de son registre géré "
                    "n'a pas pu être enregistrée; relancez la synchronisation après vérification du stockage."
                )
            await _ensure_bot_thread_channel_access(guild, channel)
            existing_threads = await all_public_threads(channel)
        except (discord.Forbidden, discord.HTTPException, OSError, RuntimeError) as exc:
            message = f"{code} / {get_subject_display_name(subject)}: {_error_text(exc)}"
            errors.append(message)
            result["failed"] = int(result["failed"]) + len(group_rows)
            for row in group_rows:
                update_teacher_assignment_thread(
                    guild.id,
                    int(row["id"]),
                    status="failed",
                    error=message,
                )
            continue

        by_id = {thread.id: thread for thread in existing_threads}
        by_name: dict[str, list[discord.Thread]] = {}
        for thread in existing_threads:
            by_name.setdefault(thread.name, []).append(thread)

        for row in group_rows:
            assignment_id = int(row["id"])
            section = int(row["section"])
            name = _thread_name(str(row["full_name"]), section)
            saved_thread_id = row["thread_id"]
            if row["thread_status"] == "created" and isinstance(saved_thread_id, int):
                saved_thread = by_id.get(saved_thread_id)
                if saved_thread is not None:
                    try:
                        await _activate_thread(saved_thread)
                    except (discord.Forbidden, discord.HTTPException) as exc:
                        message = f"{code} / {get_subject_display_name(subject)} / section {section}: {_error_text(exc)}"
                        errors.append(message)
                        update_teacher_assignment_thread(
                            guild.id, assignment_id, status="failed", error=message
                        )
                        result["failed"] = int(result["failed"]) + 1
                        continue
                    result["existing"] = int(result["existing"]) + 1
                    continue

            # This also recovers the narrow crash window after Discord created a
            # thread but before SQLite saved its ID.
            candidates = [
                thread
                for thread in by_name.get(name, [])
                if thread_owners.get(thread.id) in (None, (teacher_id, assignment_id))
            ]
            if len(candidates) > 1:
                message = (
                    f"{code} / {get_subject_display_name(subject)} / section {section}: "
                    "plusieurs threads publics non attribués portent le même nom; "
                    "aucun n'a été associé automatiquement."
                )
                errors.append(message)
                update_teacher_assignment_thread(
                    guild.id,
                    assignment_id,
                    status="failed",
                    error=message,
                )
                result["failed"] = int(result["failed"]) + 1
                continue

            if candidates:
                match = candidates[0]
                try:
                    await _activate_thread(match)
                except (discord.Forbidden, discord.HTTPException) as exc:
                    message = f"{code} / {get_subject_display_name(subject)} / section {section}: {_error_text(exc)}"
                    errors.append(message)
                    update_teacher_assignment_thread(
                        guild.id, assignment_id, status="failed", error=message
                    )
                    result["failed"] = int(result["failed"]) + 1
                    continue
                update_teacher_assignment_thread(
                    guild.id,
                    assignment_id,
                    status="created",
                    thread_id=match.id,
                    error=None,
                )
                thread_owners[match.id] = (teacher_id, assignment_id)
                result["existing"] = int(result["existing"]) + 1
                continue

            try:
                # Discord supports public threads without a starter message
                # when the type is explicit; omitting the type defaults to
                # a private thread in discord.py / API v10.
                thread = await channel.create_thread(
                    name=name,
                    type=discord.ChannelType.public_thread,
                    auto_archive_duration=10080,
                    reason=(
                        f"School Manager teacher resources: user={teacher_id}; "
                        f"{level}/{stream}/section={section}/subject={subject}"
                    ),
                )
            except (discord.Forbidden, discord.HTTPException) as exc:
                message = f"{code} / {get_subject_display_name(subject)} / section {section}: {_error_text(exc)}"
                errors.append(message)
                update_teacher_assignment_thread(
                    guild.id,
                    assignment_id,
                    status="failed",
                    error=message,
                )
                result["failed"] = int(result["failed"]) + 1
                continue

            # Save the ID immediately after creation. If persistence fails, a
            # later retry can still recover the thread by its exact name.
            update_teacher_assignment_thread(
                guild.id,
                assignment_id,
                status="created",
                thread_id=thread.id,
                error=None,
            )
            by_id[thread.id] = thread
            by_name.setdefault(name, []).append(thread)
            thread_owners[thread.id] = (teacher_id, assignment_id)
            result["created"] = int(result["created"]) + 1

    return result

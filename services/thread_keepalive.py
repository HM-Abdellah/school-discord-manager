"""Quietly keep managed public school threads active before Discord's archive timeout."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord

from config.curriculum import (
    get_levels,
    get_stream_abbreviation,
    get_stream_subjects,
    get_streams,
)
from services.discord_registry import _managed_mapping
from services.server_builder import _stream_category_name, _subject_channel_name
from services.storage import get_guild_config
from services.teacher_threads import all_public_threads

logger = logging.getLogger(__name__)

# Threads use Discord's seven-day auto-archive duration. Bump at day five to
# leave a buffer for a missed daily run or a short bot outage.
KEEPALIVE_AFTER = timedelta(days=5)
INVISIBLE_KEEPALIVE_CONTENT = "\u200b"
KEEPALIVE_SEND_DELAY_SECONDS = 0.1


def resolve_subject_channel(channel: discord.abc.GuildChannel) -> tuple[str, str, str] | None:
    """Return (level, stream, subject) only for a canonical school subject channel."""
    category = getattr(channel, "category", None)
    if category is None:
        return None

    for level in get_levels():
        for stream in get_streams(level):
            code = get_stream_abbreviation(level, stream)
            if category.name != _stream_category_name(level, stream, code):
                continue
            for subject in get_stream_subjects(level, stream):
                if channel.name == _subject_channel_name(code, subject):
                    return level, stream, subject
    return None


def _registered_ids(config: dict, section: str) -> set[int]:
    """Return valid positive IDs from the trusted managed-resource registry."""
    return {
        value
        for value in _managed_mapping(config, section).values()
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    }


def _as_utc(value: datetime) -> datetime:
    """Make a timestamp timezone-aware for safe comparisons."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def thread_last_activity_at(thread: discord.Thread) -> datetime | None:
    """Infer the most recent activity time from Discord's last message snowflake."""
    last_message_id = getattr(thread, "last_message_id", None)
    if isinstance(last_message_id, int) and last_message_id > 0:
        try:
            return _as_utc(discord.utils.snowflake_time(last_message_id))
        except (OverflowError, TypeError, ValueError):
            logger.warning("Invalid last_message_id for thread %s; falling back to creation time.", getattr(thread, "id", "?"))

    created_at = getattr(thread, "created_at", None)
    if isinstance(created_at, datetime):
        return _as_utc(created_at)
    return None


async def keep_managed_subject_threads_alive(
    guild: discord.Guild,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Refresh only stale public threads belonging to registered subject channels.

    A zero-width character is required because Discord rejects a truly empty
    message. The message is sent silently to avoid push/desktop notifications.
    Sending to an unlocked archived thread reopens it; locked threads are left
    untouched because they are under a moderator's explicit control.
    """
    summary = {
        "channels_scanned": 0,
        "threads_scanned": 0,
        "messages_sent": 0,
        "locked_skipped": 0,
        "errors": 0,
    }

    config = get_guild_config(guild.id)
    if not isinstance(config, dict):
        return summary

    managed_channel_ids = _registered_ids(config, "channels")
    managed_category_ids = _registered_ids(config, "categories")
    if not managed_channel_ids or not managed_category_ids:
        return summary

    now_utc = _as_utc(now or discord.utils.utcnow())
    cutoff = now_utc - KEEPALIVE_AFTER

    # The channel must be present in the persisted managed registry, its parent
    # category must also be registered, and its canonical name must match the
    # curriculum. Names alone are never sufficient to opt a channel into writes.
    subject_channels = [
        channel
        for channel in guild.text_channels
        if channel.id in managed_channel_ids
        and getattr(getattr(channel, "category", None), "id", None) in managed_category_ids
        and resolve_subject_channel(channel) is not None
    ]

    for channel in subject_channels:
        summary["channels_scanned"] += 1
        try:
            threads = await all_public_threads(channel)
        except (discord.Forbidden, discord.HTTPException) as exc:
            summary["errors"] += 1
            logger.warning(
                "Cannot list public threads in managed subject channel %s (%s) in guild %s: %s",
                channel.name,
                channel.id,
                guild.id,
                exc,
            )
            continue

        for thread in threads:
            summary["threads_scanned"] += 1
            if bool(getattr(thread, "locked", False)):
                summary["locked_skipped"] += 1
                continue

            last_activity = thread_last_activity_at(thread)
            if last_activity is None or last_activity > now_utc or last_activity > cutoff:
                continue

            try:
                await thread.send(
                    INVISIBLE_KEEPALIVE_CONTENT,
                    silent=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException) as exc:
                summary["errors"] += 1
                logger.warning(
                    "Could not keep managed thread %s active in guild %s: %s",
                    getattr(thread, "id", "?"),
                    guild.id,
                    exc,
                )
                continue

            summary["messages_sent"] += 1
            logger.info(
                "Sent a silent keep-alive to managed public thread %s in guild %s.",
                thread.id,
                guild.id,
            )
            await asyncio.sleep(KEEPALIVE_SEND_DELAY_SECONDS)

    return summary

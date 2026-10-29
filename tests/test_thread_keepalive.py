from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from config.curriculum import get_stream_abbreviation, get_stream_subjects
from services.server_builder import _stream_category_name, _subject_channel_name
from services.thread_keepalive import (
    INVISIBLE_KEEPALIVE_CONTENT,
    keep_managed_subject_threads_alive,
    resolve_subject_channel,
    thread_last_activity_at,
)


def _thread(thread_id, activity_at, *, locked=False, archived=False):
    return SimpleNamespace(
        id=thread_id,
        name=f"Thread {thread_id}",
        locked=locked,
        archived=archived,
        last_message_id=discord.utils.time_snowflake(activity_at),
        created_at=activity_at - timedelta(days=2),
        send=AsyncMock(),
    )


def _managed_subject_channel(channel_id=100, category_id=200):
    level = "2ème Année Bac"
    stream = "2ème Année Bac Sciences Physiques"
    subject = get_stream_subjects(level, stream)[0]
    code = get_stream_abbreviation(level, stream)
    category = SimpleNamespace(
        id=category_id,
        name=_stream_category_name(level, stream, code),
    )
    channel = SimpleNamespace(
        id=channel_id,
        name=_subject_channel_name(code, subject),
        category=category,
        category_id=category_id,
    )
    return channel, category


def test_resolve_subject_channel_accepts_canonical_subject_channel_only():
    channel, _category = _managed_subject_channel()
    assert resolve_subject_channel(channel) == (
        "2ème Année Bac",
        "2ème Année Bac Sciences Physiques",
        get_stream_subjects("2ème Année Bac", "2ème Année Bac Sciences Physiques")[0],
    )

    channel.name = "📌-2BACPC・informations"
    assert resolve_subject_channel(channel) is None


def test_thread_last_activity_uses_last_message_snowflake_and_creation_fallback():
    now = discord.utils.utcnow()
    activity_at = now - timedelta(days=6)
    thread = _thread(10, activity_at)

    assert abs((thread_last_activity_at(thread) - activity_at).total_seconds()) < 0.01

    thread.last_message_id = None
    thread.created_at = activity_at
    assert thread_last_activity_at(thread) == activity_at


@pytest.mark.asyncio
async def test_keepalive_only_bumps_stale_unlocked_threads_in_registered_subject_channels(monkeypatch):
    now = discord.utils.utcnow()
    channel, category = _managed_subject_channel()
    stale = _thread(501, now - timedelta(days=6))
    archived_stale = _thread(502, now - timedelta(days=5, minutes=1), archived=True)
    recent = _thread(503, now - timedelta(days=2))
    locked_stale = _thread(504, now - timedelta(days=6), locked=True)

    other_channel, other_category = _managed_subject_channel(channel_id=101, category_id=201)
    other_category.name = category.name
    other_channel.name = channel.name

    monkeypatch.setattr(
        "services.thread_keepalive.get_guild_config",
        lambda _guild_id: {
            "managed": {
                "channels": {channel.name: channel.id},
                "categories": {category.name: category.id},
            }
        },
    )

    scanned_channel_ids = []

    async def fake_all_public_threads(parent):
        scanned_channel_ids.append(parent.id)
        assert parent.id == channel.id
        return [stale, archived_stale, recent, locked_stale]

    monkeypatch.setattr("services.thread_keepalive.all_public_threads", fake_all_public_threads)
    guild = SimpleNamespace(id=9001, text_channels=[channel, other_channel])

    result = await keep_managed_subject_threads_alive(guild, now=now)

    assert scanned_channel_ids == [channel.id]
    assert result == {
        "channels_scanned": 1,
        "threads_scanned": 4,
        "messages_sent": 2,
        "locked_skipped": 1,
        "errors": 0,
    }
    for thread in (stale, archived_stale):
        thread.send.assert_awaited_once()
        args, kwargs = thread.send.await_args
        assert args[0] == INVISIBLE_KEEPALIVE_CONTENT == "\u200b"
        assert kwargs["silent"] is True
        assert kwargs["allowed_mentions"].everyone is False
    recent.send.assert_not_awaited()
    locked_stale.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_keepalive_fails_closed_without_managed_registry(monkeypatch):
    channel, _category = _managed_subject_channel()
    monkeypatch.setattr("services.thread_keepalive.get_guild_config", lambda _guild_id: None)

    async def unexpected_thread_scan(_channel):
        raise AssertionError("unregistered guild must not scan or write to threads")

    monkeypatch.setattr("services.thread_keepalive.all_public_threads", unexpected_thread_scan)
    result = await keep_managed_subject_threads_alive(
        SimpleNamespace(id=9001, text_channels=[channel]),
    )

    assert result["channels_scanned"] == 0
    assert result["threads_scanned"] == 0
    assert result["messages_sent"] == 0

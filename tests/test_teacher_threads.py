from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from services import teacher_threads


def _row(assignment_id=1, section=3, subject="Mathématiques", thread_id=None, status="pending", full_name="Prof Test", discord_id=101):
    return {
        "id": assignment_id,
        "guild_id": 1,
        "discord_id": discord_id,
        "level_name": "2ème Année Bac",
        "stream_name": "2ème Année Bac Sciences Physiques",
        "stream_code": "2BACPC",
        "section": section,
        "subject_name": subject,
        "thread_id": thread_id,
        "thread_status": status,
        "thread_error": None,
        "full_name": full_name,
    }


def _channel(active=(), archived=(), archive_error=None):
    async def archived_threads(limit=None):
        assert limit is None
        if archive_error is not None:
            raise archive_error
        for thread in archived:
            yield thread

    return SimpleNamespace(
        id=500,
        name="subject-channel",
        threads=list(active),
        archived_threads=archived_threads,
        create_thread=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_ensure_teacher_threads_creates_public_thread_and_persists_id(monkeypatch):
    channel = _channel()
    created = SimpleNamespace(id=9001, name="Prof Test • 3", type=discord.ChannelType.public_thread)
    channel.create_thread.return_value = created
    updates = []

    async def resolve(*_args, **_kwargs):
        return channel, False

    monkeypatch.setattr(teacher_threads, "get_teacher_assignments", lambda *_args: [_row()])
    monkeypatch.setattr(teacher_threads, "get_guild_config", lambda *_args: {})
    monkeypatch.setattr(teacher_threads, "resolve_managed_text_channel", resolve)
    monkeypatch.setattr(
        teacher_threads,
        "update_teacher_assignment_thread",
        lambda guild_id, assignment_id, **kwargs: updates.append((guild_id, assignment_id, kwargs)),
    )

    result = await teacher_threads.ensure_teacher_threads(SimpleNamespace(id=1), 101)

    assert result["created"] == 1
    assert result["existing"] == 0
    assert result["failed"] == 0
    channel.create_thread.assert_awaited_once()
    kwargs = channel.create_thread.await_args.kwargs
    assert kwargs["name"] == "Prof Test • 3"
    assert kwargs["type"] == discord.ChannelType.public_thread
    assert kwargs["auto_archive_duration"] == 10080
    assert "message" not in kwargs
    assert updates == [(1, 1, {"status": "created", "thread_id": 9001, "error": None})]


@pytest.mark.asyncio
async def test_ensure_teacher_threads_persists_managed_channel_registry_repair(monkeypatch):
    channel = _channel()
    channel.create_thread.return_value = SimpleNamespace(
        id=9101, name="Prof Test • 3", type=discord.ChannelType.public_thread
    )
    config = {"managed": {"channels": {"stale-entry": 999}}}
    repairs = []

    async def resolve(*_args, **_kwargs):
        return channel, True

    def persist(guild_id, passed_config, changed):
        repairs.append((guild_id, passed_config, changed))
        return True

    monkeypatch.setattr(teacher_threads, "get_teacher_assignments", lambda *_args: [_row()])
    monkeypatch.setattr(teacher_threads, "get_guild_config", lambda *_args: config)
    monkeypatch.setattr(teacher_threads, "resolve_managed_text_channel", resolve)
    monkeypatch.setattr(teacher_threads, "persist_registry_repair", persist)
    monkeypatch.setattr(teacher_threads, "update_teacher_assignment_thread", lambda *_args, **_kwargs: None)

    result = await teacher_threads.ensure_teacher_threads(SimpleNamespace(id=1), 101)

    assert result["created"] == 1
    assert result["failed"] == 0
    assert repairs == [(1, config, True)]


@pytest.mark.asyncio
async def test_same_teacher_and_subject_get_one_thread_per_assigned_section(monkeypatch):
    channel = _channel()
    threads = [
        SimpleNamespace(id=9001, name="Prof Test • 2", type=discord.ChannelType.public_thread),
        SimpleNamespace(id=9002, name="Prof Test • 3", type=discord.ChannelType.public_thread),
    ]
    channel.create_thread.side_effect = threads
    rows = [
        _row(assignment_id=1, section=2),
        _row(assignment_id=2, section=3),
    ]
    updates = []

    async def resolve(*_args, **_kwargs):
        return channel, False

    monkeypatch.setattr(teacher_threads, "get_teacher_assignments", lambda *_args: rows)
    monkeypatch.setattr(teacher_threads, "get_guild_config", lambda *_args: {})
    monkeypatch.setattr(teacher_threads, "resolve_managed_text_channel", resolve)
    monkeypatch.setattr(
        teacher_threads,
        "update_teacher_assignment_thread",
        lambda guild_id, assignment_id, **kwargs: updates.append((assignment_id, kwargs)),
    )

    result = await teacher_threads.ensure_teacher_threads(SimpleNamespace(id=1), 101)

    assert result == {"created": 2, "existing": 0, "failed": 0, "errors": []}
    assert channel.create_thread.await_count == 2
    assert [call.kwargs["name"] for call in channel.create_thread.await_args_list] == [
        "Prof Test • 2",
        "Prof Test • 3",
    ]
    assert all(
        call.kwargs["type"] == discord.ChannelType.public_thread
        and "message" not in call.kwargs
        for call in channel.create_thread.await_args_list
    )
    assert updates == [
        (1, {"status": "created", "thread_id": 9001, "error": None}),
        (2, {"status": "created", "thread_id": 9002, "error": None}),
    ]


@pytest.mark.asyncio
async def test_ensure_teacher_threads_reuses_exact_existing_thread(monkeypatch):
    existing = SimpleNamespace(id=9001, name="Prof Test • 3", type=discord.ChannelType.public_thread)
    channel = _channel(active=[existing])
    updates = []

    async def resolve(*_args, **_kwargs):
        return channel, False

    monkeypatch.setattr(teacher_threads, "get_teacher_assignments", lambda *_args: [_row()])
    monkeypatch.setattr(teacher_threads, "get_guild_config", lambda *_args: {})
    monkeypatch.setattr(teacher_threads, "resolve_managed_text_channel", resolve)
    monkeypatch.setattr(
        teacher_threads,
        "update_teacher_assignment_thread",
        lambda guild_id, assignment_id, **kwargs: updates.append(kwargs),
    )

    result = await teacher_threads.ensure_teacher_threads(SimpleNamespace(id=1), 101)

    assert result["created"] == 0
    assert result["existing"] == 1
    channel.create_thread.assert_not_awaited()
    assert updates[0] == {"status": "created", "thread_id": 9001, "error": None}


@pytest.mark.asyncio
async def test_ensure_teacher_threads_reactivates_matching_archived_thread(monkeypatch):
    archived = SimpleNamespace(
        id=9011,
        name="Prof Test • 3",
        type=discord.ChannelType.public_thread,
        archived=True,
        locked=True,
        edit=AsyncMock(),
    )
    channel = _channel(archived=[archived])
    updates = []

    async def resolve(*_args, **_kwargs):
        return channel, False

    monkeypatch.setattr(teacher_threads, "get_teacher_assignments", lambda *_args: [_row()])
    monkeypatch.setattr(teacher_threads, "get_guild_config", lambda *_args: {})
    monkeypatch.setattr(teacher_threads, "resolve_managed_text_channel", resolve)
    monkeypatch.setattr(
        teacher_threads,
        "update_teacher_assignment_thread",
        lambda guild_id, assignment_id, **kwargs: updates.append(kwargs),
    )

    result = await teacher_threads.ensure_teacher_threads(SimpleNamespace(id=1), 101)

    assert result["created"] == 0
    assert result["existing"] == 1
    assert result["failed"] == 0
    archived.edit.assert_awaited_once_with(
        archived=False,
        locked=False,
        reason="School Manager: restore current teacher assignment thread",
    )
    channel.create_thread.assert_not_awaited()
    assert updates[0] == {"status": "created", "thread_id": 9011, "error": None}


@pytest.mark.asyncio
async def test_ensure_teacher_threads_fails_closed_when_archives_unavailable(monkeypatch):
    response = SimpleNamespace(status=500, reason="Internal Server Error")
    channel = _channel(archive_error=discord.HTTPException(response, "archives unavailable"))
    updates = []

    async def resolve(*_args, **_kwargs):
        return channel, False

    monkeypatch.setattr(teacher_threads, "get_teacher_assignments", lambda *_args: [_row()])
    monkeypatch.setattr(teacher_threads, "get_guild_config", lambda *_args: {})
    monkeypatch.setattr(teacher_threads, "resolve_managed_text_channel", resolve)
    monkeypatch.setattr(
        teacher_threads,
        "update_teacher_assignment_thread",
        lambda guild_id, assignment_id, **kwargs: updates.append(kwargs),
    )

    result = await teacher_threads.ensure_teacher_threads(SimpleNamespace(id=1), 101)

    assert result["created"] == 0
    assert result["failed"] == 1
    channel.create_thread.assert_not_awaited()
    assert updates[0]["status"] == "failed"
    assert "archives unavailable" in updates[0]["error"]


@pytest.mark.asyncio
async def test_ensure_teacher_threads_adopts_thread_after_creation_before_db_update(monkeypatch):
    existing = SimpleNamespace(id=9010, name="Prof Test • 3", type=discord.ChannelType.public_thread)
    channel = _channel(active=[existing])
    updates = []

    async def resolve(*_args, **_kwargs):
        return channel, False

    monkeypatch.setattr(teacher_threads, "get_teacher_assignments", lambda *_args: [_row()])
    monkeypatch.setattr(teacher_threads, "get_guild_config", lambda *_args: {})
    monkeypatch.setattr(teacher_threads, "resolve_managed_text_channel", resolve)
    monkeypatch.setattr(
        teacher_threads,
        "update_teacher_assignment_thread",
        lambda guild_id, assignment_id, **kwargs: updates.append(kwargs),
    )

    result = await teacher_threads.ensure_teacher_threads(SimpleNamespace(id=1), 101)

    assert result["created"] == 0
    assert result["existing"] == 1
    channel.create_thread.assert_not_awaited()
    assert updates[0]["thread_id"] == 9010


@pytest.mark.asyncio
async def test_ensure_teacher_threads_records_discord_creation_failure_for_retry(monkeypatch):
    response = SimpleNamespace(status=403, reason="Forbidden")
    channel = _channel()
    channel.create_thread.side_effect = discord.Forbidden(response, "Missing Create Public Threads permission")
    updates = []

    async def resolve(*_args, **_kwargs):
        return channel, False

    monkeypatch.setattr(teacher_threads, "get_teacher_assignments", lambda *_args: [_row()])
    monkeypatch.setattr(teacher_threads, "get_guild_config", lambda *_args: {})
    monkeypatch.setattr(teacher_threads, "resolve_managed_text_channel", resolve)
    monkeypatch.setattr(
        teacher_threads,
        "update_teacher_assignment_thread",
        lambda guild_id, assignment_id, **kwargs: updates.append(kwargs),
    )

    result = await teacher_threads.ensure_teacher_threads(SimpleNamespace(id=1), 101)

    assert result["created"] == 0
    assert result["failed"] == 1
    assert updates[0]["status"] == "failed"
    assert "Forbidden" in updates[0]["error"]


@pytest.mark.asyncio
async def test_ensure_teacher_threads_does_not_adopt_same_named_thread_owned_by_another_teacher(monkeypatch):
    existing = SimpleNamespace(id=9001, name="Prof Test • 3", type=discord.ChannelType.public_thread)
    channel = _channel(active=[existing])
    created = SimpleNamespace(id=9002, name="Prof Test • 3", type=discord.ChannelType.public_thread)
    channel.create_thread.return_value = created
    current = _row()
    other_teacher = _row(assignment_id=77, thread_id=9001, status="created", discord_id=202)
    updates = []

    async def resolve(*_args, **_kwargs):
        return channel, False

    monkeypatch.setattr(teacher_threads, "get_teacher_assignments", lambda *_args: [current, other_teacher])
    monkeypatch.setattr(teacher_threads, "get_guild_config", lambda *_args: {})
    monkeypatch.setattr(teacher_threads, "resolve_managed_text_channel", resolve)
    monkeypatch.setattr(
        teacher_threads,
        "update_teacher_assignment_thread",
        lambda guild_id, assignment_id, **kwargs: updates.append((assignment_id, kwargs)),
    )

    result = await teacher_threads.ensure_teacher_threads(SimpleNamespace(id=1), 101)

    assert result["created"] == 1
    assert result["existing"] == 0
    channel.create_thread.assert_awaited_once()
    assert updates[0] == (1, {"status": "created", "thread_id": 9002, "error": None})

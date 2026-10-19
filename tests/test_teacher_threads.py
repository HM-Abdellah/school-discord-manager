from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from services import teacher_threads
from services.teacher_threads import _ensure_bot_thread_channel_access


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


@pytest.mark.asyncio
async def test_bot_thread_channel_access_repairs_missing_access_with_member_overwrite():
    bot_member = SimpleNamespace(
        id=777,
        guild_permissions=discord.Permissions(manage_roles=True, view_channel=True, read_message_history=True, send_messages=True, create_public_threads=True),
    )
    effective = discord.Permissions.none()
    channel = SimpleNamespace(
        name="📚-2BACPC・math",
        permissions_for=lambda member: effective,
        overwrites_for=lambda member: discord.PermissionOverwrite(embed_links=False),
        set_permissions=AsyncMock(),
    )

    repaired = await _ensure_bot_thread_channel_access(
        SimpleNamespace(me=bot_member), channel
    )

    assert repaired is True
    channel.set_permissions.assert_awaited_once()
    target = channel.set_permissions.await_args.args[0]
    overwrite = channel.set_permissions.await_args.kwargs["overwrite"]
    assert target is bot_member
    assert overwrite.view_channel is True
    assert overwrite.read_message_history is True
    assert overwrite.send_messages is True
    assert overwrite.create_public_threads is True
    assert overwrite.manage_threads is None
    assert overwrite.embed_links is False


@pytest.mark.asyncio
async def test_missing_manage_threads_does_not_block_public_thread_creation(monkeypatch):
    channel = _channel()
    created = SimpleNamespace(id=9011, name="Prof Test • 3", type=discord.ChannelType.public_thread)
    channel.create_thread.return_value = created
    base = discord.Permissions(
        view_channel=True, read_message_history=True, send_messages=True,
        create_public_threads=True, manage_roles=True,
    )
    bot_member = SimpleNamespace(id=777, guild_permissions=base)
    effective = discord.Permissions(
        view_channel=True, read_message_history=True, send_messages=True,
        create_public_threads=True,
    )
    channel.permissions_for = MagicMock(return_value=effective)
    channel.set_permissions = AsyncMock()
    async def resolve(*_args, **_kwargs):
        return channel, False
    monkeypatch.setattr(teacher_threads, "get_teacher_assignments", lambda *_args: [_row()])
    monkeypatch.setattr(teacher_threads, "get_guild_config", lambda *_args: {})
    monkeypatch.setattr(teacher_threads, "resolve_managed_text_channel", resolve)
    monkeypatch.setattr(teacher_threads, "update_teacher_assignment_thread", lambda *_args, **_kwargs: None)

    result = await teacher_threads.ensure_teacher_threads(SimpleNamespace(id=1, me=bot_member), 101)

    assert result["created"] == 1
    assert result["failed"] == 0
    channel.set_permissions.assert_not_awaited()
    channel.create_thread.assert_awaited_once()


@pytest.mark.asyncio
async def test_bot_thread_channel_access_does_not_change_correct_permissions():
    bot_member = SimpleNamespace(
        id=777,
        guild_permissions=discord.Permissions(manage_roles=True),
    )
    effective = discord.Permissions(
        view_channel=True,
        read_message_history=True,
        send_messages=True,
        create_public_threads=True,
        manage_threads=True,
    )
    channel = SimpleNamespace(
        name="📚-2BACPC・math",
        permissions_for=lambda member: effective,
        overwrites_for=lambda member: discord.PermissionOverwrite(),
        set_permissions=AsyncMock(),
    )

    repaired = await _ensure_bot_thread_channel_access(
        SimpleNamespace(me=bot_member), channel
    )

    assert repaired is False
    channel.set_permissions.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_thread_channel_access_fails_closed_without_manage_roles():
    bot_member = SimpleNamespace(
        id=777,
        guild_permissions=discord.Permissions.none(),
    )
    channel = SimpleNamespace(
        name="📚-2BACPC・math",
        permissions_for=lambda member: discord.Permissions.none(),
        overwrites_for=lambda member: discord.PermissionOverwrite(),
        set_permissions=AsyncMock(),
    )

    with pytest.raises(RuntimeError, match="Manage Roles"):
        await _ensure_bot_thread_channel_access(
            SimpleNamespace(me=bot_member), channel
        )
    channel.set_permissions.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_thread_channel_access_does_not_try_to_grant_create_threads_absent_from_guild_and_category():
    bot_member = SimpleNamespace(
        id=777,
        guild_permissions=discord.Permissions(manage_roles=True),
    )
    channel = SimpleNamespace(
        name="📚-2BACPC・math",
        category=None,
        permissions_for=lambda member: discord.Permissions.none(),
        overwrites_for=lambda member: discord.PermissionOverwrite(),
        set_permissions=AsyncMock(),
    )

    with pytest.raises(RuntimeError, match="Auto-réparation impossible : permissions absentes") as error:
        await _ensure_bot_thread_channel_access(SimpleNamespace(me=bot_member), channel)

    assert "create_public_threads" in str(error.value)
    assert "Aucune inscription ni affectation n'a été supprimée" in str(error.value)
    channel.set_permissions.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_teacher_threads_repairs_bot_channel_access_before_listing_or_creating(monkeypatch):
    events = []
    channel = _channel()
    channel.permissions_for = MagicMock(return_value=discord.Permissions.none())
    channel.overwrites_for = MagicMock(return_value=discord.PermissionOverwrite(embed_links=False))
    async def set_permissions(*args, **kwargs):
        events.append("repair_permissions")
    channel.set_permissions = AsyncMock(side_effect=set_permissions)
    async def archived_threads(limit=None):
        events.append("list_archives")
        if False:
            yield None
    channel.archived_threads = archived_threads
    async def create_thread(**kwargs):
        events.append("create_thread")
        return SimpleNamespace(id=9099, name=kwargs["name"], type=discord.ChannelType.public_thread)
    channel.create_thread = AsyncMock(side_effect=create_thread)
    bot_member = SimpleNamespace(
        id=777, guild_permissions=discord.Permissions(manage_roles=True, view_channel=True, read_message_history=True, send_messages=True, create_public_threads=True)
    )
    guild = SimpleNamespace(id=1, me=bot_member)
    monkeypatch.setattr(teacher_threads, "get_teacher_assignments", lambda *_args: [_row()])
    monkeypatch.setattr(teacher_threads, "get_guild_config", lambda *_args: {})
    async def resolve(*_args, **_kwargs):
        return channel, False
    monkeypatch.setattr(teacher_threads, "resolve_managed_text_channel", resolve)
    monkeypatch.setattr(teacher_threads, "update_teacher_assignment_thread", lambda *_args, **_kwargs: None)

    result = await teacher_threads.ensure_teacher_threads(guild, 101)

    assert result["created"] == 1
    assert events == ["repair_permissions", "list_archives", "create_thread"]


@pytest.mark.asyncio
async def test_bot_thread_channel_access_reports_missing_cached_bot_member():
    bot_member = None
    channel = SimpleNamespace(
        name="📚-2BACPC・math",
        permissions_for=MagicMock(),
        overwrites_for=MagicMock(),
        set_permissions=AsyncMock(),
    )

    with pytest.raises(RuntimeError, match="guild.me.*pas disponible"):
        await _ensure_bot_thread_channel_access(
            SimpleNamespace(me=bot_member), channel
        )

    channel.set_permissions.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_thread_channel_access_reports_discord_permission_rejection():
    bot_member = SimpleNamespace(
        id=777, guild_permissions=discord.Permissions(manage_roles=True, view_channel=True, read_message_history=True, send_messages=True, create_public_threads=True)
    )
    response = SimpleNamespace(status=403, reason="Forbidden")
    channel = SimpleNamespace(
        name="📚-2BACPC・math",
        permissions_for=MagicMock(return_value=discord.Permissions.none()),
        overwrites_for=MagicMock(return_value=discord.PermissionOverwrite()),
        set_permissions=AsyncMock(
            side_effect=discord.Forbidden(response, {"message": "Missing Access", "code": 50001})
        ),
    )

    with pytest.raises(RuntimeError, match="Forbidden.*Réparation refusée") as error:
        await _ensure_bot_thread_channel_access(
            SimpleNamespace(me=bot_member), channel
        )
    assert "Discord code 50001" in str(error.value)
    assert "Missing Access" in str(error.value)
    assert "un administrateur du serveur doit d'abord accorder" in str(error.value)


@pytest.mark.asyncio
async def test_teacher_thread_sync_stops_before_archive_scan_if_permission_repair_is_rejected(monkeypatch):
    response = SimpleNamespace(status=403, reason="Forbidden")
    channel = _channel()
    channel.permissions_for = MagicMock(return_value=discord.Permissions.none())
    channel.overwrites_for = MagicMock(return_value=discord.PermissionOverwrite())
    channel.set_permissions = AsyncMock(
        side_effect=discord.Forbidden(response, "Missing Manage Roles")
    )
    channel.archived_threads = AsyncMock(side_effect=AssertionError("archive scan must not run"))
    updates = []
    bot_member = SimpleNamespace(
        id=777, guild_permissions=discord.Permissions(manage_roles=True, view_channel=True, read_message_history=True, send_messages=True, create_public_threads=True)
    )

    monkeypatch.setattr(teacher_threads, "get_teacher_assignments", lambda *_args: [_row()])
    monkeypatch.setattr(teacher_threads, "get_guild_config", lambda *_args: {})
    async def resolve(*_args, **_kwargs):
        return channel, False
    monkeypatch.setattr(teacher_threads, "resolve_managed_text_channel", resolve)
    monkeypatch.setattr(
        teacher_threads,
        "update_teacher_assignment_thread",
        lambda guild_id, assignment_id, **kwargs: updates.append(kwargs),
    )

    result = await teacher_threads.ensure_teacher_threads(
        SimpleNamespace(id=1, me=bot_member), 101
    )

    assert result["failed"] == 1
    assert result["created"] == 0
    assert "Réparation refusée" in result["errors"][0]
    assert updates[0]["status"] == "failed"
    assert updates[0]["error"] == result["errors"][0]
    channel.archived_threads.assert_not_awaited()
    channel.create_thread.assert_not_awaited()

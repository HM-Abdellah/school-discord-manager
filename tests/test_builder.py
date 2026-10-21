from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from services.server_builder import (
    BuildPermissionError,
    ServerBuilder,
    _run_build_discord_operation,
    _stream_category_name,
    _subject_channel_overwrites,
)


def test_stream_channel_count_has_no_artificial_title_channel():
    stream = {"subjects": ["MATH", "PC", "SVT"]}
    assert ServerBuilder._stream_channel_count({}, stream) == 6


def test_stream_category_is_a_real_discord_category_name():
    name = _stream_category_name("Tronc Commun", "Tronc Commun Scientifique", "TCS")
    assert name == "📘・TC・🔬 TCS"


def test_planned_channel_names_include_only_real_resources():
    stream = {"abbreviation": "1BACSE", "subjects": ["MATH", "PC"]}
    names = ServerBuilder._planned_channel_names_for_stream(stream)
    assert "📌-1BACSE・informations" in names
    assert "🗓️-1BACSE・emploi-du-temps" in names
    assert "📝-1BACSE・examens" in names
    assert any(name.startswith("📚-1BACSE・") for name in names)
    assert not any(name.startswith("🔹・") for name in names)


def test_validate_capacity_rejects_stream_above_category_limit():
    guild = MagicMock()
    guild.channels = []
    guild.categories = []
    builder = ServerBuilder(guild)
    selected = {
        "levels": [
            {
                "name": "1ère Année Bac",
                "streams": [
                    {
                        "name": "Huge",
                        "abbreviation": "HUGE",
                        "subjects": [str(i) for i in range(48)],
                    }
                ],
            }
        ]
    }
    builder._channel_snapshot = []
    with pytest.raises(ValueError, match="dépasse la limite de 50"):
        builder._validate_capacity(selected)


@pytest.mark.asyncio
async def test_existing_main_roles_are_reused_without_create_calls():
    role_names = ("Administration", "Prof", "Prof (F)", "Élève")
    roles = [SimpleNamespace(name=name, id=index + 1) for index, name in enumerate(role_names)]
    guild = SimpleNamespace(roles=roles)
    guild.create_role = AsyncMock()

    builder = ServerBuilder(guild)
    result = await builder._ensure_main_roles()

    assert set(result) == set(role_names)
    assert all(result[name] is roles[index] for index, name in enumerate(role_names))
    guild.create_role.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_category_is_reused_without_create_call():
    category = MagicMock(spec=discord.CategoryChannel)
    category.name = "Test Category"
    category.id = 123
    guild = SimpleNamespace(categories=[category])
    guild.create_category = AsyncMock()

    builder = ServerBuilder(guild)
    builder._channel_snapshot = [category]
    result = await builder._get_or_create_category("Test Category")

    assert result is category
    guild.create_category.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_text_channel_is_reused_without_edit_or_create():
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = "test-channel"
    channel.id = 456
    channel.category_id = 123
    category = MagicMock(spec=discord.CategoryChannel)
    category.text_channels = [channel]
    category.id = 123
    category.create_text_channel = AsyncMock()
    channel.edit = AsyncMock()

    builder = ServerBuilder(SimpleNamespace())
    builder._channel_snapshot = [channel]

    result = await builder._get_or_create_text(
        category,
        "test-channel",
        topic="topic",
        overwrites={},
    )

    assert result is channel
    channel.edit.assert_not_awaited()
    category.create_text_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_text_channel_is_reused_from_category_even_when_snapshot_type_misses_it():
    channel = MagicMock()
    channel.name = "test-channel"
    channel.id = 456
    channel.category_id = 123
    category = MagicMock(spec=discord.CategoryChannel)
    category.text_channels = [channel]
    category.id = 123
    category.create_text_channel = AsyncMock()

    builder = ServerBuilder(SimpleNamespace())
    builder._channel_snapshot = []

    result = await builder._get_or_create_text(
        category,
        "test-channel",
        topic="topic",
        overwrites={},
    )

    assert result is channel
    category.create_text_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_voice_channel_is_reused_without_edit_or_create():
    channel = MagicMock(spec=discord.VoiceChannel)
    channel.name = "test-voice"
    channel.id = 789
    channel.category_id = 123
    category = MagicMock(spec=discord.CategoryChannel)
    category.voice_channels = [channel]
    category.id = 123
    category.create_voice_channel = AsyncMock()
    channel.edit = AsyncMock()

    builder = ServerBuilder(SimpleNamespace())
    builder._channel_snapshot = [channel]

    result = await builder._get_or_create_voice(category, "test-voice", {})

    assert result is channel
    channel.edit.assert_not_awaited()
    category.create_voice_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_voice_channel_is_reused_from_category_even_when_snapshot_is_empty():
    channel = MagicMock()
    channel.name = "test-voice"
    channel.id = 789
    channel.category_id = 123
    category = MagicMock(spec=discord.CategoryChannel)
    category.voice_channels = [channel]
    category.id = 123
    category.create_voice_channel = AsyncMock()

    builder = ServerBuilder(SimpleNamespace())
    builder._channel_snapshot = []

    result = await builder._get_or_create_voice(category, "test-voice", {})

    assert result is channel
    category.create_voice_channel.assert_not_awaited()


def test_validate_capacity_rejects_existing_category_that_would_exceed_limit():
    guild = SimpleNamespace(channels=[], categories=[])
    builder = ServerBuilder(guild)

    existing = [SimpleNamespace(name=f"unmanaged-{i}") for i in range(46)]
    category = MagicMock(spec=discord.CategoryChannel)
    category.name = "📘・TC・🔬 TCS"
    category.id = 123
    category.channels = existing
    category.text_channels = existing
    category.voice_channels = []
    builder._channel_snapshot = [category, *existing]

    selected = {
        "levels": [
            {
                "name": "Tronc Commun",
                "streams": [
                    {
                        "name": "Tronc Commun Scientifique",
                        "abbreviation": "TCS",
                        "subjects": ["Mathématiques", "Physique-Chimie"],
                    }
                ],
            }
        ]
    }

    with pytest.raises(ValueError, match="dépasserait.*51"):
        builder._validate_capacity(selected)


def test_subject_channel_overwrites_grant_bot_scoped_thread_access_without_exposing_channel():
    everyone = object()
    admin = object()
    professor = object()
    female_professor = object()
    teacher_stream = object()
    student_stream = object()
    student = object()
    bot_member = object()

    overwrites = _subject_channel_overwrites(
        everyone, admin, professor, female_professor, teacher_stream,
        student_stream, student, bot_member=bot_member,
    )

    assert overwrites[everyone].view_channel is False
    assert overwrites[bot_member].view_channel is True
    assert overwrites[bot_member].read_message_history is True
    assert overwrites[bot_member].send_messages is True
    assert overwrites[bot_member].create_public_threads is True
    # Routine public-thread creation must not require the moderator permission.
    assert overwrites[bot_member].manage_threads is None
    assert overwrites[student].view_channel is True
    assert overwrites[student].send_messages is False
    assert overwrites[student_stream].send_messages is True
    assert overwrites[student_stream].create_public_threads is False
    assert overwrites[student_stream].create_private_threads is False
    assert overwrites[student_stream].send_messages_in_threads is False


def test_fresh_channel_snapshot_does_not_reuse_deleted_cached_category():
    category = MagicMock(spec=discord.CategoryChannel)
    category.name = "stale-category"
    category.id = 900
    guild = SimpleNamespace(categories=[category])
    builder = ServerBuilder(guild)
    builder._channel_snapshot = []
    builder._channel_snapshot_refreshed = True

    assert builder._find_category("stale-category") is None


def test_fresh_channel_snapshot_does_not_reuse_deleted_cached_text_channel():
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = "stale-channel"
    channel.id = 901
    channel.category_id = 902
    category = MagicMock(spec=discord.CategoryChannel)
    category.id = 902
    category.text_channels = [channel]
    builder = ServerBuilder(SimpleNamespace())
    builder._channel_snapshot = []
    builder._channel_snapshot_refreshed = True

    assert builder._find_text(category, "stale-channel") is None


@pytest.mark.asyncio
async def test_created_private_category_grants_bot_view_without_exposing_everyone():
    bot_member = object()
    everyone = object()
    category = MagicMock(spec=discord.CategoryChannel)
    category.name = "private-category"
    category.id = 910
    create_category = AsyncMock(return_value=category)
    guild = SimpleNamespace(me=bot_member, categories=[], create_category=create_category)
    builder = ServerBuilder(guild)
    builder._channel_snapshot = []
    source_overwrites = {everyone: discord.PermissionOverwrite(view_channel=False)}

    await builder._get_or_create_category("private-category", source_overwrites)

    passed = create_category.await_args.kwargs["overwrites"]
    assert passed[everyone].view_channel is False
    assert passed[bot_member].view_channel is True
    assert bot_member not in source_overwrites


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_kind", ["text", "voice"])
async def test_created_private_managed_channel_grants_bot_view(channel_kind):
    bot_member = object()
    everyone = object()
    category = MagicMock(spec=discord.CategoryChannel)
    category.id = 911
    category.name = "private-category"
    category.text_channels = []
    category.voice_channels = []
    channel_type = discord.TextChannel if channel_kind == "text" else discord.VoiceChannel
    new_channel = MagicMock(spec=channel_type)
    new_channel.id = 912 if channel_kind == "text" else 913
    new_channel.name = f"private-{channel_kind}"
    guild = SimpleNamespace(me=bot_member)
    builder = ServerBuilder(guild)
    builder._channel_snapshot = []
    source_overwrites = {everyone: discord.PermissionOverwrite(view_channel=False)}

    if channel_kind == "text":
        category.create_text_channel = AsyncMock(return_value=new_channel)
        await builder._get_or_create_text(
            category, new_channel.name, topic="test", overwrites=source_overwrites
        )
        passed = category.create_text_channel.await_args.kwargs["overwrites"]
    else:
        category.create_voice_channel = AsyncMock(return_value=new_channel)
        await builder._get_or_create_voice(category, new_channel.name, source_overwrites)
        passed = category.create_voice_channel.await_args.kwargs["overwrites"]

    assert passed[everyone].view_channel is False
    assert passed[bot_member].view_channel is True
    assert bot_member not in source_overwrites




@pytest.mark.asyncio
async def test_build_permission_error_reports_operation_resource_and_discord_code():
    response = SimpleNamespace(status=403, reason="Forbidden")
    action = AsyncMock(
        side_effect=discord.Forbidden(
            response,
            {"message": "Missing Permissions", "code": 50013},
        )
    )

    with pytest.raises(BuildPermissionError) as caught:
        await _run_build_discord_operation(
            operation="create_category",
            resource_name="private-category",
            action=action,
        )

    details = str(caught.value)
    assert "operation=create_category" in details
    assert "resource_name='private-category'" in details
    assert "http_status=403" in details
    assert "discord_code=50013" in details
    assert "Missing Permissions" in details
    action.assert_awaited_once()

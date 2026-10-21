from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from services import server_builder as server_builder_module
from services.server_builder import (
    ServerBuilder,
    _stream_category_name,
    _prepare_channel_overwrites,
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
async def test_create_role_retries_50013_with_only_bot_grantable_permissions(monkeypatch, capsys):
    bot_permissions = discord.Permissions(
        manage_roles=True,
        manage_channels=True,
        view_channel=True,
    )
    guild = SimpleNamespace(
        id=1,
        me=SimpleNamespace(guild_permissions=bot_permissions),
    )
    response = SimpleNamespace(status=403, reason="Forbidden")
    forbidden = discord.Forbidden(
        response,
        {"message": "Missing Permissions", "code": 50013},
    )
    created_role = SimpleNamespace(id=700, name="Administration")
    guild.create_role = AsyncMock(side_effect=[forbidden, created_role])
    monkeypatch.setattr(server_builder_module, "ROLE_CREATE_DELAY", 0)

    builder = ServerBuilder(guild)
    requested = discord.Permissions(
        manage_roles=True,
        manage_channels=True,
        manage_messages=True,
        manage_threads=True,
        view_channel=True,
    )
    result = await builder._create_role(
        "Administration",
        permissions=requested,
        colour=discord.Colour.red(),
        hoist=True,
        mentionable=True,
        reason="test",
    )

    assert result is created_role
    assert guild.create_role.await_count == 2
    retry_permissions = guild.create_role.await_args_list[1].kwargs["permissions"]
    assert retry_permissions.manage_roles is True
    assert retry_permissions.manage_channels is True
    assert retry_permissions.view_channel is True
    assert retry_permissions.manage_messages is False
    assert retry_permissions.manage_threads is False
    assert builder.stats.roles_created == 1
    output = capsys.readouterr().out
    assert "Retrying with only bot-grantable permissions" in output
    assert "manage_messages" in output
    assert "manage_threads" in output


@pytest.mark.asyncio
async def test_create_role_does_not_retry_when_bot_lacks_manage_roles(monkeypatch):
    guild_permissions = discord.Permissions(manage_channels=True)
    guild = SimpleNamespace(
        id=1,
        me=SimpleNamespace(guild_permissions=guild_permissions),
    )
    response = SimpleNamespace(status=403, reason="Forbidden")
    forbidden = discord.Forbidden(
        response,
        {"message": "Missing Permissions", "code": 50013},
    )
    guild.create_role = AsyncMock(side_effect=forbidden)
    monkeypatch.setattr(server_builder_module, "ROLE_CREATE_DELAY", 0)

    builder = ServerBuilder(guild)
    with pytest.raises(discord.Forbidden):
        await builder._create_role(
            "Administration",
            permissions=discord.Permissions(
                manage_roles=True,
                manage_messages=True,
            ),
            colour=discord.Colour.red(),
            reason="test",
        )

    guild.create_role.assert_awaited_once()


def test_prepare_channel_overwrites_strips_only_ungrantable_allow_bits():
    bot_member = SimpleNamespace(
        guild_permissions=discord.Permissions(
            manage_roles=True,
            manage_channels=True,
            view_channel=True,
            read_message_history=True,
            send_messages=True,
        )
    )
    admin_role = object()
    everyone = object()
    guild = SimpleNamespace(me=bot_member)
    overwrites = {
        everyone: discord.PermissionOverwrite(view_channel=False),
        admin_role: discord.PermissionOverwrite(
            view_channel=True,
            manage_channels=True,
            manage_messages=True,
            manage_threads=True,
        ),
    }

    prepared, removed = _prepare_channel_overwrites(guild, overwrites)

    assert prepared[everyone].view_channel is False
    assert prepared[admin_role].view_channel is True
    assert prepared[admin_role].manage_channels is True
    assert prepared[admin_role].manage_messages is None
    assert prepared[admin_role].manage_threads is None
    assert prepared[bot_member].view_channel is True
    assert "manage_messages" in removed
    assert "manage_threads" in removed


def test_prepare_channel_overwrites_keeps_requested_permissions_for_administrator_bot():
    bot_member = SimpleNamespace(
        guild_permissions=discord.Permissions(administrator=True)
    )
    admin_role = object()
    guild = SimpleNamespace(me=bot_member)
    requested = discord.PermissionOverwrite(
        manage_messages=True,
        manage_threads=True,
    )

    prepared, removed = _prepare_channel_overwrites(
        guild,
        {admin_role: requested},
    )

    assert prepared[admin_role].manage_messages is True
    assert prepared[admin_role].manage_threads is True
    assert prepared[bot_member].view_channel is True
    assert removed == []


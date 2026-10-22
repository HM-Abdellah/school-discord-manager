import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import services.build_transaction as build_transaction
from services.discord_ownership import ManagedResourceConflict


class _FakeBuilder:
    def __init__(self, guild):
        from services.permissions import ADMIN_ROLE_NAMES

        self.guild = guild
        self.rollback = AsyncMock()
        self.main_roles = {
            name: SimpleNamespace(id=index + 100, name=name)
            for index, name in enumerate(ADMIN_ROLE_NAMES)
        }
        self.migrate_legacy_admin_overwrites = AsyncMock()

    async def build(self, config):
        config["managed"] = {"roles": {"Administration": 10}, "categories": {}, "channels": {}}
        return SimpleNamespace(levels_processed=1)


class _FailingBuilder(_FakeBuilder):
    async def build(self, config):
        config["managed"] = {"roles": {"Administration": 10}, "categories": {}, "channels": {}}
        raise RuntimeError("discord mutation failed")


async def _no_conflict(guild, config):
    return None


@pytest.mark.asyncio
async def test_build_and_persist_commits_working_config_only_after_build_success(monkeypatch):
    original = {"academic_year": "2026/2027", "levels": []}
    saved = {}

    monkeypatch.setattr(build_transaction, "TransactionalServerBuilder", _FakeBuilder)
    monkeypatch.setattr(build_transaction, "validate_managed_registry", _no_conflict)
    monkeypatch.setattr(build_transaction, "validate_managed_registry_completeness", lambda _config: None)
    monkeypatch.setattr(build_transaction, "get_guild_config", lambda _guild_id: None)

    def fake_save(guild_id, config):
        saved["guild_id"] = guild_id
        saved["config"] = config

    monkeypatch.setattr(build_transaction, "save_guild_config", fake_save)

    result = await build_transaction.build_and_persist(SimpleNamespace(id=123), original)

    assert result.levels_processed == 1
    assert original["managed"]["roles"]["Administration"] == 10
    assert saved["guild_id"] == 123
    assert saved["config"] is not original


@pytest.mark.asyncio
async def test_incomplete_managed_registry_blocks_persistence_and_rolls_back(monkeypatch):
    original = {"academic_year": "2026/2027", "levels": []}
    builder = _FakeBuilder(SimpleNamespace(id=123))
    saved = AsyncMock()

    monkeypatch.setattr(build_transaction, "TransactionalServerBuilder", lambda guild: builder)
    monkeypatch.setattr(build_transaction, "validate_managed_registry", _no_conflict)
    monkeypatch.setattr(build_transaction, "save_guild_config", saved)

    with pytest.raises(ManagedResourceConflict, match="Managed registry incomplete after build"):
        await build_transaction.build_and_persist(SimpleNamespace(id=123), original)

    saved.assert_not_awaited()
    builder.rollback.assert_awaited_once()
    assert original == {"academic_year": "2026/2027", "levels": []}


@pytest.mark.asyncio
async def test_build_failure_rolls_back_and_does_not_mutate_caller_config(monkeypatch):
    original = {"academic_year": "2026/2027", "levels": []}
    saved = AsyncMock()

    monkeypatch.setattr(build_transaction, "TransactionalServerBuilder", _FailingBuilder)
    monkeypatch.setattr(build_transaction, "validate_managed_registry", _no_conflict)
    monkeypatch.setattr(build_transaction, "validate_managed_registry_completeness", lambda _config: None)
    monkeypatch.setattr(build_transaction, "get_guild_config", lambda _guild_id: None)
    monkeypatch.setattr(build_transaction, "save_guild_config", saved)

    with pytest.raises(RuntimeError, match="discord mutation failed"):
        await build_transaction.build_and_persist(SimpleNamespace(id=123), original)

    assert original == {"academic_year": "2026/2027", "levels": []}
    saved.assert_not_called()


@pytest.mark.asyncio
async def test_persistence_failure_rolls_back_discord_and_does_not_commit_config(monkeypatch):
    original = {"academic_year": "2026/2027", "levels": []}
    builder = _FakeBuilder(SimpleNamespace(id=123))

    monkeypatch.setattr(build_transaction, "TransactionalServerBuilder", lambda guild: builder)
    monkeypatch.setattr(build_transaction, "validate_managed_registry", _no_conflict)
    monkeypatch.setattr(build_transaction, "validate_managed_registry_completeness", lambda _config: None)
    monkeypatch.setattr(build_transaction, "get_guild_config", lambda _guild_id: None)

    def fail_save(guild_id, config):
        raise OSError("disk full")

    monkeypatch.setattr(build_transaction, "save_guild_config", fail_save)

    with pytest.raises(OSError, match="disk full"):
        await build_transaction.build_and_persist(SimpleNamespace(id=123), original)

    assert original == {"academic_year": "2026/2027", "levels": []}
    builder.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_rollback_deletes_channels_before_categories_before_roles():
    builder = object.__new__(build_transaction.TransactionalServerBuilder)

    events = []

    def resource(label):
        item = SimpleNamespace()

        async def delete(*, reason):
            events.append(label)

        item.delete = delete
        return item

    builder.created_channels = [resource("channel-1"), resource("channel-2")]
    builder.created_categories = [resource("category-1")]
    builder.created_roles = [resource("role-1"), resource("role-2")]

    await builder.rollback()

    assert events == [
        "channel-2",
        "channel-1",
        "category-1",
        "role-2",
        "role-1",
    ]


@pytest.mark.asyncio
async def test_build_rejects_removing_already_managed_streams(monkeypatch):
    current = {
        "academic_year": "2026/2027",
        "levels": [
            {
                "name": "Tronc Commun",
                "streams": [
                    {"name": "Tronc Commun Scientifique", "abbreviation": "TCS"},
                    {"name": "Tronc Commun Lettres", "abbreviation": "TCL"},
                ],
            }
        ],
    }
    candidate = {
        "academic_year": "2026/2027",
        "levels": [
            {
                "name": "Tronc Commun",
                "streams": [
                    {"name": "Tronc Commun Scientifique", "abbreviation": "TCS"},
                ],
            }
        ],
    }

    monkeypatch.setattr(build_transaction, "get_guild_config", lambda _guild_id: current)

    with pytest.raises(ValueError, match="ne peut pas supprimer une filière"):
        await build_transaction.build_and_persist(SimpleNamespace(id=123), candidate)


@pytest.mark.asyncio
async def test_build_rejects_while_stream_removal_is_pending(monkeypatch):
    current = {
        "academic_year": "2026/2027",
        "pending_removal": {
            "level": "Tronc Commun",
            "stream": "Tronc Commun Scientifique",
        },
    }
    candidate = {"academic_year": "2026/2027", "levels": []}

    monkeypatch.setattr(build_transaction, "get_guild_config", lambda _guild_id: current)

    with pytest.raises(ValueError, match="suppression de filière est interrompue"):
        await build_transaction.build_and_persist(SimpleNamespace(id=123), candidate)


def test_channel_registry_completeness_accepts_discord_lowercased_keys():
    from services.discord_ownership import (
        _expected_canonical_names,
        _name_key,
        validate_managed_registry_completeness,
    )

    config = {
        "academic_year": "2026/2027",
        "levels": [
            {
                "name": "Tronc Commun",
                "streams": [
                    {
                        "name": "Tronc Commun Scientifique",
                        "abbreviation": "TCS",
                    }
                ],
            }
        ],
        "managed": {
            "roles": {},
            "categories": {},
            "channels": {},
        },
    }

    role_names, category_names, channel_names = _expected_canonical_names(config)
    config["managed"]["roles"] = {
        name: index
        for index, name in enumerate(sorted(role_names), start=1000)
    }
    config["managed"]["categories"] = {
        name: index
        for index, name in enumerate(sorted(category_names), start=2000)
    }
    config["managed"]["channels"] = {
        _name_key(name): index
        for index, name in enumerate(sorted(channel_names), start=3000)
    }

    validate_managed_registry_completeness(config)


@pytest.mark.asyncio
async def test_build_rejects_interrupted_reset_before_any_discord_mutation(monkeypatch):
    current = {"academic_year": "2026/2027", "pending_reset": {"archive": "2026-2027.db"}}
    candidate = {"academic_year": "2026/2027", "levels": []}
    monkeypatch.setattr(build_transaction, "get_guild_config", lambda _guild_id: current)

    with pytest.raises(ValueError, match="reset du serveur est interrompu"):
        await build_transaction.build_and_persist(SimpleNamespace(id=123), candidate)


@pytest.mark.asyncio
async def test_build_cancellation_attempts_rollback_before_propagating(monkeypatch):
    class _CancelledBuilder(_FakeBuilder):
        async def build(self, config):
            config["managed"] = {"roles": {"Administration": 10}, "categories": {}, "channels": {}}
            raise asyncio.CancelledError()

    builder = _CancelledBuilder(SimpleNamespace(id=123))
    monkeypatch.setattr(build_transaction, "TransactionalServerBuilder", lambda guild: builder)
    monkeypatch.setattr(build_transaction, "validate_managed_registry", _no_conflict)
    monkeypatch.setattr(build_transaction, "get_guild_config", lambda _guild_id: None)
    original = {"academic_year": "2026/2027", "levels": []}

    with pytest.raises(asyncio.CancelledError):
        await build_transaction.build_and_persist(SimpleNamespace(id=123), original)

    builder.rollback.assert_awaited_once()
    assert original == {"academic_year": "2026/2027", "levels": []}


@pytest.mark.asyncio
async def test_transaction_tracks_category_created_before_cancellation(monkeypatch):
    import discord

    category = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock(spec=discord.CategoryChannel)
    category.id = 303
    category.name = "created-category"
    guild = SimpleNamespace(categories=[], channels=[], create_category=AsyncMock(return_value=category))
    builder = build_transaction.TransactionalServerBuilder(guild)

    async def cancel_after_creation():
        builder._channel_snapshot.append(category)
        guild.channels.append(category)
        raise asyncio.CancelledError()

    monkeypatch.setattr(build_transaction.ServerBuilder, "_pace_after_create", lambda self: cancel_after_creation())
    with pytest.raises(asyncio.CancelledError):
        await builder._get_or_create_category("created-category")

    assert builder.created_categories == [category]


@pytest.mark.asyncio
async def test_transaction_tracks_role_created_before_cancellation(monkeypatch):
    import discord

    role = SimpleNamespace(id=202, name="new-role")
    guild = SimpleNamespace(roles=[])
    builder = build_transaction.TransactionalServerBuilder(guild)

    async def create_then_cancel(self, name, **kwargs):
        guild.roles.append(role)
        raise asyncio.CancelledError()

    monkeypatch.setattr(build_transaction.ServerBuilder, "_create_role", create_then_cancel)
    with pytest.raises(asyncio.CancelledError):
        await builder._create_role("new-role")

    assert builder.created_roles == [role]


@pytest.mark.asyncio
async def test_legacy_administration_overwrites_are_copied_only_to_registered_resources_and_rollback(monkeypatch):
    import discord

    class Role:
        def __init__(self, role_id, name):
            self.id = role_id
            self.name = name
            self.managed = False

        def is_default(self):
            return False

        def __hash__(self):
            return hash(self.id)

        def __eq__(self, other):
            return isinstance(other, Role) and self.id == other.id

    class Resource:
        def __init__(self, resource_id, overwrites):
            self.id = resource_id
            self.overwrites = dict(overwrites)

        async def set_permissions(self, role, *, overwrite, reason):
            if overwrite is None:
                self.overwrites.pop(role, None)
            else:
                self.overwrites[role] = overwrite

    legacy = Role(10, "Administration")
    new_roles = [
        Role(11, "Direction"),
        Role(12, "Surveillant Général"),
        Role(13, "Personnel Administratif"),
        Role(14, "Support Engineer"),
    ]
    legacy_overwrite = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    custom_direction_overwrite = discord.PermissionOverwrite(view_channel=False)
    registered_channel = Resource(20, {legacy: legacy_overwrite})
    registered_category = Resource(30, {legacy: legacy_overwrite, new_roles[0]: custom_direction_overwrite})
    unregistered_channel = Resource(40, {legacy: legacy_overwrite})
    roles_by_id = {legacy.id: legacy}
    channels_by_id = {20: registered_channel, 30: registered_category, 40: unregistered_channel}
    guild = SimpleNamespace(
        id=123,
        get_role=lambda role_id: roles_by_id.get(role_id),
        get_channel=lambda channel_id: channels_by_id.get(channel_id),
    )
    builder = build_transaction.TransactionalServerBuilder(guild)
    previous_config = {
        "management_role_id": legacy.id,
        "managed": {
            "roles": {"Administration": legacy.id},
            "channels": {"private": registered_channel.id},
            "categories": {"private-category": registered_category.id},
        },
    }

    await builder.migrate_legacy_admin_overwrites(previous_config, new_roles)

    for role in new_roles[1:]:
        assert registered_channel.overwrites[role] == legacy_overwrite
    assert registered_category.overwrites[new_roles[0]] == custom_direction_overwrite
    for role in new_roles[1:]:
        assert registered_category.overwrites[role] == legacy_overwrite
    assert set(unregistered_channel.overwrites) == {legacy}

    await builder.rollback()

    assert registered_channel.overwrites == {legacy: legacy_overwrite}
    assert registered_category.overwrites == {
        legacy: legacy_overwrite,
        new_roles[0]: custom_direction_overwrite,
    }
    assert unregistered_channel.overwrites == {legacy: legacy_overwrite}

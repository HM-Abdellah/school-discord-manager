from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import services.build_transaction as build_transaction


class _FakeBuilder:
    def __init__(self, guild):
        self.guild = guild
        self.rollback = AsyncMock()

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
async def test_build_failure_rolls_back_and_does_not_mutate_caller_config(monkeypatch):
    original = {"academic_year": "2026/2027", "levels": []}
    saved = AsyncMock()

    monkeypatch.setattr(build_transaction, "TransactionalServerBuilder", _FailingBuilder)
    monkeypatch.setattr(build_transaction, "validate_managed_registry", _no_conflict)
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

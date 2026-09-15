from copy import deepcopy
from types import SimpleNamespace

import pytest

import services.removestream_transaction as transaction


def test_journal_is_written_before_any_deletion():
    config = {"managed": {}}
    saved = []
    journal = transaction.build_removal_journal(
        level="Tronc Commun",
        stream="Tronc Commun Scientifique",
        code="TCS",
        resources=[{"kind": "channel", "id": 101, "name": "📌-TCS・informations"}],
    )

    transaction.install_removal_journal(config, journal, lambda value: saved.append(deepcopy(value)))

    assert config[transaction.PENDING_REMOVAL_KEY]["completed"] == []
    assert saved == [config]


@pytest.mark.asyncio
async def test_journal_checkpoints_each_successful_delete():
    events = []
    saved = []

    first = SimpleNamespace()

    async def first_delete(*, reason):
        events.append(("delete", 101, reason))

    first.delete = first_delete

    second = SimpleNamespace()

    async def second_delete(*, reason):
        events.append(("delete", 202, reason))
        raise RuntimeError("stop before checkpoint")

    second.delete = second_delete

    journal = transaction.build_removal_journal(
        level="Tronc Commun",
        stream="Tronc Commun Scientifique",
        code="TCS",
        resources=[
            {"kind": "channel", "id": 101, "name": "one"},
            {"kind": "role", "id": 202, "name": "two"},
        ],
    )
    config = {}

    def resolve(resource):
        return {101: first, 202: second}[resource["id"]]

    with pytest.raises(RuntimeError, match="stop before checkpoint"):
        await transaction.execute_removal_journal(
            config=config,
            journal=journal,
            resolve=resolve,
            checkpoint=lambda value: saved.append(deepcopy(value)),
        )

    assert events[0][0:2] == ("delete", 101)
    assert saved[0][transaction.PENDING_REMOVAL_KEY]["completed"] == ["channel:101"]
    assert len(saved) == 1


@pytest.mark.asyncio
async def test_not_found_is_treated_as_already_completed():
    saved = []
    target = SimpleNamespace()

    async def delete(*, reason):
        from discord import NotFound

        raise NotFound(SimpleNamespace(status=404, reason="missing"), "already gone")

    target.delete = delete

    journal = transaction.build_removal_journal(
        level="Tronc Commun",
        stream="Tronc Commun Scientifique",
        code="TCS",
        resources=[{"kind": "channel", "id": 303, "name": "gone"}],
    )
    config = {}

    await transaction.execute_removal_journal(
        config=config,
        journal=journal,
        resolve=lambda _resource: target,
        checkpoint=lambda value: saved.append(deepcopy(value)),
    )

    assert config[transaction.PENDING_REMOVAL_KEY]["completed"] == ["channel:303"]
    assert saved[-1][transaction.PENDING_REMOVAL_KEY]["completed"] == ["channel:303"]


def test_invalid_resource_is_rejected_before_execution():
    journal = transaction.build_removal_journal(
        level="Tronc Commun",
        stream="Tronc Commun Scientifique",
        code="TCS",
        resources=[{"kind": "channel", "id": 0, "name": "invalid"}],
    )

    with pytest.raises(ValueError, match="Invalid removal resource"):
        transaction._normalize_resource(journal["resources"][0])

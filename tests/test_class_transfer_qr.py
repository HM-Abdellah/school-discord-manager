from datetime import datetime, timedelta, timezone
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import services.storage as storage
from cogs.qr_onboarding import ClassQROnboarding, ClassTransferConfirmationView


@pytest.fixture
def transfer_store(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_FILE", tmp_path / "guild_config.json")
    monkeypatch.setattr(storage, "DATABASE_FILE", tmp_path / "school.db")
    storage.initialize_database()
    config = {
        "academic_year": "2026/2027",
        "levels": [
            {
                "name": "2BAC",
                "streams": [
                    {"name": "Sciences Physiques", "abbreviation": "2BACSPF", "subjects": []},
                    {"name": "Sciences Maths", "abbreviation": "2BASM", "subjects": []},
                ],
            }
        ],
    }
    storage.sync_configuration_to_database(101, config)
    year = storage.get_active_academic_year(101)
    assert year is not None
    return {"guild_id": 101, "year_id": int(year["id"])}


def _make_request(store, *, student_id=202, section=5, expires_at=None):
    now = datetime.now(timezone.utc)
    expires = expires_at or (now + timedelta(minutes=30))
    return storage.create_class_transfer_request(
        store["guild_id"],
        student_id,
        303,
        store["year_id"],
        "2BAC",
        "Sciences Physiques",
        "2BACSPF",
        section,
        "2BACSPF-" + str(section),
        405,
        406,
        now.isoformat(),
        expires.isoformat(),
    )


def test_transfer_completion_moves_section_and_preserves_history(transfer_store):
    store = transfer_store
    student_id = storage.enroll_student_record(
        store["guild_id"], 202, "Student A", store["year_id"],
        "2BAC", "Sciences Physiques", section=2,
    )
    request_id = _make_request(store, section=5)
    storage.set_class_transfer_message(request_id, 506, 607)

    completed_at = datetime.now(timezone.utc).isoformat()
    result = storage.complete_class_transfer_request(
        request_id, store["guild_id"], 202, "Student A", completed_at,
    )

    assert result == student_id
    request = storage.get_class_transfer_request(request_id)
    assert request is not None
    assert request["status"] == "completed"
    assert request["dm_channel_id"] == 506
    assert request["dm_message_id"] == 607

    history = storage.get_student_history(store["guild_id"], 202)
    assert len(history) == 2
    rows_by_status = {row["status"]: row for row in history}
    assert rows_by_status["transferred"]["section"] == 2
    assert rows_by_status["active"]["section"] == 5
    assert storage.list_pending_class_transfer_requests(datetime.now(timezone.utc).isoformat()) == []


def test_transfer_request_can_only_be_completed_once(transfer_store):
    store = transfer_store
    request_id = _make_request(store)
    storage.set_class_transfer_message(request_id, 506, 607)
    when = datetime.now(timezone.utc).isoformat()

    storage.complete_class_transfer_request(request_id, store["guild_id"], 202, "Student A", when)
    with pytest.raises(ValueError, match="no longer pending"):
        storage.complete_class_transfer_request(request_id, store["guild_id"], 202, "Student A", when)


def test_expired_transfer_is_marked_expired_and_not_enrolled(transfer_store):
    store = transfer_store
    now = datetime.now(timezone.utc)
    request_id = _make_request(store, expires_at=now - timedelta(seconds=1))
    storage.set_class_transfer_message(request_id, 506, 607)

    with pytest.raises(ValueError, match="expired"):
        storage.complete_class_transfer_request(
            request_id, store["guild_id"], 202, "Student A", now.isoformat(),
        )

    request = storage.get_class_transfer_request(request_id)
    assert request is not None and request["status"] == "expired"
    assert storage.get_student_history(store["guild_id"], 202) == []


def test_only_one_pending_transfer_per_student_is_allowed(transfer_store):
    store = transfer_store
    _make_request(store)
    with pytest.raises(sqlite3.IntegrityError):
        _make_request(store, section=4)


def test_startup_cleanup_expires_old_requests_and_cancels_orphans(transfer_store):
    store = transfer_store
    now = datetime.now(timezone.utc)
    expired_id = _make_request(store, student_id=202, expires_at=now - timedelta(seconds=5))
    orphan_id = _make_request(store, student_id=203, expires_at=now + timedelta(minutes=5))

    storage.cleanup_class_transfer_requests(now.isoformat())

    expired = storage.get_class_transfer_request(expired_id)
    orphan = storage.get_class_transfer_request(orphan_id)
    assert expired is not None and expired["status"] == "expired"
    assert orphan is not None and orphan["status"] == "cancelled"


def test_transfer_confirmation_view_is_persistent_and_request_scoped():
    view = ClassTransferConfirmationView(SimpleNamespace(), 1234)

    assert view.timeout is None
    assert view.is_persistent()
    assert len(view.children) == 1
    assert view.children[0].custom_id == "school-manager:class-transfer:confirm:1234"


@pytest.mark.asyncio
async def test_transfer_qr_rejects_member_without_managed_student_role(monkeypatch):
    import cogs.qr_onboarding as qr_module

    student_role = SimpleNamespace(id=10)
    stream_role = SimpleNamespace(id=20)
    monkeypatch.setattr(qr_module, "student_staff_conflict", lambda student, guild: None)
    monkeypatch.setattr(qr_module, "get_levels", lambda: ["2BAC"])
    monkeypatch.setattr(qr_module, "get_streams", lambda level: ["Sciences Physiques"])
    monkeypatch.setattr(qr_module, "get_active_academic_year", lambda guild_id: {"id": 1})
    monkeypatch.setattr(qr_module, "get_stream_abbreviation", lambda level, stream: "2BACSPF")
    monkeypatch.setattr(
        qr_module,
        "get_managed_role",
        Mock(side_effect=[student_role, stream_role]),
    )

    student = SimpleNamespace(id=202, bot=False, roles=[], mention="<@202>")
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=101),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    cog = ClassQROnboarding(SimpleNamespace())

    await cog.create_class_transfer_qr.callback(
        cog, interaction, student, "2BAC", "Sciences Physiques", 1
    )

    interaction.response.send_message.assert_awaited_once()
    assert "ne possède pas le rôle élève" in interaction.response.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_transfer_confirmation_rejects_another_discord_user(monkeypatch):
    request = {
        "id": 9,
        "guild_id": 101,
        "student_discord_id": 202,
        "status": "pending",
    }
    monkeypatch.setattr(
        "cogs.qr_onboarding.get_class_transfer_request",
        lambda request_id: request if request_id == 9 else None,
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=999),
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        message=SimpleNamespace(edit=AsyncMock()),
    )
    view = ClassTransferConfirmationView(SimpleNamespace(), 9)

    await view.children[0].callback(interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once()
    assert "خاص بالتلميذ المعني فقط" in interaction.followup.send.await_args.args[0]
    interaction.message.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cog_load_restores_persistent_views_for_pending_transfers(monkeypatch):
    bot = SimpleNamespace(add_view=Mock())
    pending = [{"id": 321, "dm_message_id": 654}]
    monkeypatch.setattr("cogs.qr_onboarding.cleanup_class_transfer_requests", Mock())
    monkeypatch.setattr(
        "cogs.qr_onboarding.list_pending_class_transfer_requests",
        lambda now: pending,
    )
    cog = ClassQROnboarding(bot)

    await cog.cog_load()

    bot.add_view.assert_called_once()
    args, kwargs = bot.add_view.call_args
    assert args[0].is_persistent()
    assert args[0].children[0].custom_id == "school-manager:class-transfer:confirm:321"
    assert kwargs == {"message_id": 654}


def test_cancelled_transfer_cannot_be_completed(transfer_store):
    store = transfer_store
    request_id = _make_request(store)
    storage.set_class_transfer_message(request_id, 506, 607)

    assert storage.cancel_class_transfer_request(request_id) is True
    assert storage.cancel_class_transfer_request(request_id) is False
    request = storage.get_class_transfer_request(request_id)
    assert request is not None and request["status"] == "cancelled"
    with pytest.raises(ValueError, match="no longer pending"):
        storage.complete_class_transfer_request(
            request_id,
            store["guild_id"],
            202,
            "Student A",
            datetime.now(timezone.utc).isoformat(),
        )

import sqlite3
from types import SimpleNamespace

import pytest

from services import storage


def _configure_storage(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "CONFIG_FILE", data_dir / "guild_config.json")
    monkeypatch.setattr(storage, "DATABASE_FILE", data_dir / "school.db")



def test_teacher_registration_is_one_time(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    storage.initialize_database()

    registration_id = storage.record_teacher_registration(
        1,
        101,
        "Prof A",
        "male",
        "2BAC",
        "2BACPC",
        "Mathématiques, Physique-Chimie",
    )
    assert registration_id > 0
    row = storage.get_teacher_registration(1, 101)
    assert row is not None
    assert row["stream_name"] == "2BACPC"

    with pytest.raises(RuntimeError, match="déjà enregistré"):
        storage.record_teacher_registration(
            1,
            101,
            "Prof A Again",
            "male",
            "2BAC",
            "2BACPC",
            "Mathématiques",
        )


def test_only_one_active_teacher_qr_exists_per_guild(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    storage.initialize_database()

    storage.record_teacher_qr_invite(1, "AAA111", 501, 9001, "2026-10-08T10:00:00+00:00", "2026-10-08T10:30:00+00:00", 10)

    try:
        storage.record_teacher_qr_invite(1, "BBB222", 501, 9001, "2026-10-08T10:01:00+00:00", "2026-10-08T10:31:00+00:00", 10)
    except RuntimeError as exc:
        assert "actif" in str(exc)
    else:
        raise AssertionError("A second active teacher QR was accepted.")

    rows = storage.get_teacher_qr_invites(1)
    assert [row["invite_code"] for row in rows] == ["AAA111"]

    storage.mark_teacher_qr_invite_revoked(1, "AAA111")
    storage.record_teacher_qr_invite(1, "BBB222", 501, 9001, "2026-10-08T10:02:00+00:00", "2026-10-08T10:32:00+00:00", 10)
    assert [row["invite_code"] for row in storage.get_teacher_qr_invites(1)] == ["BBB222"]


def test_reset_guild_data_clears_teacher_onboarding_state(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    storage.initialize_database()
    storage.record_teacher_qr_invite(1, "AAA111", 501, 9001, "2026-10-08T10:00:00+00:00", "2026-10-08T10:30:00+00:00", 10)
    storage.reset_guild_data(1)

    assert storage.get_teacher_qr_invites(1) == []
    with storage._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM teacher_qr_invites WHERE guild_id=1"
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_teacher_qr_join_removes_pending_role_for_already_registered_member(monkeypatch):
    from unittest.mock import AsyncMock
    from cogs.teacher_qr_onboarding import TeacherQROnboarding
    from services.permissions import ROLE_PROFESSOR, ROLE_TEACHER_PENDING

    pending = object()
    professor = object()
    guild = SimpleNamespace(id=1)
    member = SimpleNamespace(
        bot=False,
        id=101,
        guild=guild,
        roles=[pending],
        remove_roles=AsyncMock(),
        send=AsyncMock(),
    )
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_managed_role", lambda _guild, name: {ROLE_TEACHER_PENDING: pending, ROLE_PROFESSOR: professor}.get(name))
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda _guild_id, _discord_id: object())

    cog = TeacherQROnboarding(SimpleNamespace())
    await cog.on_member_join(member)

    member.remove_roles.assert_awaited_once_with(
        pending,
        reason="School Manager teacher QR already consumed",
    )
    member.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_teacher_onboarding_view_contains_completion_button():
    from cogs.teacher_qr_onboarding import TeacherOnboardingView

    view = TeacherOnboardingView(SimpleNamespace(), 1, 101)
    assert len(view.children) == 1
    button = view.children[0]
    assert button.label == "Compléter mon inscription"
    assert button.emoji.name == "📝"
    assert view.guild_id == 1
    assert view.user_id == 101


@pytest.mark.asyncio
async def test_teacher_onboarding_button_opens_modal_for_eligible_teacher(monkeypatch):
    from unittest.mock import AsyncMock
    from cogs.teacher_qr_onboarding import TeacherOnboardingView
    from services.permissions import ROLE_PROFESSOR, ROLE_TEACHER_PENDING

    pending = object()
    professor = object()
    member = SimpleNamespace(id=101, roles=[pending])
    guild = SimpleNamespace(id=1, get_member=lambda user_id: member if user_id == 101 else None)
    bot = SimpleNamespace(get_guild=lambda guild_id: guild)

    monkeypatch.setattr(
        "cogs.teacher_qr_onboarding.get_managed_role",
        lambda _guild, name: {ROLE_TEACHER_PENDING: pending, ROLE_PROFESSOR: professor}.get(name),
    )
    monkeypatch.setattr(
        "cogs.teacher_qr_onboarding.get_teacher_registration",
        lambda _guild_id, _discord_id: None,
    )

    response = SimpleNamespace(send_modal=AsyncMock())
    interaction = SimpleNamespace(user=SimpleNamespace(id=101), response=response)
    view = TeacherOnboardingView(bot, 1, 101)

    await view.children[0].callback(interaction)

    response.send_modal.assert_awaited_once()
    assert response.send_modal.await_args.args[0].view_ref is view


def test_teacher_onboarding_modal_normalizes_gender():
    from cogs.teacher_qr_onboarding import TeacherOnboardingModal

    assert TeacherOnboardingModal._normalize_gender("Prof") == "male"
    assert TeacherOnboardingModal._normalize_gender("Prof (F)") == "female"
    assert TeacherOnboardingModal._normalize_gender("femme") == "female"
    assert TeacherOnboardingModal._normalize_gender("invalid") is None


@pytest.mark.asyncio
async def test_teacher_qr_join_prompts_only_unregistered_member(monkeypatch):
    from unittest.mock import AsyncMock
    from cogs.teacher_qr_onboarding import TeacherQROnboarding
    from services.permissions import ROLE_PROFESSOR, ROLE_TEACHER_PENDING

    pending = object()
    professor = object()
    guild = SimpleNamespace(id=1)
    member = SimpleNamespace(
        bot=False,
        id=101,
        guild=guild,
        roles=[pending],
        remove_roles=AsyncMock(),
        send=AsyncMock(),
    )
    prompt = AsyncMock()
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_managed_role", lambda _guild, name: {ROLE_TEACHER_PENDING: pending, ROLE_PROFESSOR: professor}.get(name))
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda _guild_id, _discord_id: None)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.teacher_target_conflict", lambda _member, _guild: None)
    monkeypatch.setattr("cogs.teacher_qr_onboarding._send_teacher_onboarding_prompt", prompt)

    bot = SimpleNamespace()
    cog = TeacherQROnboarding(bot)
    await cog.on_member_join(member)

    member.remove_roles.assert_not_awaited()
    prompt.assert_awaited_once_with(bot, member)
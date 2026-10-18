import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

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


def test_delete_teacher_registration_removes_assignments_and_allows_fresh_registration(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    storage.initialize_database()
    storage.record_teacher_registration(1, 101, "Prof A", "male", "2ème Année Bac", "2ème Année Bac Sciences Physiques", "Mathématiques")
    storage.record_teacher_assignments(1, 101, [{
        "level": "2ème Année Bac",
        "stream": "2ème Année Bac Sciences Physiques",
        "section": 3,
        "subjects": ["Mathématiques"],
    }])
    row = storage.get_teacher_assignments(1, 101)[0]
    storage.update_teacher_assignment_thread(1, row["id"], status="created", thread_id=9001)

    storage.delete_teacher_registration(1, 101)

    assert storage.get_teacher_registration(1, 101) is None
    assert storage.get_teacher_assignments(1, 101) == []
    storage.record_teacher_registration(1, 101, "Prof A", "female", "1ère Année Bac", "1ère Année Bac Sciences Expérimentales", "Sciences de la Vie et de la Terre (SVT)")
    storage.record_teacher_assignments(1, 101, [{
        "level": "1ère Année Bac",
        "stream": "1ère Année Bac Sciences Expérimentales",
        "section": 2,
        "subjects": ["Sciences de la Vie et de la Terre (SVT)"],
    }])
    fresh = storage.get_teacher_registration(1, 101)
    assert fresh is not None
    assert fresh["profile_type"] == "female"
    assert fresh["level_name"] == "1ère Année Bac"
    assert [row["subject_name"] for row in storage.get_teacher_assignments(1, 101)] == ["Sciences de la Vie et de la Terre (SVT)"]


@pytest.mark.asyncio
async def test_member_remove_clears_teacher_data_and_archives_linked_threads(monkeypatch):
    from cogs.teacher_qr_onboarding import TeacherQROnboarding

    thread = SimpleNamespace(
        id=9001,
        type=discord.ChannelType.public_thread,
        archived=False,
        locked=False,
        edit=AsyncMock(),
    )
    guild = SimpleNamespace(
        id=1,
        get_thread=lambda thread_id: thread if thread_id == thread.id else None,
    )
    member = SimpleNamespace(bot=False, id=101, display_name="Prof A", guild=guild)
    registration = {"discord_id": 101, "full_name": "Prof A"}
    assignments = [{"id": 5, "thread_id": 9001}]
    delete_registration = Mock()
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_build_lock", lambda _guild_id: _AsyncLock())
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda *_args: registration)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_assignments", lambda *_args: assignments)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.delete_teacher_registration", delete_registration)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.record_event", lambda *_args, **_kwargs: None)

    await TeacherQROnboarding(SimpleNamespace(get_channel=lambda _id: None)).on_member_remove(member)

    delete_registration.assert_called_once_with(1, 101)
    thread.edit.assert_awaited_once_with(
        archived=True,
        locked=True,
        reason="School Manager: archive teacher assignment after leaving the server",
    )


@pytest.mark.asyncio
async def test_member_remove_still_clears_teacher_data_when_thread_archiving_fails(monkeypatch):
    from cogs.teacher_qr_onboarding import TeacherQROnboarding

    response = SimpleNamespace(status=403, reason="Forbidden")
    thread = SimpleNamespace(
        id=9001, type=discord.ChannelType.public_thread, archived=False, locked=False,
        edit=AsyncMock(side_effect=discord.Forbidden(response, "Missing Manage Threads")),
    )
    guild = SimpleNamespace(id=1, get_thread=lambda _thread_id: thread)
    member = SimpleNamespace(bot=False, id=101, display_name="Prof A", guild=guild)
    delete_registration = Mock()
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_build_lock", lambda _guild_id: _AsyncLock())
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda *_args: {"discord_id": 101})
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_assignments", lambda *_args: [{"thread_id": 9001}])
    monkeypatch.setattr("cogs.teacher_qr_onboarding.delete_teacher_registration", delete_registration)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.record_event", lambda *_args, **_kwargs: None)

    await TeacherQROnboarding(SimpleNamespace()).on_member_remove(member)

    delete_registration.assert_called_once_with(1, 101)


@pytest.mark.asyncio
async def test_member_remove_keeps_members_without_teacher_registration_untouched(monkeypatch):
    from cogs.teacher_qr_onboarding import TeacherQROnboarding

    member = SimpleNamespace(bot=False, id=101, display_name="Student", guild=SimpleNamespace(id=1))
    delete_registration = Mock()
    assignments = Mock()
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_build_lock", lambda _guild_id: _AsyncLock())
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda *_args: None)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_assignments", assignments)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.delete_teacher_registration", delete_registration)

    await TeacherQROnboarding(SimpleNamespace()).on_member_remove(member)

    assignments.assert_not_called()
    delete_registration.assert_not_called()


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
async def test_teacher_qr_join_restores_registered_member_access_and_removes_pending_role(monkeypatch):
    from unittest.mock import AsyncMock
    from cogs.teacher_qr_onboarding import TeacherQROnboarding
    from services.permissions import ROLE_PROFESSOR, ROLE_TEACHER_PENDING

    pending = SimpleNamespace(id=10, name=ROLE_TEACHER_PENDING, managed=False)
    professor = SimpleNamespace(id=11, name=ROLE_PROFESSOR, managed=False)
    guild = SimpleNamespace(id=1)
    registration = {
        "profile_type": "male",
        "level_name": "2BAC",
        "stream_name": "2BACPC",
        "subjects_text": "Mathématiques",
    }
    assignment = {
        "level_name": "2BAC",
        "stream_name": "2BACPC",
        "section": 2,
        "subject_name": "Mathématiques",
    }
    member = SimpleNamespace(
        bot=False,
        id=101,
        display_name="Prof A",
        guild=guild,
        roles=[pending],
        remove_roles=AsyncMock(),
        send=AsyncMock(),
    )
    execute = AsyncMock()
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_managed_role", lambda _guild, name: {ROLE_TEACHER_PENDING: pending, ROLE_PROFESSOR: professor}.get(name))
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda _guild_id, _discord_id: registration)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_assignments", lambda _guild_id, _discord_id: [assignment])
    monkeypatch.setattr("cogs.teacher_qr_onboarding.teacher_target_conflict", lambda _member, _guild: None)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.execute_teacher_assignment", execute)
    sync_threads = AsyncMock(return_value="✅ Threads professeur prêts : 1 (nouveaux : 1, existants : 0).")
    monkeypatch.setattr("cogs.teacher_qr_onboarding._sync_registered_teacher_threads", sync_threads)

    await TeacherQROnboarding(SimpleNamespace()).on_member_join(member)

    execute.assert_awaited_once()
    kwargs = execute.await_args.kwargs
    assert kwargs["self_registration"] is False
    assert kwargs["event_action"] is None
    assert kwargs["assignments"] == [{
        "level": "2BAC",
        "stream": "2BACPC",
        "section": 2,
        "subjects": ["Mathématiques"],
    }]
    member.remove_roles.assert_awaited_once_with(
        pending,
        reason="School Manager restored registered teacher access",
    )
    member.send.assert_awaited_once()
    assert "rétablis automatiquement" in member.send.await_args.args[0]
    sync_threads.assert_awaited_once_with(guild, member.id)


@pytest.mark.asyncio
async def test_teacher_qr_join_restores_registered_member_without_pending_role(monkeypatch):
    from unittest.mock import AsyncMock
    from cogs.teacher_qr_onboarding import TeacherQROnboarding
    from services.permissions import ROLE_TEACHER_PENDING

    pending = SimpleNamespace(id=10, name=ROLE_TEACHER_PENDING, managed=False)
    guild = SimpleNamespace(id=1)
    registration = {
        "profile_type": "male",
        "level_name": "2BAC",
        "stream_name": "2BACPC",
        "subjects_text": "Mathématiques",
    }
    member = SimpleNamespace(
        bot=False,
        id=102,
        display_name="Prof B",
        guild=guild,
        roles=[],
        remove_roles=AsyncMock(),
        send=AsyncMock(),
    )
    execute = AsyncMock()
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_managed_role", lambda _guild, name: pending if name == ROLE_TEACHER_PENDING else None)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda _guild_id, _discord_id: registration)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_assignments", lambda _guild_id, _discord_id: [])
    monkeypatch.setattr("cogs.teacher_qr_onboarding.teacher_target_conflict", lambda _member, _guild: None)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.execute_teacher_assignment", execute)
    sync_threads = AsyncMock(return_value="✅ Threads professeur prêts : 1 (nouveaux : 1, existants : 0).")
    monkeypatch.setattr("cogs.teacher_qr_onboarding._sync_registered_teacher_threads", sync_threads)

    await TeacherQROnboarding(SimpleNamespace()).on_member_join(member)

    execute.assert_awaited_once()
    assert execute.await_args.kwargs["assignments"] is None
    member.remove_roles.assert_not_awaited()
    member.send.assert_awaited_once()
    assert "rétablis automatiquement" in member.send.await_args.args[0]
    sync_threads.assert_awaited_once_with(guild, member.id)


@pytest.mark.asyncio
async def test_teacher_onboarding_view_uses_fixed_choices():
    from cogs.teacher_qr_onboarding import TeacherOnboardingView
    from config.curriculum import get_levels

    view = TeacherOnboardingView(SimpleNamespace(), 1, 101)

    assert len(view.children) == 5
    assert [option.value for option in view.gender_select.options] == ["male", "female"]
    assert [option.value for option in view.level_select.options] == get_levels()
    assert view.stream_select.disabled is True
    assert view.section_select.disabled is True
    assert view.subject_select.disabled is True
    assert view.confirm_button.disabled is True
    assert view.guild_id == 1
    assert view.user_id == 101


@pytest.mark.asyncio
async def test_teacher_onboarding_level_selection_populates_stream_choices(monkeypatch):
    from cogs.teacher_qr_onboarding import TeacherOnboardingView
    from config.curriculum import get_levels, get_streams
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

    response = SimpleNamespace(edit_message=AsyncMock())
    interaction = SimpleNamespace(user=SimpleNamespace(id=101), response=response)
    view = TeacherOnboardingView(bot, 1, 101)
    selected_level = get_levels()[-1]
    view.level_select._values = [selected_level]

    await view.level_select.callback(interaction)

    assert view.selected_level == selected_level
    assert [option.value for option in view.stream_select.options] == get_streams(selected_level)
    assert view.stream_select.disabled is False
    assert view.subject_select.disabled is True
    response.edit_message.assert_awaited_once()


def test_teacher_onboarding_subject_values_are_canonical():
    from cogs.teacher_qr_onboarding import TeacherOnboardingView
    from config.curriculum import get_stream_subjects, get_subject_display_name

    view = TeacherOnboardingView(SimpleNamespace(), 1, 101)
    level = "2ème Année Bac"
    stream = "2ème Année Bac Sciences Physiques"
    view.selected_level = level
    view.selected_stream = stream
    subjects = get_stream_subjects(level, stream)
    view.subject_select.options = [
        discord.SelectOption(
            label=get_subject_display_name(subject),
            value=subject,
        )
        for subject in subjects
    ]

    assert [option.value for option in view.subject_select.options] == subjects


class _AsyncLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeInteractionResponse:
    def __init__(self):
        self.edit_message = AsyncMock()
        self.done = True

    def is_done(self):
        return self.done


@pytest.mark.asyncio
async def test_teacher_onboarding_confirm_success(monkeypatch):
    from cogs.teacher_qr_onboarding import TeacherOnboardingView
    from services.permissions import ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, ROLE_TEACHER_PENDING

    pending = object()
    professor = object()
    professor_female = object()
    member = SimpleNamespace(
        id=101,
        display_name="Teacher",
        roles=[pending],
        add_roles=AsyncMock(),
        remove_roles=AsyncMock(),
        guild=None,
    )
    guild = SimpleNamespace(id=1, get_member=lambda _user_id: member)
    member.guild = guild
    bot = SimpleNamespace(get_guild=lambda _guild_id: guild)
    role_map = {
        ROLE_TEACHER_PENDING: pending,
        ROLE_PROFESSOR: professor,
        ROLE_PROFESSOR_FEMALE: professor_female,
    }

    async def fake_assignment(**_kwargs):
        return {
            "stream_code": "2BACPC",
            "subject_names": "Math",
            "assignments_count": 1,
            "assignment_summary": "2BACPC-S3: Math",
        }

    response = _FakeInteractionResponse()
    edit_original_response = AsyncMock()
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=101),
        response=response,
        edit_original_response=edit_original_response,
    )

    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_managed_role", lambda _guild, name: role_map.get(name))
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda _guild_id, _discord_id: None)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_build_lock", lambda _guild_id: _AsyncLock())
    monkeypatch.setattr("cogs.teacher_qr_onboarding.execute_teacher_assignment", fake_assignment)
    monkeypatch.setattr(
        "cogs.teacher_qr_onboarding.ensure_teacher_threads",
        AsyncMock(return_value={"created": 1, "existing": 0, "failed": 0, "errors": []}),
    )

    view = TeacherOnboardingView(bot, 1, 101)
    view.selected_gender = "male"
    view.selected_level = "2ème Année Bac"
    view.selected_stream = "2ème Année Bac Sciences Physiques"
    view.selected_sections = [3]
    view.selected_subjects = ["Mathématiques"]
    view._show_subject_stage()
    view._refresh_control_state()

    await view.confirm_button.callback(interaction)

    response.edit_message.assert_awaited_once()
    edit_original_response.assert_awaited_once()
    assert "Inscription terminée" in edit_original_response.await_args.kwargs["content"]
    assert all(item.disabled for item in view.children)


@pytest.mark.asyncio
async def test_teacher_onboarding_confirm_business_error_is_visible(monkeypatch):
    from cogs.teacher_qr_onboarding import TeacherOnboardingView
    from services.permissions import ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, ROLE_TEACHER_PENDING
    from services.teacher_assignment import TeacherAssignmentError

    pending = object()
    professor = object()
    professor_female = object()
    member = SimpleNamespace(
        id=101,
        display_name="Teacher",
        roles=[pending],
        add_roles=AsyncMock(),
        remove_roles=AsyncMock(),
        guild=None,
    )
    guild = SimpleNamespace(id=1, get_member=lambda _user_id: member)
    member.guild = guild
    bot = SimpleNamespace(get_guild=lambda _guild_id: guild)
    role_map = {
        ROLE_TEACHER_PENDING: pending,
        ROLE_PROFESSOR: professor,
        ROLE_PROFESSOR_FEMALE: professor_female,
    }

    async def fake_assignment(**_kwargs):
        raise TeacherAssignmentError("❌ Filière ou matière invalide.")

    response = _FakeInteractionResponse()
    edit_original_response = AsyncMock()
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=101),
        response=response,
        edit_original_response=edit_original_response,
    )

    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_managed_role", lambda _guild, name: role_map.get(name))
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda _guild_id, _discord_id: None)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_build_lock", lambda _guild_id: _AsyncLock())
    monkeypatch.setattr("cogs.teacher_qr_onboarding.execute_teacher_assignment", fake_assignment)

    view = TeacherOnboardingView(bot, 1, 101)
    view.selected_gender = "male"
    view.selected_level = "2ème Année Bac"
    view.selected_stream = "2ème Année Bac Sciences Physiques"
    view.selected_sections = [3]
    view.selected_subjects = ["Mathématiques"]
    view._show_subject_stage()
    view._refresh_control_state()

    await view.confirm_button.callback(interaction)

    message = edit_original_response.await_args.kwargs["content"]
    assert "Filière ou matière invalide" in message
    assert view.submitting is False
    assert view.confirm_button.disabled is False


@pytest.mark.asyncio
async def test_teacher_onboarding_confirm_unexpected_error_is_visible(monkeypatch, capsys):
    from cogs.teacher_qr_onboarding import TeacherOnboardingView
    from services.permissions import ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, ROLE_TEACHER_PENDING

    pending = object()
    professor = object()
    professor_female = object()
    member = SimpleNamespace(
        id=101,
        display_name="Teacher",
        roles=[pending],
        add_roles=AsyncMock(),
        remove_roles=AsyncMock(),
        guild=None,
    )
    guild = SimpleNamespace(id=1, get_member=lambda _user_id: member)
    member.guild = guild
    bot = SimpleNamespace(get_guild=lambda _guild_id: guild)
    role_map = {
        ROLE_TEACHER_PENDING: pending,
        ROLE_PROFESSOR: professor,
        ROLE_PROFESSOR_FEMALE: professor_female,
    }

    async def fake_assignment(**_kwargs):
        raise RuntimeError("unexpected test failure")

    response = _FakeInteractionResponse()
    edit_original_response = AsyncMock()
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=101),
        response=response,
        edit_original_response=edit_original_response,
    )

    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_managed_role", lambda _guild, name: role_map.get(name))
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda _guild_id, _discord_id: None)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_build_lock", lambda _guild_id: _AsyncLock())
    monkeypatch.setattr("cogs.teacher_qr_onboarding.execute_teacher_assignment", fake_assignment)

    view = TeacherOnboardingView(bot, 1, 101)
    view.selected_gender = "male"
    view.selected_level = "2ème Année Bac"
    view.selected_stream = "2ème Année Bac Sciences Physiques"
    view.selected_sections = [3]
    view.selected_subjects = ["Mathématiques"]
    view._show_subject_stage()
    view._refresh_control_state()

    await view.confirm_button.callback(interaction)

    assert "Erreur technique" in edit_original_response.await_args.kwargs["content"]
    assert "RuntimeError" in capsys.readouterr().out
    assert view.submitting is False


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


@pytest.mark.asyncio
async def test_start_teacher_registration_restores_existing_registration_without_reenrollment(monkeypatch):
    from unittest.mock import AsyncMock
    from cogs.teacher_qr_onboarding import TeacherQROnboarding

    guild = SimpleNamespace(id=1)
    registration = {
        "profile_type": "male",
        "level_name": "2BAC",
        "stream_name": "2BACPC",
        "subjects_text": "Mathématiques",
    }
    teacher = SimpleNamespace(
        id=203,
        display_name="Already registered",
        mention="<@203>",
        bot=False,
        guild=guild,
        roles=[],
        send=AsyncMock(),
    )
    actor = SimpleNamespace(id=900, display_name="School admin")
    interaction = SimpleNamespace(
        guild=guild,
        user=actor,
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), is_done=lambda: False),
        followup=SimpleNamespace(send=AsyncMock()),
        command=SimpleNamespace(name="startteacherregistration"),
    )
    restore = AsyncMock(return_value=(True, "Les rôles professeur ont été rétablis."))
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda _guild_id, _discord_id: registration)
    monkeypatch.setattr("cogs.teacher_qr_onboarding._restore_registered_teacher_access", restore)
    sync_threads = AsyncMock(return_value="✅ Threads professeur prêts : 1 (nouveaux : 1, existants : 0).")
    monkeypatch.setattr("cogs.teacher_qr_onboarding._sync_registered_teacher_threads", sync_threads)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.record_event", lambda *_args, **_kwargs: None)

    cog = TeacherQROnboarding(SimpleNamespace())
    await cog.start_teacher_registration.callback(cog, interaction, teacher)

    restore.assert_awaited_once_with(teacher, registration)
    sync_threads.assert_awaited_once_with(guild, teacher.id)
    teacher.send.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
    assert "les rôles professeur ont été rétablis" in interaction.followup.send.await_args.args[0].lower()
    assert "threads professeur prêts" in interaction.followup.send.await_args.args[0].lower()
    assert "formulaire d'inscription" not in interaction.followup.send.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_start_teacher_registration_sends_prompt_to_existing_member_without_rejoin(monkeypatch):
    from unittest.mock import AsyncMock
    from cogs.teacher_qr_onboarding import TeacherQROnboarding
    from services.permissions import ROLE_TEACHER_PENDING

    pending = SimpleNamespace(id=10, name=ROLE_TEACHER_PENDING, managed=False)
    guild = SimpleNamespace(id=1)
    teacher = SimpleNamespace(
        id=202,
        display_name="Existing member",
        mention="<@202>",
        bot=False,
        guild=guild,
        roles=[],
        add_roles=AsyncMock(),
        send=AsyncMock(),
    )
    actor = SimpleNamespace(id=900, display_name="School admin")
    interaction = SimpleNamespace(
        guild=guild,
        user=actor,
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), is_done=lambda: False),
        followup=SimpleNamespace(send=AsyncMock()),
        command=SimpleNamespace(name="startteacherregistration"),
    )
    prompt = AsyncMock(return_value=True)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda _guild_id, _discord_id: None)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_managed_role", lambda _guild, name: pending if name == ROLE_TEACHER_PENDING else None)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.teacher_target_conflict", lambda _member, _guild: None)
    monkeypatch.setattr("cogs.teacher_qr_onboarding._send_teacher_onboarding_prompt", prompt)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.record_event", lambda *_args, **_kwargs: None)

    cog = TeacherQROnboarding(SimpleNamespace())
    await cog.start_teacher_registration.callback(cog, interaction, teacher)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    teacher.add_roles.assert_awaited_once_with(
        pending,
        reason="School Manager administrator-triggered teacher onboarding",
    )
    prompt.assert_awaited_once_with(cog.bot, teacher)
    interaction.followup.send.assert_awaited_once()
    assert "Aucun départ/réintégration" in interaction.followup.send.await_args.args[0]


def test_teacher_assignments_store_multiple_sections_and_preserve_thread_state(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    storage.initialize_database()
    storage.record_teacher_registration(1, 101, "Prof Multi", "male", "2ème Année Bac", "2ème Année Bac Sciences Physiques", "Mathématiques")
    assignments = [
        {"level": "2ème Année Bac", "stream": "2ème Année Bac Sciences Physiques", "section": 3, "subjects": ["Mathématiques"]},
        {"level": "2ème Année Bac", "stream": "2ème Année Bac Sciences Physiques", "section": 4, "subjects": ["Mathématiques"]},
        {"level": "1ère Année Bac", "stream": "1ère Année Bac Sciences Expérimentales", "section": 2, "subjects": ["Sciences de la Vie et de la Terre (SVT)"]},
    ]
    assert storage.record_teacher_assignments(1, 101, assignments) == 3
    rows = storage.get_teacher_assignments(1, 101)
    assert len(rows) == 3
    assert {(row["level_name"], row["section"], row["subject_name"]) for row in rows} == {
        ("2ème Année Bac", 3, "Mathématiques"),
        ("2ème Année Bac", 4, "Mathématiques"),
        ("1ère Année Bac", 2, "Sciences de la Vie et de la Terre (SVT)"),
    }
    row = next(row for row in rows if row["section"] == 3)
    storage.update_teacher_assignment_thread(1, row["id"], status="created", thread_id=9001)
    assert storage.record_teacher_assignments(1, 101, assignments) == 0
    created = next(row for row in storage.get_teacher_assignments(1, 101) if row["section"] == 3)
    assert created["thread_status"] == "created"
    assert created["thread_id"] == 9001


def test_teacher_assignments_reject_invalid_section(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    storage.initialize_database()
    storage.record_teacher_registration(1, 101, "Prof A", "male", "2ème Année Bac", "2ème Année Bac Sciences Physiques", "Mathématiques")
    with pytest.raises(ValueError, match="section"):
        storage.record_teacher_assignments(1, 101, [{"level": "2ème Année Bac", "stream": "2ème Année Bac Sciences Physiques", "section": 9, "subjects": ["Mathématiques"]}])


def test_reset_guild_data_clears_teacher_assignments(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    storage.initialize_database()
    storage.record_teacher_registration(1, 101, "Prof A", "male", "2ème Année Bac", "2ème Année Bac Sciences Physiques", "Mathématiques")
    storage.record_teacher_assignments(1, 101, [{"level": "2ème Année Bac", "stream": "2ème Année Bac Sciences Physiques", "section": 3, "subjects": ["Mathématiques"]}])
    storage.reset_guild_data(1)
    assert storage.get_teacher_assignments(1, 101) == []


@pytest.mark.asyncio
async def test_teacher_onboarding_collects_multiple_levels_and_sections(monkeypatch):
    from cogs.teacher_qr_onboarding import TeacherOnboardingView
    from config.curriculum import get_stream_subjects, get_streams
    from services.permissions import ROLE_TEACHER_PENDING

    pending = object()
    member = SimpleNamespace(id=101, roles=[pending], display_name="Prof Multi", guild=None)
    guild = SimpleNamespace(id=1)
    member.guild = guild
    bot = SimpleNamespace()
    captured = {}

    async def fake_assignment(**kwargs):
        captured.update(kwargs)
        return {
            "stream_code": "2BACPC",
            "subject_names": "Math",
            "assignments_count": 3,
            "assignment_summary": "2BACPC-S3: Math; 2BACPC-S4: Math; 1BACSE-S2: SVT",
        }

    response = _FakeInteractionResponse()
    edit_original_response = AsyncMock()
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=101),
        response=response,
        edit_original_response=edit_original_response,
    )

    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_managed_role", lambda _guild, name: pending if name == ROLE_TEACHER_PENDING else None)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda *_args: None)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_build_lock", lambda _guild_id: _AsyncLock())
    monkeypatch.setattr("cogs.teacher_qr_onboarding.execute_teacher_assignment", fake_assignment)
    monkeypatch.setattr(
        "cogs.teacher_qr_onboarding.ensure_teacher_threads",
        AsyncMock(return_value={"created": 3, "existing": 0, "failed": 0, "errors": []}),
    )

    view = TeacherOnboardingView(bot, 1, 101)
    view._ensure_eligible = AsyncMock(return_value=member)

    view.gender_select._values = ["male"]
    await view.gender_select.callback(interaction)

    level_a = "2ème Année Bac"
    stream_a = "2ème Année Bac Sciences Physiques"
    view.level_select._values = [level_a]
    await view.level_select.callback(interaction)
    view.stream_select._values = [stream_a]
    await view.stream_select.callback(interaction)
    view.section_select._values = ["3", "4"]
    await view.section_select.callback(interaction)
    await view.continue_button.callback(interaction)
    assert "Mathématiques" in get_stream_subjects(level_a, stream_a)
    view.subject_select._values = ["Mathématiques"]
    await view.subject_select.callback(interaction)
    await view.add_assignment_button.callback(interaction)

    level_b = "1ère Année Bac"
    stream_b = "1ère Année Bac Sciences Expérimentales"
    view.level_select._values = [level_b]
    await view.level_select.callback(interaction)
    view.stream_select._values = [stream_b]
    await view.stream_select.callback(interaction)
    view.section_select._values = ["2"]
    await view.section_select.callback(interaction)
    await view.continue_button.callback(interaction)
    subject_b = "Sciences de la Vie et de la Terre (SVT)"
    assert subject_b in get_stream_subjects(level_b, stream_b)
    view.subject_select._values = [subject_b]
    await view.subject_select.callback(interaction)
    await view.confirm_button.callback(interaction)

    assert captured["assignments"] == [
        {
            "level": level_a,
            "stream": stream_a,
            "section": 3,
            "subjects": ["Mathématiques"],
        },
        {
            "level": level_a,
            "stream": stream_a,
            "section": 4,
            "subjects": ["Mathématiques"],
        },
        {
            "level": level_b,
            "stream": stream_b,
            "section": 2,
            "subjects": [subject_b],
        },
    ]
    assert captured["self_registration"] is True
    assert "Inscription terminée" in edit_original_response.await_args.kwargs["content"]

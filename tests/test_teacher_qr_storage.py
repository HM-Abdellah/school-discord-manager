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



def test_active_sections_follow_active_academic_year_student_import(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    level = "2ème Année Bac"
    stream = "2ème Année Bac Sciences Physiques"
    config = {"levels": [{"name": level, "streams": [{"name": stream, "abbreviation": "2BACPC"}]}]}

    storage.create_and_activate_academic_year(1, "2025/2026", config)
    old_year = storage.get_active_academic_year(1)
    storage.enroll_student_record(1, 1001, "Old year student", int(old_year["id"]), level, stream, section=1)

    storage.create_and_activate_academic_year(1, "2026/2027", config)
    current_year = storage.get_active_academic_year(1)
    storage.enroll_student_record(1, 1002, "Section three student", int(current_year["id"]), level, stream, section=3)
    storage.enroll_student_record(1, 1003, "Section four student", int(current_year["id"]), level, stream, section=4)
    storage.enroll_student_record(1, 1004, "Second section four student", int(current_year["id"]), level, stream, section=4)

    assert storage.get_active_sections_for_stream(1, level, stream) == [3, 4]
    assert storage.get_active_sections_for_stream(1, level, "1ère Année Bac Sciences Expérimentales") == []



def test_teacher_sections_use_registered_live_class_roles_not_student_counts(monkeypatch):
    from cogs.teacher_qr_onboarding import _get_existing_class_sections
    from config.curriculum import get_stream_abbreviation

    level = "2ème Année Bac"
    stream = "2ème Année Bac Sciences Physiques"
    code = get_stream_abbreviation(level, stream)

    class Role:
        def __init__(self, role_id, name, managed=False):
            self.id = role_id
            self.name = name
            self.managed = managed

    roles = {
        3003: Role(3003, f"Élèves - {code}-3"),
        3004: Role(3004, f"Élèves - {code}-4"),
        3005: Role(3005, f"Wrong name for {code}-5"),
        3006: Role(3006, f"Élèves - {code}-6", managed=True),
    }
    guild = SimpleNamespace(id=1, get_role=lambda role_id: roles.get(role_id))
    config = {"class_roles": {
        f"{code}-3": {"role_id": 3003, "level_name": level, "stream_name": stream, "stream_code": code, "section": 3},
        f"{code}-4": {"role_id": 3004, "level_name": level, "stream_name": stream, "stream_code": code, "section": 4},
        f"{code}-5": {"role_id": 3005, "level_name": level, "stream_name": stream, "stream_code": code, "section": 5},
        f"{code}-6": {"role_id": 3006, "level_name": level, "stream_name": stream, "stream_code": code, "section": 6},
        f"{code}-7": {"role_id": 3007, "level_name": level, "stream_name": stream, "stream_code": code, "section": 7},
        f"{code}-8": {"role_id": 3008, "level_name": "1ère Année Bac", "stream_name": stream, "stream_code": code, "section": 8},
    }}
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_guild_config", lambda _guild_id: config)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_active_sections_for_stream", lambda *_args: [])

    # With no active enrollments, registered live class roles still define the choices.
    assert _get_existing_class_sections(guild, level, stream) == [3, 4]


def test_teacher_sections_include_active_enrollments_when_class_roles_are_not_registered(monkeypatch):
    from cogs.teacher_qr_onboarding import _get_existing_class_sections

    guild = SimpleNamespace(id=1, get_role=lambda _role_id: None)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_guild_config", lambda _guild_id: {"class_roles": {}})
    monkeypatch.setattr(
        "cogs.teacher_qr_onboarding.get_active_sections_for_stream",
        lambda _guild_id, _level, _stream: [2, 4, 9, "3"],
    )

    assert _get_existing_class_sections(
        guild, "2ème Année Bac", "2ème Année Bac Sciences Physiques"
    ) == [2, 4]


def test_teacher_sections_combine_live_class_roles_and_active_enrollments(monkeypatch):
    from cogs.teacher_qr_onboarding import _get_existing_class_sections
    from config.curriculum import get_stream_abbreviation

    level = "2ème Année Bac"
    stream = "2ème Année Bac Sciences Physiques"
    code = get_stream_abbreviation(level, stream)
    role = SimpleNamespace(id=3003, name=f"Élèves - {code}-3", managed=False)
    guild = SimpleNamespace(id=1, get_role=lambda role_id: role if role_id == role.id else None)
    monkeypatch.setattr(
        "cogs.teacher_qr_onboarding.get_guild_config",
        lambda _guild_id: {"class_roles": {
            f"{code}-3": {
                "role_id": role.id, "level_name": level, "stream_name": stream,
                "stream_code": code, "section": 3,
            },
        }},
    )
    monkeypatch.setattr(
        "cogs.teacher_qr_onboarding.get_active_sections_for_stream",
        lambda *_args: [2, 3, 5],
    )

    assert _get_existing_class_sections(guild, level, stream) == [2, 3, 5]


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
async def test_prepare_teacher_reenrollment_removes_old_roles_and_deletes_old_record(monkeypatch):
    from cogs.teacher_qr_onboarding import _prepare_teacher_reenrollment
    from config.curriculum import get_stream_abbreviation, get_subject_display_name
    from services.permissions import (
        ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, ROLE_TEACHER_PENDING,
        SUBJECT_ROLE_PREFIX, STREAM_ROLE_PREFIX,
    )

    level = "2ème Année Bac"
    stream = "2ème Année Bac Sciences Physiques"
    professor = SimpleNamespace(id=11, name=ROLE_PROFESSOR)
    professor_female = SimpleNamespace(id=12, name=ROLE_PROFESSOR_FEMALE)
    pending = SimpleNamespace(id=13, name=ROLE_TEACHER_PENDING)
    stream_role = SimpleNamespace(id=14, name=f"{STREAM_ROLE_PREFIX}{get_stream_abbreviation(level, stream)}")
    subject_role = SimpleNamespace(id=15, name=f"{SUBJECT_ROLE_PREFIX}{get_subject_display_name('Mathématiques')}")
    guild = SimpleNamespace(id=1)
    member = SimpleNamespace(
        id=101, display_name="Prof A", guild=guild,
        roles=[professor, stream_role, subject_role],
        add_roles=AsyncMock(), remove_roles=AsyncMock(),
    )
    registration = {
        "profile_type": "male", "level_name": level, "stream_name": stream,
        "subjects_text": "Mathématiques",
    }
    assignments = [{
        "id": 1, "guild_id": 1, "discord_id": 101,
        "level_name": level, "stream_name": stream, "subject_name": "Mathématiques",
        "thread_id": 9001,
    }]
    role_map = {
        ROLE_PROFESSOR: professor, ROLE_PROFESSOR_FEMALE: professor_female,
        ROLE_TEACHER_PENDING: pending, stream_role.name: stream_role, subject_role.name: subject_role,
    }
    delete_registration = Mock()
    archive = AsyncMock(return_value=(1, 0))
    bot = SimpleNamespace()
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_build_lock", lambda _guild_id: _AsyncLock())
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda *_args: registration)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_assignments", lambda *_args: assignments)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_guild_config", lambda *_args: {})
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_managed_role", lambda _guild, name: role_map.get(name))
    monkeypatch.setattr("cogs.teacher_qr_onboarding.delete_teacher_registration", delete_registration)
    monkeypatch.setattr("cogs.teacher_qr_onboarding._archive_teacher_assignment_threads", archive)

    result = await _prepare_teacher_reenrollment(bot, member)

    member.add_roles.assert_awaited_once_with(pending, reason="School Manager teacher re-enrollment")
    member.remove_roles.assert_awaited_once()
    assert {role.id for role in member.remove_roles.await_args.args} == {professor.id, stream_role.id, subject_role.id}
    delete_registration.assert_called_once_with(1, 101)
    archive.assert_awaited_once_with(bot, guild, 101, assignments)
    assert result == {
        "had_registration": True, "assignments": 1, "roles_removed": 3,
        "threads_archived": 1, "thread_failures": 0,
    }


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
async def test_teacher_qr_join_restarts_stale_registration_even_with_pending_role(monkeypatch):
    from cogs.teacher_qr_onboarding import TeacherQROnboarding
    from services.permissions import ROLE_TEACHER_PENDING

    pending = SimpleNamespace(id=10, name=ROLE_TEACHER_PENDING, managed=False)
    guild = SimpleNamespace(id=1)
    registration = {"discord_id": 101, "full_name": "Old Teacher Name"}
    member = SimpleNamespace(
        bot=False, id=101, display_name="Teacher rejoining with QR", guild=guild,
        roles=[pending], send=AsyncMock(),
    )
    summary = {
        "had_registration": True, "assignments": 1, "roles_removed": 3,
        "threads_archived": 1, "thread_failures": 0,
    }
    prepare = AsyncMock(return_value=summary)
    prompt = AsyncMock(return_value=True)
    restore = AsyncMock()
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_managed_role", lambda _guild, _name: pending)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda *_args: registration)
    monkeypatch.setattr("cogs.teacher_qr_onboarding._prepare_teacher_reenrollment", prepare)
    monkeypatch.setattr("cogs.teacher_qr_onboarding._restore_registered_teacher_access", restore)
    monkeypatch.setattr("cogs.teacher_qr_onboarding._send_teacher_onboarding_prompt", prompt)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.record_event", lambda *_args, **_kwargs: None)

    bot = SimpleNamespace()
    await TeacherQROnboarding(bot).on_member_join(member)

    prepare.assert_awaited_once_with(bot, member)
    restore.assert_not_awaited()
    prompt.assert_awaited_once_with(bot, member)


@pytest.mark.asyncio
async def test_teacher_qr_join_restarts_stale_registration_without_pending_role(monkeypatch):
    from cogs.teacher_qr_onboarding import TeacherQROnboarding
    from services.permissions import ROLE_TEACHER_PENDING

    pending = SimpleNamespace(id=10, name=ROLE_TEACHER_PENDING, managed=False)
    guild = SimpleNamespace(id=1)
    registration = {"discord_id": 102, "full_name": "Prof B"}
    member = SimpleNamespace(
        bot=False, id=102, display_name="Prof B", guild=guild,
        roles=[], send=AsyncMock(),
    )
    summary = {
        "had_registration": True, "assignments": 0, "roles_removed": 0,
        "threads_archived": 0, "thread_failures": 0,
    }
    prepare = AsyncMock(return_value=summary)
    prompt = AsyncMock(return_value=True)
    restore = AsyncMock()
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_managed_role", lambda _guild, _name: pending)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda *_args: registration)
    monkeypatch.setattr("cogs.teacher_qr_onboarding._prepare_teacher_reenrollment", prepare)
    monkeypatch.setattr("cogs.teacher_qr_onboarding._restore_registered_teacher_access", restore)
    monkeypatch.setattr("cogs.teacher_qr_onboarding._send_teacher_onboarding_prompt", prompt)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.record_event", lambda *_args, **_kwargs: None)

    bot = SimpleNamespace()
    await TeacherQROnboarding(bot).on_member_join(member)

    prepare.assert_awaited_once_with(bot, member)
    restore.assert_not_awaited()
    prompt.assert_awaited_once_with(bot, member)




@pytest.mark.asyncio
async def test_teacher_onboarding_refreshes_cached_roles_before_rejecting_reenrollment(monkeypatch):
    from cogs.teacher_qr_onboarding import TeacherOnboardingView
    from services.permissions import ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, ROLE_TEACHER_PENDING

    pending = Mock(id=10, name=ROLE_TEACHER_PENDING)
    professor = Mock(id=11, name=ROLE_PROFESSOR)
    professor_female = Mock(id=12, name=ROLE_PROFESSOR_FEMALE)
    cached_member = SimpleNamespace(id=101, roles=[professor])
    refreshed_member = SimpleNamespace(id=101, roles=[pending])
    guild = SimpleNamespace(
        id=1,
        get_member=lambda _user_id: cached_member,
        fetch_member=AsyncMock(return_value=refreshed_member),
    )
    bot = SimpleNamespace(get_guild=lambda _guild_id: guild)
    monkeypatch.setattr(
        "cogs.teacher_qr_onboarding.get_managed_role",
        lambda _guild, name: {
            ROLE_TEACHER_PENDING: pending,
            ROLE_PROFESSOR: professor,
            ROLE_PROFESSOR_FEMALE: professor_female,
        }.get(name),
    )
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda *_args: None)

    view = TeacherOnboardingView(bot, 1, 101)
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=101),
        response=SimpleNamespace(send_message=AsyncMock(), is_done=lambda: False),
    )

    result = await view._ensure_eligible(interaction)

    assert result is refreshed_member
    guild.fetch_member.assert_awaited_once_with(101)
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_teacher_full_name_modal_normalizes_and_saves_submitted_name():
    from cogs.teacher_qr_onboarding import TeacherFullNameModal, TeacherOnboardingView

    view = TeacherOnboardingView(SimpleNamespace(), 1, 101)
    response = SimpleNamespace(send_message=AsyncMock())
    interaction = SimpleNamespace(user=SimpleNamespace(id=101), response=response)
    modal = TeacherFullNameModal(view)
    modal.name_input._value = "  Abdellah   Hamdan  "

    await modal.on_submit(interaction)

    assert view.full_name == "Abdellah Hamdan"
    assert response.send_message.await_args.kwargs["ephemeral"] is True
    assert "Abdellah Hamdan" in response.send_message.await_args.args[0]
    assert view.continue_button.disabled is True  # Class selections are still required.


@pytest.mark.asyncio
async def test_teacher_onboarding_view_uses_fixed_choices():
    from cogs.teacher_qr_onboarding import TeacherOnboardingView
    from config.curriculum import get_levels

    view = TeacherOnboardingView(SimpleNamespace(), 1, 101)

    assert len(view.children) == 7
    assert view.name_button.label == "Saisir mon nom complet"
    assert view.reset_button.label == "Recommencer"
    assert view.back_button.label == "Retour à la classe"
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
        self.send_message = AsyncMock()
        self.defer = AsyncMock()
        self.done = True

    def is_done(self):
        return self.done


@pytest.mark.asyncio
async def test_teacher_onboarding_reset_clears_all_unsubmitted_choices(monkeypatch):
    from cogs.teacher_qr_onboarding import TeacherOnboardingView

    view = TeacherOnboardingView(SimpleNamespace(), 1, 101)
    view._ensure_eligible = AsyncMock(return_value=SimpleNamespace(id=101))
    view.selected_gender = "male"
    view.selected_level = "2ème Année Bac"
    view.selected_stream = "2ème Année Bac Sciences Physiques"
    view.selected_sections = [3]
    view.available_sections = [3, 4]
    view.selected_subjects = ["Mathématiques"]
    view.assignments = [{"level": view.selected_level, "stream": view.selected_stream, "section": 3, "subjects": ["Mathématiques"]}]
    view._show_subject_stage()
    interaction = SimpleNamespace(user=SimpleNamespace(id=101), response=_FakeInteractionResponse())

    await view.reset_button.callback(interaction)

    assert view.stage == "class"
    assert view.selected_gender is None
    assert view.selected_level is None
    assert view.selected_stream is None
    assert view.selected_sections == []
    assert view.available_sections == []
    assert view.selected_subjects == []
    assert view.assignments == []
    assert "Formulaire réinitialisé" in interaction.response.edit_message.await_args.kwargs["content"]


@pytest.mark.asyncio
async def test_teacher_onboarding_back_button_returns_to_class_without_losing_section(monkeypatch):
    from cogs.teacher_qr_onboarding import TeacherOnboardingView

    view = TeacherOnboardingView(SimpleNamespace(), 1, 101)
    view._ensure_eligible = AsyncMock(return_value=SimpleNamespace(id=101))
    view.selected_gender = "male"
    view.selected_level = "2ème Année Bac"
    view.selected_stream = "2ème Année Bac Sciences Physiques"
    view.selected_sections = [3]
    view.available_sections = [3, 4]
    view.selected_subjects = ["Mathématiques"]
    view._show_subject_stage()
    interaction = SimpleNamespace(user=SimpleNamespace(id=101), response=_FakeInteractionResponse())

    await view.back_button.callback(interaction)

    assert view.stage == "class"
    assert view.selected_sections == [3]
    assert view.available_sections == [3, 4]
    assert view.selected_subjects == []


@pytest.mark.asyncio
async def test_teacher_onboarding_caps_subjects_at_two_across_entire_registration(monkeypatch):
    from cogs.teacher_qr_onboarding import TeacherOnboardingView
    from config.curriculum import get_stream_subjects

    view = TeacherOnboardingView(SimpleNamespace(), 1, 101)
    view._ensure_eligible = AsyncMock(return_value=SimpleNamespace(id=101))
    view.selected_level = "2ème Année Bac"
    view.selected_stream = "2ème Année Bac Sciences Physiques"
    other_subjects = [
        subject for subject in get_stream_subjects(view.selected_level, view.selected_stream)
        if subject != "Mathématiques"
    ][:2]
    assert len(other_subjects) == 2
    view.assignments = [{
        "level": view.selected_level,
        "stream": view.selected_stream,
        "section": 3,
        "subjects": ["Mathématiques"],
    }]
    view.subject_select.options = [
        discord.SelectOption(label=subject, value=subject)
        for subject in ["Mathématiques", *other_subjects]
    ]
    view.subject_select._values = other_subjects
    view._show_subject_stage()
    interaction = SimpleNamespace(user=SimpleNamespace(id=101), response=_FakeInteractionResponse())

    await view.subject_select.callback(interaction)

    assert "deux matières au total" in interaction.response.send_message.await_args.args[0]
    assert view.selected_subjects == []


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
    monkeypatch.setattr("cogs.teacher_qr_onboarding._get_existing_class_sections", lambda *_args: [3])
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_build_lock", lambda _guild_id: _AsyncLock())
    monkeypatch.setattr("cogs.teacher_qr_onboarding.execute_teacher_assignment", fake_assignment)
    monkeypatch.setattr(
        "cogs.teacher_qr_onboarding.ensure_teacher_threads",
        AsyncMock(return_value={"created": 1, "existing": 0, "failed": 0, "errors": []}),
    )

    view = TeacherOnboardingView(bot, 1, 101)
    view.full_name = "Nom Test"
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
    monkeypatch.setattr("cogs.teacher_qr_onboarding._get_existing_class_sections", lambda *_args: [3])
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_build_lock", lambda _guild_id: _AsyncLock())
    monkeypatch.setattr("cogs.teacher_qr_onboarding.execute_teacher_assignment", fake_assignment)

    view = TeacherOnboardingView(bot, 1, 101)
    view.full_name = "Nom Test"
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
    assert view.reset_button.disabled is False


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
    monkeypatch.setattr("cogs.teacher_qr_onboarding._get_existing_class_sections", lambda *_args: [3])
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_build_lock", lambda _guild_id: _AsyncLock())
    monkeypatch.setattr("cogs.teacher_qr_onboarding.execute_teacher_assignment", fake_assignment)

    view = TeacherOnboardingView(bot, 1, 101)
    view.full_name = "Nom Test"
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
async def test_start_teacher_registration_restart_resets_existing_profile_without_leave(monkeypatch):
    from cogs.teacher_qr_onboarding import TeacherQROnboarding

    guild = SimpleNamespace(id=1)
    registration = {"profile_type": "male", "level_name": "2BAC", "stream_name": "2BACPC", "subjects_text": "Mathématiques"}
    teacher = SimpleNamespace(
        id=205, display_name="Teacher already in server", mention="<@205>",
        bot=False, guild=guild, roles=[], add_roles=AsyncMock(), send=AsyncMock(),
    )
    actor = SimpleNamespace(id=900, display_name="School admin")
    interaction = SimpleNamespace(
        guild=guild, user=actor,
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), is_done=lambda: False),
        followup=SimpleNamespace(send=AsyncMock()),
        command=SimpleNamespace(name="startteacherregistration"),
    )
    summary = {"had_registration": True, "assignments": 2, "roles_removed": 4, "threads_archived": 2, "thread_failures": 0}
    prepare = AsyncMock(return_value=summary)
    prompt = AsyncMock(return_value=True)
    restore = AsyncMock()
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda *_args: registration)
    monkeypatch.setattr("cogs.teacher_qr_onboarding._prepare_teacher_reenrollment", prepare)
    monkeypatch.setattr("cogs.teacher_qr_onboarding._send_teacher_onboarding_prompt", prompt)
    monkeypatch.setattr("cogs.teacher_qr_onboarding._restore_registered_teacher_access", restore)
    monkeypatch.setattr("cogs.teacher_qr_onboarding.record_event", lambda *_args, **_kwargs: None)

    bot = SimpleNamespace()
    cog = TeacherQROnboarding(bot)
    await cog.start_teacher_registration.callback(cog, interaction, teacher, True)

    prepare.assert_awaited_once_with(bot, teacher)
    prompt.assert_awaited_once_with(bot, teacher)
    restore.assert_not_awaited()
    interaction.followup.send.assert_awaited_once()
    message = interaction.followup.send.await_args.args[0]
    assert "Aucun départ du serveur" in message
    assert "Anciennes affectations supprimées : 2" in message


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


@pytest.mark.asyncio
async def test_new_teacher_form_replaces_and_disables_previous_form():
    from cogs.teacher_qr_onboarding import _send_teacher_onboarding_prompt

    guild = SimpleNamespace(id=1)
    first_message = SimpleNamespace(edit=AsyncMock())
    second_message = SimpleNamespace(edit=AsyncMock())
    member = SimpleNamespace(
        id=101,
        guild=guild,
        send=AsyncMock(side_effect=[first_message, second_message]),
    )
    bot = SimpleNamespace()

    assert await _send_teacher_onboarding_prompt(bot, member) is True
    first_view = bot._teacher_onboarding_views[(1, 101)]
    assert await _send_teacher_onboarding_prompt(bot, member) is True
    second_view = bot._teacher_onboarding_views[(1, 101)]

    assert second_view is not first_view
    assert all(item.disabled for item in first_view.children)
    first_message.edit.assert_awaited_once()
    assert "remplacé" in first_message.edit.await_args.kwargs["content"]


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
async def test_teacher_onboarding_rejects_section_not_in_active_import(monkeypatch):
    from cogs.teacher_qr_onboarding import TeacherOnboardingView

    view = TeacherOnboardingView(SimpleNamespace(), 1, 101)
    view._ensure_eligible = AsyncMock(return_value=SimpleNamespace(id=101))
    view.selected_level = "2ème Année Bac"
    view.selected_stream = "2ème Année Bac Sciences Physiques"
    view.available_sections = [3, 4]
    view.section_select._values = ["8"]
    response = _FakeInteractionResponse()
    interaction = SimpleNamespace(user=SimpleNamespace(id=101), response=response)

    await view.section_select.callback(interaction)

    assert view.selected_sections == []
    assert "sections réellement disponibles" in response.send_message.await_args.args[0]


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
    monkeypatch.setattr(
        "cogs.teacher_qr_onboarding._get_existing_class_sections",
        lambda _guild_id, _level, stream: [3, 4] if stream == "2ème Année Bac Sciences Physiques" else [2],
    )
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_build_lock", lambda _guild_id: _AsyncLock())
    monkeypatch.setattr("cogs.teacher_qr_onboarding.execute_teacher_assignment", fake_assignment)
    monkeypatch.setattr(
        "cogs.teacher_qr_onboarding.ensure_teacher_threads",
        AsyncMock(return_value={"created": 3, "existing": 0, "failed": 0, "errors": []}),
    )

    view = TeacherOnboardingView(bot, 1, 101)
    view._ensure_eligible = AsyncMock(return_value=member)
    view.full_name = "Nom Complet Test"

    view.gender_select._values = ["male"]
    await view.gender_select.callback(interaction)

    level_a = "2ème Année Bac"
    stream_a = "2ème Année Bac Sciences Physiques"
    view.level_select._values = [level_a]
    await view.level_select.callback(interaction)
    view.stream_select._values = [stream_a]
    await view.stream_select.callback(interaction)
    assert [option.value for option in view.section_select.options] == ["3", "4"]
    assert view.section_select.max_values == 2
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
    assert captured["full_name"] == "Nom Complet Test"
    assert "Inscription terminée" in edit_original_response.await_args.kwargs["content"]


@pytest.mark.asyncio
async def test_registered_teacher_thread_sync_includes_exact_failure_details(monkeypatch):
    from contextlib import asynccontextmanager
    from cogs.teacher_qr_onboarding import _sync_registered_teacher_threads

    @asynccontextmanager
    async def no_lock(_guild_id):
        yield

    failure = "2BACPC / Math: RuntimeError: Salon de matière géré introuvable : 📚-2BACPC・math"
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_build_lock", no_lock)
    monkeypatch.setattr(
        "cogs.teacher_qr_onboarding.ensure_teacher_threads",
        AsyncMock(return_value={"created": 0, "existing": 0, "failed": 1, "errors": [failure]}),
    )

    message = await _sync_registered_teacher_threads(SimpleNamespace(id=1), 203)

    assert "1 thread(s) n'ont pas pu être préparés" in message
    assert "Détails techniques" in message
    assert "2BACPC" in message
    assert "introuvable" in message


@pytest.mark.asyncio
async def test_existing_teacher_repair_dm_warns_when_threads_failed(monkeypatch):
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
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=900, display_name="School admin"),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), is_done=lambda: False),
        followup=SimpleNamespace(send=AsyncMock()),
        command=SimpleNamespace(name="startteacherregistration"),
    )
    monkeypatch.setattr("cogs.teacher_qr_onboarding.get_teacher_registration", lambda *_args: registration)
    monkeypatch.setattr(
        "cogs.teacher_qr_onboarding._restore_registered_teacher_access",
        AsyncMock(return_value=(True, "Les rôles professeur ont été rétablis.")),
    )
    detail = "⚠️ 1 thread(s) n'ont pas pu être préparés.\n**Détails techniques :**\n• Math: canal introuvable"
    monkeypatch.setattr("cogs.teacher_qr_onboarding._sync_registered_teacher_threads", AsyncMock(return_value=detail))
    monkeypatch.setattr("cogs.teacher_qr_onboarding.record_event", lambda *_args, **_kwargs: None)

    cog = TeacherQROnboarding(SimpleNamespace())
    await cog.start_teacher_registration.callback(cog, interaction, teacher)

    dm_message = teacher.send.await_args.args[0]
    admin_message = interaction.followup.send.await_args.args[0]
    assert "l'administration a été informée" in dm_message
    assert "préparation de votre espace de matière" in dm_message
    assert "Détails techniques" in admin_message
    assert "canal introuvable" in admin_message

from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from cogs.removestream_fix import _recorded_id, _stream_channel_names, _stream_role_names
from cogs.server_v3 import _valid_academic_year
from services.role_conflicts import student_staff_conflict, student_transfer_conflict, teacher_target_conflict


def test_stream_destructive_scope_builds_only_canonical_managed_names(monkeypatch):
    monkeypatch.setattr(
        "cogs.removestream_fix.get_stream_subjects",
        lambda _level, _stream: ["Mathématiques", "Physique et Chimie"],
    )
    monkeypatch.setattr(
        "cogs.removestream_fix.get_stream_abbreviation",
        lambda _level, _stream: "TCS",
    )
    monkeypatch.setattr(
        "cogs.removestream_fix._subject_channel_name",
        lambda code, subject: f"📚-{code}・{subject}",
    )
    monkeypatch.setattr(
        "cogs.removestream_fix.STREAM_ROLE_PREFIX",
        "Filière - ",
    )
    monkeypatch.setattr(
        "cogs.removestream_fix.STUDENT_STREAM_ROLE_PREFIX",
        "Élèves - ",
    )

    role_names = _stream_role_names("Tronc Commun", "Tronc Commun Scientifique")
    channel_names = _stream_channel_names("Tronc Commun", "Tronc Commun Scientifique")

    assert role_names == {"Filière - TCS", "Élèves - TCS"}
    assert channel_names == {
        "📌-TCS・informations",
        "🗓️-TCS・emploi-du-temps",
        "📝-TCS・examens",
        "📚-TCS・Mathématiques",
        "📚-TCS・Physique et Chimie",
    }


def test_recorded_id_fails_closed_for_missing_or_invalid_values():
    config = {"managed": {"channels": {"x": "123", "y": 0, "z": 55}}}
    assert _recorded_id(config, "channels", "x") is None
    assert _recorded_id(config, "channels", "y") is None
    assert _recorded_id(config, "channels", "z") == 55


@pytest.mark.parametrize(
    "value",
    ["1999/2000", "2101/2102", "2026/2028", "2026-2027", "2026/27"],
)
def test_invalid_academic_years_are_rejected(value):
    assert _valid_academic_year(value) is False


@pytest.mark.parametrize("value", ["2000/2001", "2026/2027", "2100/2101"])
def test_valid_academic_years_are_accepted(value):
    assert _valid_academic_year(value) is True


class FakeRole:
    def __init__(self, name: str, role_id: int, managed: bool = False):
        self.name = name
        self.id = role_id
        self.managed = managed

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return isinstance(other, FakeRole) and self.id == other.id


def _role(name, role_id):
    return FakeRole(name, role_id)


def test_student_assignment_allows_student_reassignment_but_blocks_staff(monkeypatch):
    admin = _role("Administration", 10)
    prof = _role("Prof", 11)
    student = _role("Élève", 12)

    monkeypatch.setattr(
        "services.role_conflicts.get_managed_role",
        lambda _guild, name: {
            "Administration": admin,
            "Prof": prof,
            "Prof (F)": None,
            "Élève": student,
        }.get(name),
    )

    guild = SimpleNamespace(roles=[admin, prof, student])
    enrolled_student = SimpleNamespace(bot=False, roles=[student])
    teacher = SimpleNamespace(bot=False, roles=[prof])
    admin_member = SimpleNamespace(bot=False, roles=[admin])

    assert student_staff_conflict(enrolled_student, guild) is None
    assert student_staff_conflict(teacher, guild) is not None
    assert student_staff_conflict(admin_member, guild) is not None


def test_teacher_assignment_blocks_student_admin_and_bot(monkeypatch):
    admin = _role("Administration", 10)
    prof = _role("Prof", 11)
    student = _role("Élève", 12)

    monkeypatch.setattr(
        "services.role_conflicts.get_managed_role",
        lambda _guild, name: {
            "Administration": admin,
            "Prof": prof,
            "Prof (F)": None,
            "Élève": student,
        }.get(name),
    )
    guild = SimpleNamespace(id=123, roles=[admin, prof, student])
    monkeypatch.setattr("services.role_conflicts.get_guild_config", lambda _guild_id: {"levels": []})

    assert teacher_target_conflict(SimpleNamespace(bot=True, roles=[]), guild) is not None
    assert teacher_target_conflict(SimpleNamespace(bot=False, roles=[student]), guild) is not None
    assert teacher_target_conflict(SimpleNamespace(bot=False, roles=[admin]), guild) is not None
    assert teacher_target_conflict(SimpleNamespace(bot=False, roles=[prof]), guild) is None


def test_teacher_commands_enforce_shared_teacher_target_conflict_gate():
    source = open("cogs/teachers.py", encoding="utf-8").read()
    assignment_source = open("services/teacher_assignment.py", encoding="utf-8").read()
    assert "teacher_target_conflict(teacher, guild)" in source
    assert "teacher_target_conflict(member, guild)" in source
    assert "teacher_target_conflict(teacher, guild)" in assignment_source


def test_student_assignment_enforces_shared_student_staff_conflict_gate():
    source = open("cogs/students.py", encoding="utf-8").read()
    assert "student_staff_conflict(student, guild)" in source


def test_class_transfer_requires_target_to_keep_student_role(monkeypatch):
    student = _role("Élève", 12)
    monkeypatch.setattr(
        "services.role_conflicts.get_managed_role",
        lambda _guild, name: student if name == "Élève" else None,
    )
    guild = SimpleNamespace(roles=[student])
    member = SimpleNamespace(bot=False, roles=[])

    assert student_transfer_conflict(member, guild) is not None

    member.roles = [student]
    assert student_transfer_conflict(member, guild) is None


def test_class_transfer_confirmation_uses_current_student_role_guard():
    source = open("cogs/qr_onboarding.py", encoding="utf-8").read()
    confirmation = source[source.index("class ClassTransferConfirmationView"):]
    assert "student_transfer_conflict(member, guild)" in confirmation


class FakeComparableRole:
    def __init__(self, name: str, role_id: int, position: int, managed: bool = False):
        self.name = name
        self.id = role_id
        self.position = position
        self.managed = managed

    def is_default(self):
        return self.position == 0

    def __ge__(self, other):
        return self.position >= other.position


def test_reset_role_hierarchy_fails_before_mutation():
    from cogs.security_hardening_v3 import _validate_reset_role_hierarchy

    bot_role = FakeComparableRole("Bot", 99, 10)
    protected = FakeComparableRole("Filière - TCS", 55, 12)
    guild = SimpleNamespace(
        me=SimpleNamespace(top_role=bot_role),
        get_role=lambda role_id: {55: protected, 99: bot_role}.get(role_id),
    )

    message = _validate_reset_role_hierarchy(guild, {55})
    assert message is not None
    assert "hiérarchie" in message


def test_reset_archives_before_discord_deletion():
    source = open("cogs/security_hardening_v3.py", encoding="utf-8").read()
    assert "archive_path = archive_guild_database" in source
    assert source.index("archive_path = archive_guild_database") < source.index('operation="delete_channel"')


def test_latency_sensitive_commands_acknowledge_before_slow_work():
    teacher_source = open("cogs/teachers.py", encoding="utf-8").read()
    full_teacher_source = open("cogs/command_fixes.py", encoding="utf-8").read()

    report = teacher_source[teacher_source.index("async def report_absence"):teacher_source.index("\n\nasync def setup", teacher_source.index("async def report_absence"))]
    full = full_teacher_source[full_teacher_source.index("async def assign_teacher_full"):full_teacher_source.index("\n\nasync def setup", full_teacher_source.index("async def assign_teacher_full"))]

    assert report.index("await interaction.response.defer(ephemeral=True)") < report.index("_find_managed_channel(")
    assert report.index("await interaction.followup.send") > report.index("await interaction.response.defer(ephemeral=True)")
    assert full.index("await interaction.response.defer(ephemeral=True)") < full.index("execute_teacher_assignment(")
    assert "except TeacherAssignmentError as exc" in full


@pytest.mark.asyncio
async def test_reset_discord_operation_failure_reports_exact_target_and_api_code(caplog):
    from cogs.security_hardening_v3 import ResetOperationError, _run_reset_discord_operation

    response = SimpleNamespace(status=403, reason="Forbidden")
    resource = SimpleNamespace(id=987654, name="teacher-private")
    action = AsyncMock(
        side_effect=discord.Forbidden(
            response,
            {"message": "Missing Access", "code": 50001},
        )
    )

    with pytest.raises(ResetOperationError) as caught:
        await _run_reset_discord_operation(
            operation="delete_channel",
            resource_type="channel",
            resource=resource,
            action=action,
        )

    details = str(caught.value)
    assert "operation=delete_channel" in details
    assert "resource_type=channel" in details
    assert "resource_id=987654" in details
    assert "resource_name='teacher-private'" in details
    assert "http_status=403" in details
    assert "discord_code=50001" in details
    assert "exception_type=Forbidden" in details
    assert "resetserver API operation failed" in caplog.text
    action.assert_awaited_once()


def test_reset_routes_all_resource_deletions_through_diagnostic_wrapper():
    source = open("cogs/security_hardening_v3.py", encoding="utf-8").read()
    reset_body = source[source.index("async def reset_server"):source.index("\n\nasync def setup")]

    assert reset_body.count("await _run_reset_discord_operation(") == 3
    assert 'operation="delete_channel"' in reset_body
    assert 'operation="delete_category"' in reset_body
    assert 'operation="delete_role"' in reset_body

from types import SimpleNamespace

import pytest

from cogs.removestream_fix import _recorded_id, _stream_channel_names, _stream_role_names
from cogs.server_v3 import _valid_academic_year
from services.role_conflicts import student_staff_conflict, teacher_target_conflict


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
    guild = SimpleNamespace(roles=[admin, prof, student])

    assert teacher_target_conflict(SimpleNamespace(bot=True, roles=[]), guild) is not None
    assert teacher_target_conflict(SimpleNamespace(bot=False, roles=[student]), guild) is not None
    assert teacher_target_conflict(SimpleNamespace(bot=False, roles=[admin]), guild) is not None
    assert teacher_target_conflict(SimpleNamespace(bot=False, roles=[prof]), guild) is None


def test_teacher_commands_enforce_shared_teacher_target_conflict_gate():
    source = open("cogs/teachers.py", encoding="utf-8").read()
    full_source = open("cogs/command_fixes.py", encoding="utf-8").read()
    assert "teacher_target_conflict(teacher, guild)" in source
    assert "teacher_target_conflict(member, guild)" in source
    assert "teacher_target_conflict(teacher, guild)" in full_source


def test_student_assignment_enforces_shared_student_staff_conflict_gate():
    source = open("cogs/students.py", encoding="utf-8").read()
    assert "student_staff_conflict(student, guild)" in source

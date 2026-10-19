from types import SimpleNamespace

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
    assert "archive_path = prior_archive or archive_guild_database(guild.id, archive_year)" in source
    assert source.index("archive_path = prior_archive or archive_guild_database") < source.index("await channel.delete")


def test_latency_sensitive_commands_acknowledge_before_slow_work():
    teacher_source = open("cogs/teachers.py", encoding="utf-8").read()
    full_teacher_source = open("cogs/command_fixes.py", encoding="utf-8").read()

    report = teacher_source[teacher_source.index("async def report_absence"):teacher_source.index("\n\nasync def setup", teacher_source.index("async def report_absence"))]
    full = full_teacher_source[full_teacher_source.index("async def assign_teacher_full"):full_teacher_source.index("\n\nasync def setup", full_teacher_source.index("async def assign_teacher_full"))]

    assert report.index("await interaction.response.defer(ephemeral=True)") < report.index("_find_managed_channel(")
    assert report.index("await interaction.followup.send") > report.index("await interaction.response.defer(ephemeral=True)")
    assert full.index("await interaction.response.defer(ephemeral=True)") < full.index("execute_teacher_assignment(")
    assert "except TeacherAssignmentError as exc" in full


def test_reset_includes_registered_class_roles_outside_managed_role_registry():
    from cogs.security_hardening_v3 import _registered_class_role_ids

    section_role = FakeComparableRole("Élèves - 2BACPC-2", 303, 3)
    guild = SimpleNamespace(roles=[section_role], get_role=lambda role_id: section_role if role_id == 303 else None)
    config = {
        "class_roles": {
            "2BACPC-2": {
                "role_id": 303,
                "level_name": "2ème Année Bac",
                "stream_name": "2ème Année Bac Sciences Physiques",
                "stream_code": "2BACPC",
                "section": 2,
            }
        }
    }

    assert _registered_class_role_ids(guild, config) == {303}


def test_reset_refuses_mismatched_registered_class_role_identity():
    from cogs.security_hardening_v3 import _registered_class_role_ids

    section_role = FakeComparableRole("Manual Unrelated Role", 303, 3)
    guild = SimpleNamespace(roles=[section_role], get_role=lambda role_id: section_role if role_id == 303 else None)
    config = {
        "class_roles": {
            "2BACPC-2": {
                "role_id": 303,
                "level_name": "2ème Année Bac",
                "stream_name": "2ème Année Bac Sciences Physiques",
                "stream_code": "2BACPC",
                "section": 2,
            }
        }
    }

    with pytest.raises(RuntimeError, match="identité enregistrée"):
        _registered_class_role_ids(guild, config)


def test_reset_always_archives_even_without_active_academic_year():
    source = open("cogs/security_hardening_v3.py", encoding="utf-8").read()
    assert 'else "no-active-year"' in source
    assert "archive_path = prior_archive or archive_guild_database(guild.id, archive_year)" in source
    assert source.index("archive_path = prior_archive or archive_guild_database") < source.index("await channel.delete")


def test_reset_reuses_only_valid_archive_for_matching_guild(tmp_path, monkeypatch):
    import sqlite3
    from cogs import security_hardening_v3 as reset_module

    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    archive = archive_dir / "2026-2027.db"
    with sqlite3.connect(archive) as conn:
        conn.execute(
            "CREATE TABLE archive_metadata (id INTEGER PRIMARY KEY CHECK(id=1), guild_id INTEGER NOT NULL, academic_year TEXT NOT NULL, archived_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO archive_metadata VALUES (1,101,'2026/2027','2026-10-01T00:00:00+00:00')"
        )

    monkeypatch.setattr(reset_module, "DATA_DIR", tmp_path)
    assert reset_module._existing_reset_archive(101, archive.name) == archive
    assert reset_module._existing_reset_archive(202, archive.name) is None
    assert reset_module._existing_reset_archive(101, "../school.db") is None


def test_reset_preflight_detects_unregistered_channels_inside_managed_categories():
    from cogs.security_hardening_v3 import _unregistered_children_of_managed_categories

    managed_text = SimpleNamespace(id=201, name="📌-TCS・informations", category_id=200)
    unmanaged_text = SimpleNamespace(id=999, name="manual-notes", category_id=200)
    outside_text = SimpleNamespace(id=1000, name="unrelated", category_id=555)
    category = SimpleNamespace(id=200, name="managed-category", category_id=None)
    live_channels = {item.id: item for item in (category, managed_text, unmanaged_text, outside_text)}

    assert _unregistered_children_of_managed_categories(live_channels, {201}, {200}) == [
        (200, "manual-notes")
    ]


def test_reset_preflight_allows_categories_with_only_registered_children():
    from cogs.security_hardening_v3 import _unregistered_children_of_managed_categories

    category = SimpleNamespace(id=200, name="managed-category", category_id=None)
    managed_text = SimpleNamespace(id=201, name="managed-text", category_id=200)
    live_channels = {200: category, 201: managed_text}

    assert _unregistered_children_of_managed_categories(live_channels, {201}, {200}) == []

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.command_fixes import _global_subject_role_name
from cogs.students import _student_assignment_roles
from cogs.teachers import MENTION_RE
from services import storage


class FakeMember:
    def __init__(self, roles):
        self.id = 500
        self.display_name = "Student"
        self.mention = "<@500>"
        self.roles = list(roles)
        self.add_roles = AsyncMock()
        self.remove_roles = AsyncMock()


class FakeGuild:
    def __init__(self, config):
        self.id = 123
        self._config = config


@pytest.mark.parametrize(
    "roles, expected_names",
    [
        (
            [
                SimpleNamespace(id=1, name="Administration"),
                SimpleNamespace(id=2, name="Prof"),
                SimpleNamespace(id=3, name="Élève"),
                SimpleNamespace(id=4, name="Élèves - TCS"),
            ],
            {"Élève", "Élèves - TCS"},
        ),
        (
            [SimpleNamespace(id=9, name="Administration"), SimpleNamespace(id=10, name="Club")],
            set(),
        ),
    ],
)
def test_student_assignment_cleanup_never_targets_admin_or_prof_roles(monkeypatch, roles, expected_names):
    guild = FakeGuild({"managed": {"roles": {role.name: role.id for role in roles}}})
    monkeypatch.setattr("cogs.students.get_guild_config", lambda _guild_id: guild._config)
    member = FakeMember(roles)

    result = _student_assignment_roles(member, guild)

    assert {role.name for role in result} == expected_names
    assert "Administration" not in {role.name for role in result}
    assert "Prof" not in {role.name for role in result}


def test_teacher_mentions_are_deduplicated_by_member_id():
    matches = [int(match.group(1)) for match in MENTION_RE.finditer("<@101> <@!101> <@202> <@101>")]
    unique_ids = []
    for member_id in matches:
        if member_id not in unique_ids:
            unique_ids.append(member_id)
    assert unique_ids == [101, 202]



@pytest.mark.asyncio
async def test_legacy_subject_role_migration_can_scan_config_without_runtime_name_error(monkeypatch):
    from cogs.command_fixes import _migrate_legacy_subject_roles

    config = {
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
        ]
    }
    guild = SimpleNamespace(id=123, roles=[], channels=[])
    member = SimpleNamespace(roles=[])

    monkeypatch.setattr("cogs.command_fixes.get_guild_config", lambda _guild_id: config)

    migrated, created_roles, tracked_roles, permission_backups = await _migrate_legacy_subject_roles(
        guild, member, config
    )

    assert migrated == []
    assert created_roles == []
    assert tracked_roles == []
    assert permission_backups == []


@pytest.mark.asyncio
async def test_global_subject_role_refuses_unmanaged_same_name_collision(monkeypatch):
    from cogs.command_fixes import _get_or_create_global_subject_role

    existing = SimpleNamespace(name=_global_subject_role_name("Mathématiques"), id=777, managed=False)
    guild = SimpleNamespace(
        id=123,
        roles=[existing],
        create_role=AsyncMock(),
    )

    monkeypatch.setattr(
        "cogs.command_fixes.get_managed_role",
        lambda _guild, _name: None,
    )

    with pytest.raises(RuntimeError, match="Unmanaged role collision"):
        await _get_or_create_global_subject_role(
            guild,
            {"managed": {"roles": {}}},
            "Mathématiques",
        )

    guild.create_role.assert_not_awaited()


def test_student_state_snapshot_restore_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_FILE", tmp_path / "guild_config.json")
    monkeypatch.setattr(storage, "DATABASE_FILE", tmp_path / "school.db")

    guild_id = 321
    first_stream = "Tronc Commun Scientifique"
    second_stream = "Tronc Commun Lettres"
    config = {
        "academic_year": "2026/2027",
        "levels": [
            {
                "name": "Tronc Commun",
                "abbreviation": "TC",
                "streams": [
                    {
                        "name": first_stream,
                        "abbreviation": "TCS",
                        "subjects": [],
                    },
                    {
                        "name": second_stream,
                        "abbreviation": "TCL",
                        "subjects": [],
                    },
                ],
            }
        ],
    }

    storage.save_guild_config(guild_id, config)
    storage.enroll_student_record(
        guild_id,
        777,
        "Student",
        int(storage.get_active_academic_year(guild_id)["id"]),
        "Tronc Commun",
        first_stream,
    )
    snapshot = storage.snapshot_student_state(guild_id, 777)

    storage.enroll_student_record(
        guild_id,
        777,
        "Student",
        int(storage.get_active_academic_year(guild_id)["id"]),
        "Tronc Commun",
        second_stream,
    )
    storage.restore_student_state(guild_id, 777, snapshot)

    rows = storage.get_student_history(guild_id, 777)
    assert [row["stream_name"] for row in rows if row["status"] == "active"] == [first_stream]

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


@pytest.mark.asyncio
async def test_student_visibility_mutation_uses_only_persisted_managed_channel_ids(monkeypatch):
    from cogs import students as students_module
    from services.server_builder import CATEGORY_VOICE, _stream_category_name

    level = "Tronc Commun"
    stream = "Tronc Commun Scientifique"
    code = "TCS"
    category_name = _stream_category_name(level, stream, code)

    class FakeChannel:
        def __init__(self, channel_id, name, category_id):
            self.id = channel_id
            self.name = name
            self.category_id = category_id
            self.overwrites = {}
            self.edit = AsyncMock()

    category_id = 200
    managed_id = 201
    unmanaged_id = 999
    managed = FakeChannel(managed_id, f"📌-{code}・informations", category_id)
    unmanaged = FakeChannel(unmanaged_id, f"📌-{code}・informations", category_id)
    channels = {managed_id: managed, unmanaged_id: unmanaged}

    config = {
        "levels": [{"name": level, "streams": [{"name": stream, "abbreviation": code, "subjects": []}]}],
        "managed": {
            "categories": {category_name: category_id, CATEGORY_VOICE: 300},
            "channels": {
                f"📌-{code}・informations": managed_id,
                f"🗓️-{code}・emploi-du-temps": 202,
                f"📝-{code}・examens": 203,
                f"🔊-{code}-à-distance": 204,
            },
        },
    }
    guild = SimpleNamespace(id=123, get_channel=lambda resource_id: channels.get(resource_id))
    student_role = object()

    monkeypatch.setattr("cogs.students.get_guild_config", lambda _guild_id: config)
    monkeypatch.setattr(students_module.discord, "TextChannel", FakeChannel)
    monkeypatch.setattr(students_module.discord, "VoiceChannel", FakeChannel)

    await students_module._grant_student_global_stream_view(guild, student_role)

    managed.edit.assert_awaited_once()
    unmanaged.edit.assert_not_awaited()


def test_school_role_ids_do_not_adopt_unconfigured_canonical_role_names(monkeypatch):
    from cogs.students import _school_role_ids

    unrelated = SimpleNamespace(id=900, name="Élève", managed=False)
    guild = SimpleNamespace(id=123, roles=[unrelated])
    monkeypatch.setattr("cogs.students.get_guild_config", lambda _guild_id: {})

    assert _school_role_ids(guild) == set()


def test_teacher_conflict_ignores_unmanaged_same_name_student_stream_role(monkeypatch):
    from services.role_conflicts import teacher_target_conflict

    unrelated = SimpleNamespace(id=901, name="Élèves - TCS", managed=False)
    guild = SimpleNamespace(id=123)
    member = SimpleNamespace(bot=False, roles=[unrelated])
    monkeypatch.setattr("services.role_conflicts.get_guild_config", lambda _guild_id: {"levels": []})
    monkeypatch.setattr("services.role_conflicts.get_managed_role", lambda _guild, name: None)

    assert teacher_target_conflict(member, guild) is None


@pytest.mark.asyncio
async def test_legacy_subject_role_migration_touches_only_managed_channels(monkeypatch):
    from cogs.command_fixes import _migrate_legacy_subject_roles

    class FakeRole:
        def __init__(self, role_id, name):
            self.id = role_id
            self.name = name
            self.managed = False

        def __hash__(self):
            return hash(self.id)

        def __eq__(self, other):
            return isinstance(other, FakeRole) and self.id == other.id

    class FakeChannel:
        def __init__(self, channel_id, old_role, managed):
            self.id = channel_id
            self.overwrites = {old_role: object()}
            self.set_permissions = AsyncMock()
            self.managed = managed

    old_role = FakeRole(101, "Matière - TCS - Math")
    new_role = FakeRole(202, "Matière - Mathématiques")
    managed_channel = FakeChannel(10, old_role, True)
    unmanaged_channel = FakeChannel(20, old_role, False)
    guild = SimpleNamespace(
        id=123,
        channels=[managed_channel, unmanaged_channel],
    )
    member = FakeMember([old_role])
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
        ],
        "managed": {"channels": {"managed": 10}, "roles": {}},
    }

    async def fake_get_or_create(_guild, _config, _subject):
        return new_role

    monkeypatch.setattr("cogs.command_fixes._get_or_create_global_subject_role", fake_get_or_create)
    monkeypatch.setattr("cogs.command_fixes.get_guild_config", lambda _guild_id: config)

    await _migrate_legacy_subject_roles(guild, member, config)

    managed_channel.set_permissions.assert_awaited_once()
    unmanaged_channel.set_permissions.assert_not_awaited()

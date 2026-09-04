from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.students import _student_assignment_roles
from cogs.teachers import MENTION_RE


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

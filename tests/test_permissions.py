from types import SimpleNamespace

import pytest

from services.permissions import ROLE_ADMIN, ROLE_PROFESSOR, ROLE_TEACHER_PENDING, _hierarchy_error, get_managed_role, management_check, owner_only_check, teacher_assignment_check


class FakeRole:
    def __init__(self, name: str, position: int, role_id: int = 1, *, managed: bool = False) -> None:
        self.name = name
        self.position = position
        self.id = role_id
        self.managed = managed

    def is_default(self) -> bool:
        return self.position == 0

    def __ge__(self, other: "FakeRole") -> bool:
        return self.position >= other.position


def role(name: str, position: int, role_id: int):
    return FakeRole(name, position, role_id)


async def noop(*args, **kwargs):
    return None



def _teacher_assignment_fixture():
    everyone = role("@everyone", 0, 1)
    pending = role(ROLE_TEACHER_PENDING, 5, 43)
    professor = role(ROLE_PROFESSOR, 4, 44)
    bot_role = role("Bot", 10, 99)
    guild = SimpleNamespace(
        owner_id=999,
        id=123,
        roles=[everyone, pending, professor, bot_role],
        default_role=everyone,
        me=SimpleNamespace(top_role=bot_role, guild_permissions=SimpleNamespace(manage_channels=True, manage_roles=True)),
        get_role=lambda rid: {43: pending, 44: professor}.get(rid),
        guild_permissions=SimpleNamespace(manage_channels=True, manage_roles=True),
    )
    response = SimpleNamespace(is_done=lambda: False, send_message=noop)
    return guild, response, pending, professor


@pytest.mark.asyncio
async def test_teacher_assignment_check_allows_pending_self_registration(monkeypatch):
    guild, response, pending, _professor = _teacher_assignment_fixture()
    user = SimpleNamespace(id=101, roles=[pending])
    interaction = SimpleNamespace(
        guild=guild,
        user=user,
        response=response,
        namespace=SimpleNamespace(teacher=None),
    )
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {
        "management_role_id": 42,
        "managed": {"roles": {ROLE_TEACHER_PENDING: 43, ROLE_PROFESSOR: 44}},
    })
    monkeypatch.setattr("services.permissions.get_teacher_registration", lambda _guild_id, _discord_id: None)

    @teacher_assignment_check(lock=False)
    async def dummy(_interaction):
        return True

    predicate = dummy.__discord_app_commands_checks__[0]
    assert await predicate(interaction) is True


@pytest.mark.asyncio
async def test_teacher_assignment_check_rejects_self_targeting_another_member(monkeypatch):
    guild, response, pending, _professor = _teacher_assignment_fixture()
    user = SimpleNamespace(id=101, roles=[pending])
    interaction = SimpleNamespace(
        guild=guild,
        user=user,
        response=response,
        namespace=SimpleNamespace(teacher=SimpleNamespace(id=202)),
    )
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {
        "management_role_id": 42,
        "managed": {"roles": {ROLE_TEACHER_PENDING: 43, ROLE_PROFESSOR: 44}},
    })
    monkeypatch.setattr("services.permissions.get_teacher_registration", lambda _guild_id, _discord_id: None)

    @teacher_assignment_check(lock=False)
    async def dummy(_interaction):
        return True

    predicate = dummy.__discord_app_commands_checks__[0]
    assert await predicate(interaction) is False


@pytest.mark.asyncio
async def test_teacher_assignment_check_rejects_already_registered_teacher(monkeypatch):
    guild, response, pending, _professor = _teacher_assignment_fixture()
    user = SimpleNamespace(id=101, roles=[pending])
    interaction = SimpleNamespace(
        guild=guild,
        user=user,
        response=response,
        namespace=SimpleNamespace(teacher=None),
    )
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {
        "management_role_id": 42,
        "managed": {"roles": {ROLE_TEACHER_PENDING: 43, ROLE_PROFESSOR: 44}},
    })
    monkeypatch.setattr("services.permissions.get_teacher_registration", lambda _guild_id, _discord_id: object())

    @teacher_assignment_check(lock=False)
    async def dummy(_interaction):
        return True

    predicate = dummy.__discord_app_commands_checks__[0]
    assert await predicate(interaction) is False


@pytest.mark.asyncio
async def test_management_check_requires_configured_role_id(monkeypatch):
    everyone = role("@everyone", 0, 1)
    admin = role(ROLE_ADMIN, 5, 42)
    bot_role = role("Bot", 10, 99)
    guild = SimpleNamespace(owner_id=999, id=123, roles=[everyone, admin, bot_role], default_role=everyone, me=SimpleNamespace(top_role=bot_role), get_role=lambda rid: admin if rid == 42 else None, guild_permissions=SimpleNamespace(manage_channels=True, manage_roles=True))
    response = SimpleNamespace(is_done=lambda: False, send_message=noop)
    user = SimpleNamespace(id=123, roles=[admin])
    interaction = SimpleNamespace(guild=guild, user=user, response=response, command=SimpleNamespace(name="status"))
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {})

    @management_check()
    async def dummy(_interaction):
        return True

    predicate = dummy.__discord_app_commands_checks__[0]
    assert await predicate(interaction) is False
    assert dummy.__discord_app_commands_default_permissions__.manage_roles is True


@pytest.mark.asyncio
async def test_configured_admin_role_id_is_accepted(monkeypatch):
    everyone = role("@everyone", 0, 1)
    admin = role(ROLE_ADMIN, 5, 42)
    bot_role = role("Bot", 10, 99)
    guild = SimpleNamespace(owner_id=999, id=123, roles=[everyone, admin, bot_role], default_role=everyone, me=SimpleNamespace(top_role=bot_role), get_role=lambda rid: admin if rid == 42 else None, guild_permissions=SimpleNamespace(manage_channels=True, manage_roles=True))
    response = SimpleNamespace(is_done=lambda: False, send_message=noop)
    user = SimpleNamespace(id=123, roles=[admin])
    interaction = SimpleNamespace(guild=guild, user=user, response=response, command=SimpleNamespace(name="status"))
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {"management_role_id": 42})

    @management_check()
    async def dummy(_interaction):
        return True

    predicate = dummy.__discord_app_commands_checks__[0]
    assert await predicate(interaction) is True
    assert not hasattr(dummy, "__discord_app_commands_default_permissions__")


def test_same_name_role_with_different_id_is_not_managed(monkeypatch):
    clone = role(ROLE_ADMIN, 5, 77)
    guild = SimpleNamespace(id=123, get_role=lambda rid: clone if rid == 77 else None)
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {"management_role_id": 42, "managed": {"roles": {ROLE_ADMIN: 42}}})
    assert get_managed_role(guild, ROLE_ADMIN) is None


def test_exact_managed_role_id_is_resolved(monkeypatch):
    managed_admin = role(ROLE_ADMIN, 5, 42)
    guild = SimpleNamespace(id=123, get_role=lambda rid: managed_admin if rid == 42 else None)
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {"management_role_id": 42, "managed": {"roles": {ROLE_ADMIN: 42}}})
    assert get_managed_role(guild, ROLE_ADMIN) is managed_admin


def test_same_name_prof_role_is_not_used_without_managed_id(monkeypatch):
    clone = role(ROLE_PROFESSOR, 5, 77)
    guild = SimpleNamespace(id=123, get_role=lambda rid: clone if rid == 77 else None)
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {"managed": {"roles": {}}})
    assert get_managed_role(guild, ROLE_PROFESSOR) is None


def test_hierarchy_error_uses_recorded_role_ids_only(monkeypatch):
    everyone = role("@everyone", 0, 1)
    unrelated_clone = role(ROLE_ADMIN, 20, 77)
    bot_role = role("Bot", 10, 99)
    guild = SimpleNamespace(roles=[everyone, unrelated_clone, bot_role], id=123, default_role=everyone, me=SimpleNamespace(top_role=bot_role))
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {"managed": {"roles": {}}})
    assert _hierarchy_error(guild) is None


def test_hierarchy_error_identifies_low_bot_role(monkeypatch):
    everyone = role("@everyone", 0, 1)
    school_role = role("Filière - 1BACSE", 12, 55)
    bot_role = role("Bot", 10, 99)
    guild = SimpleNamespace(roles=[everyone, school_role, bot_role], id=123, default_role=everyone, me=SimpleNamespace(top_role=bot_role))
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {"managed": {"roles": {"Filière - 1BACSE": 55}}})
    message = _hierarchy_error(guild)
    assert message is not None
    assert "Filière - 1BACSE" in message


def test_management_check_can_skip_lock_when_command_owns_transaction():
    @management_check(lock=False)
    async def dummy(_interaction):
        return True

    assert not hasattr(dummy, "__wrapped__")


def test_management_check_wraps_lock_by_default():
    @management_check()
    async def dummy(_interaction):
        return True

    assert hasattr(dummy, "__wrapped__")


@pytest.mark.asyncio
async def test_mutations_are_blocked_while_removal_recovery_is_pending(monkeypatch):
    everyone = role("@everyone", 0, 1)
    admin = role(ROLE_ADMIN, 5, 42)
    bot_role = role("Bot", 10, 99)
    guild = SimpleNamespace(
        owner_id=999,
        id=123,
        roles=[everyone, admin, bot_role],
        default_role=everyone,
        me=SimpleNamespace(top_role=bot_role),
        get_role=lambda rid: admin if rid == 42 else None,
        guild_permissions=SimpleNamespace(manage_channels=True, manage_roles=True),
    )
    response = SimpleNamespace(is_done=lambda: False, send_message=noop)
    user = SimpleNamespace(id=123, roles=[admin])
    interaction = SimpleNamespace(guild=guild, user=user, response=response, command=SimpleNamespace(name="assignstudent"))
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {"management_role_id": 42, "managed": {"roles": {"Administration": 42}}, "pending_removal": {"level": "Tronc Commun", "stream": "TCS"}})

    @management_check(lock=False)
    async def dummy(_interaction):
        return True

    predicate = dummy.__discord_app_commands_checks__[0]
    assert await predicate(interaction) is False


@pytest.mark.asyncio
async def test_owner_mutations_are_blocked_while_removal_recovery_is_pending(monkeypatch):
    everyone = role("@everyone", 0, 1)
    bot_role = role("Bot", 10, 99)
    guild = SimpleNamespace(
        owner_id=999,
        id=123,
        roles=[everyone, bot_role],
        default_role=everyone,
        me=SimpleNamespace(top_role=bot_role),
        get_role=lambda _rid: None,
        guild_permissions=SimpleNamespace(manage_channels=True, manage_roles=True),
    )
    response = SimpleNamespace(is_done=lambda: False, send_message=noop)
    user = SimpleNamespace(id=999, roles=[])
    interaction = SimpleNamespace(guild=guild, user=user, response=response, command=SimpleNamespace(name="resetserver"))
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {"management_role_id": 42, "managed": {"roles": {"Administration": 42}}, "pending_removal": {"level": "Tronc Commun", "stream": "TCS"}})

    @owner_only_check(lock=False)
    async def dummy(_interaction):
        return True

    predicate = dummy.__discord_app_commands_checks__[0]
    assert await predicate(interaction) is False


@pytest.mark.asyncio
async def test_read_only_status_remains_available_during_removal_recovery(monkeypatch):
    everyone = role("@everyone", 0, 1)
    admin = role(ROLE_ADMIN, 5, 42)
    bot_role = role("Bot", 10, 99)
    guild = SimpleNamespace(
        owner_id=999,
        id=123,
        roles=[everyone, admin, bot_role],
        default_role=everyone,
        me=SimpleNamespace(top_role=bot_role),
        get_role=lambda rid: admin if rid == 42 else None,
        guild_permissions=SimpleNamespace(manage_channels=True, manage_roles=True),
    )
    response = SimpleNamespace(is_done=lambda: False, send_message=noop)
    user = SimpleNamespace(id=123, roles=[admin])
    interaction = SimpleNamespace(guild=guild, user=user, response=response, command=SimpleNamespace(name="status"))
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {"management_role_id": 42, "managed": {"roles": {"Administration": 42}}, "pending_removal": {"level": "Tronc Commun", "stream": "TCS"}})

    @management_check(lock=False)
    async def dummy(_interaction):
        return True

    predicate = dummy.__discord_app_commands_checks__[0]
    assert await predicate(interaction) is True


def test_stream_specific_access_does_not_use_base_student_role():
    from services.permissions import (
        public_voice_overwrites,
        stream_area_overwrites,
        subject_channel_overwrites,
    )

    everyone = object()
    admin = object()
    professor = object()
    professor_female = object()
    student = object()
    teacher_stream = object()
    student_stream = object()

    stream = stream_area_overwrites(
        everyone,
        admin,
        professor,
        professor_female,
        student,
        teacher_stream,
        student_stream,
    )
    subject = subject_channel_overwrites(
        everyone,
        admin,
        professor,
        professor_female,
        teacher_stream,
        student_stream,
        student_role=student,
    )
    voice = public_voice_overwrites(
        everyone,
        admin,
        professor,
        professor_female,
        student,
        teacher_stream,
        student_stream,
    )

    assert student not in stream
    assert student not in subject
    assert student not in voice
    assert student_stream in stream
    assert student_stream in subject
    assert student_stream in voice
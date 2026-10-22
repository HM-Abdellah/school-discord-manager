import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

import pytest

from services.permissions import (
    ROLE_ADMIN, ROLE_DIRECTION, ROLE_PERSONNEL_ADMINISTRATIF, ROLE_PROFESSOR,
    ROLE_SUPPORT_ENGINEER, ROLE_SURVEILLANT_GENERAL, ROLE_TEACHER_PENDING,
    _hierarchy_error, admin_qr_available_roles, admin_qr_check, can_create_admin_qr_for_role,
    direction_or_owner_check, get_managed_role, management_check,
    owner_only_check, teacher_assignment_check,
)


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
    assert dummy.__discord_app_commands_default_permissions__.manage_roles is True


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
    assert subject[student_stream].view_channel is True
    assert subject[student_stream].send_messages is True
    assert subject[student_stream].create_public_threads is False
    assert subject[student_stream].create_private_threads is False
    assert subject[student_stream].send_messages_in_threads is False
    assert student_stream in voice

@pytest.mark.asyncio
async def test_mutation_lock_rejects_concurrent_command_before_interaction_expires(monkeypatch):
    from services.build_guard import get_build_lock

    guild_id = 991234
    lock = get_build_lock(guild_id)
    called = AsyncMock()
    response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = SimpleNamespace(id=guild_id)
    interaction.response = response
    interaction.followup = SimpleNamespace(send=AsyncMock())

    @management_check()
    async def dummy(_interaction):
        await called()

    async with lock:
        task = asyncio.create_task(dummy(interaction))
        await task

    called.assert_not_awaited()
    response.send_message.assert_awaited_once()
    assert "déjà en cours" in response.send_message.await_args.args[0]
    assert response.send_message.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_teacher_commands_do_not_require_admin_manage_roles_default():
    from services.permissions import teacher_professor_check

    @teacher_professor_check(lock=False)
    async def dummy(_interaction):
        return True

    assert getattr(dummy, "__discord_app_commands_default_permissions__", None) is None


@pytest.mark.asyncio
async def test_management_mutations_are_blocked_during_interrupted_reset(monkeypatch):
    everyone = role("@everyone", 0, 1)
    admin = role(ROLE_ADMIN, 5, 42)
    bot_role = role("Bot", 10, 99)
    guild = SimpleNamespace(
        owner_id=999, id=992222, roles=[everyone, admin, bot_role], default_role=everyone,
        me=SimpleNamespace(top_role=bot_role),
        get_role=lambda rid: admin if rid == 42 else None,
        guild_permissions=SimpleNamespace(manage_channels=True, manage_roles=True),
    )
    response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
    user = SimpleNamespace(id=123, roles=[admin])
    interaction = SimpleNamespace(guild=guild, user=user, response=response, command=SimpleNamespace(name="build"))
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: {"management_role_id": 42, "pending_reset": {"archive": "2026-2027.db"}})

    @management_check(lock=False)
    async def dummy(_interaction):
        return True

    assert await dummy.__discord_app_commands_checks__[0](interaction) is False
    response.send_message.assert_awaited_once()
    assert "reset" in response.send_message.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_all_replacement_management_roles_authorize_normal_commands_but_support_is_not_owner(monkeypatch):
    from services.permissions import (
        ADMIN_ROLE_NAMES,
        ROLE_STUDENT,
        management_authorized,
    )

    everyone = role("@everyone", 0, 1)
    replacement_roles = [role(name, 5, 100 + index) for index, name in enumerate(ADMIN_ROLE_NAMES)]
    student = role(ROLE_STUDENT, 2, 200)
    bot_role = role("Bot", 10, 999)
    roles_by_id = {item.id: item for item in [everyone, *replacement_roles, student, bot_role]}
    guild = SimpleNamespace(
        owner_id=9999,
        id=123,
        roles=list(roles_by_id.values()),
        default_role=everyone,
        me=SimpleNamespace(top_role=bot_role),
        get_role=lambda role_id: roles_by_id.get(role_id),
    )
    monkeypatch.setattr(
        "services.permissions.get_guild_config",
        lambda _guild_id: {"managed": {"roles": {item.name: item.id for item in replacement_roles}}},
    )

    for item in replacement_roles:
        user_roles = [item]
        if item.name == "Support Engineer":
            user_roles.append(student)
        interaction = SimpleNamespace(guild=guild, user=SimpleNamespace(id=1234, roles=user_roles))
        assert management_authorized(interaction) is True

    owner = SimpleNamespace(id=guild.owner_id, roles=[])
    assert management_authorized(SimpleNamespace(guild=guild, user=owner)) is True


@pytest.mark.asyncio
async def test_support_engineer_cannot_use_owner_only_commands(monkeypatch):
    support = role("Support Engineer", 5, 501)
    everyone = role("@everyone", 0, 1)
    bot_role = role("Bot", 10, 999)
    guild = SimpleNamespace(
        owner_id=9999,
        id=123,
        roles=[everyone, support, bot_role],
        default_role=everyone,
        me=SimpleNamespace(top_role=bot_role),
        get_role=lambda role_id: support if role_id == support.id else None,
    )
    response = SimpleNamespace(is_done=lambda: False, send_message=noop)
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=1234, roles=[support]),
        response=response,
        command=SimpleNamespace(name="resetserver"),
    )
    monkeypatch.setattr(
        "services.permissions.get_guild_config",
        lambda _guild_id: {"managed": {"roles": {"Support Engineer": support.id}}},
    )

    @owner_only_check(lock=False)
    async def dummy(_interaction):
        return True

    predicate = dummy.__discord_app_commands_checks__[0]
    assert await predicate(interaction) is False


def test_unregistered_new_management_role_is_not_adopted_by_name(monkeypatch):
    from services.permissions import ROLE_DIRECTION

    unregistered = role(ROLE_DIRECTION, 5, 701)
    guild = SimpleNamespace(id=123, get_role=lambda role_id: unregistered if role_id == unregistered.id else None)
    monkeypatch.setattr(
        "services.permissions.get_guild_config",
        lambda _guild_id: {
            "levels": [{"name": "Tronc Commun", "streams": [{"name": "Tronc Commun Scientifique"}]}],
            "managed": {"roles": {}},
        },
    )

    assert get_managed_role(guild, ROLE_DIRECTION) is None


def _staff_access_fixture(monkeypatch):
    everyone = role("@everyone", 0, 1)
    direction = role(ROLE_DIRECTION, 5, 51)
    surveillant = role(ROLE_SURVEILLANT_GENERAL, 5, 52)
    personnel = role(ROLE_PERSONNEL_ADMINISTRATIF, 5, 53)
    support = role(ROLE_SUPPORT_ENGINEER, 5, 54)
    bot_role = role("Bot", 10, 99)
    all_roles = [everyone, direction, surveillant, personnel, support, bot_role]
    roles_by_id = {item.id: item for item in all_roles}
    guild = SimpleNamespace(
        id=123,
        owner_id=999,
        roles=all_roles,
        default_role=everyone,
        me=SimpleNamespace(
            top_role=bot_role,
            guild_permissions=SimpleNamespace(
                manage_channels=True,
                manage_roles=True,
                administrator=False,
            ),
        ),
        get_role=lambda role_id: roles_by_id.get(role_id),
    )
    config = {
        "management_role_id": direction.id,
        "managed": {
            "roles": {
                ROLE_DIRECTION: direction.id,
                ROLE_SURVEILLANT_GENERAL: surveillant.id,
                ROLE_PERSONNEL_ADMINISTRATIF: personnel.id,
                ROLE_SUPPORT_ENGINEER: support.id,
            }
        },
    }
    monkeypatch.setattr("services.permissions.get_guild_config", lambda _guild_id: config)
    response = SimpleNamespace(is_done=lambda: False, send_message=noop)
    return guild, response, direction, surveillant, personnel, support


def _staff_interaction(guild, response, user_id, user_roles, command_name):
    return SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=user_id, roles=user_roles),
        response=response,
        command=SimpleNamespace(name=command_name),
    )


@pytest.mark.asyncio
async def test_resetserver_allows_direction_and_owner_but_not_other_managed_roles(monkeypatch):
    guild, response, direction, surveillant, _personnel, support = _staff_access_fixture(monkeypatch)

    @direction_or_owner_check(lock=False)
    async def dummy(_interaction):
        return True

    predicate = dummy.__discord_app_commands_checks__[0]
    assert await predicate(_staff_interaction(guild, response, 1234, [direction], "resetserver")) is True
    assert await predicate(_staff_interaction(guild, response, guild.owner_id, [], "resetserver")) is True
    assert await predicate(_staff_interaction(guild, response, 1234, [surveillant], "resetserver")) is False
    assert await predicate(_staff_interaction(guild, response, 1234, [support], "resetserver")) is False


@pytest.mark.asyncio
async def test_createadminqr_authorizes_only_owner_direction_and_surveillant(monkeypatch):
    guild, response, direction, surveillant, personnel, support = _staff_access_fixture(monkeypatch)

    @admin_qr_check(lock=False)
    async def dummy(_interaction):
        return True

    predicate = dummy.__discord_app_commands_checks__[0]
    assert await predicate(_staff_interaction(guild, response, guild.owner_id, [], "createadminqr")) is True
    assert await predicate(_staff_interaction(guild, response, 1234, [direction], "createadminqr")) is True
    assert await predicate(_staff_interaction(guild, response, 1234, [surveillant], "createadminqr")) is True
    assert await predicate(_staff_interaction(guild, response, 1234, [personnel], "createadminqr")) is False
    assert await predicate(_staff_interaction(guild, response, 1234, [support], "createadminqr")) is False


def test_createadminqr_target_policy_limits_staff_to_personnel_administratif(monkeypatch):
    guild, response, direction, surveillant, _personnel, support = _staff_access_fixture(monkeypatch)
    direction_interaction = _staff_interaction(guild, response, 1234, [direction], "createadminqr")
    surveillant_interaction = _staff_interaction(guild, response, 1235, [surveillant], "createadminqr")
    owner_interaction = _staff_interaction(guild, response, guild.owner_id, [], "createadminqr")
    support_interaction = _staff_interaction(guild, response, 1236, [support], "createadminqr")

    assert can_create_admin_qr_for_role(direction_interaction, ROLE_PERSONNEL_ADMINISTRATIF) is True
    assert can_create_admin_qr_for_role(direction_interaction, ROLE_DIRECTION) is False
    assert can_create_admin_qr_for_role(direction_interaction, ROLE_SURVEILLANT_GENERAL) is False
    assert can_create_admin_qr_for_role(surveillant_interaction, ROLE_PERSONNEL_ADMINISTRATIF) is True
    assert can_create_admin_qr_for_role(surveillant_interaction, ROLE_DIRECTION) is False
    assert can_create_admin_qr_for_role(owner_interaction, ROLE_DIRECTION) is True
    assert can_create_admin_qr_for_role(owner_interaction, ROLE_SURVEILLANT_GENERAL) is True
    assert can_create_admin_qr_for_role(owner_interaction, ROLE_PERSONNEL_ADMINISTRATIF) is True
    assert can_create_admin_qr_for_role(support_interaction, ROLE_PERSONNEL_ADMINISTRATIF) is False
    assert admin_qr_available_roles(direction_interaction) == (ROLE_PERSONNEL_ADMINISTRATIF,)
    assert admin_qr_available_roles(surveillant_interaction) == (ROLE_PERSONNEL_ADMINISTRATIF,)
    assert admin_qr_available_roles(owner_interaction) == (
        ROLE_DIRECTION, ROLE_SURVEILLANT_GENERAL, ROLE_PERSONNEL_ADMINISTRATIF
    )
    assert admin_qr_available_roles(support_interaction) == ()
    assert can_create_admin_qr_for_role(owner_interaction, ROLE_SUPPORT_ENGINEER) is False

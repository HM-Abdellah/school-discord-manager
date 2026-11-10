from copy import deepcopy
import pytest

from services import admin_role_registry
from services.permissions import (
    ROLE_DIRECTION,
    ROLE_PERSONNEL_ADMINISTRATIF,
    ROLE_SUPPORT_ENGINEER,
    ROLE_SURVEILLANT_GENERAL,
)


class FakeRole:
    def __init__(self, role_id: int, name: str, *, managed: bool = False, default: bool = False):
        self.id = role_id
        self.name = name
        self.managed = managed
        self._default = default

    def is_default(self):
        return self._default


class FakeGuild:
    def __init__(self, roles):
        self.id = 123
        self.roles = list(roles)
        self._roles_by_id = {role.id: role for role in self.roles}

    def get_role(self, role_id):
        return self._roles_by_id.get(role_id)


def _admin_roles():
    return [
        FakeRole(11, ROLE_DIRECTION),
        FakeRole(12, ROLE_SURVEILLANT_GENERAL),
        FakeRole(13, ROLE_PERSONNEL_ADMINISTRATIF),
        FakeRole(14, ROLE_SUPPORT_ENGINEER),
    ]


def _config():
    return {
        "academic_year": "2026/2027",
        "levels": [{"name": "2ème Année Bac", "streams": [{"name": "Sciences Physiques"}]}],
        "management_role_id": 91,
        "managed": {
            "roles": {
                "Administration": 91,
                "Prof": 92,
            },
            "categories": {"General": 101},
            "channels": {"actualites": 201},
        },
        "class_roles": {"2BACPC-1": {"role_id": 301}},
        "unrelated_setting": {"keep": True},
    }


def test_sync_admin_role_registry_registers_existing_roles_and_preserves_other_config(monkeypatch):
    current = _config()
    saved = []
    audited = []
    monkeypatch.setattr(admin_role_registry, "get_guild_config", lambda _guild_id: current)
    monkeypatch.setattr(admin_role_registry, "save_guild_config", lambda guild_id, config: saved.append((guild_id, deepcopy(config))))
    monkeypatch.setattr(admin_role_registry, "record_event", lambda *args, **kwargs: audited.append((args, kwargs)))

    names = admin_role_registry.sync_admin_role_registry(
        FakeGuild(_admin_roles()),
        actor_id=777,
        actor_name="Server Owner",
    )

    assert names == (
        ROLE_DIRECTION,
        ROLE_SURVEILLANT_GENERAL,
        ROLE_PERSONNEL_ADMINISTRATIF,
        ROLE_SUPPORT_ENGINEER,
    )
    assert len(saved) == 1
    guild_id, updated = saved[0]
    assert guild_id == 123
    assert updated["managed"]["roles"] == {
        "Administration": 91,
        "Prof": 92,
        ROLE_DIRECTION: 11,
        ROLE_SURVEILLANT_GENERAL: 12,
        ROLE_PERSONNEL_ADMINISTRATIF: 13,
        ROLE_SUPPORT_ENGINEER: 14,
    }
    assert updated["managed"]["categories"] == current["managed"]["categories"]
    assert updated["managed"]["channels"] == current["managed"]["channels"]
    assert updated["class_roles"] == current["class_roles"]
    assert updated["unrelated_setting"] == current["unrelated_setting"]
    assert "Direction" not in current["managed"]["roles"]
    assert len(audited) == 1
    assert audited[0][0][3] == "sync_admin_role_registry"


def test_sync_admin_role_registry_requires_existing_setup(monkeypatch):
    monkeypatch.setattr(admin_role_registry, "get_guild_config", lambda _guild_id: None)
    saved = []
    monkeypatch.setattr(admin_role_registry, "save_guild_config", lambda *args: saved.append(args))

    with pytest.raises(ValueError, match="configuration scolaire"):
        admin_role_registry.sync_admin_role_registry(
            FakeGuild(_admin_roles()),
            actor_id=777,
            actor_name="Server Owner",
        )

    assert saved == []


def test_sync_admin_role_registry_fails_atomically_when_a_required_role_is_missing(monkeypatch):
    monkeypatch.setattr(admin_role_registry, "get_guild_config", lambda _guild_id: _config())
    saved = []
    monkeypatch.setattr(admin_role_registry, "save_guild_config", lambda *args: saved.append(args))
    guild = FakeGuild(_admin_roles()[:-1])
    guild.roles = [role for role in guild.roles if role.name != ROLE_SURVEILLANT_GENERAL]
    guild._roles_by_id = {role.id: role for role in guild.roles}

    with pytest.raises(ValueError, match="Surveillant Général"):
        admin_role_registry.sync_admin_role_registry(
            guild,
            actor_id=777,
            actor_name="Server Owner",
        )

    assert saved == []


def test_sync_admin_role_registry_rejects_duplicate_canonical_names(monkeypatch):
    monkeypatch.setattr(admin_role_registry, "get_guild_config", lambda _guild_id: _config())
    saved = []
    monkeypatch.setattr(admin_role_registry, "save_guild_config", lambda *args: saved.append(args))
    guild = FakeGuild([*_admin_roles(), FakeRole(99, ROLE_DIRECTION)])

    with pytest.raises(ValueError, match="Plusieurs rôles"):
        admin_role_registry.sync_admin_role_registry(
            guild,
            actor_id=777,
            actor_name="Server Owner",
        )

    assert saved == []


def test_sync_admin_role_registry_repairs_a_deleted_stale_id(monkeypatch):
    current = _config()
    current["managed"]["roles"][ROLE_DIRECTION] = 9999
    saved = []
    monkeypatch.setattr(admin_role_registry, "get_guild_config", lambda _guild_id: current)
    monkeypatch.setattr(admin_role_registry, "save_guild_config", lambda guild_id, config: saved.append(deepcopy(config)))

    names = admin_role_registry.sync_admin_role_registry(
        FakeGuild(_admin_roles()),
        actor_id=777,
        actor_name="Server Owner",
    )

    assert ROLE_DIRECTION in names
    assert saved[0]["managed"]["roles"][ROLE_DIRECTION] == 11


def test_sync_admin_role_registry_rejects_a_live_identity_conflict(monkeypatch):
    current = _config()
    current["managed"]["roles"][ROLE_DIRECTION] = 88
    roles = [*_admin_roles(), FakeRole(88, "Unrelated role")]
    saved = []
    monkeypatch.setattr(admin_role_registry, "get_guild_config", lambda _guild_id: current)
    monkeypatch.setattr(admin_role_registry, "save_guild_config", lambda *args: saved.append(args))

    with pytest.raises(ValueError, match="autre rôle Discord encore présent"):
        admin_role_registry.sync_admin_role_registry(
            FakeGuild(roles),
            actor_id=777,
            actor_name="Server Owner",
        )

    assert saved == []


def test_sync_admin_role_registry_does_not_require_optional_support_engineer(monkeypatch):
    current = _config()
    current["managed"]["roles"].pop(ROLE_SUPPORT_ENGINEER, None)
    saved = []
    monkeypatch.setattr(admin_role_registry, "get_guild_config", lambda _guild_id: current)
    monkeypatch.setattr(admin_role_registry, "save_guild_config", lambda guild_id, config: saved.append(deepcopy(config)))

    names = admin_role_registry.sync_admin_role_registry(
        FakeGuild(_admin_roles()[:-1]),
        actor_id=777,
        actor_name="Server Owner",
    )

    assert ROLE_SUPPORT_ENGINEER not in names
    assert set(names) == {
        ROLE_DIRECTION,
        ROLE_SURVEILLANT_GENERAL,
        ROLE_PERSONNEL_ADMINISTRATIF,
    }
    assert ROLE_SUPPORT_ENGINEER not in saved[0]["managed"]["roles"]

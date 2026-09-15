from types import SimpleNamespace

import pytest

from services.discord_ownership import ManagedResourceConflict, validate_managed_registry


def test_matching_registered_role_identity_is_accepted():
    config = {"managed": {"roles": {"Administration": 101}}}
    guild = SimpleNamespace(roles=[SimpleNamespace(id=101, name="Administration")], channels=[])

    validate_managed_registry(guild, config)


def test_same_name_different_role_id_is_rejected():
    config = {"managed": {"roles": {"Administration": 101}}}
    guild = SimpleNamespace(roles=[SimpleNamespace(id=202, name="Administration")], channels=[])

    with pytest.raises(ManagedResourceConflict, match="same name exists"):
        validate_managed_registry(guild, config)


def test_missing_registered_role_is_allowed_when_no_same_name_resource_exists():
    config = {"managed": {"roles": {"Administration": 101}}}
    guild = SimpleNamespace(roles=[SimpleNamespace(id=202, name="Other")], channels=[])

    validate_managed_registry(guild, config)


def test_registered_channel_name_on_different_live_id_is_rejected():
    config = {
        "managed": {
            "categories": {"📘・TC・🔬 TCS": 301},
            "channels": {"📌-TCS・informations": 401},
        }
    }
    guild = SimpleNamespace(
        roles=[],
        channels=[
            SimpleNamespace(id=302, name="📘・TC・🔬 TCS"),
            SimpleNamespace(id=402, name="📌-TCS・informations"),
        ],
    )

    with pytest.raises(ManagedResourceConflict, match="same name exists"):
        validate_managed_registry(guild, config)


def test_duplicate_live_same_name_is_rejected_for_registered_channel():
    config = {"managed": {"channels": {"examens": 401}}}
    guild = SimpleNamespace(
        roles=[],
        channels=[
            SimpleNamespace(id=401, name="examens"),
            SimpleNamespace(id=402, name="examens"),
        ],
    )

    with pytest.raises(ManagedResourceConflict, match="same name exists"):
        validate_managed_registry(guild, config)

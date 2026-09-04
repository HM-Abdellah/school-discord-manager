from cogs.server_v3 import _configured_managed_ids


def test_reset_scope_uses_only_persisted_managed_ids():
    config = {
        "managed": {
            "roles": {"Administration": 101, "Élève": 102},
            "channels": {"📌-TCS・informations": 201},
            "categories": {"📘・TC・🔬 TCS": 301},
        }
    }

    roles, channels, categories = _configured_managed_ids(config)

    assert roles == {101, 102}
    assert channels == {201}
    assert categories == {301}


def test_reset_scope_ignores_names_and_invalid_values():
    config = {
        "managed": {
            "roles": {"Administration": 0, "fake": "999", "student": -4},
            "channels": {"custom-name": 42.5},
            "categories": {"bad": None},
        }
    }

    roles, channels, categories = _configured_managed_ids(config)

    assert roles == set()
    assert channels == set()
    assert categories == set()


def test_reset_scope_is_empty_when_managed_registry_is_missing():
    assert _configured_managed_ids({"academic_year": "2026/2027"}) == (set(), set(), set())

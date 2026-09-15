from services import year_management


def test_rollback_updates_database_config_and_refreshes_cache(monkeypatch):
    config = {"academic_year": "2026/2027", "levels": []}
    row = {"name": "2025/2026"}
    state = {"active": "2026/2027", "config": config.copy(), "committed": False, "rolled_back": False}

    class FakeConn:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def execute(self, sql, params=()):
            if "UPDATE academic_years SET is_active=0" in sql:
                state["active"] = None
            elif "UPDATE academic_years SET is_active=1" in sql:
                state["active"] = params[1]
                return type("Result", (), {"rowcount": 1})()
            return type("Result", (), {"rowcount": 1})()
        def commit(self):
            state["committed"] = True
        def rollback(self):
            state["rolled_back"] = True

    monkeypatch.setattr(year_management, "get_guild_config", lambda _guild: config.copy())
    monkeypatch.setattr(year_management, "list_academic_years", lambda _guild: [row])
    monkeypatch.setattr(year_management, "_connect", lambda: FakeConn())
    monkeypatch.setattr(year_management, "_refresh_json_cache", lambda: state.__setitem__("cache_refreshed", True))

    previous, changed = year_management.rollback_guild_config_year(1, "2025/2026")

    assert previous == "2026/2027"
    assert changed is True
    assert state["active"] == "2025/2026"
    assert state["committed"] is True
    assert state.get("cache_refreshed") is True
    assert state["rolled_back"] is False


def test_rollback_rejects_unknown_year(monkeypatch):
    monkeypatch.setattr(year_management, "get_guild_config", lambda _guild: {"academic_year": "2026/2027"})
    monkeypatch.setattr(year_management, "list_academic_years", lambda _guild: [])

    try:
        year_management.rollback_guild_config_year(1, "2024/2025")
    except ValueError as exc:
        assert "n'est pas enregistrée" in str(exc)
    else:
        raise AssertionError("Unknown academic year must be rejected")

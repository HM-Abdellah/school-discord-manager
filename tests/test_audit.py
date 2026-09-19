from services import audit


def test_record_event_is_best_effort_when_sqlite_fails(monkeypatch, capsys):
    def fail_initialize():
        raise OSError("disk failure")

    monkeypatch.setattr(audit, "initialize_audit_log", fail_initialize)

    result = audit.record_event(1, 2, "Admin", "build")

    assert result is None
    assert "Event recording failed" in capsys.readouterr().out


def test_recent_events_returns_empty_when_sqlite_fails(monkeypatch, capsys):
    def fail_initialize():
        raise OSError("disk failure")

    monkeypatch.setattr(audit, "initialize_audit_log", fail_initialize)

    assert audit.recent_events(1) == []
    assert "Event read failed" in capsys.readouterr().out

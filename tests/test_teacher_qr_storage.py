import sqlite3

from services import storage


def _configure_storage(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "CONFIG_FILE", data_dir / "guild_config.json")
    monkeypatch.setattr(storage, "DATABASE_FILE", data_dir / "school.db")


def test_teacher_onboarding_request_is_idempotent_for_pending_member(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    storage.initialize_database()

    first = storage.submit_teacher_onboarding_request(
        1,
        101,
        "Prof A",
        "male",
        "Mathématiques",
        "2BACPC",
        "Disponible le mercredi.",
    )
    second = storage.submit_teacher_onboarding_request(
        1,
        101,
        "Prof A Updated",
        "female",
        "Physique et Chimie",
        "1BACSE, 2BACPC",
        "",
    )

    assert first == second
    row = storage.get_teacher_onboarding_request(1, 101, status="pending")
    assert row is not None
    assert row["full_name"] == "Prof A Updated"
    assert row["profile_type"] == "female"
    assert row["subjects_text"] == "Physique et Chimie"
    assert row["streams_text"] == "1BACSE, 2BACPC"


def test_teacher_onboarding_request_can_be_approved_or_rejected(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    storage.initialize_database()

    storage.submit_teacher_onboarding_request(1, 101, "Prof A", "male", "Math", "2BACPC")
    storage.approve_teacher_onboarding_request(1, 101, 9001)
    row = storage.get_teacher_onboarding_request(1, 101, status="approved")
    assert row is not None
    assert row["reviewed_by"] == 9001

    storage.submit_teacher_onboarding_request(1, 102, "Prof B", "female", "SVT", "2BACSVT")
    storage.reject_teacher_onboarding_request(1, 102, 9002, "Compte non reconnu")
    rejected = storage.get_teacher_onboarding_request(1, 102, status="rejected")
    assert rejected is not None
    assert rejected["reviewed_by"] == 9002
    assert rejected["rejection_reason"] == "Compte non reconnu"


def test_only_one_active_teacher_qr_exists_per_guild(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    storage.initialize_database()

    storage.record_teacher_qr_invite(1, "AAA111", 501, 9001, "2026-10-08T10:00:00+00:00", "2026-10-08T10:30:00+00:00", 10)

    try:
        storage.record_teacher_qr_invite(1, "BBB222", 501, 9001, "2026-10-08T10:01:00+00:00", "2026-10-08T10:31:00+00:00", 10)
    except RuntimeError as exc:
        assert "actif" in str(exc)
    else:
        raise AssertionError("A second active teacher QR was accepted.")

    rows = storage.get_teacher_qr_invites(1)
    assert [row["invite_code"] for row in rows] == ["AAA111"]

    storage.mark_teacher_qr_invite_revoked(1, "AAA111")
    storage.record_teacher_qr_invite(1, "BBB222", 501, 9001, "2026-10-08T10:02:00+00:00", "2026-10-08T10:32:00+00:00", 10)
    assert [row["invite_code"] for row in storage.get_teacher_qr_invites(1)] == ["BBB222"]


def test_reset_guild_data_clears_teacher_onboarding_state(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    storage.initialize_database()
    storage.record_teacher_qr_invite(1, "AAA111", 501, 9001, "2026-10-08T10:00:00+00:00", "2026-10-08T10:30:00+00:00", 10)
    storage.submit_teacher_onboarding_request(1, 101, "Prof A", "male", "Math", "2BACPC")

    storage.reset_guild_data(1)

    assert storage.get_teacher_qr_invites(1) == []
    assert storage.get_teacher_onboarding_request(1, 101) is None
    with storage._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM teacher_qr_invites WHERE guild_id=1"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM teacher_onboarding_requests WHERE guild_id=1"
        ).fetchone()[0] == 0

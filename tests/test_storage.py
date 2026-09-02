from services import storage


def test_json_save_is_atomic_and_round_trips(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "CONFIG_FILE", data_dir / "guild_config.json")
    monkeypatch.setattr(storage, "DATABASE_FILE", data_dir / "school.db")
    payload = {"123": {"academic_year": "2026/2027", "levels": []}}
    storage.save_all(payload)
    assert storage.load_all() == payload
    assert not list(data_dir.glob(".guild_config.json.*"))


def test_sqlite_uses_wal_and_busy_timeout(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "DATABASE_FILE", data_dir / "school.db")
    conn = storage._connect()
    try:
        assert str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000
    finally:
        conn.close()


def test_duplicate_active_enrollments_are_deduplicated_before_unique_index(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "DATABASE_FILE", data_dir / "school.db")
    storage.initialize_database()
    with storage._connect() as conn:
        conn.execute("DROP INDEX uq_one_active_enrollment_per_student")
        year = conn.execute("INSERT INTO academic_years(guild_id,name,is_active,created_at) VALUES(1,'2026/2027',1,'2026-09-01')").lastrowid
        stream = conn.execute("INSERT INTO streams(guild_id,academic_year_id,level_name,stream_name,role_name) VALUES(1,?,?,?,?)", (year, 'TC', 'TCS', 'Filière - TCS')).lastrowid
        student = conn.execute("INSERT INTO students(guild_id,discord_id,display_name,created_at) VALUES(1,99,'Student','2026-09-01')").lastrowid
        conn.execute("INSERT INTO enrollments(student_id,stream_id,start_date,status) VALUES(?,?,?,'active')", (student, stream, '2026-09-01'))
        conn.execute("INSERT INTO enrollments(student_id,stream_id,start_date,status) VALUES(?,?,?,'active')", (student, stream, '2026-09-02'))
    storage.initialize_database()
    with storage._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM enrollments WHERE student_id=? AND status='active'", (student,)).fetchone()[0] == 1
        assert conn.execute("PRAGMA index_list('enrollments')").fetchall()


def test_enrolling_same_stream_twice_is_idempotent(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "DATABASE_FILE", data_dir / "school.db")
    storage.initialize_database()
    year_id = storage.ensure_academic_year(1, "2026/2027", active=True)
    storage.sync_configuration_to_database(1, {"academic_year": "2026/2027", "levels": [{"name": "TC", "streams": [{"name": "TCS", "abbreviation": "TCS"}]}]})
    storage.enroll_student_record(1, 99, "Student", year_id, "TC", "TCS")
    storage.enroll_student_record(1, 99, "Student", year_id, "TC", "TCS")
    with storage._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM enrollments WHERE student_id=1 AND status='active'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM enrollments WHERE student_id=1").fetchone()[0] == 1

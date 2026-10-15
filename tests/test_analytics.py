import csv
from datetime import datetime, timedelta, timezone

import pytest

from services import analytics, audit, storage


def _configure_storage(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "CONFIG_FILE", data_dir / "guild_config.json")
    monkeypatch.setattr(storage, "DATABASE_FILE", data_dir / "school.db")
    return data_dir


def test_command_usage_records_no_user_identity_fields(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    analytics.initialize_analytics()
    assert analytics.record_command_usage(101, "status", "completed")
    assert analytics.record_command_usage(101, "status", "raised_error")

    with storage._connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(command_usage_events)")}
        rows = conn.execute(
            "SELECT guild_id, command_name, outcome, created_at FROM command_usage_events ORDER BY id"
        ).fetchall()

    assert {"guild_id", "command_name", "outcome", "created_at"}.issubset(columns)
    assert not {"user_id", "actor_id", "username", "target", "arguments", "message_content"} & columns
    assert len(rows) == 2
    assert [row["outcome"] for row in rows] == ["completed", "raised_error"]


def test_report_is_aggregated_by_day_command_and_outcome(tmp_path, monkeypatch):
    data_dir = _configure_storage(tmp_path, monkeypatch)
    analytics.initialize_analytics()
    now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)

    analytics.record_command_usage(101, "status", "completed", occurred_at=now - timedelta(hours=2))
    analytics.record_command_usage(101, "status", "completed", occurred_at=now - timedelta(hours=1))
    analytics.record_command_usage(101, "build", "raised_error", occurred_at=now - timedelta(minutes=30))
    analytics.record_command_usage(202, "status", "completed", occurred_at=now - timedelta(hours=1))

    report_path, summary = analytics.export_usage_report(101, 7, now=now)

    with report_path.open("r", encoding="utf-8-sig", newline="") as handle:
        report_rows = list(csv.DictReader(handle))

    assert summary["total"] == 3
    assert summary["completed"] == 2
    assert summary["raised_errors"] == 1
    assert {row["command_or_action"] for row in report_rows} == {"status", "build"}
    assert {row["category"] for row in report_rows} == {"command_invocation"}
    assert len(report_rows) == 2
    assert all(row["date_utc"] == "2026-10-14" for row in report_rows)
    assert report_path.parent == data_dir / "reports"
    assert report_path.exists()
    assert "101" not in report_path.read_text(encoding="utf-8-sig")


def test_audited_actions_are_aggregated_without_actor_or_target_details(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    analytics.initialize_analytics()
    now = datetime.now(timezone.utc)
    audit.record_event(101, 501, "Private Admin Name", "assignstudent", "Private Student Name", "Private details")

    report_path, summary = analytics.export_usage_report(101, 1, now=now + timedelta(seconds=2))
    with report_path.open("r", encoding="utf-8-sig", newline="") as handle:
        report_rows = list(csv.DictReader(handle))

    audited = [row for row in report_rows if row["category"] == "audited_action"]
    assert len(audited) == 1
    assert audited[0]["command_or_action"] == "assignstudent"
    assert audited[0]["outcome"] == "recorded"
    assert audited[0]["count"] == "1"
    assert summary["audited_actions"] == 1
    csv_text = report_path.read_text(encoding="utf-8-sig")
    assert "Private Admin Name" not in csv_text
    assert "Private Student Name" not in csv_text
    assert "Private details" not in csv_text


def test_usage_report_command_does_not_inflate_usage_metrics(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    analytics.initialize_analytics()
    assert not analytics.record_command_usage(101, "usagereport", "completed")
    with storage._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM command_usage_events").fetchone()[0] == 0


def test_old_usage_events_are_pruned_to_retention_window(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    analytics.initialize_analytics()
    too_old = datetime.now(timezone.utc) - timedelta(days=analytics.RETENTION_DAYS + 2)
    analytics.record_command_usage(101, "old", "completed", occurred_at=too_old)
    with storage._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM command_usage_events").fetchone()[0] == 0


def test_report_days_are_bounded(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    analytics.initialize_analytics()
    with pytest.raises(ValueError):
        analytics.export_usage_report(101, 0)
    with pytest.raises(ValueError):
        analytics.export_usage_report(101, analytics.REPORT_MAX_DAYS + 1)


@pytest.mark.asyncio
async def test_usage_report_command_registers_with_management_permission():
    import discord
    from discord.ext import commands

    from cogs.analytics import UsageAnalytics

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    await bot.add_cog(UsageAnalytics(bot))
    command = bot.tree.get_command("usagereport")
    assert command is not None
    assert command.default_permissions is not None
    assert command.default_permissions.manage_roles is True
    await bot.close()

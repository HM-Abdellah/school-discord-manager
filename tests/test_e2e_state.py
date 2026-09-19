from types import SimpleNamespace

import discord

from e2e.state import capture_permission_overwrites, diff_snapshots


def test_permission_overwrites_are_captured_as_deterministic_bitmasks(monkeypatch):
    class FakeRole:
        def __init__(self, role_id):
            self.id = role_id

    monkeypatch.setattr("e2e.state.discord.Role", FakeRole)
    role = FakeRole(42)
    overwrite = discord.PermissionOverwrite(view_channel=True, send_messages=False)
    channel = SimpleNamespace(overwrites={role: overwrite})

    captured = capture_permission_overwrites(channel)

    assert len(captured) == 1
    assert captured[0].target_id == 42
    assert captured[0].target_type == "role"
    assert captured[0].allow > 0
    assert captured[0].deny > 0


def test_permission_changes_appear_in_snapshot_diff():
    before = {
        "channels": [{
            "id": 100,
            "name": "📚-TCS・Mathématiques",
            "type": "text",
            "category_id": 200,
            "position": 1,
            "permission_overwrites": [{
                "target_id": 42,
                "target_type": "role",
                "allow": 1024,
                "deny": 2048,
            }],
        }],
        "roles": [],
    }
    after = {
        "channels": [{
            "id": 100,
            "name": "📚-TCS・Mathématiques",
            "type": "text",
            "category_id": 200,
            "position": 1,
            "permission_overwrites": [{
                "target_id": 42,
                "target_type": "role",
                "allow": 1024,
                "deny": 4096,
            }],
        }],
        "roles": [],
    }

    diff = diff_snapshots(before, after)

    assert diff["channels"]["changed"]
    assert diff["channels"]["changed"][0]["before"]["permission_overwrites"][0]["deny"] == 2048
    assert diff["channels"]["changed"][0]["after"]["permission_overwrites"][0]["deny"] == 4096

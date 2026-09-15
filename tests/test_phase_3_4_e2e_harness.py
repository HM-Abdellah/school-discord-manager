from e2e.state import diff_snapshots


def test_diff_snapshots_is_id_based_and_deterministic():
    before = {
        "channels": [
            {"id": 20, "name": "voice", "type": "voice", "category_id": 10, "position": 2},
            {"id": 10, "name": "category", "type": "category", "category_id": None, "position": 1},
        ],
        "roles": [
            {"id": 5, "name": "Member", "managed": False, "default": False, "position": 1},
        ],
    }
    after = {
        "channels": [
            {"id": 20, "name": "voice-renamed", "type": "voice", "category_id": 10, "position": 2},
            {"id": 30, "name": "new", "type": "text", "category_id": 10, "position": 3},
        ],
        "roles": [
            {"id": 7, "name": "Teacher", "managed": False, "default": False, "position": 2},
        ],
    }

    diff = diff_snapshots(before, after)

    assert [item["id"] for item in diff["channels"]["added"]] == [30]
    assert [item["id"] for item in diff["channels"]["removed"]] == [10]
    assert [item["before"]["id"] for item in diff["channels"]["changed"]] == [20]
    assert [item["id"] for item in diff["roles"]["added"]] == [7]
    assert [item["id"] for item in diff["roles"]["removed"]] == [5]


def test_diff_snapshots_does_not_treat_renamed_resource_as_new_resource():
    before = {
        "channels": [
            {"id": 42, "name": "managed", "type": "text", "category_id": 1, "position": 1},
        ],
        "roles": [],
    }
    after = {
        "channels": [
            {"id": 42, "name": "managed-renamed-by-human", "type": "text", "category_id": 1, "position": 1},
        ],
        "roles": [],
    }

    diff = diff_snapshots(before, after)

    assert diff["channels"]["added"] == []
    assert diff["channels"]["removed"] == []
    assert len(diff["channels"]["changed"]) == 1

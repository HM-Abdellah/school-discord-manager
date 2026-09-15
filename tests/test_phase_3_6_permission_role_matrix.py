import json
from pathlib import Path


MATRIX_PATH = Path(__file__).parents[1] / "e2e" / "matrix" / "permission_roles.json"


def load_matrix():
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def test_permission_matrix_uses_two_account_model():
    payload = load_matrix()
    actors = payload["actors"]

    assert set(actors) == {"OWNER", "MEMBER", "MEMBER_ADMIN"}
    assert payload["cases"]
    assert all("id" in case and "actor" in case and "expectation" in case for case in payload["cases"])
    assert len({case["id"] for case in payload["cases"]}) == len(payload["cases"])


def test_permission_matrix_covers_authorization_hierarchy_and_role_conflicts():
    payload = load_matrix()
    cases = payload["cases"]

    ids = {case["id"] for case in cases}
    assert {"AUTH-001", "AUTH-002", "AUTH-003", "AUTH-004"} <= ids
    assert any(case["id"].startswith("ROLE-") for case in cases)
    assert any("Manage Channels" in case["expectation"] or "Manage Channels" in case["precondition"] for case in cases)
    assert any("Manage Roles" in case["expectation"] or "Manage Roles" in case["precondition"] for case in cases)

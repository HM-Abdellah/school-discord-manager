import json
from pathlib import Path


MATRIX_PATH = Path(__file__).parents[1] / "e2e" / "matrix" / "failure_recovery.json"


def load_matrix():
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def test_failure_recovery_matrix_has_unique_cases():
    payload = load_matrix()
    cases = payload["cases"]
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    assert len(cases) >= 15
    assert all(case["id"].startswith("FAIL-") for case in cases)


def test_failure_recovery_matrix_covers_destructive_and_persistence_boundaries():
    payload = load_matrix()
    text = " ".join(
        case["operation"] + " " + case["fault"] + " " + case["expectation"]
        for case in payload["cases"]
    ).casefold()
    assert "/removestream" in text
    assert "/resetserver" in text
    assert "persist" in text
    assert "retry" in text
    assert "unmanaged" in text

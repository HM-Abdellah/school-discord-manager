import json
from pathlib import Path


MATRIX_PATH = Path(__file__).parents[1] / "e2e" / "matrix" / "concurrency_regression.json"


def load_matrix():
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def test_concurrency_regression_matrix_has_unique_cases():
    payload = load_matrix()
    cases = payload["cases"]
    ids = [case["id"] for case in cases]
    assert len(cases) >= 12
    assert len(ids) == len(set(ids))
    assert any(case["id"].startswith("CONC-") for case in cases)
    assert any(case["id"].startswith("REG-") for case in cases)


def test_concurrency_matrix_covers_shared_mutation_boundary():
    payload = load_matrix()
    text = " ".join(case["operation"] + " " + case["expectation"] for case in payload["cases"]).casefold()
    assert "build lock" in text
    assert "serialize" in text
    assert "/resetserver" in text
    assert "/removestream" in text


def test_regression_matrix_covers_persistence_and_identity_drift():
    payload = load_matrix()
    text = " ".join(case["operation"] + " " + case["expectation"] for case in payload["cases"]).casefold()
    assert "sqlite" in text
    assert "json" in text
    assert "managed id" in text
    assert "unmanaged" in text

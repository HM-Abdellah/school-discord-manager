import json
from pathlib import Path


MATRIX_PATH = Path(__file__).parents[1] / "e2e" / "matrix" / "sections_timetable_exam.json"


def load_matrix():
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def test_section_domain_is_explicitly_bounded():
    payload = load_matrix()
    domain = payload["domain"]
    assert domain["section_min"] == 1
    assert domain["section_max"] == 8
    assert "not a stream" in domain["architecture"]


def test_section_matrix_covers_both_edges_and_rejection_boundaries():
    payload = load_matrix()
    inputs = {case["input"] for case in payload["cases"]}
    assert "section=1" in inputs
    assert "section=8" in inputs
    assert "section=0" in inputs
    assert "section=9" in inputs


def test_section_matrix_enforces_shared_channel_model():
    payload = load_matrix()
    expectations = " ".join(case["expectation"] for case in payload["cases"])
    assert "channel count" in expectations
    assert "shared stream timetable channel" in expectations
    assert "shared stream exam channel" in expectations
    assert "does not create an unregistered replacement channel" in expectations

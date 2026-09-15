import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
INDEX = ROOT / "e2e" / "matrix" / "index.json"


def test_phase_3_matrix_manifest_is_complete():
    payload = json.loads(INDEX.read_text(encoding="utf-8"))
    assert payload["phase"] == "3"
    names = payload["matrices"]
    assert len(names) == 5
    assert len(names) == len(set(names))
    for name in names:
        path = ROOT / "e2e" / "matrix" / name
        assert path.is_file(), name
        matrix = json.loads(path.read_text(encoding="utf-8"))
        assert matrix["cases"], name


def test_phase_3_global_case_ids_are_unique():
    payload = json.loads(INDEX.read_text(encoding="utf-8"))
    case_ids = []
    for name in payload["matrices"]:
        matrix = json.loads((ROOT / "e2e" / "matrix" / name).read_text(encoding="utf-8"))
        case_ids.extend(case["id"] for case in matrix["cases"])
    assert len(case_ids) == len(set(case_ids))


def test_phase_3_gate_requires_real_live_e2e_execution():
    payload = json.loads(INDEX.read_text(encoding="utf-8"))
    required = payload["gate"]["required_for_phase_completion"]
    assert "live E2E scenarios executed against a dedicated Discord test guild" in required
    assert payload["execution_model"]["account_count"] == 2
    assert "self-bots" in payload["execution_model"]["live_automation_note"]

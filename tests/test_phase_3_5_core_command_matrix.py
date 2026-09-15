import json
from pathlib import Path


MATRIX_PATH = Path(__file__).parents[1] / "e2e" / "matrix" / "core_commands.json"
REQUIRED_KEYS = {"id", "command", "precondition", "action", "discord_expectation"}


def load_matrix():
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def test_core_command_matrix_is_structured():
    payload = load_matrix()

    assert payload["phase"] == "3.5"
    cases = payload["cases"]
    assert len(cases) >= 15

    ids = [case["id"] for case in cases]
    commands = [case["command"] for case in cases]
    assert len(ids) == len(set(ids))
    assert len(commands) == len(set(commands))

    for case in cases:
        assert REQUIRED_KEYS <= set(case)
        assert case["id"].startswith("CORE-")
        assert case["command"].startswith("/")
        assert all(isinstance(case[key], str) and case[key].strip() for key in REQUIRED_KEYS - {"id"})


def test_core_matrix_covers_destructive_and_repetition_paths():
    payload = load_matrix()
    by_command = {case["command"]: case for case in payload["cases"]}

    assert "/removestream" in by_command
    assert "/resetserver" in by_command
    assert "unmanaged" in by_command["/removestream"]["action"]
    assert "unmanaged" in by_command["/resetserver"]["action"]
    assert "Repeat" in by_command["/build"]["action"]

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _runtime_extensions() -> tuple[str, ...]:
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "SchoolBot":
            continue
        for statement in node.body:
            if not isinstance(statement, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "EXTENSIONS" for target in statement.targets):
                continue
            value = ast.literal_eval(statement.value)
            assert isinstance(value, tuple)
            assert all(isinstance(item, str) for item in value)
            return value

    raise AssertionError("SchoolBot.EXTENSIONS was not found")


def test_edge_case_hardening_is_not_a_runtime_dependency():
    assert "cogs.edge_case_hardening" not in _runtime_extensions()


def test_removestream_has_a_single_runtime_owner():
    extensions = _runtime_extensions()
    assert extensions.count("cogs.removestream_fix") == 1

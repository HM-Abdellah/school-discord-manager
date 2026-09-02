import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _function_node(path: Path, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"Function not found: {name}")


def _call_line_numbers(node):
    result = {}
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                result.setdefault(child.func.id, []).append(child.lineno)
            elif isinstance(child.func, ast.Attribute):
                result.setdefault(child.func.attr, []).append(child.lineno)
    return result


def test_setup_build_happens_before_config_persistence():
    node = _function_node(ROOT / "cogs" / "setup.py", "build_callback")
    lines = _call_line_numbers(node)
    assert min(lines["build"]) < min(lines["save_guild_config"])


def test_assignstudent_uses_atomic_record_helper_after_discord_mutation():
    node = _function_node(ROOT / "cogs" / "students.py", "assign_student")
    lines = _call_line_numbers(node)
    assert min(lines["add_roles"]) < min(lines["enroll_student_record"])


def test_leave_school_marks_database_after_discord_role_removal():
    node = _function_node(ROOT / "cogs" / "students.py", "leave_school")
    lines = _call_line_numbers(node)
    assert min(lines["remove_roles"]) < min(lines["mark_student_left"])

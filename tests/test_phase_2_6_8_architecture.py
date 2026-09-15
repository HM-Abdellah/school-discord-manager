import ast
from pathlib import Path

from bot import SchoolBot

ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    "removestream": "cogs.removestream_fix",
    "setexam": "cogs.section_aware_exam",
    "set_timetable": "cogs.section_aware_timetable",
    "newyear": "cogs.server_v3",
    "rollbackyear": "cogs.year_rollback",
    "resetserver": "cogs.security_hardening_v3",
    "assignstudent": "cogs.security_hardening_v3",
    "assignteacher": "cogs.security_hardening_v3",
    "assignteacherfull": "cogs.command_fixes",
    "assignsubjectteachers": "cogs.command_fixes",
    "reportabsence": "cogs.command_fixes",
}


def command_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)):
                continue
            if decorator.func.attr != "command":
                continue
            for keyword in decorator.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    result.append(str(keyword.value.value))
    return result


def runtime_definitions() -> dict[str, list[str]]:
    definitions = {name: [] for name in EXPECTED}
    for extension in SchoolBot.EXTENSIONS:
        path = ROOT / (extension.replace(".", "/") + ".py")
        for name in command_names(path):
            if name in definitions:
                definitions[name].append(extension)
    return definitions


def test_runtime_commands_have_exactly_one_owner():
    definitions = runtime_definitions()
    assert all(definitions[name] == [owner] for name, owner in EXPECTED.items())


def test_security_v2_is_not_a_runtime_extension_or_command_source():
    assert "cogs.security_hardening_v2" not in SchoolBot.EXTENSIONS
    legacy = ROOT / "cogs" / "security_hardening_v2.py"
    assert command_names(legacy) == []
    source = legacy.read_text(encoding="utf-8")
    assert "from cogs.setup import" not in source
    assert "OVERRIDDEN_COMMANDS" not in source
    assert "_patch_setup_build_callback" not in source


def test_runtime_cogs_do_not_import_command_cogs():
    excluded = {"cogs.admin"}
    for extension in SchoolBot.EXTENSIONS:
        if extension in excluded or not extension.startswith("cogs."):
            continue
        source = (ROOT / (extension.replace(".", "/") + ".py")).read_text(encoding="utf-8")
        assert "from cogs." not in source, extension
        assert "import cogs." not in source, extension


def test_security_v3_declares_only_its_three_commands():
    assert sorted(command_names(ROOT / "cogs" / "security_hardening_v3.py")) == [
        "assignstudent",
        "assignteacher",
        "resetserver",
    ]

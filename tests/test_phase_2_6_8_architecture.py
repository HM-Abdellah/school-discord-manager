import ast
import os
from pathlib import Path

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("DISCORD_GUILD_ID", "123456789")

from bot import SchoolBot

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "removestream": "cogs.removestream_fix",
    "setexam": "cogs.section_aware_exam",
    "set_timetable": "cogs.section_aware_timetable",
    "newyear": "cogs.server_v3",
    "rollbackyear": "cogs.year_rollback",
    "resetserver": "cogs.security_hardening_v3",
    "assignstudent": "cogs.students",
    "assignteacher": "cogs.teachers",
    "assignteacherfull": "cogs.command_fixes",
    "assignsubjectteachers": "cogs.teachers",
    "reportabsence": "cogs.teachers",
}


def command_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: list[str] = []
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
    mismatches = {
        name: {"expected": owner, "found": definitions[name]}
        for name, owner in EXPECTED.items()
        if definitions[name] != [owner]
    }
    assert not mismatches, f"Runtime command ownership mismatch: {mismatches}"


def test_security_v2_is_not_a_runtime_extension_or_command_source():
    assert "cogs.security_hardening_v2" not in SchoolBot.EXTENSIONS
    legacy = ROOT / "cogs" / "security_hardening_v2.py"
    assert command_names(legacy) == []
    source = legacy.read_text(encoding="utf-8")
    assert "from cogs.setup import" not in source
    assert "OVERRIDDEN_COMMANDS" not in source
    assert "_patch_setup_build_callback" not in source


def test_runtime_cogs_do_not_import_command_cogs():
    for extension in SchoolBot.EXTENSIONS:
        if not extension.startswith("cogs.") or extension == "cogs.admin":
            continue
        source = (ROOT / (extension.replace(".", "/") + ".py")).read_text(encoding="utf-8")
        assert "from cogs." not in source, extension
        assert "import cogs." not in source, extension


def test_security_v3_declares_only_resetserver():
    assert command_names(ROOT / "cogs" / "security_hardening_v3.py") == ["resetserver"]

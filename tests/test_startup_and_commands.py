import ast
import importlib
import inspect
import os
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest
from discord.ext import commands

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("DISCORD_GUILD_ID", "123456789")

from bot import SchoolBot
from cogs.admin import AdminCommands
from cogs.server_v3 import _configured_managed_ids, _expected_structure_names

EXPECTED_RUNTIME_OWNERS = {
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


def _command_names_in_file(path: str) -> list[str]:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=path)
    names: list[str] = []
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
                    names.append(str(keyword.value.value))
    return names


def test_all_bot_extensions_have_async_setup_entrypoints():
    for extension in SchoolBot.EXTENSIONS:
        module = importlib.import_module(extension)
        setup = getattr(module, "setup", None)
        assert setup is not None
        assert inspect.iscoroutinefunction(setup)


def test_runtime_loader_declares_final_command_owners():
    extensions = list(SchoolBot.EXTENSIONS)
    assert "cogs.edge_case_hardening" not in extensions
    assert "cogs.discord_aware_commands" not in extensions
    assert "cogs.security_hardening_v2" not in extensions
    assert "cogs.security_hardening_v3" in extensions
    assert extensions.index("cogs.removestream_fix") < extensions.index("cogs.section_aware_exam")
    assert extensions.index("cogs.section_aware_exam") < extensions.index("cogs.section_aware_timetable")


def test_critical_commands_have_one_source_definition_and_expected_owner():
    modules = {
        "cogs.server_v3": "cogs/server_v3.py",
        "cogs.students": "cogs/students.py",
        "cogs.teachers": "cogs/teachers.py",
        "cogs.security_hardening_v3": "cogs/security_hardening_v3.py",
        "cogs.command_fixes": "cogs/command_fixes.py",
        "cogs.removestream_fix": "cogs/removestream_fix.py",
        "cogs.section_aware_exam": "cogs/section_aware_exam.py",
        "cogs.section_aware_timetable": "cogs/section_aware_timetable.py",
        "cogs.year_rollback": "cogs/year_rollback.py",
    }
    definitions: dict[str, list[str]] = {command: [] for command in EXPECTED_RUNTIME_OWNERS}
    for module, path in modules.items():
        for command in _command_names_in_file(path):
            if command in definitions:
                definitions[command].append(module)
    for command, owner in EXPECTED_RUNTIME_OWNERS.items():
        assert definitions[command] == [owner], f"{command}: {definitions[command]}"


def test_command_cogs_have_no_cross_cog_imports():
    for extension in SchoolBot.EXTENSIONS:
        if not extension.startswith("cogs."):
            continue
        source = Path(extension.replace(".", "/") + ".py").read_text(encoding="utf-8")
        assert "from cogs." not in source
        assert "import cogs." not in source


def test_security_v3_owns_only_resetserver():
    assert _command_names_in_file("cogs/security_hardening_v3.py") == ["resetserver"]


def test_legacy_security_v2_has_no_commands():
    assert _command_names_in_file("cogs/security_hardening_v2.py") == []


def test_shared_command_helpers_live_outside_cogs():
    autocomplete = Path("services/command_autocomplete.py").read_text(encoding="utf-8")
    assert "async def level_autocomplete" in autocomplete
    assert "async def stream_autocomplete" in autocomplete
    resolver = Path("services/discord_registry.py").read_text(encoding="utf-8")
    assert "async def resolve_managed_text_channel" in resolver
    assert "def persist_registry_repair" in resolver


def test_section_aware_commands_are_configured_for_maximum_section_eight():
    exam_source = inspect.getsource(importlib.import_module("cogs.section_aware_exam"))
    timetable_source = inspect.getsource(importlib.import_module("cogs.section_aware_timetable"))
    assert "MAX_SECTIONS = 8" in exam_source
    assert "app_commands.Range[int, 1, MAX_SECTIONS]" in exam_source
    assert "MAX_SECTIONS = 8" in timetable_source
    assert "app_commands.Range[int, 1, MAX_SECTIONS]" in timetable_source


def test_legacy_resource_discovery_requires_exact_canonical_names():
    config = {"levels": [{"name": "Tronc Commun", "streams": [{"name": "Tronc Commun Scientifique", "abbreviation": "TCS", "subjects": ["Mathématiques"]}]}], "managed": {"roles": {}, "channels": {}, "categories": {}}}
    managed_category = SimpleNamespace(id=301, name="📘・TC・🔬 TCS", channels=[SimpleNamespace(id=401, name="📌-TCS・informations")])
    unrelated_category = SimpleNamespace(id=302, name="My TCS Club", channels=[SimpleNamespace(id=402, name="club-chat")])
    guild = SimpleNamespace(categories=[managed_category, unrelated_category], channels=[managed_category.channels[0], unrelated_category.channels[0]], roles=[SimpleNamespace(id=501, name="Filière - TCS", managed=False), SimpleNamespace(id=502, name="My TCS Role", managed=False)])
    roles, channels, categories = _configured_managed_ids(config, guild)
    assert roles == {501}
    assert channels == {401}
    assert categories == {301}


def test_build_structure_does_not_require_on_demand_subject_roles():
    config = {"levels": [{"name": "Tronc Commun", "streams": [{"name": "Tronc Commun Scientifique", "abbreviation": "TCS", "subjects": ["Mathématiques", "Physique-Chimie"]}]}]}
    expected_roles, expected_categories, expected_channels = _expected_structure_names(config)
    assert expected_roles == {"Administration", "Prof", "Prof (F)", "Élève", "Filière - TCS", "Élèves - TCS"}
    assert "Matière - TCS - MAT" not in expected_roles
    assert "Matière - TCS - PC" not in expected_roles
    assert len(expected_channels["📘・TC・🔬 TCS"]) == 5


@pytest.mark.asyncio
async def test_admin_commands_register_only_admin_dashboard_commands():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    await bot.add_cog(AdminCommands(bot))
    assert bot.tree.get_command("adminpanel") is not None
    assert bot.tree.get_command("serverhealth") is not None
    assert bot.tree.get_command("setexam") is None
    await bot.close()


def test_bot_startup_has_no_runtime_fix_dependency():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert "runtime_fixes" not in source
    assert "apply_runtime_fixes" not in source

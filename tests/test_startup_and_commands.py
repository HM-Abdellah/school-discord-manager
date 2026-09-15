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
    "resetserver": "cogs.security_hardening_v2",
    "assignstudent": "cogs.security_hardening_v2",
    "assignteacher": "cogs.security_hardening_v2",
    "assignteacherfull": "cogs.command_fixes",
    "assignsubjectteachers": "cogs.command_fixes",
    "reportabsence": "cogs.command_fixes",
}


def _command_names_in_file(path: str) -> list[str]:
    source = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=path)
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "command"
            ):
                continue
            for keyword in decorator.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    names.append(str(keyword.value.value))
    return names


def test_all_bot_extensions_have_async_setup_entrypoints():
    for extension in SchoolBot.EXTENSIONS:
        module = importlib.import_module(extension)
        setup = getattr(module, "setup", None)
        assert setup is not None, f"Missing setup() in {extension}"
        assert inspect.iscoroutinefunction(setup), f"setup() must be async in {extension}"


def test_runtime_loader_declares_final_command_owners():
    extensions = list(SchoolBot.EXTENSIONS)
    assert "cogs.edge_case_hardening" not in extensions
    assert extensions.index("cogs.removestream_fix") < extensions.index("cogs.section_aware_exam")
    assert extensions.index("cogs.section_aware_exam") < extensions.index("cogs.section_aware_timetable")


def test_critical_commands_have_one_source_definition_and_expected_owner():
    modules = {
        "cogs.server_v3": "cogs/server_v3.py",
        "cogs.security_hardening_v2": "cogs/security_hardening_v2.py",
        "cogs.command_fixes": "cogs/command_fixes.py",
        "cogs.removestream_fix": "cogs/removestream_fix.py",
        "cogs.section_aware_exam": "cogs/section_aware_exam.py",
        "cogs.section_aware_timetable": "cogs/section_aware_timetable.py",
    }
    definitions: dict[str, list[str]] = {command: [] for command in EXPECTED_RUNTIME_OWNERS}
    for module, path in modules.items():
        for command in _command_names_in_file(path):
            if command in definitions:
                definitions[command].append(module)

    for command, owner in EXPECTED_RUNTIME_OWNERS.items():
        assert definitions[command] == [owner], (
            f"{command} must have exactly one source owner; "
            f"found {definitions[command]}"
        )


def test_section_aware_commands_are_configured_for_maximum_section_eight():
    exam_module = importlib.import_module("cogs.section_aware_exam")
    timetable_module = importlib.import_module("cogs.section_aware_timetable")
    assert "app_commands.Range[int, 1, 8]" in inspect.getsource(exam_module)
    assert "MAX_SECTIONS = 8" in inspect.getsource(timetable_module)
    assert "app_commands.Range[int, 1, MAX_SECTIONS]" in inspect.getsource(timetable_module)


def test_legacy_resource_discovery_requires_exact_canonical_names():
    config = {
        "levels": [
            {
                "name": "Tronc Commun",
                "streams": [
                    {
                        "name": "Tronc Commun Scientifique",
                        "abbreviation": "TCS",
                        "subjects": ["Mathématiques"],
                    }
                ],
            }
        ],
        "managed": {"roles": {}, "channels": {}, "categories": {}},
    }
    managed_category = SimpleNamespace(
        id=301,
        name="📘・TC・🔬 TCS",
        channels=[SimpleNamespace(id=401, name="📌-TCS・informations")],
    )
    unrelated_category = SimpleNamespace(
        id=302,
        name="My TCS Club",
        channels=[SimpleNamespace(id=402, name="club-chat")],
    )
    guild = SimpleNamespace(
        categories=[managed_category, unrelated_category],
        channels=[managed_category.channels[0], unrelated_category.channels[0]],
        roles=[
            SimpleNamespace(id=501, name="Filière - TCS", managed=False),
            SimpleNamespace(id=502, name="My TCS Role", managed=False),
        ],
    )
    roles, channels, categories = _configured_managed_ids(config, guild)
    assert roles == {501}
    assert channels == {401}
    assert categories == {301}


def test_build_structure_does_not_require_on_demand_subject_roles():
    config = {
        "levels": [
            {
                "name": "Tronc Commun",
                "streams": [
                    {
                        "name": "Tronc Commun Scientifique",
                        "abbreviation": "TCS",
                        "subjects": ["Mathématiques", "Physique-Chimie"],
                    }
                ]
            }
        ]
    }
    expected_roles, expected_categories, expected_channels = _expected_structure_names(config)
    assert expected_roles == {"Administration", "Prof", "Prof (F)", "Élève", "Filière - TCS", "Élèves - TCS"}
    assert "Matière - TCS - MAT" not in expected_roles
    assert "Matière - TCS - PC" not in expected_roles
    assert len(expected_channels["📘・TC・🔬 TCS"]) == 5


@pytest.mark.asyncio
async def test_admin_commands_register_only_admin_dashboard_commands():
    intents = discord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents)
    await bot.add_cog(AdminCommands(bot))
    assert bot.tree.get_command("adminpanel") is not None
    assert bot.tree.get_command("serverhealth") is not None
    assert bot.tree.get_command("setexam") is None
    await bot.close()


def test_bot_startup_has_no_runtime_fix_dependency():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert "runtime_fixes" not in source
    assert "apply_runtime_fixes" not in source

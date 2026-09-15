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
}


def test_all_bot_extensions_have_async_setup_entrypoints():
    for extension in SchoolBot.EXTENSIONS:
        module = importlib.import_module(extension)
        setup = getattr(module, "setup", None)
        assert setup is not None, f"Missing setup() in {extension}"
        assert inspect.iscoroutinefunction(setup), f"setup() must be async in {extension}"


def test_runtime_loader_has_one_explicit_final_owner_per_overridden_command():
    extensions = list(SchoolBot.EXTENSIONS)
    assert "cogs.edge_case_hardening" not in extensions

    assert extensions.index(EXPECTED_RUNTIME_OWNERS["removestream"]) < extensions.index(EXPECTED_RUNTIME_OWNERS["setexam"])
    assert extensions.index(EXPECTED_RUNTIME_OWNERS["setexam"]) < extensions.index(EXPECTED_RUNTIME_OWNERS["set_timetable"])
    assert "cogs.command_fixes" in extensions

    final_owner_positions = {
        command: extensions.index(owner)
        for command, owner in EXPECTED_RUNTIME_OWNERS.items()
    }
    assert len(final_owner_positions) == len(EXPECTED_RUNTIME_OWNERS)


def test_section_aware_commands_are_configured_for_maximum_section_eight():
    exam_source = inspect.getsource(importlib.import_module("cogs.section_aware_exam"))
    timetable_source = inspect.getsource(importlib.import_module("cogs.section_aware_timetable"))
    assert "MAX_SECTIONS = 8" in exam_source
    assert "app_commands.Range[int, 1, MAX_SECTIONS]" in exam_source
    assert "MAX_SECTIONS = 8" in timetable_source
    assert "app_commands.Range[int, 1, MAX_SECTIONS]" in timetable_source


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

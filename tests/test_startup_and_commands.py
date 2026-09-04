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
from cogs.server_v3 import _configured_managed_ids


def test_all_bot_extensions_have_async_setup_entrypoints():
    for extension in SchoolBot.EXTENSIONS:
        module = importlib.import_module(extension)
        setup = getattr(module, "setup", None)
        assert setup is not None, f"Missing setup() in {extension}"
        assert inspect.iscoroutinefunction(setup), f"setup() must be async in {extension}"


def test_bot_startup_has_no_runtime_fix_dependency():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert "runtime_fixes" not in source
    assert "apply_runtime_fixes" not in source


@pytest.mark.asyncio
async def test_admin_commands_can_register_without_mutating_discord_metadata():
    intents = discord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents)
    await bot.add_cog(AdminCommands(bot))
    command = bot.tree.get_command("setexam")
    assert command is not None
    content_parameter = next(parameter for parameter in command.parameters if parameter.name == "content")
    assert getattr(content_parameter, "autocomplete", None) is not None
    await bot.close()


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

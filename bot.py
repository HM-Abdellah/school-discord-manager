"""Application entry point for School Discord Manager."""

from __future__ import annotations

import os

import discord
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing. Copy .env.example to .env and add your bot token."
    )


class SchoolBot(commands.Bot):
    """Main bot with application-command synchronization and cog loading."""

    EXTENSIONS = (
        "cogs.setup",
        "cogs.server",
        "cogs.students",
        "cogs.teachers",
    )

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )

    async def setup_hook(self) -> None:
        for extension in self.EXTENSIONS:
            await self.load_extension(extension)

        synced = await self.tree.sync()
        print(f"✅ Synced {len(synced)} application command(s).")

    async def on_ready(self) -> None:
        print("=" * 72)
        print("🏫 SCHOOL DISCORD MANAGER")
        print("=" * 72)
        print(f"Bot      : {self.user}")
        print(f"Bot ID   : {self.user.id}")
        print(f"Servers  : {len(self.guilds)}")
        print("Commands : /setup /build /status /assignstudent /assignteacher /reportabsence")
        print("=" * 72)


bot = SchoolBot()


if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        print("❌ Invalid Discord bot token.")
        print("Check the DISCORD_TOKEN value in .env.")

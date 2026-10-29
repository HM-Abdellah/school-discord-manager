"""Schedule low-noise keep-alive checks for managed public school threads."""

from __future__ import annotations

import logging

from discord.ext import commands, tasks

from services.thread_keepalive import keep_managed_subject_threads_alive

logger = logging.getLogger(__name__)


class ManagedThreadKeepAlive(commands.Cog):
    """Keep registered subject threads discoverable without posting visible text."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.refresh_loop.start()

    def cog_unload(self) -> None:
        self.refresh_loop.cancel()

    @tasks.loop(hours=24)
    async def refresh_loop(self) -> None:
        for guild in tuple(self.bot.guilds):
            try:
                result = await keep_managed_subject_threads_alive(guild)
            except Exception:
                # One broken guild/config must not stop scheduled maintenance
                # for the remaining guilds or cancel the loop.
                logger.exception("Unexpected thread keep-alive failure for guild %s.", guild.id)
                continue

            if result["messages_sent"] or result["errors"] or result["locked_skipped"]:
                logger.info(
                    "Thread keep-alive guild=%s channels=%s threads=%s messages=%s "
                    "locked_skipped=%s errors=%s",
                    guild.id,
                    result["channels_scanned"],
                    result["threads_scanned"],
                    result["messages_sent"],
                    result["locked_skipped"],
                    result["errors"],
                )

    @refresh_loop.before_loop
    async def before_refresh_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ManagedThreadKeepAlive(bot))

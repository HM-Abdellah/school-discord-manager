"""CLI for observing a live Discord E2E scenario.

A scenario is intentionally split into two actors:
1. a real Discord user performs the slash command in the test guild;
2. this runner captures Discord state before and after and emits a deterministic diff.

This avoids self-bot behavior while still testing the real Discord surface.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import discord

from .config import E2EConfig
from .state import capture_guild, diff_snapshots, read_snapshot, write_snapshot


class E2EObserver(discord.Client):
    def __init__(self, config: E2EConfig) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents)
        self.config = config
        self.result: discord.Guild | None = None

    async def on_ready(self) -> None:
        guild = self.get_guild(self.config.guild_id)
        if guild is None:
            try:
                guild = await self.fetch_guild(self.config.guild_id)
            except discord.HTTPException as exc:
                await self.close()
                raise RuntimeError(
                    f"cannot access E2E guild {self.config.guild_id}"
                ) from exc
        self.result = guild
        await self.close()

    async def run_capture(self, output: Path) -> None:
        await self.start(self.config.token)
        if self.result is None:
            raise RuntimeError("Discord client finished without resolving the guild")
        state = await capture_guild(self.result)
        write_snapshot(state, output)


async def capture(config: E2EConfig, output: Path) -> None:
    client = E2EObserver(config)
    await client.run_capture(output)


def command_capture(args: argparse.Namespace) -> None:
    config = E2EConfig.from_env()
    asyncio.run(capture(config, Path(args.output)))
    print(f"snapshot written: {args.output}")


def command_diff(args: argparse.Namespace) -> None:
    before = read_snapshot(Path(args.before))
    after = read_snapshot(Path(args.after))
    payload = diff_snapshots(before, after)

    import json

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="School Discord Manager E2E observer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture_parser = subparsers.add_parser(
        "capture",
        help="capture live guild state using the E2E bot token",
    )
    capture_parser.add_argument("--output", required=True, help="snapshot JSON path")
    capture_parser.set_defaults(func=command_capture)

    diff_parser = subparsers.add_parser(
        "diff",
        help="compare two previously captured snapshots",
    )
    diff_parser.add_argument("--before", required=True)
    diff_parser.add_argument("--after", required=True)
    diff_parser.set_defaults(func=command_diff)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

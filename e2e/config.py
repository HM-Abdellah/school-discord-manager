"""Configuration for the live E2E harness.

The harness intentionally uses a bot token for observation only. Slash-command
execution is performed by a real test user account in Discord; a bot cannot act
as a normal user account without violating Discord's platform rules.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class E2EConfig:
    token: str
    guild_id: int

    @classmethod
    def from_env(cls) -> "E2EConfig":
        token = os.getenv("E2E_BOT_TOKEN", "").strip()
        guild_id_raw = os.getenv("E2E_GUILD_ID", "").strip()

        if not token:
            raise RuntimeError("E2E_BOT_TOKEN is required")
        if not guild_id_raw:
            raise RuntimeError("E2E_GUILD_ID is required")
        try:
            guild_id = int(guild_id_raw)
        except ValueError as exc:
            raise RuntimeError("E2E_GUILD_ID must be numeric") from exc
        if guild_id <= 0:
            raise RuntimeError("E2E_GUILD_ID must be positive")

        return cls(token=token, guild_id=guild_id)

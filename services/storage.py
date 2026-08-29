"""Small JSON persistence layer for local bot configuration.

The generated JSON files are ignored by Git so no school/server configuration
is accidentally committed to a public repository.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_FILE = DATA_DIR / "guild_config.json"


def _ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_all() -> dict[str, Any]:
    _ensure_storage()

    if not CONFIG_FILE.exists():
        return {}

    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_all(data: dict[str, Any]) -> None:
    _ensure_storage()
    CONFIG_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_guild_config(guild_id: int) -> dict[str, Any] | None:
    return load_all().get(str(guild_id))


def save_guild_config(guild_id: int, config: dict[str, Any]) -> None:
    data = load_all()
    data[str(guild_id)] = config
    save_all(data)


def delete_guild_config(guild_id: int) -> None:
    data = load_all()
    data.pop(str(guild_id), None)
    save_all(data)

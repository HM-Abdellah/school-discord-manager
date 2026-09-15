"""Startup recovery helpers for the SQLite-backed configuration store."""

from __future__ import annotations

from services.storage import load_all, save_all


def recover_json_cache() -> bool:
    """Refresh the JSON cache from the SQLite source of truth."""
    data = load_all()
    try:
        save_all(data)
    except OSError:
        return False
    return True

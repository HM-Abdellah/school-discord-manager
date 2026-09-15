"""Crash-safe orchestration for destructive stream removal.

Discord mutations are not database transactions: once a resource is deleted,
it cannot be rolled back by the bot. This module therefore uses a small
write-ahead journal stored in the guild config. The journal is written before
any deletion and checkpointed after every successful (or already-completed)
delete so a later retry can resume idempotently.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

import discord

PENDING_REMOVAL_KEY = "pending_removal"
JOURNAL_VERSION = 1
CheckpointCallable = Callable[[dict[str, Any]], None]


def build_removal_journal(
    *,
    level: str,
    stream: str,
    code: str,
    resources: list[dict[str, Any]],
    category: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deletion plan with stable resource identities."""
    journal: dict[str, Any] = {
        "version": JOURNAL_VERSION,
        "level": level,
        "stream": stream,
        "code": code,
        "resources": deepcopy(resources),
        "completed": [],
    }
    if category is not None:
        journal["category"] = deepcopy(category)
    return journal


def install_removal_journal(
    config: dict[str, Any],
    journal: dict[str, Any],
    checkpoint: CheckpointCallable,
) -> None:
    """Persist the journal before any destructive Discord operation."""
    config[PENDING_REMOVAL_KEY] = deepcopy(journal)
    checkpoint(config)


def get_pending_removal(config: dict[str, Any]) -> dict[str, Any] | None:
    """Return a structurally valid pending-removal journal, if present."""
    value = config.get(PENDING_REMOVAL_KEY) if isinstance(config, dict) else None
    if not isinstance(value, dict) or value.get("version") != JOURNAL_VERSION:
        return None
    resources = value.get("resources")
    completed = value.get("completed")
    if not isinstance(resources, list) or not isinstance(completed, list):
        return None
    return value


def _resource_key(resource: dict[str, Any]) -> str:
    kind = str(resource.get("kind", ""))
    resource_id = resource.get("id")
    return f"{kind}:{resource_id}"


def _normalize_resource(resource: dict[str, Any]) -> dict[str, Any]:
    kind = resource.get("kind")
    resource_id = resource.get("id")
    if kind not in {"channel", "role"} or not isinstance(resource_id, int) or resource_id <= 0:
        raise ValueError(f"Invalid removal resource: {resource!r}")
    return {
        "kind": kind,
        "id": resource_id,
        "name": str(resource.get("name", "")),
    }


def _normalize_completed(completed: list[Any]) -> set[str]:
    return {
        item
        for item in completed
        if isinstance(item, str)
    }


async def execute_removal_journal(
    *,
    config: dict[str, Any],
    journal: dict[str, Any],
    resolve: Callable[[dict[str, Any]], Any | None],
    checkpoint: CheckpointCallable,
) -> None:
    """Execute all pending deletions idempotently and checkpoint each one.

    ``resolve`` must return the live Discord object for an existing ID, or
    ``None`` when that ID is already gone. ``NotFound`` is treated as success
    because the journal's invariant is that the target no longer exists.
    """
    normalized = [_normalize_resource(item) for item in journal["resources"]]
    completed = _normalize_completed(journal.get("completed", []))

    for resource in normalized:
        key = _resource_key(resource)
        if key in completed:
            continue

        target = resolve(resource)
        if target is not None:
            try:
                await target.delete(reason="School Manager crash-safe stream removal")
            except discord.NotFound:
                pass

        completed.add(key)
        journal["completed"] = sorted(completed)
        config[PENDING_REMOVAL_KEY] = deepcopy(journal)
        checkpoint(config)


def clear_removal_journal(
    config: dict[str, Any],
    *,
    checkpoint: CheckpointCallable,
) -> None:
    """Remove the journal only after Discord and logical config are finalized."""
    config.pop(PENDING_REMOVAL_KEY, None)
    checkpoint(config)

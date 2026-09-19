"""Live Discord guild state capture and deterministic diff helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

import discord


@dataclass(frozen=True)
class PermissionOverwriteState:
    target_id: int
    target_type: str
    allow: int
    deny: int


@dataclass(frozen=True)
class ChannelState:
    id: int
    name: str
    type: str
    category_id: int | None
    position: int
    permission_overwrites: tuple[PermissionOverwriteState, ...]


@dataclass(frozen=True)
class RoleState:
    id: int
    name: str
    managed: bool
    default: bool
    position: int


@dataclass(frozen=True)
class GuildState:
    guild_id: int
    guild_name: str
    channels: tuple[ChannelState, ...]
    roles: tuple[RoleState, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "guild_id": self.guild_id,
            "guild_name": self.guild_name,
            "channels": [asdict(item) for item in self.channels],
            "roles": [asdict(item) for item in self.roles],
        }


def channel_type(channel: discord.abc.GuildChannel) -> str:
    if isinstance(channel, discord.CategoryChannel):
        return "category"
    if isinstance(channel, discord.TextChannel):
        return "text"
    if isinstance(channel, discord.VoiceChannel):
        return "voice"
    if isinstance(channel, discord.StageChannel):
        return "stage"
    if isinstance(channel, discord.ForumChannel):
        return "forum"
    return type(channel).__name__.lower()


def capture_permission_overwrites(channel: discord.abc.GuildChannel) -> tuple[PermissionOverwriteState, ...]:
    states: list[PermissionOverwriteState] = []
    for target, overwrite in channel.overwrites.items():
        allow, deny = overwrite.pair()
        target_type = "role" if isinstance(target, discord.Role) else "member" if isinstance(target, discord.Member) else type(target).__name__.lower()
        states.append(
            PermissionOverwriteState(
                target_id=int(target.id),
                target_type=target_type,
                allow=int(allow.value),
                deny=int(deny.value),
            )
        )
    states.sort(key=lambda item: (item.target_type, item.target_id))
    return tuple(states)


async def capture_guild(guild: discord.Guild) -> GuildState:
    channels = list(await guild.fetch_channels())
    channels.sort(key=lambda item: (item.position, item.id))

    roles = list(await guild.fetch_roles())
    roles.sort(key=lambda item: (item.position, item.id))

    channel_states = tuple(
        ChannelState(
            id=channel.id,
            name=channel.name,
            type=channel_type(channel),
            category_id=getattr(channel, "category_id", None),
            position=channel.position,
            permission_overwrites=capture_permission_overwrites(channel),
        )
        for channel in channels
    )
    role_states = tuple(
        RoleState(
            id=role.id,
            name=role.name,
            managed=role.managed,
            default=role.is_default(),
            position=role.position,
        )
        for role in roles
    )
    return GuildState(
        guild_id=guild.id,
        guild_name=guild.name,
        channels=channel_states,
        roles=role_states,
    )


def write_snapshot(state: GuildState, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_snapshot(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"cannot read snapshot: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid snapshot JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("snapshot root must be an object")
    return payload


def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Return only deterministic additions/removals/changes by Discord ID."""

    def index(items: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(items, list):
            raise RuntimeError("snapshot collection must be a list")
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict) or "id" not in item:
                raise RuntimeError("snapshot entry must contain an id")
            key = str(item["id"])
            result[key] = item
        return result

    result: dict[str, Any] = {}
    for collection in ("channels", "roles"):
        old = index(before.get(collection, []))
        new = index(after.get(collection, []))
        added_ids = sorted(set(new) - set(old), key=int)
        removed_ids = sorted(set(old) - set(new), key=int)
        changed_ids = sorted(
            (key for key in set(old) & set(new) if old[key] != new[key]),
            key=int,
        )
        result[collection] = {
            "added": [new[item] for item in added_ids],
            "removed": [old[item] for item in removed_ids],
            "changed": [
                {"before": old[item], "after": new[item]} for item in changed_ids
            ],
        }
    return result

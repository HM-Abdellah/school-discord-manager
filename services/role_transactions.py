"""Small helpers for reversible managed-role mutations."""

from __future__ import annotations

import discord


def snapshot_role_presence(
    member: discord.Member,
    roles: list[discord.Role],
) -> set[int]:
    """Return the IDs of tracked roles currently present on the member."""
    tracked_ids = {role.id for role in roles}
    return {role.id for role in member.roles if role.id in tracked_ids}


async def restore_role_presence(
    member: discord.Member,
    roles: list[discord.Role],
    present_ids: set[int],
    *,
    reason: str,
) -> None:
    """Restore only the tracked role presence from a pre-mutation snapshot."""
    roles_by_id = {role.id: role for role in roles}
    current_ids = {
        role.id
        for role in member.roles
        if role.id in roles_by_id
    }
    desired_ids = present_ids & roles_by_id.keys()

    to_remove = [
        roles_by_id[role_id]
        for role_id in current_ids - desired_ids
        if not roles_by_id[role_id].managed
    ]
    to_add = [
        roles_by_id[role_id]
        for role_id in desired_ids - current_ids
        if not roles_by_id[role_id].managed
    ]

    if to_remove:
        await member.remove_roles(*to_remove, reason=reason)
    if to_add:
        await member.add_roles(*to_add, reason=reason)

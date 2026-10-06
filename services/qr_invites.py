"""Discord invite and QR helpers for secure class onboarding."""

from __future__ import annotations

from io import BytesIO
from typing import Iterable

import discord
import qrcode
from discord.http import Route

DEFAULT_QR_MAX_AGE = 30 * 60
DEFAULT_QR_MAX_USES = 42
MAX_INVITE_AGE = 7 * 24 * 60 * 60
MAX_INVITE_USES = 42


async def create_role_invite(
    bot: discord.Client,
    channel: discord.abc.GuildChannel,
    roles: Iterable[discord.Role],
    *,
    max_age: int = DEFAULT_QR_MAX_AGE,
    max_uses: int = DEFAULT_QR_MAX_USES,
) -> str:
    """Create a unique Discord invite that auto-assigns the supplied roles."""

    role_ids = [str(role.id) for role in roles]
    if not role_ids:
        raise ValueError("At least one role is required for a role-backed invite.")
    if not 0 <= max_age <= MAX_INVITE_AGE:
        raise ValueError("Invite max_age must be between 0 and 7 days.")
    if not 1 <= max_uses <= MAX_INVITE_USES:
        raise ValueError("Invite max_uses must be between 1 and 42.")

    route = Route(
        "POST",
        "/channels/{channel_id}/invites",
        channel_id=channel.id,
    )
    payload = {
        "max_age": max_age,
        "max_uses": max_uses,
        "temporary": False,
        "unique": True,
        "role_ids": role_ids,
    }
    data = await bot.http.request(
        route,
        json=payload,
        reason="School Manager class QR onboarding",
    )
    if not isinstance(data, dict) or not data.get("code"):
        raise RuntimeError("Discord returned an invalid invite payload.")
    return "https://discord.gg/" + str(data["code"])


def qr_file(invite_url: str, filename: str) -> discord.File:
    """Render an invite URL as a PNG suitable for a Discord attachment."""

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(invite_url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return discord.File(buffer, filename=filename)


async def delete_invite(bot: discord.Client, invite_code: str) -> None:
    """Revoke a Discord invite by code."""
    route = Route(
        "DELETE",
        "/invites/{invite_code}",
        invite_code=invite_code,
    )
    await bot.http.request(
        route,
        reason="School Manager class QR revoked",
    )

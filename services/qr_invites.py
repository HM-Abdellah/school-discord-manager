"""Discord invite and QR helpers for secure class onboarding."""

from __future__ import annotations

from io import BytesIO
from typing import Iterable

import discord
from discord import app_commands
import qrcode
from discord.http import Route

DEFAULT_QR_MAX_AGE = 30 * 60
DEFAULT_QR_MAX_USES = 42
MAX_INVITE_AGE = 7 * 24 * 60 * 60
MAX_INVITE_USES = 42

# Shared expiration choices for every QR creation command. Discord invite age is
# limited to seven days; class-transfer requests use the same choices.
QR_EXPIRATION_OPTIONS: tuple[tuple[str, int], ...] = (
    ("30 minutes", 30 * 60),
    ("1 heure", 60 * 60),
    ("1 h 30", 90 * 60),
    ("2 heures", 2 * 60 * 60),
    ("2 h 30", 150 * 60),
    ("3 heures", 3 * 60 * 60),
    ("4 heures", 4 * 60 * 60),
    ("6 heures", 6 * 60 * 60),
    ("12 heures", 12 * 60 * 60),
    ("24 heures", 24 * 60 * 60),
    ("2 jours", 2 * 24 * 60 * 60),
    ("3 jours", 3 * 24 * 60 * 60),
    ("7 jours", MAX_INVITE_AGE),
)


def qr_expiration_choices() -> list[app_commands.Choice[int]]:
    """Return the shared expiration choices displayed by QR creation commands."""
    return [
        app_commands.Choice(name=label, value=seconds)
        for label, seconds in QR_EXPIRATION_OPTIONS
    ]


def qr_expiration_label(seconds: int) -> str | None:
    """Return the user-facing label for an allowed QR expiration, or None."""
    return next(
        (label for label, value in QR_EXPIRATION_OPTIONS if value == seconds),
        None,
    )


async def create_role_invite(
    bot: discord.Client,
    channel: discord.abc.GuildChannel,
    roles: Iterable[discord.Role],
    *,
    max_age: int = DEFAULT_QR_MAX_AGE,
    max_uses: int = DEFAULT_QR_MAX_USES,
    reason: str = "School Manager QR onboarding",
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
        reason=reason,
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

"""Fail-closed destructive security controls for School Manager."""

from __future__ import annotations

import logging
import sqlite3
from copy import copy
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

from services.build_guard import get_build_lock
from services.discord_ownership import validate_managed_registry
from services.permissions import owner_only_check
from services.storage import archive_guild_database, get_active_academic_year, get_guild_config, reset_guild_data

OWNED_COMMANDS = {"resetserver"}
logger = logging.getLogger(__name__)


class ResetPreflightError(RuntimeError):
    """A reset preflight repair failed before any Discord resource was deleted."""


def _managed_mapping(config: dict, section: str) -> dict:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    value = managed.get(section, {}) if isinstance(managed, dict) else {}
    return value if isinstance(value, dict) else {}


class ResetOperationError(RuntimeError):
    """A Discord reset operation failed with bounded, safe diagnostic context."""


def _bounded_resource_name(resource: object) -> str:
    name = getattr(resource, "name", None)
    if not isinstance(name, str):
        return "<unknown>"
    safe_name = " ".join(name.replace("`", "'").split())[:80]
    return safe_name or "<unnamed>"


async def _run_reset_discord_operation(
    *,
    operation: str,
    resource_type: str,
    resource: object,
    action: Callable[[], Awaitable[object]],
) -> None:
    """Run one Discord API action and retain its target and HTTP failure code."""
    try:
        await action()
    except discord.HTTPException as exc:
        resource_id = getattr(resource, "id", "unknown")
        resource_name = _bounded_resource_name(resource)
        http_status = getattr(exc, "status", "unknown")
        discord_code = getattr(exc, "code", "unknown")
        details = (
            f"operation={operation}; resource_type={resource_type}; resource_id={resource_id}; "
            f"resource_name='{resource_name}'; http_status={http_status}; "
            f"discord_code={discord_code}; exception_type={type(exc).__name__}"
        )
        logger.error("resetserver API operation failed: %s", details)
        raise ResetOperationError(details) from exc


async def _fetch_reset_channels(guild: discord.Guild) -> dict[int, discord.abc.GuildChannel]:
    """Resolve registered resources from a fresh API snapshot plus the existing cache."""
    channels = list(getattr(guild, "channels", []))
    fetch_channels = getattr(guild, "fetch_channels", None)
    if callable(fetch_channels):
        channels.extend(await fetch_channels())
    return {
        int(channel.id): channel
        for channel in channels
        if getattr(channel, "id", None) is not None
    }


def _reset_access_hint(api_code: object) -> str:
    if api_code == 50001:
        return (
            "Discord renvoie Missing Access (50001). Vérifie les permission overwrites "
            "View Channel du bot sur le salon et sa catégorie parente."
        )
    if api_code == 50013:
        return (
            "Discord renvoie Missing Permissions (50013). Vérifie Manage Channels et Manage Roles "
            "dans les permissions serveur/catégorie du bot."
        )
    return "Vérifie les permissions et les overwrites du bot sur cette ressource."


async def _ensure_bot_can_view_managed_resources(
    guild: discord.Guild,
    live_channels: dict[int, discord.abc.GuildChannel],
    channel_ids: set[int],
    category_ids: set[int],
) -> int:
    """Repair only the bot-member View Channel overwrite for registered resources."""
    bot_member = getattr(guild, "me", None)
    if bot_member is None:
        raise ResetPreflightError(
            "Impossible d'identifier le membre du bot pour vérifier l'accès aux ressources gérées."
        )

    repaired = 0
    for resource_id in [*sorted(category_ids), *sorted(channel_ids)]:
        resource = live_channels.get(resource_id)
        if resource is None:
            continue
        permissions_for = getattr(resource, "permissions_for", None)
        if not callable(permissions_for):
            raise ResetPreflightError(
                f"Impossible de vérifier l'accès à la ressource gérée ID {resource_id}; "
                "aucune suppression Discord n'a encore été lancée."
            )
        try:
            effective_permissions = permissions_for(bot_member)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ResetPreflightError(
                f"Impossible de calculer les permissions du bot pour la ressource ID {resource_id}: {exc}"
            ) from exc
        if getattr(effective_permissions, "view_channel", False):
            continue

        current_overwrite_for = getattr(resource, "overwrites_for", None)
        bot_overwrite = copy(
            current_overwrite_for(bot_member)
            if callable(current_overwrite_for)
            else discord.PermissionOverwrite()
        )
        bot_overwrite.view_channel = True
        overwrites = dict(getattr(resource, "overwrites", {}) or {})
        overwrites[bot_member] = bot_overwrite
        try:
            await resource.edit(
                overwrites=overwrites,
                reason="School Manager reset recovery: restore bot visibility to managed resource",
            )
        except discord.HTTPException as exc:
            api_code = getattr(exc, "code", None)
            api_status = getattr(exc, "status", None)
            name = str(getattr(resource, "name", resource_id)).replace("`", "'")[:80]
            raise ResetPreflightError(
                f"Impossible de restaurer View Channel pour la ressource gérée "
                f"'{name}' (ID {resource_id}; HTTP {api_status}; code {api_code}). "
                f"{_reset_access_hint(api_code)} Aucune suppression Discord n'a encore été lancée."
            ) from exc
        repaired += 1
    return repaired


def _validate_reset_role_hierarchy(guild: discord.Guild, role_ids: set[int]) -> str | None:
    """Reject a reset before mutation when any managed role is above the bot."""
    bot_member = guild.me
    if bot_member is None:
        return "Impossible de vérifier la hiérarchie du bot."
    top_role = bot_member.top_role
    for role_id in sorted(role_ids):
        role = guild.get_role(role_id)
        if role is None:
            continue
        if role.managed:
            return "Le rôle géré %r est un rôle Discord-managed non supprimable." % role.name
        if role.is_default():
            return "Le rôle géré %r est @everyone et ne peut pas être supprimé." % role.name
        if role >= top_role:
            return "Le rôle géré %r est au-dessus ou au même niveau que le rôle du bot. Aucun reset ne sera exécuté tant que la hiérarchie n'est pas corrigée." % role.name
    return None


class HardenedResetCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="resetserver", description="Supprimer uniquement les ressources School Manager enregistrées.")
    @app_commands.describe(confirm="Écris RESET SCHOOL MANAGER pour confirmer. Réservé au propriétaire.")
    @owner_only_check(lock=False)
    async def reset_server(self, interaction: discord.Interaction, confirm: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if confirm.strip().upper() != "RESET SCHOOL MANAGER":
            await interaction.response.send_message("❌ Confirmation exacte requise : `RESET SCHOOL MANAGER`.", ephemeral=True)
            return

        config = get_guild_config(guild.id) or {}
        try:
            await validate_managed_registry(guild, config)
        except RuntimeError as exc:
            await interaction.response.send_message(
                f"❌ Reset refusé : identité gérée incohérente (`{exc}`). Aucun resource n'a été supprimé.",
                ephemeral=True,
            )
            return

        role_ids = {value for value in _managed_mapping(config, "roles").values() if isinstance(value, int) and value > 0}
        channel_ids = {value for value in _managed_mapping(config, "channels").values() if isinstance(value, int) and value > 0}
        category_ids = {value for value in _managed_mapping(config, "categories").values() if isinstance(value, int) and value > 0}

        hierarchy_error = _validate_reset_role_hierarchy(guild, role_ids)
        if hierarchy_error:
            await interaction.response.send_message(f"❌ Reset refusé : {hierarchy_error}", ephemeral=True)
            return

        await interaction.response.send_message("🧹 **RESET SCHOOL MANAGER EN COURS...**", ephemeral=True)
        deleted_channels = deleted_categories = deleted_roles = retained_categories = 0
        archive_name = None

        try:
            async with get_build_lock(guild.id):
                active_year = get_active_academic_year(guild.id)
                if active_year is not None:
                    try:
                        archive_path = archive_guild_database(guild.id, str(active_year["name"]))
                        archive_name = archive_path.name
                    except (OSError, sqlite3.Error) as exc:
                        await interaction.followup.send(
                            f"❌ Reset refusé : impossible de créer l'archive de l'année active (`{type(exc).__name__}: {exc}`). Aucun resource Discord n'a été supprimé.",
                            ephemeral=True,
                        )
                        return

                live_channels = await _fetch_reset_channels(guild)
                await _ensure_bot_can_view_managed_resources(
                    guild, live_channels, channel_ids, category_ids
                )

                for channel_id in sorted(channel_ids):
                    channel = live_channels.get(channel_id) or guild.get_channel(channel_id)
                    if channel is None or not isinstance(channel, discord.abc.GuildChannel):
                        continue
                    await _run_reset_discord_operation(
                        operation="delete_channel",
                        resource_type="channel",
                        resource=channel,
                        action=lambda: channel.delete(reason="School Manager scoped reset"),
                    )
                    deleted_channels += 1

                for category_id in sorted(category_ids):
                    category = live_channels.get(category_id) or guild.get_channel(category_id)
                    if not isinstance(category, discord.CategoryChannel):
                        continue
                    remaining = [channel for channel in guild.channels if getattr(channel, "category_id", None) == category.id]
                    if remaining:
                        retained_categories += 1
                        continue
                    await _run_reset_discord_operation(
                        operation="delete_category",
                        resource_type="category",
                        resource=category,
                        action=lambda: category.delete(reason="School Manager scoped reset"),
                    )
                    deleted_categories += 1

                top_role = guild.me.top_role if guild.me is not None else None
                for role_id in sorted(role_ids):
                    role = guild.get_role(role_id)
                    if role is None:
                        continue
                    await _run_reset_discord_operation(
                        operation="delete_role",
                        resource_type="role",
                        resource=role,
                        action=lambda: role.delete(reason="School Manager scoped reset"),
                    )
                    deleted_roles += 1

                reset_guild_data(guild.id)
        except ResetPreflightError as exc:
            logger.error("resetserver permission preflight failed: %s", exc)
            await interaction.followup.send(
                f"❌ Reset interrompu avant les suppressions Discord : `{exc}`. "
                "Aucune suppression Discord n'a été lancée pendant cette tentative.",
                ephemeral=True,
            )
            return
        except ResetOperationError as exc:
            await interaction.followup.send(
                f"❌ Reset interrompu : `{exc}`. Aucun nom n'a été utilisé pour élargir le scope.",
                ephemeral=True,
            )
            return
        except (discord.Forbidden, discord.HTTPException, discord.NotFound, OSError) as exc:
            await interaction.followup.send(
                f"❌ Reset interrompu : `{type(exc).__name__}`. Aucun nom n'a été utilisé pour élargir le scope.",
                ephemeral=True,
            )
            return

        suffix = f" Catégories conservées car elles contiennent des ressources non gérées : **{retained_categories}**." if retained_categories else ""
        archive_suffix = f" Archive créée : `{archive_name}`." if archive_name else " Aucune année active à archiver."
        await interaction.followup.send(
            f"✅ Reset terminé. Channels: **{deleted_channels}** · Catégories: **{deleted_categories}** · Rôles: **{deleted_roles}**. Seuls les IDs gérés enregistrés ont été ciblés.{suffix}{archive_suffix}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HardenedResetCommands(bot))

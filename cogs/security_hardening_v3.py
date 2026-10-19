"""Fail-closed destructive security controls for School Manager."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from services.build_guard import get_build_lock
from services.discord_ownership import validate_managed_registry
from services.permissions import STUDENT_STREAM_ROLE_PREFIX, owner_only_check
from services.storage import (
    DATA_DIR,
    archive_guild_database,
    get_active_academic_year,
    get_guild_config,
    reset_guild_data,
    save_guild_config,
)

OWNED_COMMANDS = {"resetserver"}


class ResetPreflightError(RuntimeError):
    """Reset refused before the first destructive Discord mutation."""


def _managed_mapping(config: dict, section: str) -> dict:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    value = managed.get(section, {}) if isinstance(managed, dict) else {}
    return value if isinstance(value, dict) else {}


def _registered_class_role_ids(
    guild: discord.Guild,
    config: dict,
    *,
    roles_by_id: dict[int, discord.Role] | None = None,
) -> set[int]:
    """Validate and include section roles stored outside the managed-role map.

    Class QR roles live in ``config['class_roles']`` and historically were not
    part of ``config['managed']['roles']``. Omitting them from reset left
    orphaned section roles behind, causing the next QR creation to fail on a
    name collision after its registry had been cleared.
    """
    registry = config.get("class_roles", {}) if isinstance(config, dict) else {}
    if registry is None:
        return set()
    if not isinstance(registry, dict):
        raise RuntimeError("Le registre des rôles de classe est invalide; reset refusé.")

    roles = list(roles_by_id.values()) if roles_by_id is not None else list(getattr(guild, "roles", []))
    get_role = roles_by_id.get if roles_by_id is not None else guild.get_role
    role_ids: set[int] = set()
    for class_key, entry in registry.items():
        if not isinstance(entry, dict):
            raise RuntimeError(f"Entrée du rôle de classe `{class_key}` invalide; reset refusé.")
        role_id = entry.get("role_id")
        code = entry.get("stream_code")
        section = entry.get("section")
        if (
            type(role_id) is not int
            or role_id <= 0
            or not isinstance(code, str)
            or not code.strip()
            or type(section) is not int
            or not 1 <= section <= 8
            or str(class_key) != f"{code}-{section}"
        ):
            raise RuntimeError(f"Identité du rôle de classe `{class_key}` invalide; reset refusé.")

        expected_name = f"{STUDENT_STREAM_ROLE_PREFIX}{code}-{section}"
        role = get_role(role_id)
        if role is not None:
            if getattr(role, "managed", False) or role.is_default() or role.name != expected_name:
                raise RuntimeError(
                    f"Le rôle de classe `{class_key}` ne correspond pas à son identité enregistrée; reset refusé."
                )
        duplicates = [
            item for item in roles
            if item.id != role_id and item.name == expected_name
        ]
        if duplicates:
            duplicate_ids = ", ".join(str(item.id) for item in duplicates)
            raise RuntimeError(
                f"Un autre rôle porte le nom `{expected_name}` (IDs: {duplicate_ids}); reset refusé."
            )
        # Keep the ID even if its role no longer exists, so retrying an interrupted
        # reset remains deterministic and the registry is cleared only at success.
        role_ids.add(role_id)
    return role_ids


def _existing_reset_archive(guild_id: int, archive_name: object) -> Path | None:
    """Reuse and validate the original pre-reset archive during recovery retries."""
    if not isinstance(archive_name, str) or not archive_name or Path(archive_name).name != archive_name:
        return None
    archive_dir = (DATA_DIR / "archives").resolve()
    candidate = (archive_dir / archive_name).resolve()
    if candidate.parent != archive_dir or not candidate.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{candidate.as_posix()}?mode=ro", uri=True) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            metadata = conn.execute("SELECT guild_id FROM archive_metadata WHERE id=1").fetchone()
        if integrity is None or integrity[0] != "ok" or metadata is None or int(metadata[0]) != int(guild_id):
            return None
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None
    return candidate


def _validate_reset_role_hierarchy(
    guild: discord.Guild,
    role_ids: set[int],
    *,
    roles_by_id: dict[int, discord.Role] | None = None,
) -> str | None:
    """Reject a reset before mutation when any registered role is above the bot."""
    bot_member = guild.me
    if bot_member is None:
        return "Impossible de vérifier la hiérarchie du bot."
    top_role = bot_member.top_role
    get_role = roles_by_id.get if roles_by_id is not None else guild.get_role
    for role_id in sorted(role_ids):
        role = get_role(role_id)
        if role is None:
            continue
        if role.managed:
            return "Le rôle géré %r est un rôle Discord-managed non supprimable." % role.name
        if role.is_default():
            return "Le rôle géré %r est @everyone et ne peut pas être supprimé." % role.name
        if role >= top_role:
            return "Le rôle géré %r est au-dessus ou au même niveau que le rôle du bot. Aucun reset ne sera exécuté tant que la hiérarchie n'est pas corrigée." % role.name
    return None


async def _fetch_reset_channels(guild: discord.Guild) -> dict[int, discord.abc.GuildChannel]:
    """Use a fresh API snapshot; a cold cache must not make reset skip channels."""
    fetch_channels = getattr(guild, "fetch_channels", None)
    if callable(fetch_channels):
        channels = await fetch_channels()
    else:
        channels = getattr(guild, "channels", [])
    return {
        int(channel.id): channel
        for channel in channels
        if getattr(channel, "id", None) is not None
    }


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

        # Acknowledge before acquiring the guild lock, then read and validate the
        # registry under that same lock. Otherwise a concurrent build could save
        # new resource IDs after reset has already captured an outdated snapshot.
        await interaction.response.send_message("🧹 **RESET SCHOOL MANAGER EN COURS...**", ephemeral=True)
        deleted_channels = deleted_categories = deleted_roles = retained_categories = 0
        archive_name: str | None = None
        reset_started = False

        try:
            async with get_build_lock(guild.id):
                config = get_guild_config(guild.id) or {}
                reset_started = config.get("pending_reset") is not None
                try:
                    await validate_managed_registry(guild, config)
                except RuntimeError as exc:
                    raise ResetPreflightError(
                        "Identité gérée incohérente; aucun resource Discord n'a été supprimé. " + str(exc)
                    ) from exc

                # Fetch the live Discord state inside the lock before identifying
                # resources; cache misses must not silently skip deletion.
                live_channels = await _fetch_reset_channels(guild)
                fetch_roles = getattr(guild, "fetch_roles", None)
                if callable(fetch_roles):
                    live_roles = list(await fetch_roles())
                else:
                    live_roles = list(getattr(guild, "roles", []))
                roles_by_id = {int(role.id): role for role in live_roles}

                role_ids = {
                    value for value in _managed_mapping(config, "roles").values()
                    if type(value) is int and value > 0
                }
                management_role_id = config.get("management_role_id")
                if type(management_role_id) is int and management_role_id > 0:
                    role_ids.add(management_role_id)
                role_ids.update(_registered_class_role_ids(guild, config, roles_by_id=roles_by_id))
                channel_ids = {
                    value for value in _managed_mapping(config, "channels").values()
                    if type(value) is int and value > 0
                }
                category_ids = {
                    value for value in _managed_mapping(config, "categories").values()
                    if type(value) is int and value > 0
                }

                hierarchy_error = _validate_reset_role_hierarchy(guild, role_ids, roles_by_id=roles_by_id)
                if hierarchy_error:
                    raise ResetPreflightError(hierarchy_error)

                active_year = get_active_academic_year(guild.id)
                archive_year = str(active_year["name"]) if active_year is not None else "no-active-year"
                pending_marker = config.get("pending_reset")
                prior_archive = (
                    _existing_reset_archive(guild.id, pending_marker.get("archive"))
                    if isinstance(pending_marker, dict)
                    else None
                )
                try:
                    # Never replace the disaster-recovery point with a snapshot of
                    # an already partially deleted server during a reset retry.
                    archive_path = prior_archive or archive_guild_database(guild.id, archive_year)
                    archive_name = archive_path.name
                except (OSError, sqlite3.Error) as exc:
                    raise ResetPreflightError(
                        "Impossible de créer l'archive; aucun resource Discord n'a été supprimé. "
                        + f"{type(exc).__name__}: {exc}"
                    ) from exc

                # Keep a durable recovery marker while any Discord resources or
                # database state still need cleanup. The permission guard blocks
                # other mutating commands until reset is successfully retried.
                config["pending_reset"] = {
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "archive": archive_name,
                }
                save_guild_config(guild.id, config)
                reset_started = True

                for channel_id in sorted(channel_ids):
                    channel = live_channels.get(channel_id)
                    if channel is None:
                        continue
                    if not isinstance(channel, discord.abc.GuildChannel):
                        raise RuntimeError(
                            f"Le resource enregistré ID {channel_id} n'est pas un salon Discord supprimable."
                        )
                    try:
                        await channel.delete(reason="School Manager scoped reset")
                    except discord.NotFound:
                        pass
                    live_channels.pop(channel_id, None)
                    deleted_channels += 1

                for category_id in sorted(category_ids):
                    category = live_channels.get(category_id)
                    if category is None:
                        continue
                    if not isinstance(category, discord.CategoryChannel):
                        raise RuntimeError(
                            f"Le resource catégorie ID {category_id} n'est pas une catégorie Discord."
                        )
                    remaining = [
                        channel for channel_id, channel in live_channels.items()
                        if channel_id not in channel_ids
                        and getattr(channel, "category_id", None) == category.id
                    ]
                    if remaining:
                        retained_categories += 1
                        continue
                    try:
                        await category.delete(reason="School Manager scoped reset")
                    except discord.NotFound:
                        pass
                    live_channels.pop(category_id, None)
                    deleted_categories += 1

                for role_id in sorted(role_ids):
                    role = roles_by_id.get(role_id)
                    if role is None:
                        continue
                    try:
                        await role.delete(reason="School Manager scoped reset")
                    except discord.NotFound:
                        pass
                    deleted_roles += 1

                reset_guild_data(guild.id)
        except ResetPreflightError as exc:
            if reset_started:
                message = (
                    f"❌ Le reset reste interrompu : `{exc}`. Le marqueur de récupération est conservé; "
                    "ne lance pas `/setup` ni `/build` avant d'avoir résolu ce blocage."
                )
            else:
                message = f"❌ Reset refusé avant suppression : `{exc}`"
            await interaction.followup.send(message, ephemeral=True)
            return
        except (discord.Forbidden, discord.HTTPException, OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
            if reset_started:
                message = (
                    f"❌ Reset interrompu : `{type(exc).__name__}: {exc}`. "
                    "L'état de récupération est enregistré; corrige le problème puis relance `/resetserver` "
                    "avant d'utiliser `/setup` ou `/build`."
                )
            else:
                message = (
                    f"❌ Reset interrompu avant la suppression des ressources : `{type(exc).__name__}: {exc}`. "
                    "Aucune modification Discord destructive n'a été lancée."
                )
            await interaction.followup.send(message, ephemeral=True)
            return

        suffix = f" Catégories conservées car elles contiennent des ressources non gérées : **{retained_categories}**." if retained_categories else ""
        archive_suffix = f" Archive créée : `{archive_name}`."
        await interaction.followup.send(
            f"✅ Reset terminé. Channels: **{deleted_channels}** · Catégories: **{deleted_categories}** · Rôles: **{deleted_roles}**. Seuls les IDs enregistrés ont été ciblés, y compris les rôles de classe.{suffix}{archive_suffix}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HardenedResetCommands(bot))

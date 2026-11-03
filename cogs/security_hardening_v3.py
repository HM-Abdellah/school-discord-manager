"""Fail-closed destructive security controls for School Manager."""

from __future__ import annotations

import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from services.build_guard import get_build_lock
from services.discord_ownership import validate_managed_registry
from services.permissions import direction_or_owner_check
from services.permissions import STUDENT_STREAM_ROLE_PREFIX
from services.storage import archive_guild_database, get_active_academic_year, get_guild_config, get_latest_archived_class_roles, reset_guild_data

OWNED_COMMANDS = {"resetserver"}


def _managed_mapping(config: dict, section: str) -> dict:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    value = managed.get(section, {}) if isinstance(managed, dict) else {}
    return value if isinstance(value, dict) else {}


def _registered_class_role_ids(guild: discord.Guild, config: dict) -> set[int]:
    """Resolve only class roles whose persisted ID and canonical identity agree."""
    registry = config.get("class_roles", {}) if isinstance(config, dict) else {}
    if not isinstance(registry, dict):
        raise RuntimeError("Le registre des rôles de classe est invalide.")

    role_ids: set[int] = set()
    for key, entry in registry.items():
        if not isinstance(entry, dict):
            raise RuntimeError("Une entrée du registre des rôles de classe est invalide.")

        role_id = entry.get("role_id")
        stream_code = entry.get("stream_code")
        section = entry.get("section")
        if (
            type(role_id) is not int
            or role_id <= 0
            or not isinstance(stream_code, str)
            or not stream_code.strip()
            or type(section) is not int
            or not 1 <= section <= 8
        ):
            raise RuntimeError("Une identité de rôle de classe enregistrée est invalide.")

        expected_key = f"{stream_code}-{section}"
        if key != expected_key:
            raise RuntimeError(
                f"La clé du rôle de classe `{key}` ne correspond pas à `{expected_key}`."
            )

        role = guild.get_role(role_id)
        if role is None:
            continue

        expected_name = f"{STUDENT_STREAM_ROLE_PREFIX}{expected_key}"
        if role.managed or role.is_default() or role.name != expected_name:
            raise RuntimeError(
                f"Le rôle de classe ID `{role_id}` ne correspond pas à l'identité attendue `{expected_name}`. Aucun reset ne sera exécuté."
            )
        role_ids.add(role_id)

    return role_ids


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
    @app_commands.describe(confirm="Écris RESET SCHOOL MANAGER pour confirmer. Propriétaire ou Direction.")
    @direction_or_owner_check(lock=False)
    async def reset_server(self, interaction: discord.Interaction, confirm: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if confirm.strip().upper() != "RESET SCHOOL MANAGER":
            await interaction.response.send_message("❌ Confirmation exacte requise : `RESET SCHOOL MANAGER`.", ephemeral=True)
            return

        stored_config = get_guild_config(guild.id)
        config = stored_config if isinstance(stored_config, dict) else {}
        archived_class_roles = (
            get_latest_archived_class_roles(guild.id)
            if stored_config is None
            else None
        )
        class_registry_config = (
            {"class_roles": archived_class_roles}
            if archived_class_roles is not None
            else config
        )
        try:
            await validate_managed_registry(guild, config)
            class_role_ids = _registered_class_role_ids(guild, class_registry_config)
        except RuntimeError as exc:
            await interaction.response.send_message(
                f"❌ Reset refusé : identité gérée incohérente (`{exc}`). Aucun resource n'a été supprimé.",
                ephemeral=True,
            )
            return

        role_ids = {value for value in _managed_mapping(config, "roles").values() if isinstance(value, int) and value > 0} | class_role_ids
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
                        await interaction.edit_original_response(
                            content=f"❌ Reset refusé : impossible de créer l'archive de l'année active (`{type(exc).__name__}: {exc}`). Aucun resource Discord n'a été supprimé.",
                        )
                        return

                for channel_id in sorted(channel_ids):
                    channel = guild.get_channel(channel_id)
                    if channel is None or not isinstance(channel, discord.abc.GuildChannel):
                        continue
                    await channel.delete(reason="School Manager scoped reset")
                    deleted_channels += 1

                for category_id in sorted(category_ids):
                    category = guild.get_channel(category_id)
                    if not isinstance(category, discord.CategoryChannel):
                        continue
                    remaining = [channel for channel in guild.channels if getattr(channel, "category_id", None) == category.id]
                    if remaining:
                        retained_categories += 1
                        continue
                    await category.delete(reason="School Manager scoped reset")
                    deleted_categories += 1

                top_role = guild.me.top_role if guild.me is not None else None
                for role_id in sorted(role_ids):
                    role = guild.get_role(role_id)
                    if role is None:
                        continue
                    await role.delete(reason="School Manager scoped reset")
                    deleted_roles += 1

                reset_guild_data(guild.id)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound, OSError) as exc:
            await interaction.edit_original_response(
                content=f"❌ Reset interrompu : `{type(exc).__name__}`. Aucun nom n'a été utilisé pour élargir le scope.",
            )
            return

        suffix = f" Catégories conservées car elles contiennent des ressources non gérées : **{retained_categories}**." if retained_categories else ""
        archive_suffix = f" Archive créée : `{archive_name}`." if archive_name else " Aucune année active à archiver."
        await interaction.edit_original_response(
            content=f"✅ Reset terminé. Channels: **{deleted_channels}** · Catégories: **{deleted_categories}** · Rôles: **{deleted_roles}**. Seuls les IDs gérés enregistrés ont été ciblés.{suffix}{archive_suffix}",
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HardenedResetCommands(bot))

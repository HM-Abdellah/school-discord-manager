"""Administrative dashboard, server health, and staff-authorized role-registry recovery."""

from __future__ import annotations

import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from services.admin_role_registry import sync_admin_role_registry
from services.audit import recent_events
from services.permissions import (
    ROLE_PROFESSOR,
    ROLE_PROFESSOR_FEMALE,
    _preflight_message,
    get_managed_role,
    admin_role_registry_check,
    management_check,
)
from services.storage import get_guild_config, list_academic_years


class AdminCommands(commands.Cog):
    """Administrative diagnostics and authorized registry recovery."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="adminpanel", description="Afficher le tableau de bord de l'établissement.")
    @management_check()
    async def admin_panel(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return

        config = get_guild_config(guild.id) or {}
        years = list_academic_years(guild.id)
        active_year = next((row["name"] for row in years if row["is_active"]), config.get("academic_year", "—"))
        student_role = get_managed_role(guild, "Élève")
        professor_roles = {
            role
            for role in (
                get_managed_role(guild, ROLE_PROFESSOR),
                get_managed_role(guild, ROLE_PROFESSOR_FEMALE),
            )
            if role is not None
        }
        students = sum(
            1
            for member in guild.members
            if student_role is not None and student_role in member.roles
        )
        teachers = sum(
            1
            for member in guild.members
            if any(role in professor_roles for role in member.roles)
        )
        streams = sum(len(level.get("streams", [])) for level in config.get("levels", []))
        events = recent_events(guild.id, 5)
        recent = "\n".join(f"• `{event['action']}` — {event['target'] or '—'}" for event in events) or "Aucune action enregistrée."

        embed = discord.Embed(
            title="🏫 SCHOOL MANAGER",
            description="Tableau de bord administratif",
            colour=discord.Colour.blurple(),
        )
        embed.add_field(name="📅 Année active", value=str(active_year), inline=True)
        embed.add_field(name="👨‍🎓 Élèves", value=str(students), inline=True)
        embed.add_field(name="👨‍🏫 Professeurs", value=str(teachers), inline=True)
        embed.add_field(name="📚 Filières", value=str(streams), inline=True)
        embed.add_field(name="🧩 Channels", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="🎭 Rôles", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="📋 Dernières actions", value=recent[:1024], inline=False)
        embed.add_field(name="🩺 Health", value="Utilise `/serverhealth` pour le diagnostic détaillé.", inline=False)
        embed.set_footer(text="School Discord Manager")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="serverhealth", description="Vérifier la santé du serveur avant une opération.")
    @management_check()
    async def server_health(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return

        checks: list[str] = []
        bot_member = guild.me
        if bot_member is None:
            checks.append("❌ Bot introuvable dans le serveur.")
        else:
            checks.append("✅ Bot connecté")
            checks.append("✅ Manage Channels" if bot_member.guild_permissions.manage_channels else "❌ Manage Channels manquant")
            checks.append("✅ Manage Roles" if bot_member.guild_permissions.manage_roles else "❌ Manage Roles manquant")
            hierarchy = _preflight_message(interaction, needs_channels=False, needs_roles=True)
            checks.append("✅ Role hierarchy" if hierarchy is None else hierarchy)

        total = len(guild.channels)
        checks.append(f"✅ Channel count: **{total}/500**" if total < 500 else f"❌ Channel count: **{total}/500**")
        largest_category = max((len(category.channels) for category in guild.categories), default=0)
        checks.append(f"✅ Category capacity: largest **{largest_category}/50**" if largest_category < 50 else f"❌ Category capacity: largest **{largest_category}/50**")
        config = get_guild_config(guild.id)
        checks.append("✅ Configuration" if config else "⚠️ Aucune configuration enregistrée")

        colour = discord.Colour.green() if all(item.startswith("✅") or item.startswith("⚠️") for item in checks) else discord.Colour.orange()
        embed = discord.Embed(title="🩺 SERVER HEALTH", description="\n".join(checks), colour=colour)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="syncadminroles",
        description="Réparer le registre des rôles administratifs (propriétaire, Direction ou Surveillant Général).",
    )
    @admin_role_registry_check()
    async def sync_admin_roles(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            synced_names = sync_admin_role_registry(
                guild,
                actor_id=interaction.user.id,
                actor_name=getattr(interaction.user, "display_name", None) or str(interaction.user),
            )
        except ValueError as exc:
            await interaction.followup.send(f"❌ Synchronisation annulée : {exc}", ephemeral=True)
            return
        except (OSError, sqlite3.Error) as exc:
            await interaction.followup.send(
                f"❌ Impossible d'enregistrer le registre des rôles : {type(exc).__name__}. "
                "Aucun changement Discord n'a été effectué.",
                ephemeral=True,
            )
            return

        roles_text = ", ".join(f"`{name}`" for name in synced_names)
        await interaction.followup.send(
            "✅ **Registre administratif synchronisé.**\n"
            f"Rôles enregistrés : {roles_text}.\n\n"
            "Les titulaires de ces rôles peuvent maintenant utiliser les commandes qui leur sont autorisées. "
            "Aucun rôle, membre, permission Discord ou salon n'a été modifié.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCommands(bot))

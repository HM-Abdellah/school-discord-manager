"""Owner-only recovery command for rolling an academic year back."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from services.permissions import owner_only_check
from services.year_management import rollback_guild_config_year


class YearRollbackCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="rollbackyear", description="Revenir exceptionnellement à une année scolaire précédente.")
    @app_commands.describe(
        year="Année scolaire cible, par exemple 2026/2027",
        confirm="Écris ROLLBACK SCHOOL YEAR pour confirmer.",
    )
    @owner_only_check()
    async def rollback_year(self, interaction: discord.Interaction, year: str, confirm: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if confirm.strip().upper() != "ROLLBACK SCHOOL YEAR":
            await interaction.response.send_message(
                "❌ Confirmation exacte requise : `ROLLBACK SCHOOL YEAR`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            previous_year, changed = rollback_guild_config_year(guild.id, year)
        except ValueError as exc:
            await interaction.followup.send(f"❌ Rollback annulé : `{exc}`", ephemeral=True)
            return
        except OSError as exc:
            await interaction.followup.send(f"❌ Rollback annulé : `{exc}`", ephemeral=True)
            return
        except Exception as exc:
            await interaction.followup.send(
                f"❌ Rollback annulé : `{type(exc).__name__}: {exc}`",
                ephemeral=True,
            )
            return

        if not changed:
            await interaction.followup.send(
                f"ℹ️ **{year}** est déjà l'année scolaire active.",
                ephemeral=True,
            )
            return

        print(f"[ROLLBACK] guild={guild.id} {previous_year} -> {year}", flush=True)
        print(f"[ROLLBACK] active academic year: {year}", flush=True)
        await interaction.followup.send(
            f"✅ Rollback terminé. **{year}** est maintenant l'année scolaire active.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(YearRollbackCommands(bot))

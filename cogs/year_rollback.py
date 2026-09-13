"""Owner-only recovery command for rolling an academic year back."""

from __future__ import annotations

from copy import deepcopy

import discord
from discord import app_commands
from discord.ext import commands

from services.build_guard import get_build_lock
from services.permissions import owner_only_check
from services.storage import CONFIG_FILE, _connect, get_guild_config, initialize_database, list_academic_years, load_all, save_all


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

        config = get_guild_config(guild.id)
        if not config:
            await interaction.response.send_message("❌ Configuration absente.", ephemeral=True)
            return

        current_year = config.get("academic_year")
        if year == current_year:
            await interaction.response.send_message(
                f"ℹ️ **{year}** est déjà l'année scolaire active.",
                ephemeral=True,
            )
            return

        initialize_database()
        years = list_academic_years(guild.id)
        target = next((row for row in years if row["name"] == year), None)
        if target is None:
            await interaction.response.send_message(
                f"❌ L'année **{year}** n'est pas enregistrée. Utilise `/years` pour voir les années disponibles.",
                ephemeral=True,
            )
            return

        lock = get_build_lock(guild.id)
        if lock.locked():
            await interaction.response.send_message(
                "⏳ Une construction est déjà en cours sur ce serveur.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"↩️ Rollback vers **{year}** en cours...",
            ephemeral=True,
        )

        old_data = load_all()
        new_data = deepcopy(old_data)
        new_data[str(guild.id)] = deepcopy(config)
        new_data[str(guild.id)]["academic_year"] = year

        try:
            async with lock:
                with _connect() as conn:
                    try:
                        conn.execute("UPDATE academic_years SET is_active=0 WHERE guild_id=?", (guild.id,))
                        conn.execute(
                            "UPDATE academic_years SET is_active=1 WHERE guild_id=? AND name=?",
                            (guild.id, year),
                        )
                        if conn.execute(
                            "SELECT changes()"
                        ).fetchone()[0] != 1:
                            raise OSError(f"Impossible d'activer l'année {year} dans la base de données.")
                        save_all(new_data)
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        try:
                            save_all(old_data)
                        except Exception:
                            pass
                        raise
        except OSError as exc:
            await interaction.followup.send(
                f"❌ Rollback annulé : `{exc}`",
                ephemeral=True,
            )
            return
        except (discord.Forbidden, discord.HTTPException) as exc:
            await interaction.followup.send(
                f"❌ Rollback interrompu : `{type(exc).__name__}: {exc}`",
                ephemeral=True,
            )
            return
        except Exception as exc:
            await interaction.followup.send(
                f"❌ Rollback annulé : `{type(exc).__name__}: {exc}`",
                ephemeral=True,
            )
            return

        print(f"[ROLLBACK] guild={guild.id} {current_year} -> {year}", flush=True)
        print(f"[ROLLBACK] active academic year: {year}", flush=True)
        print(f"[ROLLBACK] config updated: {CONFIG_FILE}", flush=True)
        await interaction.followup.send(
            f"✅ Rollback terminé. **{year}** est maintenant l'année scolaire active.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(YearRollbackCommands(bot))

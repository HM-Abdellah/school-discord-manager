"""Interactive setup wizard for the stream-based school Discord architecture."""

from __future__ import annotations

from datetime import date
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import get_level, get_levels, get_stream_abbreviation, get_stream_subjects, get_streams
from services.build_diagnostics import DiagnosticServerBuilder
from services.build_guard import get_build_lock
from services.permissions import management_check
from services.storage import get_active_academic_year, save_guild_config


def default_academic_year() -> str:
    now = date.today()
    start = now.year if now.month >= 8 else now.year - 1
    return f"{start}/{start + 1}"


def current_year_for(guild_id: int) -> str:
    row = get_active_academic_year(guild_id)
    return str(row["name"]) if row else default_academic_year()


class SetupBaseView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = 900) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Cette configuration appartient à un autre administrateur.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        message = f"❌ Erreur pendant la configuration : `{type(error).__name__}: {error}`"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass
        print(f"[SETUP UI ERROR] {type(error).__name__}: {error}")

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


class LevelSelect(discord.ui.Select):
    def __init__(self, view: "LevelView") -> None:
        self.parent_view = view
        options = [
            discord.SelectOption(
                label=name,
                value=name,
                description=f"{len(get_streams(name))} filière(s) disponibles",
                emoji=("📘" if name == "Tronc Commun" else "1️⃣" if "1ère" in name else "2️⃣"),
            )
            for name in get_levels()
        ]
        super().__init__(placeholder="Sélectionne les niveaux présents...", min_values=1, max_values=len(options), options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = [name for name in get_levels() if name in self.values]
        if not selected:
            await interaction.response.send_message("❌ Sélectionne au moins un niveau.", ephemeral=True)
            return
        self.parent_view.selected_level_names = selected
        await self.parent_view.show_current_level(interaction)


class LevelView(SetupBaseView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.selected_level_names: list[str] = []
        self.current_level_index = 0
        self.completed_levels: list[dict[str, Any]] = []
        self.add_item(LevelSelect(self))

    async def show_current_level(self, interaction: discord.Interaction) -> None:
        if not self.selected_level_names:
            await interaction.response.send_message("❌ Aucun niveau sélectionné. Relance `/setup`.", ephemeral=True)
            return
        level_name = self.selected_level_names[self.current_level_index]
        await interaction.response.edit_message(
            content=(
                f"## 📚 {level_name}\n\n"
                f"Niveau **{self.current_level_index + 1}/{len(self.selected_level_names)}**\n\n"
                "Sélectionne uniquement les **filières présentes dans ton établissement**.\n"
                "Les codes courts servent uniquement à garder Discord compact.\n"
                "Le serveur sera organisé par niveau, puis les filières seront regroupées visuellement."
            ),
            view=StreamView(self.owner_id, self.selected_level_names, self.current_level_index, self.completed_levels),
        )


class StreamSelect(discord.ui.Select):
    def __init__(self, view: "StreamView") -> None:
        self.parent_view = view
        options = [
            discord.SelectOption(
                label=name,
                value=name,
                description=f"{get_stream_abbreviation(view.current_level, name)} • {len(get_stream_subjects(view.current_level, name))} matières",
            )
            for name in get_streams(view.current_level)
        ]
        super().__init__(placeholder="Sélectionne les filières présentes dans ton établissement...", min_values=1, max_values=len(options), options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = [name for name in get_streams(self.parent_view.current_level) if name in self.values]
        if not selected:
            await interaction.response.send_message("❌ Sélectionne au moins une filière.", ephemeral=True)
            return
        await self.parent_view.finish_level(interaction, selected)


class StreamView(SetupBaseView):
    def __init__(self, owner_id: int, selected_level_names: list[str], level_index: int, completed_levels: list[dict[str, Any]]) -> None:
        super().__init__(owner_id)
        self.selected_level_names = list(selected_level_names)
        self.level_index = level_index
        self.completed_levels = list(completed_levels)
        self.current_level = self.selected_level_names[level_index]
        self.add_item(StreamSelect(self))

    async def finish_level(self, interaction: discord.Interaction, selected_streams: list[str]) -> None:
        level = get_level(self.current_level)
        streams = [
            {
                "name": stream_name,
                "abbreviation": get_stream_abbreviation(self.current_level, stream_name),
                "subjects": get_stream_subjects(self.current_level, stream_name),
            }
            for stream_name in selected_streams
        ]
        completed = list(self.completed_levels)
        completed.append({"name": self.current_level, "abbreviation": level["abbreviation"], "streams": streams})
        next_index = self.level_index + 1
        if next_index < len(self.selected_level_names):
            next_level = self.selected_level_names[next_index]
            await interaction.response.edit_message(
                content=f"## ✅ {self.current_level} configuré\n\nPassage au niveau suivant : **{next_level}**",
                view=StreamView(self.owner_id, self.selected_level_names, next_index, completed),
            )
            return
        guild_id = interaction.guild.id if interaction.guild else 0
        config = {"academic_year": current_year_for(guild_id), "levels": completed}
        summary = SummaryView(self.owner_id, config)
        await interaction.response.edit_message(content=summary.format_summary(), view=summary)


class SummaryView(SetupBaseView):
    def __init__(self, owner_id: int, config: dict[str, Any]) -> None:
        super().__init__(owner_id)
        self.config = config
        build = discord.ui.Button(label="Construire le serveur", style=discord.ButtonStyle.success, emoji="🏗️")
        restart = discord.ui.Button(label="Recommencer", style=discord.ButtonStyle.secondary, emoji="🔄")
        cancel = discord.ui.Button(label="Annuler", style=discord.ButtonStyle.danger, emoji="❌")
        build.callback = self.build_callback
        restart.callback = self.restart_callback
        cancel.callback = self.cancel_callback
        self.add_item(build)
        self.add_item(restart)
        self.add_item(cancel)

    def format_summary(self) -> str:
        levels = self.config.get("levels", [])
        stream_count = sum(len(level.get("streams", [])) for level in levels)
        lines = [
            "## ✅ Configuration prête",
            "",
            f"📅 Année scolaire : **{self.config.get('academic_year', 'non définie')}**",
            "",
            "**Organisation :** une catégorie Discord par niveau.",
            "**Filières :** regroupées visuellement dans leur niveau.",
            "**Aucune classe ne sera créée comme rôle ou channel.**",
            "**Matières :** un channel par matière et par filière.",
            "**Emploi du temps :** un channel par filière pour toutes ses classes/groupes.",
            "**Codes courts :** utilisés pour garder Discord compact.",
            "",
        ]
        for level in levels:
            lines.append(f"### 📚 {level['name']}")
            for stream in level.get("streams", []):
                code = stream.get("abbreviation", stream["name"])
                lines.append(f"• **{code}** — {stream['name']} → {len(stream.get('subjects', []))} matières")
            lines.append("")
        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"**Niveaux :** {len(levels)}",
            f"**Filières sélectionnées :** {stream_count}",
            "**Par filière :** titre visuel · informations · emploi du temps · examens · channels par matière · à-distance",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])
        return "\n".join(lines)

    async def build_callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        lock = get_build_lock(guild_id)
        if lock.locked():
            await interaction.response.send_message(
                "⏳ Une construction/reconciliation est déjà en cours sur ce serveur. Attends qu'elle se termine avant de relancer `/build`.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(content="🏗️ **Construction en cours...**", view=None)
        try:
            async with lock:
                # Discord is the source of truth for the build step. Only persist the
                # configuration after the complete builder succeeds.
                stats = await DiagnosticServerBuilder(interaction.guild).build(self.config)
                save_guild_config(guild_id, self.config)
        except discord.Forbidden:
            await interaction.edit_original_response(content="❌ Permission refusée. Vérifie les permissions et la hiérarchie des rôles.")
            return
        except discord.HTTPException as exc:
            await interaction.edit_original_response(content=f"❌ Discord API : `{exc}`")
            return
        except Exception as exc:
            await interaction.edit_original_response(content=f"❌ Erreur : `{type(exc).__name__}: {exc}`")
            return
        await interaction.edit_original_response(
            content=(
                "# ✅ Serveur construit avec succès\n\n"
                f"• Niveaux : **{stats.levels_processed}**\n"
                f"• Filières : **{stats.streams_processed}**\n"
                f"• Rôles créés : **{stats.roles_created}**\n"
                f"• Catégories : **{stats.categories_created}**\n"
                f"• Texte : **{stats.text_channels_created}**\n"
                f"• Vocaux : **{stats.voice_channels_created}**"
            )
        )

    async def restart_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            content="## 🏫 School Discord Manager\n\nSélectionne les niveaux présents.",
            view=LevelView(self.owner_id),
        )

    async def cancel_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="❌ Configuration annulée.", view=None)


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="setup", description="Configurer les niveaux et filières du serveur scolaire.")
    @management_check()
    async def setup_command(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild.id if interaction.guild else 0
        await interaction.response.send_message(
            "## 🏫 School Discord Manager\n\n"
            "Sélectionne les niveaux présents, puis uniquement les filières présentes dans ton établissement.\n"
            "Les noms complets restent utilisés dans la configuration interne; les codes courts servent à garder Discord compact.\n\n"
            f"📅 Année active : **{current_year_for(guild_id)}**",
            view=LevelView(interaction.user.id),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Setup(bot))

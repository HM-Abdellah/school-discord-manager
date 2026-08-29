"""Interactive setup wizard for the compact school Discord architecture."""

from __future__ import annotations

from datetime import date
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import (
    MAX_CLASSES_PER_STREAM,
    get_level,
    get_levels,
    get_stream_class_names,
    get_stream_subjects,
    get_streams,
)
from services.server_builder import ServerBuilder
from services.storage import get_active_academic_year, save_guild_config


def default_academic_year() -> str:
    now = date.today()
    start = now.year if now.month >= 8 else now.year - 1
    return f"{start}/{start + 1}"


def current_year_for(guild_id: int) -> str:
    row = get_active_academic_year(guild_id)
    return str(row["name"]) if row else default_academic_year()


class SetupBaseView(discord.ui.View):
    """Base view restricted to the administrator who started the wizard."""

    def __init__(self, owner_id: int, *, timeout: float = 900) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ Cette configuration appartient à un autre administrateur.",
                ephemeral=True,
            )
            return False
        return True

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        """Always answer failed UI interactions instead of letting Discord time out."""
        message = (
            "❌ Une erreur s'est produite pendant la configuration.\n"
            f"`{type(error).__name__}: {error}`"
        )
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
        levels = get_levels()
        options = [
            discord.SelectOption(
                label=name,
                value=name,
                description=f"Configurer {len(get_streams(name))} filière(s)",
                emoji=("📚" if name == "Tronc Commun" else "1️⃣" if "1ère" in name else "2️⃣"),
            )
            for name in levels
        ]
        super().__init__(
            placeholder="Sélectionne les niveaux présents...",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = [name for name in get_levels() if name in self.values]
        if not selected:
            await interaction.response.send_message(
                "❌ Sélectionne au moins un niveau.",
                ephemeral=True,
            )
            return
        self.parent_view.selected_level_names = selected
        await self.parent_view.show_current_level(interaction, reset=True)


class LevelView(SetupBaseView):
    """First setup screen: select academic levels."""

    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.selected_level_names: list[str] = []
        self.current_level_index = 0
        self.completed_levels: list[dict[str, Any]] = []
        self.add_item(LevelSelect(self))

    async def show_current_level(
        self,
        interaction: discord.Interaction,
        *,
        reset: bool = False,
    ) -> None:
        if reset:
            self.current_level_index = 0
            self.completed_levels = []

        if not self.selected_level_names:
            await interaction.response.send_message(
                "❌ Aucun niveau sélectionné. Relance `/setup`.",
                ephemeral=True,
            )
            return

        if not 0 <= self.current_level_index < len(self.selected_level_names):
            await interaction.response.send_message(
                "❌ L'étape de configuration est devenue invalide. Relance `/setup`.",
                ephemeral=True,
            )
            return

        level_name = self.selected_level_names[self.current_level_index]
        view = StreamView(
            owner_id=self.owner_id,
            selected_level_names=list(self.selected_level_names),
            level_index=self.current_level_index,
            completed_levels=list(self.completed_levels),
        )

        if interaction.response.is_done():
            await interaction.edit_original_response(
                content=(
                    f"## 📚 {level_name}\n\n"
                    f"Niveau **{self.current_level_index + 1}/{len(self.selected_level_names)}**\n"
                    f"Filières disponibles : **{len(get_streams(level_name))}**\n\n"
                    "Sélectionne les filières présentes dans ton établissement."
                ),
                view=view,
            )
        else:
            await interaction.response.edit_message(
                content=(
                    f"## 📚 {level_name}\n\n"
                    f"Niveau **{self.current_level_index + 1}/{len(self.selected_level_names)}**\n"
                    f"Filières disponibles : **{len(get_streams(level_name))}**\n\n"
                    "Sélectionne les filières présentes dans ton établissement."
                ),
                view=view,
            )


class StreamSelect(discord.ui.Select):
    def __init__(self, view: "StreamView") -> None:
        self.parent_view = view
        options = [
            discord.SelectOption(
                label=name,
                value=name,
                description=(
                    f"{len(get_stream_subjects(view.current_level, name))} matières · "
                    f"{len(get_stream_class_names(view.current_level, name))} classe(s) par défaut"
                ),
            )
            for name in get_streams(view.current_level)
        ]
        super().__init__(
            placeholder="Sélectionne les filières...",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = [name for name in get_streams(self.parent_view.current_level) if name in self.values]
        if not selected:
            await interaction.response.send_message(
                "❌ Sélectionne au moins une filière.",
                ephemeral=True,
            )
            return
        self.parent_view.selected_streams = selected
        self.parent_view.stream_index = 0
        await self.parent_view.ask_class_count(interaction)


class StreamView(SetupBaseView):
    def __init__(
        self,
        owner_id: int,
        selected_level_names: list[str],
        level_index: int,
        completed_levels: list[dict[str, Any]],
    ) -> None:
        super().__init__(owner_id)
        self.selected_level_names = list(selected_level_names)
        self.level_index = level_index
        self.completed_levels = list(completed_levels)
        self.current_level = self.selected_level_names[level_index]
        self.selected_streams: list[str] = []
        self.stream_index = 0
        self.stream_counts: list[dict[str, Any]] = []
        self.add_item(StreamSelect(self))

    async def ask_class_count(self, interaction: discord.Interaction) -> None:
        if not self.selected_streams or not 0 <= self.stream_index < len(self.selected_streams):
            await interaction.response.send_message(
                "❌ La sélection des filières est invalide. Relance `/setup`.",
                ephemeral=True,
            )
            return

        stream_name = self.selected_streams[self.stream_index]
        subjects = get_stream_subjects(self.current_level, stream_name)
        default_count = len(get_stream_class_names(self.current_level, stream_name))

        await interaction.response.edit_message(
            content=(
                f"## 📚 {self.current_level}\n\n"
                f"Filière : **{stream_name}**\n"
                f"Étape : **{self.stream_index + 1}/{len(self.selected_streams)}**\n"
                f"Matières : **{len(subjects)}**\n"
                f"Classes par défaut : **{default_count}**\n\n"
                "Choisis le nombre réel de classes."
            ),
            view=ClassCountView(
                owner_id=self.owner_id,
                parent=self,
                stream_name=stream_name,
                default_count=default_count,
            ),
        )

    async def save_class_count(
        self,
        interaction: discord.Interaction,
        stream_name: str,
        class_count: int,
    ) -> None:
        if not 1 <= class_count <= MAX_CLASSES_PER_STREAM:
            await interaction.response.send_message(
                f"❌ Le nombre de classes doit être entre 1 et {MAX_CLASSES_PER_STREAM}.",
                ephemeral=True,
            )
            return

        if stream_name not in self.selected_streams:
            await interaction.response.send_message(
                "❌ Cette filière n'est plus valide. Relance `/setup`.",
                ephemeral=True,
            )
            return

        subjects = get_stream_subjects(self.current_level, stream_name)
        classes = [f"Classe {i}" for i in range(1, class_count + 1)]

        self.stream_counts.append(
            {
                "name": stream_name,
                "class_count": class_count,
                "classes": classes,
                "subjects": subjects,
            }
        )
        self.stream_index += 1

        if self.stream_index < len(self.selected_streams):
            await self.ask_class_count(interaction)
            return

        level = get_level(self.current_level)
        completed_levels = list(self.completed_levels)
        completed_levels.append(
            {
                "name": self.current_level,
                "abbreviation": level["abbreviation"],
                "streams": list(self.stream_counts),
            }
        )

        next_level_index = self.level_index + 1
        if next_level_index < len(self.selected_level_names):
            next_level = self.selected_level_names[next_level_index]
            await interaction.response.edit_message(
                content=(
                    f"## ✅ {self.current_level} configuré\n\n"
                    f"Passage au niveau suivant : **{next_level}**\n"
                    f"Niveau : **{next_level_index + 1}/{len(self.selected_level_names)}**\n\n"
                    "Sélectionne les filières présentes dans ton établissement."
                ),
                view=StreamView(
                    self.owner_id,
                    self.selected_level_names,
                    next_level_index,
                    completed_levels,
                ),
            )
            return

        guild_id = interaction.guild.id if interaction.guild else 0
        config = {
            "academic_year": current_year_for(guild_id),
            "levels": completed_levels,
        }
        summary = SummaryView(self.owner_id, config)
        await interaction.response.edit_message(
            content=summary.format_summary(),
            view=summary,
        )


class ClassCountSelect(discord.ui.Select):
    def __init__(self, view: "ClassCountView") -> None:
        self.parent_view = view
        default = max(1, min(view.default_count, MAX_CLASSES_PER_STREAM))
        options = [
            discord.SelectOption(
                label=str(number),
                value=str(number),
                description="Nombre recommandé" if number == default else f"{number} classe(s)",
                default=number == default,
            )
            for number in range(1, MAX_CLASSES_PER_STREAM + 1)
        ]
        super().__init__(
            placeholder="Nombre de classes...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.parent.save_class_count(
            interaction,
            self.parent_view.stream_name,
            int(self.values[0]),
        )


class ClassCountView(SetupBaseView):
    def __init__(
        self,
        owner_id: int,
        parent: StreamView,
        stream_name: str,
        default_count: int,
    ) -> None:
        super().__init__(owner_id)
        self.parent = parent
        self.stream_name = stream_name
        self.default_count = max(1, min(default_count, MAX_CLASSES_PER_STREAM))
        self.add_item(ClassCountSelect(self))


class SummaryView(SetupBaseView):
    def __init__(self, owner_id: int, config: dict[str, Any]) -> None:
        super().__init__(owner_id)
        self.config = config

        build = discord.ui.Button(
            label="Construire le serveur",
            style=discord.ButtonStyle.success,
            emoji="🏗️",
        )
        restart = discord.ui.Button(
            label="Recommencer",
            style=discord.ButtonStyle.secondary,
            emoji="🔄",
        )
        cancel = discord.ui.Button(
            label="Annuler",
            style=discord.ButtonStyle.danger,
            emoji="❌",
        )
        build.callback = self.build_callback
        restart.callback = self.restart_callback
        cancel.callback = self.cancel_callback
        self.add_item(build)
        self.add_item(restart)
        self.add_item(cancel)

    def calculate(self) -> tuple[int, int, int]:
        total_classes = sum(
            int(stream["class_count"])
            for level in self.config.get("levels", [])
            for stream in level.get("streams", [])
        )
        levels = len(self.config.get("levels", []))
        estimated_channels = 7 + (6 * levels)
        return total_classes, levels, estimated_channels

    def format_summary(self) -> str:
        total_classes, levels, channels = self.calculate()
        lines = [
            "## ✅ Configuration prête",
            "",
            f"📅 Année scolaire : **{self.config.get('academic_year', 'non définie')}**",
            "",
            "Matières = **tags Forum** · Classes = **rôles**.",
            "",
        ]

        for level in self.config.get("levels", []):
            lines.append(f"### 📚 {level['name']}")
            for stream in level.get("streams", []):
                lines.append(
                    f"• **{stream['name']}** → {stream['class_count']} classe(s) → "
                    f"{len(stream.get('subjects', []))} matière(s) en tags"
                )
            lines.append("")

        lines.extend(
            [
                "━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"**Niveaux :** {levels}",
                f"**Classes :** {total_classes}",
                f"**Channels structurants :** ~{channels}",
                "**Forums pédagogiques :** 3 par niveau",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            ]
        )
        return "\n".join(lines)

    async def build_callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return

        await interaction.response.edit_message(
            content="🏗️ **Construction en cours...**\n\nCréation d'une structure compacte et organisée.",
            view=None,
        )

        try:
            save_guild_config(interaction.guild.id, self.config)
            stats = await ServerBuilder(interaction.guild).build(self.config)
        except discord.Forbidden:
            await interaction.edit_original_response(
                content="❌ Permission refusée. Vérifie les permissions et la hiérarchie des rôles."
            )
            return
        except discord.HTTPException as exc:
            await interaction.edit_original_response(content=f"❌ Discord API : `{exc}`")
            return
        except Exception as exc:
            await interaction.edit_original_response(
                content=f"❌ Erreur : `{type(exc).__name__}: {exc}`"
            )
            return

        await interaction.edit_original_response(
            content=(
                "# ✅ Serveur construit avec succès\n\n"
                f"• Niveaux : **{stats.levels_processed}**\n"
                f"• Classes : **{stats.classes_processed}**\n"
                f"• Rôles : **{stats.roles_created}**\n"
                f"• Catégories : **{stats.categories_created}**\n"
                f"• Texte : **{stats.text_channels_created}**\n"
                f"• Forums : **{stats.forums_created}**\n"
                f"• Vocaux : **{stats.voice_channels_created}**"
            )
        )

    async def restart_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            content=(
                "## 🏫 School Discord Manager\n\n"
                "Sélectionne les niveaux présents dans ton établissement."
            ),
            view=LevelView(self.owner_id),
        )

    async def cancel_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            content="❌ Configuration annulée.",
            view=None,
        )


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="setup",
        description="Configurer les niveaux, filières et classes du serveur scolaire.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_command(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild.id if interaction.guild else 0
        await interaction.response.send_message(
            "## 🏫 School Discord Manager\n\n"
            "Sélectionne les niveaux présents dans ton établissement.\n"
            "Les matières seront organisées comme **tags Forum**.\n\n"
            f"📅 Année active : **{current_year_for(guild_id)}**",
            view=LevelView(interaction.user.id),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Setup(bot))

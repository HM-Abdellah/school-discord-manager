"""Interactive setup wizard for the compact school Discord architecture."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import (
    CURRICULUM,
    MAX_CLASSES_PER_STREAM,
    get_level,
    get_levels,
    get_stream_class_names,
    get_stream_subjects,
    get_streams,
)
from services.server_builder import ServerBuilder
from services.storage import save_guild_config


class SetupBaseView(discord.ui.View):
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
                description=f"Configurer {len(get_streams(name))} filière(s)",
                emoji="📚" if name == "Tronc Commun" else ("1️⃣" if "1ère" in name else "2️⃣"),
            )
            for name in get_levels()
        ]
        super().__init__(
            placeholder="Sélectionne les niveaux présents...",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = [name for name in get_levels() if name in self.values]
        await self.parent_view.start_next_level(interaction, selected)


class LevelView(SetupBaseView):
    level_order = tuple(get_levels())

    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.selected_level_names: list[str] = []
        self.completed_levels: list[dict] = []
        self.current_level_index = 0
        self.add_item(LevelSelect(self))

    async def start_next_level(self, interaction: discord.Interaction, selected: list[str]) -> None:
        self.selected_level_names = selected
        self.completed_levels = []
        self.current_level_index = 0
        await self.show_current_level(interaction)

    async def show_current_level(self, interaction: discord.Interaction) -> None:
        level_name = self.selected_level_names[self.current_level_index]
        view = StreamView(
            owner_id=self.owner_id,
            selected_level_names=self.selected_level_names,
            level_index=self.current_level_index,
            completed_levels=self.completed_levels,
        )
        stream_count = len(get_streams(level_name))
        await interaction.response.edit_message(
            content=(
                f"## 📚 {level_name}\n\n"
                f"Niveau **{self.current_level_index + 1}/{len(self.selected_level_names)}**\n"
                f"Filières disponibles : **{stream_count}**\n\n"
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
                description=f"{len(get_stream_subjects(view.current_level, name))} matières · "
                            f"{len(get_stream_class_names(view.current_level, name))} classe(s) par défaut",
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
        self.parent_view.selected_streams = list(self.values)
        self.parent_view.stream_index = 0
        await self.parent_view.ask_class_count(interaction)


class StreamView(SetupBaseView):
    def __init__(self, owner_id: int, selected_level_names: list[str], level_index: int, completed_levels: list[dict]) -> None:
        super().__init__(owner_id)
        self.selected_level_names = selected_level_names
        self.level_index = level_index
        self.completed_levels = completed_levels
        self.current_level = selected_level_names[level_index]
        self.selected_streams: list[str] = []
        self.stream_index = 0
        self.stream_counts: list[dict] = []
        self.add_item(StreamSelect(self))

    async def ask_class_count(self, interaction: discord.Interaction) -> None:
        stream_name = self.selected_streams[self.stream_index]
        class_names = get_stream_class_names(self.current_level, stream_name)
        subjects = get_stream_subjects(self.current_level, stream_name)
        view = ClassCountView(
            owner_id=self.owner_id,
            parent=self,
            stream_name=stream_name,
            default_count=len(class_names),
            subject_count=len(subjects),
        )
        defaults_text = ", ".join(class_names) if class_names else "Aucune classe prédéfinie"
        if len(defaults_text) > 500:
            defaults_text = f"{len(class_names)} classe(s) prédéfinie(s)"
        await interaction.response.edit_message(
            content=(
                f"## 📚 {self.current_level}\n\n"
                f"Filière : **{stream_name}**\n"
                f"Étape : **{self.stream_index + 1}/{len(self.selected_streams)}**\n"
                f"Matières : **{len(subjects)}**\n"
                f"Classes par défaut : **{defaults_text}**\n\n"
                "Choisis le nombre de classes à exposer dans cette filière."
            ),
            view=view,
        )

    async def save_class_count(self, interaction: discord.Interaction, stream_name: str, class_count: int) -> None:
        configured_classes = get_stream_class_names(self.current_level, stream_name)
        subjects = get_stream_subjects(self.current_level, stream_name)
        self.stream_counts.append(
            {
                "name": stream_name,
                "class_count": class_count,
                "classes": configured_classes[:class_count],
                "subjects": subjects,
            }
        )
        self.stream_index += 1
        if self.stream_index < len(self.selected_streams):
            await self.ask_class_count(interaction)
            return

        level = get_level(self.current_level)
        self.completed_levels.append(
            {
                "name": self.current_level,
                "abbreviation": level["abbreviation"],
                "streams": self.stream_counts,
            }
        )
        next_index = self.level_index + 1
        if next_index < len(self.selected_level_names):
            next_level = self.selected_level_names[next_index]
            next_view = StreamView(
                owner_id=self.owner_id,
                selected_level_names=self.selected_level_names,
                level_index=next_index,
                completed_levels=self.completed_levels,
            )
            await interaction.response.edit_message(
                content=(
                    f"## ✅ {self.current_level} configuré\n\n"
                    f"Passage au niveau suivant : **{next_level}**\n"
                    f"Niveau : **{next_index + 1}/{len(self.selected_level_names)}**\n\n"
                    "Sélectionne les filières présentes dans ton établissement."
                ),
                view=next_view,
            )
            return

        summary = SummaryView(self.owner_id, {"levels": self.completed_levels})
        await interaction.response.edit_message(content=summary.format_summary(), view=summary)


class ClassCountSelect(discord.ui.Select):
    def __init__(self, view: "ClassCountView") -> None:
        self.parent_view = view
        options = [
            discord.SelectOption(
                label=str(number),
                value=str(number),
                description=("Nombre recommandé" if number == view.default_count else f"{number} classe(s)"),
                default=number == view.default_count,
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
    def __init__(self, owner_id: int, parent: StreamView, stream_name: str, default_count: int, subject_count: int) -> None:
        super().__init__(owner_id)
        self.parent = parent
        self.stream_name = stream_name
        self.default_count = max(1, min(default_count, MAX_CLASSES_PER_STREAM))
        self.subject_count = subject_count
        self.add_item(ClassCountSelect(self))


class SummaryView(SetupBaseView):
    def __init__(self, owner_id: int, config: dict) -> None:
        super().__init__(owner_id)
        self.config = config
        for label, style, emoji, callback in (
            ("Construire le serveur", discord.ButtonStyle.success, "🏗️", self.build_callback),
            ("Recommencer", discord.ButtonStyle.secondary, "🔄", self.restart_callback),
            ("Annuler", discord.ButtonStyle.danger, "❌", self.cancel_callback),
        ):
            button = discord.ui.Button(label=label, style=style, emoji=emoji)
            button.callback = callback
            self.add_item(button)

    def calculate(self) -> tuple[int, int, int]:
        total_classes = sum(
            int(stream["class_count"])
            for level in self.config["levels"]
            for stream in level["streams"]
        )
        levels = len(self.config["levels"])
        estimated_channels = 7 * levels + 7
        return total_classes, levels, estimated_channels

    def format_summary(self) -> str:
        total_classes, levels, channels = self.calculate()
        lines = [
            "## ✅ Configuration prête",
            "",
            "La nouvelle structure **ne crée plus un salon par matière ni par classe**.",
            "Les matières deviennent des tags de Forum et les classes deviennent des rôles.",
            "",
        ]
        for level in self.config["levels"]:
            lines.append(f"### 📚 {level['name']}")
            for stream in level["streams"]:
                lines.append(
                    f"• **{stream['name']}** → {stream['class_count']} classe(s) → "
                    f"{len(stream['subjects'])} matière(s) dans les Forums"
                )
            lines.append("")
        lines.extend(
            [
                "━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"**Niveaux :** {levels}",
                f"**Classes :** {total_classes}",
                f"**Channels créés par la structure :** ~{channels}",
                "**Forums matières :** 3 par niveau",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            ]
        )
        return "\n".join(lines)

    async def build_callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Cette configuration doit être utilisée dans un serveur.", ephemeral=True)
            return
        await interaction.response.edit_message(
            content="🏗️ **Construction en cours...**\n\nCréation d'une structure compacte et organisée.",
            view=None,
        )
        try:
            save_guild_config(interaction.guild.id, self.config)
            stats = await ServerBuilder(interaction.guild).build(self.config)
        except discord.Forbidden:
            await interaction.edit_original_response(content="❌ Permission refusée par Discord. Vérifie les permissions du bot et la hiérarchie des rôles.")
            return
        except discord.HTTPException as exc:
            await interaction.edit_original_response(content=f"❌ Discord API a retourné une erreur : `{exc}`")
            return
        except Exception as exc:
            await interaction.edit_original_response(content=f"❌ Erreur inattendue : `{type(exc).__name__}: {exc}`")
            return
        await interaction.edit_original_response(
            content=(
                "# ✅ Serveur construit avec succès\n\n"
                f"• Rôles créés : **{stats.roles_created}**\n"
                f"• Catégories créées : **{stats.categories_created}**\n"
                f"• Salons texte créés : **{stats.text_channels_created}**\n"
                f"• Forums créés : **{stats.forums_created}**\n"
                f"• Salons vocaux créés : **{stats.voice_channels_created}**\n"
                f"• Classes traitées : **{stats.classes_processed}**\n"
                f"• Niveaux traités : **{stats.levels_processed}**"
            )
        )

    async def restart_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            content="## 🏫 School Discord Manager\n\nSélectionne les niveaux présents dans ton établissement.",
            view=LevelView(self.owner_id),
        )

    async def cancel_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="❌ Configuration annulée.", view=None)


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="setup",
        description="Configure les niveaux, filières et classes du serveur scolaire.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "## 🏫 School Discord Manager\n\n"
            "Sélectionne les niveaux présents dans ton établissement.\n"
            "Les matières seront organisées automatiquement dans des Forums.",
            view=LevelView(interaction.user.id),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Setup(bot))

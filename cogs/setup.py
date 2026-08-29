"""Interactive setup wizard for the compact school Discord architecture."""

from __future__ import annotations

from datetime import date

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
from services.storage import save_guild_config


def default_academic_year() -> str:
    now = date.today()
    start = now.year if now.month >= 8 else now.year - 1
    return f"{start}/{start + 1}"


class SetupBaseView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = 900) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Cette configuration appartient à un autre administrateur.", ephemeral=True)
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
            ) for name in get_levels()
        ]
        super().__init__(placeholder="Sélectionne les niveaux présents...", min_values=1, max_values=len(options), options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.start_next_level(interaction, [name for name in get_levels() if name in self.values])


class LevelView(SetupBaseView):
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
        await interaction.response.edit_message(
            content=(f"## 📚 {level_name}\n\nNiveau **{self.current_level_index + 1}/{len(self.selected_level_names)}**\n"
                     f"Filières disponibles : **{len(get_streams(level_name))}**\n\nSélectionne les filières présentes dans ton établissement."),
            view=StreamView(self.owner_id, self.selected_level_names, self.current_level_index, self.completed_levels),
        )


class StreamSelect(discord.ui.Select):
    def __init__(self, view: "StreamView") -> None:
        self.parent_view = view
        options = [
            discord.SelectOption(
                label=name,
                value=name,
                description=f"{len(get_stream_subjects(view.current_level, name))} matières · {len(get_stream_class_names(view.current_level, name))} classe(s) par défaut",
            ) for name in get_streams(view.current_level)
        ]
        super().__init__(placeholder="Sélectionne les filières...", min_values=1, max_values=len(options), options=options)

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
        subjects = get_stream_subjects(self.current_level, stream_name)
        default_count = len(get_stream_class_names(self.current_level, stream_name))
        await interaction.response.edit_message(
            content=(f"## 📚 {self.current_level}\n\nFilière : **{stream_name}**\n"
                     f"Étape : **{self.stream_index + 1}/{len(self.selected_streams)}**\n"
                     f"Matières : **{len(subjects)}**\nClasses par défaut : **{default_count}**\n\n"
                     "Choisis le nombre réel de classes. Les noms Classe 1, Classe 2, etc. seront générés automatiquement."),
            view=ClassCountView(self.owner_id, self, stream_name, default_count),
        )

    async def save_class_count(self, interaction: discord.Interaction, stream_name: str, class_count: int) -> None:
        subjects = get_stream_subjects(self.current_level, stream_name)
        classes = [f"Classe {i}" for i in range(1, class_count + 1)]
        self.stream_counts.append({"name": stream_name, "class_count": class_count, "classes": classes, "subjects": subjects})
        self.stream_index += 1
        if self.stream_index < len(self.selected_streams):
            await self.ask_class_count(interaction)
            return
        level = get_level(self.current_level)
        self.completed_levels.append({"name": self.current_level, "abbreviation": level["abbreviation"], "streams": self.stream_counts})
        next_index = self.level_index + 1
        if next_index < len(self.selected_level_names):
            await interaction.response.edit_message(
                content=f"## ✅ {self.current_level} configuré\n\nPassage au niveau suivant : **{self.selected_level_names[next_index]}**",
                view=StreamView(self.owner_id, self.selected_level_names, next_index, self.completed_levels),
            )
            return
        config = {"academic_year": default_academic_year(), "levels": self.completed_levels}
        summary = SummaryView(self.owner_id, config)
        await interaction.response.edit_message(content=summary.format_summary(), view=summary)


class ClassCountSelect(discord.ui.Select):
    def __init__(self, view: "ClassCountView") -> None:
        self.parent_view = view
        default = max(1, min(view.default_count, MAX_CLASSES_PER_STREAM))
        options = [discord.SelectOption(label=str(n), value=str(n), description="Nombre recommandé" if n == default else f"{n} classe(s)", default=n == default) for n in range(1, MAX_CLASSES_PER_STREAM + 1)]
        super().__init__(placeholder="Nombre de classes...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.parent.save_class_count(interaction, self.parent_view.stream_name, int(self.values[0]))


class ClassCountView(SetupBaseView):
    def __init__(self, owner_id: int, parent: StreamView, stream_name: str, default_count: int) -> None:
        super().__init__(owner_id)
        self.parent = parent
        self.stream_name = stream_name
        self.default_count = max(1, min(default_count, MAX_CLASSES_PER_STREAM))
        self.add_item(ClassCountSelect(self))


class SummaryView(SetupBaseView):
    def __init__(self, owner_id: int, config: dict) -> None:
        super().__init__(owner_id)
        self.config = config
        for label, style, emoji, callback in (("Construire le serveur", discord.ButtonStyle.success, "🏗️", self.build_callback), ("Recommencer", discord.ButtonStyle.secondary, "🔄", self.restart_callback), ("Annuler", discord.ButtonStyle.danger, "❌", self.cancel_callback)):
            button = discord.ui.Button(label=label, style=style, emoji=emoji)
            button.callback = callback
            self.add_item(button)

    def calculate(self) -> tuple[int, int, int]:
        total_classes = sum(int(s["class_count"]) for l in self.config["levels"] for s in l["streams"])
        levels = len(self.config["levels"])
        return total_classes, levels, 7 * levels + 7

    def format_summary(self) -> str:
        total_classes, levels, channels = self.calculate()
        lines = ["## ✅ Configuration prête", "", f"📅 Année scolaire : **{self.config['academic_year']}**", "", "Les matières seront des **tags Forum** et les classes des **rôles**.", ""]
        for level in self.config["levels"]:
            lines.append(f"### 📚 {level['name']}")
            for stream in level["streams"]:
                lines.append(f"• **{stream['name']}** → {stream['class_count']} classe(s) → {len(stream['subjects'])} matière(s) en tags")
            lines.append("")
        lines.extend(["━━━━━━━━━━━━━━━━━━━━━━━━━━", f"**Niveaux :** {levels}", f"**Classes :** {total_classes}", f"**Channels structurants :** ~{channels}", "**Forums pédagogiques :** 3 par niveau", "━━━━━━━━━━━━━━━━━━━━━━━━━━"])
        return "\n".join(lines)

    async def build_callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.edit_message(content="🏗️ **Construction en cours...**", view=None)
        try:
            save_guild_config(interaction.guild.id, self.config)
            stats = await ServerBuilder(interaction.guild).build(self.config)
        except discord.Forbidden:
            await interaction.edit_original_response(content="❌ Permission refusée. Vérifie les permissions et la hiérarchie des rôles.")
            return
        except discord.HTTPException as exc:
            await interaction.edit_original_response(content=f"❌ Discord API : `{exc}`")
            return
        except Exception as exc:
            await interaction.edit_original_response(content=f"❌ Erreur : `{type(exc).__name__}: {exc}`")
            return
        await interaction.edit_original_response(content=("# ✅ Serveur construit avec succès\n\n"
            f"• Niveaux : **{stats.levels_processed}**\n• Classes : **{stats.classes_processed}**\n"
            f"• Rôles : **{stats.roles_created}**\n• Catégories : **{stats.categories_created}**\n"
            f"• Texte : **{stats.text_channels_created}**\n• Forums : **{stats.forums_created}**\n• Vocaux : **{stats.voice_channels_created}**"))

    async def restart_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="## 🏫 School Discord Manager\n\nSélectionne les niveaux présents dans ton établissement.", view=LevelView(self.owner_id))

    async def cancel_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="❌ Configuration annulée.", view=None)


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="setup", description="Configurer les niveaux, filières et classes du serveur scolaire.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "## 🏫 School Discord Manager\n\nSélectionne les niveaux présents dans ton établissement.\nLes matières seront organisées automatiquement dans des Forums.\n\n📅 Année détectée : **" + default_academic_year() + "**",
            view=LevelView(interaction.user.id), ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Setup(bot))

"""Interactive school configuration wizard using Discord UI components."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import CURRICULUM, MAX_CLASSES_PER_STREAM
from services.server_builder import ServerBuilder
from services.storage import save_guild_config


class SetupBaseView(discord.ui.View):
    """Base view that restricts interactions to the administrator who started it."""

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
                label="Tronc Commun",
                value="Tronc Commun",
                emoji="📚",
            ),
            discord.SelectOption(
                label="1ère Année Bac",
                value="1ère Année Bac",
                emoji="1️⃣",
            ),
            discord.SelectOption(
                label="2ème Année Bac",
                value="2ème Année Bac",
                emoji="2️⃣",
            ),
        ]
        super().__init__(
            placeholder="Choisis un niveau...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.start_stream_selection(
            interaction,
            self.values[0],
        )


class LevelView(SetupBaseView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.add_item(LevelSelect(self))

    async def start_stream_selection(
        self,
        interaction: discord.Interaction,
        level_name: str,
    ) -> None:
        next_view = StreamView(
            owner_id=self.owner_id,
            selected_levels=[],
            current_level=level_name,
        )
        await interaction.response.edit_message(
            content=(
                f"📚 **{level_name}**\n\n"
                "Sélectionne toutes les filières présentes dans ton établissement."
            ),
            view=next_view,
        )


class StreamSelect(discord.ui.Select):
    def __init__(self, view: "StreamView") -> None:
        self.parent_view = view
        streams = CURRICULUM[view.current_level]["filieres"]
        options = [
            discord.SelectOption(
                label=name,
                value=name,
                description=f"{len(subjects)} matières",
            )
            for name, subjects in streams.items()
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
    def __init__(
        self,
        owner_id: int,
        selected_levels: list[dict],
        current_level: str,
    ) -> None:
        super().__init__(owner_id)
        self.selected_levels = selected_levels
        self.current_level = current_level
        self.selected_streams: list[str] = []
        self.stream_index = 0
        self.stream_counts: list[dict] = []
        self.add_item(StreamSelect(self))

    async def ask_class_count(self, interaction: discord.Interaction) -> None:
        stream_name = self.selected_streams[self.stream_index]
        subjects = CURRICULUM[self.current_level]["filieres"][stream_name]

        view = ClassCountView(
            owner_id=self.owner_id,
            parent=self,
            stream_name=stream_name,
            subject_count=len(subjects),
        )

        await interaction.response.edit_message(
            content=(
                f"📚 **{self.current_level}**\n\n"
                f"Filière : **{stream_name}**\n"
                f"Matières : **{len(subjects)}**\n\n"
                "Combien de classes veux-tu créer pour cette filière ?"
            ),
            view=view,
        )

    async def save_class_count(
        self,
        interaction: discord.Interaction,
        stream_name: str,
        class_count: int,
    ) -> None:
        self.stream_counts.append(
            {
                "name": stream_name,
                "class_count": class_count,
                "subjects": list(CURRICULUM[self.current_level]["filieres"][stream_name]),
            }
        )

        self.stream_index += 1

        if self.stream_index < len(self.selected_streams):
            await self.ask_class_count(interaction)
            return

        self.selected_levels.append(
            {
                "name": self.current_level,
                "abbreviation": CURRICULUM[self.current_level]["abbreviation"],
                "streams": self.stream_counts,
            }
        )

        summary = SummaryView(
            owner_id=self.owner_id,
            config={"levels": self.selected_levels},
        )
        await interaction.response.edit_message(
            content=summary.format_summary(),
            view=summary,
        )


class ClassCountSelect(discord.ui.Select):
    def __init__(self, view: "ClassCountView") -> None:
        self.parent_view = view
        options = [
            discord.SelectOption(
                label=str(number),
                value=str(number),
                description=f"{number} classe(s)",
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
        subject_count: int,
    ) -> None:
        super().__init__(owner_id)
        self.parent = parent
        self.stream_name = stream_name
        self.subject_count = subject_count
        self.add_item(ClassCountSelect(self))


class SummaryView(SetupBaseView):
    def __init__(self, owner_id: int, config: dict) -> None:
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

    def format_summary(self) -> str:
        lines = [
            "## ✅ Configuration prête",
            "",
            "Vérifie la configuration avant de lancer la construction.\n",
        ]
        total_classes = 0
        total_forums = 0

        for level in self.config["levels"]:
            lines.append(f"### 📚 {level['name']}")
            for stream in level["streams"]:
                classes = stream["class_count"]
                subjects = len(stream["subjects"])
                total_classes += classes
                total_forums += classes * subjects
                lines.append(
                    f"• **{stream['name']}** → {classes} classe(s) → {subjects} matière(s)"
                )
            lines.append("")

        lines.extend(
            [
                "━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"**Classes :** {total_classes}",
                f"**Forums matières :** {total_forums}",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            ]
        )
        return "\n".join(lines)

    async def build_callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Cette configuration doit être utilisée dans un serveur.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content="🏗️ **Construction en cours...**\n\nCette opération peut prendre plusieurs minutes.",
            view=None,
        )

        try:
            save_guild_config(interaction.guild.id, self.config)
            stats = await ServerBuilder(interaction.guild).build(self.config)
        except discord.Forbidden:
            await interaction.edit_original_response(
                content=(
                    "❌ Discord a refusé une opération. Vérifie les permissions du bot "
                    "et sa position dans la hiérarchie des rôles."
                )
            )
            return
        except discord.HTTPException as exc:
            await interaction.edit_original_response(
                content=f"❌ Discord API a retourné une erreur : `{exc}`"
            )
            return
        except Exception as exc:
            await interaction.edit_original_response(
                content=f"❌ Erreur inattendue : `{type(exc).__name__}: {exc}`"
            )
            return

        await interaction.edit_original_response(
            content=(
                "# ✅ Serveur construit avec succès\n\n"
                f"• Rôles créés : **{stats.roles_created}**\n"
                f"• Catégories créées : **{stats.categories_created}**\n"
                f"• Salons texte créés : **{stats.text_channels_created}**\n"
                f"• Forums créés : **{stats.forums_created}**\n"
                f"• Salons vocaux créés : **{stats.voice_channels_created}**\n"
                f"• Classes traitées : **{stats.classes_processed}**"
            )
        )

    async def restart_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            content=(
                "## 🏫 School Discord Manager\n\n"
                "Choisis le niveau que tu veux configurer."
            ),
            view=LevelView(self.owner_id),
        )

    async def cancel_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            content="❌ Configuration annulée.",
            view=None,
        )


class Setup(commands.Cog):
    """Interactive setup wizard for administrators."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="setup",
        description="Configure the school's levels, streams and number of classes.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_command(self, interaction: discord.Interaction) -> None:
        view = LevelView(interaction.user.id)
        await interaction.response.send_message(
            "## 🏫 School Discord Manager\n\n"
            "Choisis le niveau que tu veux configurer.",
            view=view,
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Setup(bot))

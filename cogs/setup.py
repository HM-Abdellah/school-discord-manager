"""Interactive setup wizard for the compact school Discord architecture."""

from __future__ import annotations

from datetime import date

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import MAX_CLASSES_PER_STREAM, get_level, get_levels, get_stream_class_names, get_stream_subjects, get_streams
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
    def __init__(self, owner_id: int, *, timeout: float = 900) -> None:
        super().__init__(timeout=timeout); self.owner_id = owner_id
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Cette configuration appartient à un autre administrateur.", ephemeral=True); return False
        return True
    async def on_timeout(self) -> None:
        for child in self.children: child.disabled = True


class LevelSelect(discord.ui.Select):
    def __init__(self, view: "LevelView") -> None:
        self.parent_view = view
        options = [discord.SelectOption(label=n, value=n, description=f"Configurer {len(get_streams(n))} filière(s)", emoji="📚" if n == "Tronc Commun" else ("1️⃣" if "1ère" in n else "2️⃣")) for n in get_levels()]
        super().__init__(placeholder="Sélectionne les niveaux présents...", min_values=1, max_values=len(options), options=options)
    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.start_next_level(interaction, [n for n in get_levels() if n in self.values])


class LevelView(SetupBaseView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id); self.selected_level_names=[]; self.completed_levels=[]; self.current_level_index=0; self.add_item(LevelSelect(self))
    async def start_next_level(self, interaction, selected) -> None:
        self.selected_level_names=selected; self.completed_levels=[]; self.current_level_index=0; await self.show_current_level(interaction)
    async def show_current_level(self, interaction) -> None:
        level=self.selected_level_names[self.current_level_index]
        await interaction.response.edit_message(content=f"## 📚 {level}\n\nNiveau **{self.current_level_index+1}/{len(self.selected_level_names)}**\nFilières disponibles : **{len(get_streams(level))}**\n\nSélectionne les filières présentes.", view=StreamView(self.owner_id,self.selected_level_names,self.current_level_index,self.completed_levels))


class StreamSelect(discord.ui.Select):
    def __init__(self, view: "StreamView") -> None:
        self.parent_view=view
        options=[discord.SelectOption(label=n,value=n,description=f"{len(get_stream_subjects(view.current_level,n))} matières · {len(get_stream_class_names(view.current_level,n))} classe(s) par défaut") for n in get_streams(view.current_level)]
        super().__init__(placeholder="Sélectionne les filières...",min_values=1,max_values=len(options),options=options)
    async def callback(self, interaction) -> None:
        self.parent_view.selected_streams=list(self.values); self.parent_view.stream_index=0; await self.parent_view.ask_class_count(interaction)


class StreamView(SetupBaseView):
    def __init__(self, owner_id, selected_level_names, level_index, completed_levels) -> None:
        super().__init__(owner_id); self.selected_level_names=selected_level_names; self.level_index=level_index; self.completed_levels=completed_levels; self.current_level=selected_level_names[level_index]; self.selected_streams=[]; self.stream_index=0; self.stream_counts=[]; self.add_item(StreamSelect(self))
    async def ask_class_count(self, interaction) -> None:
        stream=self.selected_streams[self.stream_index]; subjects=get_stream_subjects(self.current_level,stream); default=len(get_stream_class_names(self.current_level,stream))
        await interaction.response.edit_message(content=f"## 📚 {self.current_level}\n\nFilière : **{stream}**\nÉtape : **{self.stream_index+1}/{len(self.selected_streams)}**\nMatières : **{len(subjects)}**\nClasses par défaut : **{default}**\n\nChoisis le nombre réel de classes.",view=ClassCountView(self.owner_id,self,stream,default))
    async def save_class_count(self, interaction, stream_name, class_count) -> None:
        subjects=get_stream_subjects(self.current_level,stream_name); classes=[f"Classe {i}" for i in range(1,class_count+1)]
        self.stream_counts.append({"name":stream_name,"class_count":class_count,"classes":classes,"subjects":subjects}); self.stream_index+=1
        if self.stream_index<len(self.selected_streams): await self.ask_class_count(interaction); return
        level=get_level(self.current_level); self.completed_levels.append({"name":self.current_level,"abbreviation":level["abbreviation"],"streams":self.stream_counts})
        nxt=self.level_index+1
        if nxt<len(self.selected_level_names):
            await interaction.response.edit_message(content=f"## ✅ {self.current_level} configuré\n\nPassage au niveau suivant : **{self.selected_level_names[nxt]}**",view=StreamView(self.owner_id,self.selected_level_names,nxt,self.completed_levels)); return
        guild_id=interaction.guild.id if interaction.guild else 0
        config={"academic_year":current_year_for(guild_id),"levels":self.completed_levels}; summary=SummaryView(self.owner_id,config)
        await interaction.response.edit_message(content=summary.format_summary(),view=summary)


class ClassCountSelect(discord.ui.Select):
    def __init__(self, view: "ClassCountView") -> None:
        self.parent_view=view; default=max(1,min(view.default_count,MAX_CLASSES_PER_STREAM))
        options=[discord.SelectOption(label=str(n),value=str(n),description="Nombre recommandé" if n==default else f"{n} classe(s)",default=n==default) for n in range(1,MAX_CLASSES_PER_STREAM+1)]
        super().__init__(placeholder="Nombre de classes...",min_values=1,max_values=1,options=options)
    async def callback(self, interaction) -> None: await self.parent_view.parent.save_class_count(interaction,self.parent_view.stream_name,int(self.values[0]))


class ClassCountView(SetupBaseView):
    def __init__(self, owner_id,parent,stream_name,default_count) -> None:
        super().__init__(owner_id); self.parent=parent; self.stream_name=stream_name; self.default_count=max(1,min(default_count,MAX_CLASSES_PER_STREAM)); self.add_item(ClassCountSelect(self))


class SummaryView(SetupBaseView):
    def __init__(self, owner_id, config) -> None:
        super().__init__(owner_id); self.config=config
        for label,style,emoji,callback in (("Construire le serveur",discord.ButtonStyle.success,"🏗️",self.build_callback),("Recommencer",discord.ButtonStyle.secondary,"🔄",self.restart_callback),("Annuler",discord.ButtonStyle.danger,"❌",self.cancel_callback)):
            b=discord.ui.Button(label=label,style=style,emoji=emoji); b.callback=callback; self.add_item(b)
    def calculate(self):
        total=sum(int(s["class_count"]) for l in self.config["levels"] for s in l["streams"]); levels=len(self.config["levels"]); return total,levels,7*levels+7
    def format_summary(self):
        total,levels,channels=self.calculate(); lines=["## ✅ Configuration prête","",f"📅 Année scolaire : **{self.config['academic_year']}**","","Matières = **tags Forum** · Classes = **rôles**.",""]
        for level in self.config["levels"]:
            lines.append(f"### 📚 {level['name']}")
            for stream in level["streams"]: lines.append(f"• **{stream['name']}** → {stream['class_count']} classe(s) → {len(stream['subjects'])} matière(s) en tags")
            lines.append("")
        lines += ["━━━━━━━━━━━━━━━━━━━━━━━━━━",f"**Niveaux :** {levels}",f"**Classes :** {total}",f"**Channels structurants :** ~{channels}","**Forums pédagogiques :** 3 par niveau","━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        return "\n".join(lines)
    async def build_callback(self, interaction) -> None:
        if interaction.guild is None: await interaction.response.send_message("❌ Serveur requis.",ephemeral=True); return
        await interaction.response.edit_message(content="🏗️ **Construction en cours...**",view=None)
        try:
            save_guild_config(interaction.guild.id,self.config); stats=await ServerBuilder(interaction.guild).build(self.config)
        except discord.Forbidden: await interaction.edit_original_response(content="❌ Permission refusée. Vérifie les permissions et la hiérarchie des rôles."); return
        except discord.HTTPException as exc: await interaction.edit_original_response(content=f"❌ Discord API : `{exc}`"); return
        except Exception as exc: await interaction.edit_original_response(content=f"❌ Erreur : `{type(exc).__name__}: {exc}`"); return
        await interaction.edit_original_response(content=("# ✅ Serveur construit avec succès\n\n"f"• Niveaux : **{stats.levels_processed}**\n• Classes : **{stats.classes_processed}**\n• Rôles : **{stats.roles_created}**\n• Catégories : **{stats.categories_created}**\n• Texte : **{stats.text_channels_created}**\n• Forums : **{stats.forums_created}**\n• Vocaux : **{stats.voice_channels_created}**"))
    async def restart_callback(self, interaction) -> None: await interaction.response.edit_message(content="## 🏫 School Discord Manager\n\nSélectionne les niveaux présents.",view=LevelView(self.owner_id))
    async def cancel_callback(self, interaction) -> None: await interaction.response.edit_message(content="❌ Configuration annulée.",view=None)


class Setup(commands.Cog):
    def __init__(self,bot): self.bot=bot
    @app_commands.command(name="setup",description="Configurer les niveaux, filières et classes du serveur scolaire.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_command(self,interaction):
        await interaction.response.send_message(f"## 🏫 School Discord Manager\n\nSélectionne les niveaux présents.\nLes matières seront des **tags Forum**.\n\n📅 Année active : **{current_year_for(interaction.guild.id)}**",view=LevelView(interaction.user.id),ephemeral=True)


async def setup(bot): await bot.add_cog(Setup(bot))

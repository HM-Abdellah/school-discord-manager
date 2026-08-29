"""Build the school Discord server around shared academic streams."""

from __future__ import annotations

import re

import discord

from config.curriculum import GENERAL_CHANNELS, PROFESSOR_CHANNELS, get_stream_abbreviation, get_stream_subjects, get_subject_display_name
from services.permissions import ROLE_ADMIN, ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, ROLE_STUDENT, STREAM_ROLE_PREFIX, general_area_overwrites, public_voice_overwrites, stream_announcement_overwrites, stream_area_overwrites, subject_channel_overwrites, teacher_area_overwrites

CATEGORY_GENERAL = "🏢・INFORMATIONS & ADMINISTRATION"
CATEGORY_PROFESSORS = "👨‍🏫・ESPACE PROFESSEURS"
CATEGORY_VOICE = "🔊・SALLES VIRTUELLES"
LEVEL_CATEGORY_NAMES = {"Tronc Commun": "📘・TRONC COMMUN", "1ère Année Bac": "1️⃣・1BAC", "2ème Année Bac": "2️⃣・2BAC"}
STREAM_EMOJIS = {"Tronc Commun Scientifique": "🔬", "Tronc Commun Lettres": "📩", "Tronc Commun Technologique": "⚙️", "Tronc Commun Originel": "📜", "1ère Année Bac Sciences Expérimentales": "🧪", "1ère Année Bac Sciences Mathématiques": "📐", "1ère Année Bac Lettres et Sciences Humaines": "📩", "1ère Année Bac Sciences Économiques et Gestion": "💼", "1ère Année Bac Sciences et Technologies Électriques": "⚡", "1ère Année Bac Sciences et Technologies Mécaniques": "🔧", "1ère Année Bac Arts Appliqués": "🎨", "2ème Année Bac Sciences Physiques": "⚛️", "2ème Année Bac Sciences de la Vie et de la Terre": "🌱", "2ème Année Bac Sciences Mathématiques A": "📐", "2ème Année Bac Sciences Mathématiques B": "📐", "2ème Année Bac Lettres": "✉️", "2ème Année Bac Sciences Humaines": "🧠", "2ème Année Bac Sciences Économiques": "💼", "2ème Année Bac Sciences de Gestion Comptable": "📊", "2ème Année Bac Arts Appliqués": "🎨"}


def _safe_name(value: str, max_length: int = 80) -> str:
    value = value.lower().replace("’", "'").replace(" ", "-")
    value = re.sub(r"[^\w\-àâçéèêëîïôûùüÿñæœ']+", "-", value, flags=re.UNICODE)
    return re.sub(r"-+", "-", value).strip("-")[:max_length]


def _subject_channel_name(stream_code: str, subject: str) -> str:
    return f"📚-{stream_code}・{_safe_name(get_subject_display_name(subject), 55)}"


def _stream_header_name(stream_name: str, stream_code: str) -> str:
    return f"🔹-{STREAM_EMOJIS.get(stream_name, '🎓')}・{stream_code}"


def _level_category_name(level_name: str) -> str:
    return LEVEL_CATEGORY_NAMES.get(level_name, f"📚・{_safe_name(level_name, 30).upper()}")


def _stream_role_name(level_name: str, stream_name: str) -> str:
    return f"{STREAM_ROLE_PREFIX}{get_stream_abbreviation(level_name, stream_name)}"


class BuildStats:
    def __init__(self) -> None:
        self.roles_created = 0
        self.categories_created = 0
        self.text_channels_created = 0
        self.forums_created = 0
        self.voice_channels_created = 0
        self.levels_processed = 0
        self.streams_processed = 0


class ServerBuilder:
    """Build a compact hierarchy grouped by level and visually grouped by stream."""

    def __init__(self, guild: discord.Guild) -> None:
        self.guild = guild
        self.stats = BuildStats()

    async def build(self, selected: dict) -> BuildStats:
        roles = await self._ensure_main_roles()
        await self._ensure_general_area(roles)
        await self._ensure_professor_area(roles)
        voice_category = await self._get_or_create_category(CATEGORY_VOICE)
        for level in selected.get("levels", []):
            await self._build_level(level, roles, voice_category)
        return self.stats

    async def _ensure_main_roles(self) -> dict[str, discord.Role]:
        roles: dict[str, discord.Role] = {}
        for role_name, colour in ((ROLE_ADMIN, discord.Colour.red()), (ROLE_PROFESSOR, discord.Colour.blue()), (ROLE_PROFESSOR_FEMALE, discord.Colour.blue()), (ROLE_STUDENT, discord.Colour.green())):
            role = discord.utils.get(self.guild.roles, name=role_name)
            if role is None:
                permissions = discord.Permissions.none()
                if role_name == ROLE_ADMIN:
                    permissions.administrator = True
                elif role_name in (ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE):
                    for permission in ("view_channel", "read_message_history", "connect", "speak", "stream", "use_application_commands"):
                        setattr(permissions, permission, True)
                else:
                    for permission in ("view_channel", "send_messages", "read_message_history", "create_public_threads", "send_messages_in_threads", "connect", "speak", "use_application_commands"):
                        setattr(permissions, permission, True)
                role = await self.guild.create_role(name=role_name, permissions=permissions, colour=colour, hoist=True, mentionable=True, reason="School manager main role")
                self.stats.roles_created += 1
            roles[role_name] = role
        return roles

    async def _ensure_stream_role(self, level_name: str, stream_name: str) -> discord.Role:
        role_name = _stream_role_name(level_name, stream_name)
        role = discord.utils.get(self.guild.roles, name=role_name)
        if role is None:
            role = await self.guild.create_role(name=role_name, permissions=discord.Permissions.none(), colour=discord.Colour.teal(), mentionable=True, reason="School manager stream role")
            self.stats.roles_created += 1
        return role

    async def _get_or_create_category(self, name, overwrites=None):
        category = discord.utils.find(lambda item: isinstance(item, discord.CategoryChannel) and item.name == name, self.guild.channels)
        if category:
            if overwrites is not None:
                await category.edit(overwrites=overwrites, reason="School manager reconciliation")
            return category
        kwargs = {"name": name, "reason": "School manager automatic setup"}
        if overwrites is not None:
            kwargs["overwrites"] = overwrites
        category = await self.guild.create_category(**kwargs)
        self.stats.categories_created += 1
        return category

    async def _get_or_create_text(self, category, name, *, topic, overwrites, preserve_member_overwrites=False):
        channel = discord.utils.find(lambda item: isinstance(item, discord.TextChannel) and item.name == name, category.channels)
        if channel:
            effective = dict(overwrites)
            if preserve_member_overwrites:
                for target, permission in channel.overwrites.items():
                    if isinstance(target, discord.Member):
                        effective[target] = permission
            await channel.edit(topic=topic, overwrites=effective, reason="School manager reconciliation")
            return channel
        channel = await category.create_text_channel(name=name, topic=topic, overwrites=overwrites, reason="School manager automatic setup")
        self.stats.text_channels_created += 1
        return channel

    async def _get_or_create_voice(self, category, name, overwrites):
        channel = discord.utils.find(lambda item: isinstance(item, discord.VoiceChannel) and item.name == name, category.channels)
        if channel:
            await channel.edit(overwrites=overwrites, reason="School manager reconciliation")
            return channel
        channel = await category.create_voice_channel(name=name, overwrites=overwrites, reason="School manager shared remote class")
        self.stats.voice_channels_created += 1
        return channel

    async def _ensure_general_area(self, roles):
        overwrites = general_area_overwrites(self.guild.default_role, roles[ROLE_ADMIN], roles[ROLE_PROFESSOR], roles[ROLE_PROFESSOR_FEMALE], roles[ROLE_STUDENT])
        category = await self._get_or_create_category(CATEGORY_GENERAL, overwrites)
        topics = {"actualites": "Annonces et informations officielles de l'établissement.", "absences": "Informations concernant les absences des professeurs.", "results": "Résultats et communications scolaires importantes.", "post_bac": "Orientation, études supérieures, bourses et opportunités post-bac.", "contests": "Concours, clubs, activités et événements scolaires."}
        for key, name in GENERAL_CHANNELS.items():
            await self._get_or_create_text(category, name, topic=topics[key], overwrites=overwrites)

    async def _ensure_professor_area(self, roles):
        overwrites = teacher_area_overwrites(self.guild.default_role, roles[ROLE_ADMIN], roles[ROLE_PROFESSOR], roles[ROLE_PROFESSOR_FEMALE], roles[ROLE_STUDENT])
        category = await self._get_or_create_category(CATEGORY_PROFESSORS, overwrites)
        await self._get_or_create_text(category, PROFESSOR_CHANNELS["discussion"], topic="Espace privé de discussion et de coordination des professeurs.", overwrites=overwrites)
        await self._get_or_create_voice(category, PROFESSOR_CHANNELS["meeting"], overwrites)

    async def _build_level(self, level, roles, voice_category):
        level_name = level["name"]
        level_category = await self._get_or_create_category(_level_category_name(level_name))
        for stream in level.get("streams", []):
            stream_name = stream["name"]
            stream_code = stream.get("abbreviation") or get_stream_abbreviation(level_name, stream_name)
            subjects = list(stream.get("subjects", [])) or get_stream_subjects(level_name, stream_name)
            stream_role = await self._ensure_stream_role(level_name, stream_name)
            area = stream_area_overwrites(self.guild.default_role, roles[ROLE_ADMIN], roles[ROLE_PROFESSOR], roles[ROLE_PROFESSOR_FEMALE], roles[ROLE_STUDENT], stream_role)
            announcements = stream_announcement_overwrites(self.guild.default_role, roles[ROLE_ADMIN], roles[ROLE_PROFESSOR], roles[ROLE_PROFESSOR_FEMALE], roles[ROLE_STUDENT], stream_role)
            await self._get_or_create_text(level_category, _stream_header_name(stream_name, stream_code), topic=f"Filière {stream_code} — {stream_name}.", overwrites=announcements)
            await self._get_or_create_text(level_category, f"📌-{stream_code}・informations", topic=f"Informations et organisation de {stream_name} — {level_name}.", overwrites=announcements)
            await self._get_or_create_text(level_category, f"🗓️-{stream_code}・emploi-du-temps", topic=f"Emplois du temps de {stream_name} ({level_name}).", overwrites=announcements)
            await self._get_or_create_text(level_category, f"📝-{stream_code}・examens", topic=f"Dates, horaires et consignes des examens pour {stream_name} — {level_name}.", overwrites=announcements)
            for subject in subjects:
                await self._get_or_create_text(
                    level_category,
                    _subject_channel_name(stream_code, subject),
                    topic=f"Cours, devoirs, exercices, examens blancs et ressources de {get_subject_display_name(subject)} pour {stream_name} ({level_name}). Seuls les enseignants affectés peuvent publier.",
                    overwrites=subject_channel_overwrites(self.guild.default_role, roles[ROLE_ADMIN], roles[ROLE_PROFESSOR], roles[ROLE_PROFESSOR_FEMALE], stream_role),
                    preserve_member_overwrites=True,
                )
            await self._get_or_create_voice(voice_category, f"🔊-{_safe_name(stream_code, 30)}-à-distance", public_voice_overwrites(self.guild.default_role, roles[ROLE_ADMIN], roles[ROLE_PROFESSOR], roles[ROLE_PROFESSOR_FEMALE], roles[ROLE_STUDENT], stream_role))
            self.stats.streams_processed += 1
        self.stats.levels_processed += 1

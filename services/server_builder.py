"""Build the school Discord server around a compact level/stream hierarchy."""

from __future__ import annotations

import re

import discord

from config.curriculum import (
    GENERAL_CHANNELS,
    PROFESSOR_CHANNELS,
    get_stream_abbreviation,
    get_stream_subjects,
    get_subject_display_name,
    get_subject_internal_code,
)
from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_PROFESSOR_FEMALE,
    ROLE_STUDENT,
    STREAM_ROLE_PREFIX,
    SUBJECT_ROLE_PREFIX,
    general_area_overwrites,
    public_voice_overwrites,
    stream_announcement_overwrites,
    subject_channel_overwrites,
    teacher_area_overwrites,
)

CATEGORY_GENERAL = "🏢・INFORMATIONS & ADMINISTRATION"
CATEGORY_PROFESSORS = "👨‍🏫・ESPACE PROFESSEURS"
CATEGORY_VOICE = "🔊・SALLES VIRTUELLES"

LEVEL_CATEGORY_NAMES = {
    "Tronc Commun": "📘・TRONC COMMUN",
    "1ère Année Bac": "1️⃣・1BAC",
    "2ème Année Bac": "2️⃣・2BAC",
}

STREAM_EMOJIS = {
    "Tronc Commun Scientifique": "🔬",
    "Tronc Commun Lettres": "📩",
    "Tronc Commun Technologique": "⚙️",
    "1ère Année Bac Sciences Expérimentales": "🧪",
    "1ère Année Bac Sciences Mathématiques": "📐",
    "1ère Année Bac Lettres et Sciences Humaines": "📩",
    "1ère Année Bac Sciences Économiques et Gestion": "💼",
    "1ère Année Bac Sciences et Technologies Électriques": "⚡",
    "1ère Année Bac Sciences et Technologies Mécaniques": "🔧",
    "2ème Année Bac Sciences Physiques": "⚛️",
    "2ème Année Bac Sciences de la Vie et de la Terre": "🌱",
    "2ème Année Bac Sciences Mathématiques A": "📐",
    "2ème Année Bac Sciences Mathématiques B": "📐",
    "2ème Année Bac Lettres": "✉️",
    "2ème Année Bac Sciences Humaines": "🧠",
    "2ème Année Bac Sciences Économiques": "💼",
    "2ème Année Bac Sciences de Gestion Comptable": "📊",
}


def _safe_name(value: str, max_length: int = 80) -> str:
    value = value.lower().replace("’", "'").replace(" ", "-")
    value = re.sub(r"[^\w\-àâçéèêëîïôûùüÿñæœ']+", "-", value, flags=re.UNICODE)
    return re.sub(r"-+", "-", value).strip("-")[:max_length]


def _subject_channel_name(stream_code: str, subject: str) -> str:
    return f"📚-{stream_code}・{_safe_name(get_subject_display_name(subject), 55)}"


def _stream_header_name(stream_name: str, stream_code: str) -> str:
    """Legacy helper for identifying headers created by older versions."""
    return f"🔹・{STREAM_EMOJIS.get(stream_name, '🎓')}・{stream_code}"


def _level_category_name(level_name: str) -> str:
    return LEVEL_CATEGORY_NAMES.get(level_name, f"📚・{_safe_name(level_name, 30).upper()}")


def _stream_role_name(level_name: str, stream_name: str) -> str:
    return f"{STREAM_ROLE_PREFIX}{get_stream_abbreviation(level_name, stream_name)}"


def _subject_role_name(level_name: str, stream_name: str, subject: str) -> str:
    stream_code = get_stream_abbreviation(level_name, stream_name)
    subject_code = get_subject_internal_code(subject)
    # Compact, scoped role: the stream is part of the role so Math in 1BACSE
    # cannot grant write access to Math in another stream.
    return f"{SUBJECT_ROLE_PREFIX}{stream_code} - {subject_code}"[:100]


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
    """Build one category per level and group streams visually by compact channel prefixes."""

    def __init__(self, guild: discord.Guild) -> None:
        self.guild = guild
        self.stats = BuildStats()

    @staticmethod
    def _planned_channel_names(level: dict) -> set[str]:
        """Plan channels that physically belong to a level category.

        There is intentionally no separate stream-header channel: Discord counts every
        channel toward the category limit. The first text channel of each stream is its
        `informations` channel and carries the stream emoji/code, acting as the visual divider.
        """
        names: set[str] = set()
        for stream in level.get("streams", []):
            code = stream.get("abbreviation") or ""
            names.update({
                f"📌{STREAM_EMOJIS.get(stream['name'], '🎓')}・{code}・informations",
                f"🗓️-{code}・emploi-du-temps",
                f"📝-{code}・examens",
            })
            names.update(_subject_channel_name(code, subject) for subject in stream.get("subjects", []))
        return names

    def _validate_capacity(self, selected: dict) -> None:
        total_new_channels = 0
        for level in selected.get("levels", []):
            category_name = _level_category_name(level["name"])
            category = discord.utils.find(
                lambda item: isinstance(item, discord.CategoryChannel) and item.name == category_name,
                self.guild.categories,
            )
            existing_names = {channel.name for channel in category.channels} if category else set()
            planned_names = self._planned_channel_names(level)
            projected = (len(category.channels) if category else 0) + len(planned_names - existing_names)
            if projected > 50:
                raise ValueError(
                    f"La catégorie `{category_name}` dépasserait la limite Discord de 50 salons ({projected}). "
                    "Réduis le nombre de filières sélectionnées dans ce niveau ou utilise moins de canaux par filière."
                )
            total_new_channels += len(planned_names - existing_names)

        current_total = len(self.guild.channels)
        if current_total + total_new_channels > 500:
            raise ValueError(
                f"La construction dépasserait la limite Discord de 500 salons ({current_total + total_new_channels}). "
                "Réduis la configuration de l'établissement."
            )

    async def build(self, selected: dict) -> BuildStats:
        self._validate_capacity(selected)
        roles = await self._ensure_main_roles()
        await self._ensure_general_area(roles)
        await self._ensure_professor_area(roles)
        voice_category = await self._get_or_create_category(CATEGORY_VOICE)
        for level in selected.get("levels", []):
            await self._build_level(level, roles, voice_category)
        return self.stats

    async def _ensure_main_roles(self) -> dict[str, discord.Role]:
        roles: dict[str, discord.Role] = {}
        for role_name, colour in (
            (ROLE_ADMIN, discord.Colour.red()),
            (ROLE_PROFESSOR, discord.Colour.blue()),
            (ROLE_PROFESSOR_FEMALE, discord.Colour.blue()),
            (ROLE_STUDENT, discord.Colour.green()),
        ):
            role = discord.utils.get(self.guild.roles, name=role_name)
            if role is None:
                role = await self.guild.create_role(
                    name=role_name,
                    permissions=discord.Permissions.none(),
                    colour=colour,
                    hoist=True,
                    mentionable=True,
                    reason="School manager main role",
                )
                self.stats.roles_created += 1

            if role_name == ROLE_ADMIN:
                perms = discord.Permissions.none()
                for permission in (
                    "view_channel", "send_messages", "read_message_history",
                    "manage_channels", "manage_permissions", "manage_roles",
                    "manage_messages", "manage_threads", "connect", "speak", "stream",
                    "use_application_commands",
                ):
                    setattr(perms, permission, True)
                await role.edit(permissions=perms, reason="School manager least-privilege administration role")
            elif role_name in (ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE):
                perms = discord.Permissions.none()
                for permission in (
                    "view_channel", "read_message_history", "connect", "speak", "stream",
                    "use_application_commands",
                ):
                    setattr(perms, permission, True)
                await role.edit(permissions=perms, reason="School manager teacher base role")
            else:
                perms = discord.Permissions.none()
                for permission in (
                    "view_channel", "send_messages", "read_message_history",
                    "create_public_threads", "send_messages_in_threads", "connect", "speak",
                    "use_application_commands",
                ):
                    setattr(perms, permission, True)
                await role.edit(permissions=perms, reason="School manager student base role")
            roles[role_name] = role
        return roles

    async def _ensure_stream_role(self, level_name: str, stream_name: str) -> discord.Role:
        role_name = _stream_role_name(level_name, stream_name)
        role = discord.utils.get(self.guild.roles, name=role_name)
        if role is None:
            role = await self.guild.create_role(
                name=role_name,
                permissions=discord.Permissions.none(),
                colour=discord.Colour.teal(),
                mentionable=True,
                reason="School manager stream role",
            )
            self.stats.roles_created += 1
        return role

    async def _ensure_subject_role(self, level_name: str, stream_name: str, subject: str) -> discord.Role:
        role_name = _subject_role_name(level_name, stream_name, subject)
        role = discord.utils.get(self.guild.roles, name=role_name)
        if role is None:
            role = await self.guild.create_role(
                name=role_name,
                permissions=discord.Permissions.none(),
                colour=discord.Colour.dark_blue(),
                mentionable=False,
                reason="School manager stream-subject teacher role",
            )
            self.stats.roles_created += 1
        return role

    async def _get_or_create_category(self, name: str, overwrites=None):
        category = discord.utils.find(
            lambda item: isinstance(item, discord.CategoryChannel) and item.name == name,
            self.guild.channels,
        )
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

    async def _get_or_create_text(self, category, name: str, *, topic: str, overwrites):
        channel = discord.utils.find(
            lambda item: isinstance(item, discord.TextChannel) and item.name == name,
            category.channels,
        )
        if channel:
            await channel.edit(topic=topic, overwrites=overwrites, reason="School manager reconciliation")
            return channel
        channel = await category.create_text_channel(
            name=name,
            topic=topic,
            overwrites=overwrites,
            reason="School manager automatic setup",
        )
        self.stats.text_channels_created += 1
        return channel

    async def _get_or_create_voice(self, category, name: str, overwrites):
        channel = discord.utils.find(
            lambda item: isinstance(item, discord.VoiceChannel) and item.name == name,
            category.channels,
        )
        if channel:
            await channel.edit(overwrites=overwrites, reason="School manager reconciliation")
            return channel
        channel = await category.create_voice_channel(
            name=name,
            overwrites=overwrites,
            reason="School manager automatic setup",
        )
        self.stats.voice_channels_created += 1
        return channel

    async def _ensure_general_area(self, roles):
        overwrites = general_area_overwrites(
            self.guild.default_role, roles[ROLE_ADMIN], roles[ROLE_PROFESSOR],
            roles[ROLE_PROFESSOR_FEMALE], roles[ROLE_STUDENT]
        )
        category = await self._get_or_create_category(CATEGORY_GENERAL, overwrites)
        topics = {
            "actualites": "Annonces et informations officielles de l'établissement.",
            "absences": "Informations concernant les absences des professeurs.",
            "results": "Résultats et communications scolaires importantes.",
            "post_bac": "Orientation, études supérieures, bourses et opportunités post-bac.",
            "contests": "Concours, clubs, activités et événements scolaires.",
        }
        for key, name in GENERAL_CHANNELS.items():
            await self._get_or_create_text(category, name, topic=topics[key], overwrites=overwrites)

    async def _ensure_professor_area(self, roles):
        overwrites = teacher_area_overwrites(
            self.guild.default_role, roles[ROLE_ADMIN], roles[ROLE_PROFESSOR],
            roles[ROLE_PROFESSOR_FEMALE], roles[ROLE_STUDENT]
        )
        category = await self._get_or_create_category(CATEGORY_PROFESSORS, overwrites)
        await self._get_or_create_text(
            category,
            PROFESSOR_CHANNELS["discussion"],
            topic="Espace privé de discussion et de coordination des professeurs.",
            overwrites=overwrites,
        )
        await self._get_or_create_voice(category, PROFESSOR_CHANNELS["meeting"], overwrites)

    async def _cleanup_legacy_stream_headers(self, level_category):
        for channel in list(level_category.channels):
            if channel.name.startswith("🔹-") or channel.name.startswith("🔹・"):
                try:
                    await channel.delete(reason="School manager legacy stream header cleanup")
                except (discord.Forbidden, discord.HTTPException):
                    pass

    async def _build_level(self, level, roles, voice_category):
        level_name = level["name"]
        level_category = await self._get_or_create_category(_level_category_name(level_name))
        await self._cleanup_legacy_stream_headers(level_category)

        for stream in level.get("streams", []):
            stream_name = stream["name"]
            stream_code = stream.get("abbreviation") or get_stream_abbreviation(level_name, stream_name)
            subjects = list(stream.get("subjects", [])) or get_stream_subjects(level_name, stream_name)
            stream_role = await self._ensure_stream_role(level_name, stream_name)
            announcements = stream_announcement_overwrites(
                self.guild.default_role,
                roles[ROLE_ADMIN],
                roles[ROLE_PROFESSOR],
                roles[ROLE_PROFESSOR_FEMALE],
                roles[ROLE_STUDENT],
                stream_role,
            )

            stream_emoji = STREAM_EMOJIS.get(stream_name, "🎓")
            await self._get_or_create_text(
                level_category,
                f"📌{stream_emoji}・{stream_code}・informations",
                topic=f"🔹 {stream_code} — {stream_name}. Informations générales et organisation de la filière.",
                overwrites=announcements,
            )
            await self._get_or_create_text(
                level_category,
                f"🗓️-{stream_code}・emploi-du-temps",
                topic=f"Emplois du temps de {stream_name} ({level_name}).",
                overwrites=announcements,
            )
            await self._get_or_create_text(
                level_category,
                f"📝-{stream_code}・examens",
                topic=f"Dates, horaires et consignes des examens pour {stream_name} — {level_name}.",
                overwrites=announcements,
            )

            for subject in subjects:
                subject_role = await self._ensure_subject_role(level_name, stream_name, subject)
                await self._get_or_create_text(
                    level_category,
                    _subject_channel_name(stream_code, subject),
                    topic=(
                        f"Cours, devoirs, exercices, examens blancs et ressources de {get_subject_display_name(subject)} "
                        f"pour {stream_name} ({level_name}). Les enseignants doivent posséder le rôle matière correspondant pour publier."
                    ),
                    overwrites=subject_channel_overwrites(
                        self.guild.default_role,
                        roles[ROLE_ADMIN],
                        roles[ROLE_PROFESSOR],
                        roles[ROLE_PROFESSOR_FEMALE],
                        stream_role,
                        subject_role,
                    ),
                )

            await self._get_or_create_voice(
                voice_category,
                f"🔊-{_safe_name(stream_code, 30)}-à-distance",
                public_voice_overwrites(
                    self.guild.default_role,
                    roles[ROLE_ADMIN],
                    roles[ROLE_PROFESSOR],
                    roles[ROLE_PROFESSOR_FEMALE],
                    roles[ROLE_STUDENT],
                    stream_role,
                ),
            )
            self.stats.streams_processed += 1

        self.stats.levels_processed += 1

"""Build the school Discord server around shared academic streams."""

from __future__ import annotations

import re

import discord

from config.curriculum import (
    GENERAL_CHANNELS,
    PROFESSOR_CHANNELS,
    get_stream_subjects,
)
from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_STUDENT,
    general_area_overwrites,
    public_voice_overwrites,
    teacher_area_overwrites,
    level_area_overwrites,
    level_announcement_overwrites,
)

CATEGORY_GENERAL = "🏢・INFORMATIONS & ADMINISTRATION"
CATEGORY_PROFESSORS = "👨‍🏫・ESPACE PROFESSEURS"
CATEGORY_VOICE = "🔊・SALLES VIRTUELLES"


def _safe_name(value: str, max_length: int = 90) -> str:
    value = value.lower().replace("’", "'").replace(" ", "-")
    value = re.sub(r"[^\w\-àâçéèêëîïôûùüÿñæœ']+", "-", value, flags=re.UNICODE)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:max_length]


def _subject_channel_name(subject: str) -> str:
    return f"📚-{_safe_name(subject)}"


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
    """Create one shared academic category per selected stream and one channel per subject."""

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
        for role_name, colour in (
            (ROLE_ADMIN, discord.Colour.red()),
            (ROLE_PROFESSOR, discord.Colour.blue()),
            (ROLE_STUDENT, discord.Colour.green()),
        ):
            role = discord.utils.get(self.guild.roles, name=role_name)
            if role is None:
                permissions = discord.Permissions.none()
                if role_name == ROLE_ADMIN:
                    permissions.administrator = True
                elif role_name == ROLE_PROFESSOR:
                    for permission in (
                        "view_channel", "send_messages", "read_message_history", "manage_messages",
                        "manage_threads", "create_public_threads", "create_private_threads",
                        "send_messages_in_threads", "connect", "speak", "stream", "use_application_commands",
                    ):
                        setattr(permissions, permission, True)
                else:
                    for permission in (
                        "view_channel", "send_messages", "read_message_history", "create_public_threads",
                        "send_messages_in_threads", "connect", "speak", "use_application_commands",
                    ):
                        setattr(permissions, permission, True)
                role = await self.guild.create_role(
                    name=role_name,
                    permissions=permissions,
                    colour=colour,
                    hoist=True,
                    mentionable=True,
                    reason="School manager main role",
                )
                self.stats.roles_created += 1
            roles[role_name] = role
        return roles

    async def _get_or_create_category(self, name, overwrites=None):
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

    async def _get_or_create_text(self, category, name, *, topic, overwrites):
        channel = discord.utils.find(
            lambda item: isinstance(item, discord.TextChannel) and item.name == name,
            category.channels,
        )
        if channel:
            await channel.edit(topic=topic, overwrites=overwrites, reason="School manager reconciliation")
            return channel
        channel = await category.create_text_channel(
            name=name, topic=topic, overwrites=overwrites, reason="School manager automatic setup"
        )
        self.stats.text_channels_created += 1
        return channel

    async def _get_or_create_voice(self, category, name, overwrites):
        channel = discord.utils.find(
            lambda item: isinstance(item, discord.VoiceChannel) and item.name == name,
            category.channels,
        )
        if channel:
            await channel.edit(overwrites=overwrites, reason="School manager reconciliation")
            return channel
        channel = await category.create_voice_channel(
            name=name, overwrites=overwrites, reason="School manager shared remote class"
        )
        self.stats.voice_channels_created += 1
        return channel

    async def _ensure_general_area(self, roles):
        overwrites = general_area_overwrites(
            self.guild.default_role, roles[ROLE_ADMIN], roles[ROLE_PROFESSOR], roles[ROLE_STUDENT]
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
            self.guild.default_role, roles[ROLE_ADMIN], roles[ROLE_PROFESSOR], roles[ROLE_STUDENT]
        )
        category = await self._get_or_create_category(CATEGORY_PROFESSORS, overwrites)
        await self._get_or_create_text(
            category,
            PROFESSOR_CHANNELS["discussion"],
            topic="Espace privé de discussion et de coordination des professeurs.",
            overwrites=overwrites,
        )
        await self._get_or_create_voice(category, PROFESSOR_CHANNELS["meeting"], overwrites)

    async def _build_level(self, level, roles, voice_category):
        level_name = level["name"]
        streams = level.get("streams", [])
        level_overwrites = level_area_overwrites(
            self.guild.default_role, roles[ROLE_ADMIN], roles[ROLE_PROFESSOR], [roles[ROLE_STUDENT]]
        )
        announcement_overwrites = level_announcement_overwrites(
            self.guild.default_role, roles[ROLE_ADMIN], roles[ROLE_PROFESSOR], [roles[ROLE_STUDENT]]
        )

        for stream in streams:
            stream_name = stream["name"]
            subjects = list(stream.get("subjects", [])) or get_stream_subjects(level_name, stream_name)
            category = await self._get_or_create_category(f"🎓・{stream_name}", level_overwrites)

            await self._get_or_create_text(
                category,
                "📌-informations",
                topic=f"Informations et organisation de la filière {stream_name}.",
                overwrites=announcement_overwrites,
            )
            await self._get_or_create_text(
                category,
                "🗓️-emploi-du-temps",
                topic=f"Emplois du temps de tous les groupes/classes de {stream_name}.",
                overwrites=announcement_overwrites,
            )
            await self._get_or_create_text(
                category,
                "📝-examens",
                topic=f"Dates, horaires et consignes des examens pour {stream_name}.",
                overwrites=announcement_overwrites,
            )

            for subject in subjects:
                await self._get_or_create_text(
                    category,
                    _subject_channel_name(subject),
                    topic=(
                        f"Cours, devoirs, exercices, examens blancs et ressources de {subject} "
                        f"pour toute la filière {stream_name}."
                    ),
                    overwrites=level_overwrites,
                )

            await self._get_or_create_voice(
                voice_category,
                f"🔊-{_safe_name(stream_name)}-à-distance",
                public_voice_overwrites(
                    self.guild.default_role,
                    roles[ROLE_ADMIN],
                    roles[ROLE_PROFESSOR],
                    [roles[ROLE_STUDENT]],
                ),
            )
            self.stats.streams_processed += 1

        self.stats.levels_processed += 1

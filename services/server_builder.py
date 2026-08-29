"""Build a compact, role-driven Discord school structure."""

from __future__ import annotations

import asyncio
import re

import discord

from config.curriculum import (
    EXAM_CHANNELS,
    FORUM_GUIDE_TAGS,
    FORUM_MAX_TAGS,
    GENERAL_CHANNELS,
    PROFESSOR_CHANNELS,
    get_level_subjects,
    get_stream_class_names,
    get_stream_subjects,
)
from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_STUDENT,
    administrator_overwrite,
    general_area_overwrites,
    level_announcement_overwrites,
    level_area_overwrites,
    public_voice_overwrites,
    teacher_area_overwrites,
)

CATEGORY_GENERAL = "🏢・INFORMATIONS & ADMINISTRATION"
CATEGORY_PROFESSORS = "👨‍🏫・ESPACE PROFESSEURS"
CATEGORY_VOICE = "🔊・SALLES VIRTUELLES"

LEVEL_MARKERS = {
    "Tronc Commun": "📚・TRONC COMMUN",
    "1ère Année Bac": "📚・1ÈRE ANNÉE BAC",
    "2ème Année Bac": "📚・2ÈME ANNÉE BAC",
}


def _safe_name(value: str, max_length: int = 90) -> str:
    """Turn a display name into a readable Discord channel name."""
    value = value.lower().replace("’", "'").replace(" ", "-")
    value = re.sub(r"[^\w\-àâçéèêëîïôûùüÿñæœ']+", "-", value, flags=re.UNICODE)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:max_length]


class BuildStats:
    def __init__(self) -> None:
        self.roles_created = 0
        self.categories_created = 0
        self.text_channels_created = 0
        self.forums_created = 0
        self.voice_channels_created = 0
        self.classes_processed = 0
        self.levels_processed = 0


class ServerBuilder:
    """Create one compact content area per level; never create a channel per subject/class."""

    def __init__(self, guild: discord.Guild) -> None:
        self.guild = guild
        self.stats = BuildStats()

    async def build(self, selected: dict) -> BuildStats:
        roles = await self._ensure_main_roles()
        await self._ensure_general_area(roles)
        await self._ensure_professor_area(roles)

        for level in selected.get("levels", []):
            await self._build_level(level, roles)

        await self._cleanup_legacy_subject_channels()
        return self.stats

    async def _ensure_main_roles(self) -> dict[str, discord.Role]:
        roles: dict[str, discord.Role] = {}

        definitions = (
            (ROLE_ADMIN, discord.Colour.red(), True, administrator_overwrite()),
            (ROLE_PROFESSOR, discord.Colour.blue(), True, None),
            (ROLE_STUDENT, discord.Colour.green(), True, None),
        )

        for role_name, colour, hoist, _ in definitions:
            role = discord.utils.get(self.guild.roles, name=role_name)
            if role is None:
                permissions = discord.Permissions.none()
                if role_name == ROLE_ADMIN:
                    permissions.administrator = True
                elif role_name == ROLE_PROFESSOR:
                    for permission in (
                        "view_channel", "send_messages", "read_message_history",
                        "manage_messages", "manage_threads", "create_public_threads",
                        "create_private_threads", "send_messages_in_threads",
                        "connect", "speak", "stream", "use_application_commands",
                    ):
                        setattr(permissions, permission, True)
                else:
                    for permission in (
                        "view_channel", "send_messages", "read_message_history",
                        "create_public_threads", "send_messages_in_threads",
                        "connect", "speak", "use_application_commands",
                    ):
                        setattr(permissions, permission, True)

                role = await self.guild.create_role(
                    name=role_name,
                    permissions=permissions,
                    colour=colour,
                    hoist=hoist,
                    mentionable=True,
                    reason="School manager main role",
                )
                self.stats.roles_created += 1
                await asyncio.sleep(0.15)

            roles[role_name] = role

        return roles

    async def _get_or_create_category(
        self,
        name: str,
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] | None = None,
    ) -> discord.CategoryChannel:
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
        await asyncio.sleep(0.15)
        return category

    async def _get_or_create_text(
        self,
        category: discord.CategoryChannel,
        name: str,
        *,
        topic: str,
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite],
    ) -> discord.TextChannel:
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
        await asyncio.sleep(0.15)
        return channel

    async def _get_or_create_voice(
        self,
        category: discord.CategoryChannel,
        name: str,
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite],
    ) -> discord.VoiceChannel:
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
            reason="School manager virtual classroom",
        )
        self.stats.voice_channels_created += 1
        await asyncio.sleep(0.15)
        return channel

    async def _get_or_create_forum(
        self,
        category: discord.CategoryChannel,
        name: str,
        topic: str,
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite],
        subject_tags: list[str],
    ) -> discord.ForumChannel:
        channel = discord.utils.find(
            lambda item: isinstance(item, discord.ForumChannel) and item.name == name,
            category.channels,
        )

        tag_names: list[str] = []
        for tag in FORUM_GUIDE_TAGS:
            if tag not in tag_names:
                tag_names.append(tag)
        for tag in subject_tags:
            if tag not in tag_names and len(tag_names) < FORUM_MAX_TAGS:
                tag_names.append(tag)

        forum_tags = [discord.ForumTag(name=tag) for tag in tag_names[:FORUM_MAX_TAGS]]

        if channel:
            await channel.edit(
                topic=topic,
                overwrites=overwrites,
                available_tags=forum_tags,
                reason="School manager forum reconciliation",
            )
            return channel

        channel = await category.create_forum(
            name=name,
            topic=topic,
            overwrites=overwrites,
            available_tags=forum_tags,
            default_layout=discord.ForumLayoutType.list_view,
            reason="School manager compact academic forum",
        )
        self.stats.forums_created += 1
        await asyncio.sleep(0.2)
        return channel

    async def _ensure_general_area(self, roles: dict[str, discord.Role]) -> None:
        overwrites = general_area_overwrites(
            self.guild.default_role,
            roles[ROLE_ADMIN],
            roles[ROLE_PROFESSOR],
            roles[ROLE_STUDENT],
        )
        category = await self._get_or_create_category(CATEGORY_GENERAL, overwrites)
        topics = {
            "actualites": "Actualités et informations officielles de l'établissement.",
            "absences": "Informations administratives liées aux absences des professeurs.",
            "results": "Résultats, annonces scolaires et communications importantes.",
            "post_bac": "Orientation, études supérieures, bourses et opportunités post-bac.",
            "contests": "Concours, clubs, activités et événements scolaires.",
        }
        for key, name in GENERAL_CHANNELS.items():
            await self._get_or_create_text(
                category,
                name,
                topic=topics[key],
                overwrites=overwrites,
            )

    async def _ensure_professor_area(self, roles: dict[str, discord.Role]) -> None:
        overwrites = teacher_area_overwrites(
            self.guild.default_role,
            roles[ROLE_ADMIN],
            roles[ROLE_PROFESSOR],
            roles[ROLE_STUDENT],
        )
        category = await self._get_or_create_category(CATEGORY_PROFESSORS, overwrites)
        await self._get_or_create_text(
            category,
            PROFESSOR_CHANNELS["discussion"],
            topic="Espace privé de discussion et de coordination des professeurs.",
            overwrites=overwrites,
        )
        await self._get_or_create_voice(
            category,
            PROFESSOR_CHANNELS["meeting"],
            overwrites,
        )

    async def _build_level(self, level: dict, roles: dict[str, discord.Role]) -> None:
        level_name = level["name"]
        abbreviation = level["abbreviation"]
        stream_data = level.get("streams", [])

        class_roles: list[discord.Role] = []
        for stream in stream_data:
            stream_name = stream["name"]
            class_count = int(stream.get("class_count", len(stream.get("classes", [])) or 1))
            configured_names = list(stream.get("classes", []))
            subjects = list(stream.get("subjects", []))
            if not subjects:
                subjects = get_stream_subjects(level_name, stream_name)
            if not configured_names:
                configured_names = get_stream_class_names(level_name, stream_name)

            for index in range(class_count):
                class_name = configured_names[index] if index < len(configured_names) else f"Classe {index + 1}"
                role_name = f"Élève - {abbreviation} - {stream_name} - {class_name}"
                role = discord.utils.get(self.guild.roles, name=role_name)
                if role is None:
                    role = await self.guild.create_role(
                        name=role_name,
                        permissions=discord.Permissions.none(),
                        colour=discord.Colour.teal(),
                        mentionable=True,
                        reason="School manager class role",
                    )
                    self.stats.roles_created += 1
                    await asyncio.sleep(0.1)
                class_roles.append(role)
                self.stats.classes_processed += 1

        overwrites = level_area_overwrites(
            self.guild.default_role,
            roles[ROLE_ADMIN],
            roles[ROLE_PROFESSOR],
            class_roles,
        )
        announcement_overwrites = level_announcement_overwrites(
            self.guild.default_role,
            roles[ROLE_ADMIN],
            roles[ROLE_PROFESSOR],
            class_roles,
        )
        category = await self._get_or_create_category(
            LEVEL_MARKERS.get(level_name, f"📚・{level_name.upper()}"),
            overwrites,
        )

        subjects = get_level_subjects(level_name)
        if not subjects:
            for stream in stream_data:
                subjects.extend(stream.get("subjects", []))
            subjects = list(dict.fromkeys(subjects))

        await self._get_or_create_text(
            category,
            "📢-annonces",
            topic=f"Annonces officielles — {level_name}.",
            overwrites=announcement_overwrites,
        )
        await self._get_or_create_text(
            category,
            "🗓️-organisation",
            topic=f"Organisation, horaires et informations pratiques — {level_name}.",
            overwrites=announcement_overwrites,
        )

        await self._get_or_create_forum(
            category,
            f"📚-cours-{_safe_name(abbreviation)}",
            f"Cours et ressources pédagogiques de {level_name}. Une publication = un sujet ou une ressource.",
            overwrites,
            subjects,
        )
        await self._get_or_create_forum(
            category,
            f"💬-questions-{_safe_name(abbreviation)}",
            f"Questions et discussions pédagogiques de {level_name}.",
            overwrites,
            subjects,
        )
        await self._get_or_create_forum(
            category,
            f"📝-devoirs-{_safe_name(abbreviation)}",
            f"Devoirs, contrôles, examens blancs et préparation des examens de {level_name}.",
            overwrites,
            subjects,
        )

        if level_name in EXAM_CHANNELS:
            await self._get_or_create_text(
                category,
                EXAM_CHANNELS[level_name],
                topic=(
                    "Préparation à l'examen régional."
                    if level_name == "1ère Année Bac"
                    else "Préparation à l'examen national."
                ),
                overwrites=announcement_overwrites,
            )

        voice_category = await self._get_or_create_category(CATEGORY_VOICE)
        await self._get_or_create_voice(
            voice_category,
            f"🔊-{_safe_name(abbreviation)}-classe",
            public_voice_overwrites(
                self.guild.default_role,
                roles[ROLE_ADMIN],
                roles[ROLE_PROFESSOR],
                class_roles,
            ),
        )
        self.stats.levels_processed += 1

    async def _cleanup_legacy_subject_channels(self) -> None:
        """Remove only channels that clearly match the previous per-subject builder."""
        legacy_keywords = {
            "Mathématiques", "Physique et Chimie", "Sciences de la Vie",
            "Sciences de l'ingénieur", "Arabe", "Français", "Anglais",
            "Histoire Géographie", "Education Islamique", "Philosophie",
            "Informatique", "Droit", "Comptabilité et Mathématiques financières",
            "Économie et Organisation Administrative des Entreprises",
            "Économie générale et Statistiques", "Informatique de gestion",
            "Sciences Végétales et Animales (SVA)",
        }
        for channel in list(self.guild.channels):
            if not isinstance(channel, discord.ForumChannel):
                continue
            if channel.name not in legacy_keywords:
                continue
            try:
                await channel.delete(reason="Replace legacy per-subject forum with compact level forums")
                await asyncio.sleep(0.1)
            except (discord.Forbidden, discord.HTTPException):
                continue

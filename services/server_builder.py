"""Build and reconcile the Discord school structure from a saved configuration."""

from __future__ import annotations

import asyncio

import discord

from config.curriculum import (
    EXAM_CHANNELS,
    FORUM_TAGS,
    GENERAL_CHANNELS,
    PROFESSOR_CHANNELS,
)
from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_STUDENT,
    class_area_overwrites,
    general_area_overwrites,
    read_only_class_overwrites,
    teacher_area_overwrites,
    virtual_classroom_overwrites,
)

CATEGORY_GENERAL = "🏢 Administration & Actualités"
CATEGORY_PROFESSORS = "🤫 Espace Professeurs"

LEVEL_MARKERS = {
    "Tronc Commun": "📚 TRONC COMMUN",
    "1ère Année Bac": "📚 PREMIÈRE ANNÉE BAC",
    "2ème Année Bac": "📚 DEUXIÈME ANNÉE BAC",
}


class BuildStats:
    """Counters returned after a successful build."""

    def __init__(self) -> None:
        self.roles_created = 0
        self.categories_created = 0
        self.text_channels_created = 0
        self.forums_created = 0
        self.voice_channels_created = 0
        self.classes_processed = 0


class ServerBuilder:
    """Create the school server structure and reconcile repeated builds safely."""

    def __init__(self, guild: discord.Guild) -> None:
        self.guild = guild
        self.stats = BuildStats()

    async def build(self, selected: dict) -> BuildStats:
        roles = await self._ensure_main_roles()
        await self._ensure_general_area(roles)
        await self._ensure_professor_area(roles)

        for level in selected["levels"]:
            await self._ensure_level_marker(level["name"])

            for stream in level["streams"]:
                for class_number in range(1, stream["class_count"] + 1):
                    await self._ensure_class(
                        level_name=level["name"],
                        abbreviation=level["abbreviation"],
                        stream_name=stream["name"],
                        class_number=class_number,
                        subjects=stream["subjects"],
                        roles=roles,
                    )
                    self.stats.classes_processed += 1
                    await asyncio.sleep(0.5)

        return self.stats

    async def _ensure_main_roles(self) -> dict[str, discord.Role]:
        roles: dict[str, discord.Role] = {}

        admin = discord.utils.get(self.guild.roles, name=ROLE_ADMIN)
        if admin is None:
            permissions = discord.Permissions.none()
            permissions.administrator = True
            admin = await self.guild.create_role(
                name=ROLE_ADMIN,
                permissions=permissions,
                colour=discord.Colour.red(),
                hoist=True,
                mentionable=True,
                reason="School manager main role",
            )
            self.stats.roles_created += 1

        professor = discord.utils.get(self.guild.roles, name=ROLE_PROFESSOR)
        if professor is None:
            permissions = discord.Permissions.none()
            for attr in (
                "view_channel",
                "send_messages",
                "read_message_history",
                "manage_messages",
                "manage_threads",
                "create_public_threads",
                "create_private_threads",
                "send_messages_in_threads",
                "connect",
                "speak",
                "stream",
                "use_application_commands",
            ):
                setattr(permissions, attr, True)

            professor = await self.guild.create_role(
                name=ROLE_PROFESSOR,
                permissions=permissions,
                colour=discord.Colour.blue(),
                hoist=True,
                mentionable=True,
                reason="School manager main role",
            )
            self.stats.roles_created += 1

        student = discord.utils.get(self.guild.roles, name=ROLE_STUDENT)
        if student is None:
            permissions = discord.Permissions.none()
            for attr in (
                "view_channel",
                "send_messages",
                "read_message_history",
                "create_public_threads",
                "send_messages_in_threads",
                "connect",
                "speak",
                "use_application_commands",
            ):
                setattr(permissions, attr, True)

            student = await self.guild.create_role(
                name=ROLE_STUDENT,
                permissions=permissions,
                colour=discord.Colour.green(),
                hoist=True,
                mentionable=True,
                reason="School manager main role",
            )
            self.stats.roles_created += 1

        roles[ROLE_ADMIN] = admin
        roles[ROLE_PROFESSOR] = professor
        roles[ROLE_STUDENT] = student
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
            if isinstance(overwrites, dict):
                await category.edit(
                    overwrites=overwrites,
                    reason="School manager permission reconciliation",
                )
            return category

        kwargs = {
            "name": name,
            "reason": "School manager automatic setup",
        }
        if isinstance(overwrites, dict):
            kwargs["overwrites"] = overwrites

        category = await self.guild.create_category(**kwargs)
        self.stats.categories_created += 1
        await asyncio.sleep(0.3)
        return category

    async def _get_or_create_text(
        self,
        category: discord.CategoryChannel,
        name: str,
        *,
        topic: str | None = None,
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] | None = None,
    ) -> discord.TextChannel:
        channel = discord.utils.find(
            lambda item: isinstance(item, discord.TextChannel) and item.name == name,
            category.channels,
        )

        if channel:
            kwargs = {}
            if topic is not None and channel.topic != topic:
                kwargs["topic"] = topic
            if isinstance(overwrites, dict):
                kwargs["overwrites"] = overwrites
            if kwargs:
                await channel.edit(
                    **kwargs,
                    reason="School manager permission reconciliation",
                )
            return channel

        kwargs = {
            "name": name,
            "reason": "School manager automatic setup",
        }
        if topic:
            kwargs["topic"] = topic
        if isinstance(overwrites, dict):
            kwargs["overwrites"] = overwrites

        channel = await category.create_text_channel(**kwargs)
        self.stats.text_channels_created += 1
        await asyncio.sleep(0.25)
        return channel

    async def _get_or_create_voice(
        self,
        category: discord.CategoryChannel,
        name: str,
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] | None = None,
    ) -> discord.VoiceChannel:
        channel = discord.utils.find(
            lambda item: isinstance(item, discord.VoiceChannel) and item.name == name,
            category.channels,
        )

        if channel:
            if isinstance(overwrites, dict):
                await channel.edit(
                    overwrites=overwrites,
                    reason="School manager permission reconciliation",
                )
            return channel

        kwargs = {
            "name": name,
            "reason": "School manager automatic setup",
        }
        if isinstance(overwrites, dict):
            kwargs["overwrites"] = overwrites

        channel = await category.create_voice_channel(**kwargs)
        self.stats.voice_channels_created += 1
        await asyncio.sleep(0.25)
        return channel

    async def _get_or_create_forum(
        self,
        category: discord.CategoryChannel,
        name: str,
        topic: str,
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite],
    ) -> discord.ForumChannel:
        channel = discord.utils.find(
            lambda item: isinstance(item, discord.ForumChannel) and item.name == name,
            category.channels,
        )

        if channel:
            await channel.edit(
                topic=topic,
                overwrites=overwrites,
                reason="School manager permission reconciliation",
            )
            return channel

        tags = [discord.ForumTag(name=tag) for tag in FORUM_TAGS]
        channel = await self.guild.create_forum(
            name=name,
            category=category,
            topic=topic,
            overwrites=overwrites,
            available_tags=tags,
            reason="School manager subject forum",
        )
        self.stats.forums_created += 1
        await asyncio.sleep(0.35)
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
            "actualites": "Actualités générales, annonces et informations officielles.",
            "absences": "Absences des professeurs, dates et durée des absences.",
            "results": "Résultats, annonces scolaires et informations officielles.",
            "post_bac": "Bourses, formations, universités, écoles et opportunités post-bac.",
            "contests": "Concours, activités, événements et opportunités importantes.",
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
            topic="Espace confidentiel réservé aux professeurs et à l'administration.",
            overwrites=overwrites,
        )

        await self._get_or_create_voice(
            category,
            PROFESSOR_CHANNELS["meeting"],
            overwrites,
        )

    async def _ensure_level_marker(self, level_name: str) -> discord.CategoryChannel:
        return await self._get_or_create_category(
            LEVEL_MARKERS.get(level_name, f"📚 {level_name}")
        )

    async def _ensure_class(
        self,
        *,
        level_name: str,
        abbreviation: str,
        stream_name: str,
        class_number: int,
        subjects: list[str],
        roles: dict[str, discord.Role],
    ) -> discord.CategoryChannel:
        class_role_name = (
            f"Élève - {abbreviation} - {stream_name} - Classe {class_number}"
        )

        class_role = discord.utils.get(
            self.guild.roles,
            name=class_role_name,
        )

        if class_role is None:
            class_role = await self.guild.create_role(
                name=class_role_name,
                permissions=discord.Permissions.none(),
                colour=discord.Colour.teal(),
                mentionable=True,
                reason="School manager class role",
            )
            self.stats.roles_created += 1

        category_name = (
            f"{abbreviation} - {stream_name} - Classe {class_number}"
        )

        overwrites = class_area_overwrites(
            self.guild.default_role,
            roles[ROLE_ADMIN],
            roles[ROLE_PROFESSOR],
            roles[ROLE_STUDENT],
            class_role,
        )

        category = await self._get_or_create_category(
            category_name,
            overwrites,
        )

        exam_overwrites = read_only_class_overwrites(
            self.guild.default_role,
            roles[ROLE_ADMIN],
            roles[ROLE_PROFESSOR],
            roles[ROLE_STUDENT],
            class_role,
        )

        if level_name in EXAM_CHANNELS:
            exam_name = EXAM_CHANNELS[level_name]
            topic = (
                "Préparation au régional" if level_name == "1ère Année Bac"
                else "Préparation au national"
            )
            await self._get_or_create_text(
                category,
                exam_name,
                topic=f"{topic} — {category_name}",
                overwrites=exam_overwrites,
            )
        else:
            await self._get_or_create_text(
                category,
                "📢-annonces-classe",
                topic=f"Annonces de classe — {category_name}",
                overwrites=exam_overwrites,
            )

        await self._get_or_create_text(
            category,
            "💬-discussion-classe",
            topic=f"Discussion générale de {category_name}.",
            overwrites=overwrites,
        )

        for subject in subjects:
            await self._get_or_create_forum(
                category,
                subject,
                f"Forum de {subject} — {category_name}",
                overwrites,
            )

        await self._get_or_create_voice(
            category,
            "🔊-classe-virtuelle",
            virtual_classroom_overwrites(
                self.guild.default_role,
                roles[ROLE_ADMIN],
                roles[ROLE_PROFESSOR],
                roles[ROLE_STUDENT],
                class_role,
            ),
        )

        return category

"""Simple, stable Discord server builder for School Manager."""

from __future__ import annotations

import asyncio
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
    STUDENT_STREAM_ROLE_PREFIX,
    SUBJECT_ROLE_PREFIX,
    general_area_overwrites,
    public_voice_overwrites,
    stream_announcement_overwrites,
    teacher_area_overwrites,
    student_view_overwrite,
)

CATEGORY_GENERAL = "🏢・INFORMATIONS & ADMINISTRATION"
CATEGORY_PROFESSORS = "👨‍🏫・ESPACE PROFESSEURS"
CATEGORY_VOICE = "🔊・SALLES VIRTUELLES"
LEVEL_PREFIXES = {
    "Tronc Commun": "📘・TC",
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

ROLE_CREATE_DELAY = 1.0
RESOURCE_CREATE_DELAY = 0.2


def _safe_name(value: str, max_length: int = 80) -> str:
    value = value.lower().replace("’", "'").replace(" ", "-")
    value = re.sub(r"[^\w\-àâçéèêëîïôûùüÿñæœ']+", "-", value, flags=re.UNICODE)
    return re.sub(r"-+", "-", value).strip("-")[:max_length]


def _subject_channel_name(stream_code: str, subject: str) -> str:
    return f"📚-{stream_code}・{_safe_name(get_subject_display_name(subject), 55)}"


def _stream_category_name(level_name: str, stream_name: str, stream_code: str | None = None) -> str:
    code = stream_code or get_stream_abbreviation(level_name, stream_name)
    prefix = LEVEL_PREFIXES.get(level_name, f"📚・{_safe_name(level_name, 24).upper()}")
    emoji = STREAM_EMOJIS.get(stream_name, "🎓")
    return f"{prefix}・{emoji} {code}"[:100]


def _stream_role_name(level_name: str, stream_name: str) -> str:
    return f"{STREAM_ROLE_PREFIX}{get_stream_abbreviation(level_name, stream_name)}"


def _student_stream_role_name(level_name: str, stream_name: str) -> str:
    return f"{STUDENT_STREAM_ROLE_PREFIX}{get_stream_abbreviation(level_name, stream_name)}"


def _subject_role_name(level_name: str, stream_name: str, subject: str) -> str:
    return f"{SUBJECT_ROLE_PREFIX}{get_stream_abbreviation(level_name, stream_name)} - {get_subject_internal_code(subject)}"[:100]


def _stream_channel_prefixes(stream_code: str) -> tuple[str, ...]:
    return (
        f"📌-{stream_code}・",
        f"📌{stream_code}・",
        f"🗓️-{stream_code}・",
        f"📝-{stream_code}・",
        f"📚-{stream_code}・",
    )


def _level_category_name(level_name: str) -> str:
    return LEVEL_PREFIXES.get(level_name, f"📚・{_safe_name(level_name, 30).upper()}")


def _subject_channel_overwrites(everyone, admin_role, professor_role, female_professor_role, teacher_stream_role, student_stream_role, student_role=None):
    teacher = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, create_public_threads=True, send_messages_in_threads=True)
    professor = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)
    student = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, create_public_threads=True, send_messages_in_threads=True)
    admin = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, manage_permissions=True, manage_messages=True, manage_threads=True)
    overwrites = {everyone: discord.PermissionOverwrite(view_channel=False), admin_role: admin, professor_role: professor, female_professor_role: professor, teacher_stream_role: teacher, student_stream_role: student}
    if student_role is not None:
        overwrites[student_role] = student_view_overwrite()
    return overwrites


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
    """Create the managed structure with as few Discord API requests as possible."""

    def __init__(self, guild: discord.Guild) -> None:
        self.guild = guild
        self.stats = BuildStats()
        self.managed: dict[str, dict[str, int]] = {"roles": {}, "categories": {}, "channels": {}}
        self._channel_snapshot: list[discord.abc.GuildChannel] = []

    @staticmethod
    def _stream_channel_count(level: dict, stream: dict) -> int:
        return 3 + len(list(stream.get("subjects", [])))

    @staticmethod
    def _planned_channel_names_for_stream(stream: dict) -> set[str]:
        code = stream.get("abbreviation") or ""
        return {f"📌-{code}・informations", f"🗓️-{code}・emploi-du-temps", f"📝-{code}・examens", *{_subject_channel_name(code, subject) for subject in stream.get("subjects", [])}}

    def _remember_role(self, role: discord.Role) -> None:
        self.managed["roles"][role.name] = role.id

    def _remember_category(self, category: discord.CategoryChannel) -> None:
        self.managed["categories"][category.name] = category.id

    def _remember_channel(self, channel: discord.abc.GuildChannel) -> None:
        self.managed["channels"][channel.name] = channel.id

    async def _refresh_channel_snapshot(self) -> None:
        """Take a fresh API snapshot so idempotency never depends on a stale cache."""
        self._channel_snapshot = list(await self.guild.fetch_channels())

    def _find_category(self, name: str) -> discord.CategoryChannel | None:
        return discord.utils.find(
            lambda channel: isinstance(channel, discord.CategoryChannel) and channel.name == name,
            self._channel_snapshot,
        )

    def _find_text(self, category: discord.CategoryChannel, name: str) -> discord.TextChannel | None:
        return discord.utils.find(
            lambda channel: isinstance(channel, discord.TextChannel) and channel.parent_id == category.id and channel.name == name,
            self._channel_snapshot,
        )

    def _find_voice(self, category: discord.CategoryChannel, name: str) -> discord.VoiceChannel | None:
        return discord.utils.find(
            lambda channel: isinstance(channel, discord.VoiceChannel) and channel.parent_id == category.id and channel.name == name,
            self._channel_snapshot,
        )

    async def _pace_after_create(self) -> None:
        await asyncio.sleep(RESOURCE_CREATE_DELAY)

    def _category_count(self) -> int:
        return sum(isinstance(channel, discord.CategoryChannel) for channel in self._channel_snapshot)

    def _validate_capacity(self, selected: dict) -> None:
        streams = [stream for level in selected.get("levels", []) for stream in level.get("streams", [])]
        for stream in streams:
            if self._stream_channel_count({}, stream) > 50:
                code = stream.get("abbreviation") or stream.get("name", "stream")
                raise ValueError(f"La filière `{code}` dépasse la limite de 50 salons dans sa catégorie.")

        fixed_category_specs = (
            (CATEGORY_GENERAL, set(GENERAL_CHANNELS.values()), set()),
            (CATEGORY_PROFESSORS, {PROFESSOR_CHANNELS["discussion"]}, {PROFESSOR_CHANNELS["meeting"]}),
            (CATEGORY_VOICE, set(), set()),
        )
        required_category_names = {name for name, _, _ in fixed_category_specs}
        required_category_names.update(
            _stream_category_name(level["name"], stream["name"], stream.get("abbreviation"))
            for level in selected.get("levels", [])
            for stream in level.get("streams", [])
        )
        missing_categories = sum(1 for name in required_category_names if self._find_category(name) is None)

        missing_channels = 0
        for category_name, expected_text, expected_voice in fixed_category_specs:
            category = self._find_category(category_name)
            if category is None:
                missing_channels += len(expected_text) + len(expected_voice)
                continue
            existing_text = {channel.name for channel in self._channel_snapshot if isinstance(channel, discord.TextChannel) and channel.parent_id == category.id}
            existing_voice = {channel.name for channel in self._channel_snapshot if isinstance(channel, discord.VoiceChannel) and channel.parent_id == category.id}
            missing_channels += len(expected_text - existing_text)
            missing_channels += len(expected_voice - existing_voice)

        for level in selected.get("levels", []):
            for stream in level.get("streams", []):
                category = self._find_category(_stream_category_name(level["name"], stream["name"], stream.get("abbreviation")))
                expected = self._planned_channel_names_for_stream(stream)
                existing = {channel.name for channel in self._channel_snapshot if isinstance(channel, discord.TextChannel) and category is not None and channel.parent_id == category.id}
                missing_channels += len(expected - existing)

        voice_category = self._find_category(CATEGORY_VOICE)
        if voice_category is None:
            missing_channels += len(streams)
        else:
            existing_voice = {channel.name for channel in self._channel_snapshot if isinstance(channel, discord.VoiceChannel) and channel.parent_id == voice_category.id}
            missing_channels += sum(
                1
                for stream in streams
                if f"🔊-{_safe_name(stream.get('abbreviation') or stream.get('name', ''), 30)}-à-distance" not in existing_voice
            )

        projected_channels = len(self._channel_snapshot) + missing_channels
        if projected_channels > 500:
            raise ValueError(f"La construction dépasserait la limite Discord de 500 salons ({projected_channels}).")
        if self._category_count() + missing_categories > 50:
            raise ValueError(f"La construction dépasserait la limite Discord de 50 catégories ({self._category_count() + missing_categories}).")

    async def build(self, selected: dict) -> BuildStats:
        print(f"[BUILD] Start guild={self.guild.id}", flush=True)
        print("[BUILD] Phase: refresh channels", flush=True)
        await self._refresh_channel_snapshot()
        print("[BUILD] Phase: capacity check", flush=True)
        self._validate_capacity(selected)
        print("[BUILD] Phase: main roles", flush=True)
        roles = await self._ensure_main_roles()
        selected["managed"] = self.managed
        selected["management_role_id"] = roles[ROLE_ADMIN].id
        print("[BUILD] Phase: general area", flush=True)
        await self._ensure_general_area(roles)
        print("[BUILD] Phase: professor area", flush=True)
        await self._ensure_professor_area(roles)
        print("[BUILD] Phase: voice area", flush=True)
        voice_category = await self._get_or_create_category(CATEGORY_VOICE)
        for level in selected.get("levels", []):
            print(f"[BUILD] Level start: {level.get('name')}", flush=True)
            await self._build_level(level, roles, voice_category)
            self.stats.levels_processed += 1
        print("[BUILD] Complete", flush=True)
        return self.stats

    async def _create_role(self, name: str, *, permissions: discord.Permissions, colour: discord.Colour, hoist: bool = False, mentionable: bool = False, reason: str) -> discord.Role:
        print(f"[BUILD] -> create role: {name}", flush=True)
        role = await self.guild.create_role(name=name, permissions=permissions, colour=colour, hoist=hoist, mentionable=mentionable, reason=reason)
        print(f"[BUILD] <- create role: {name}", flush=True)
        self.stats.roles_created += 1
        await asyncio.sleep(ROLE_CREATE_DELAY)
        return role

    async def _ensure_main_roles(self) -> dict[str, discord.Role]:
        specs = {
            ROLE_ADMIN: (discord.Colour.red(), True, True, ("view_channel", "send_messages", "read_message_history", "manage_channels", "manage_permissions", "manage_roles", "manage_messages", "manage_threads", "connect", "speak", "stream", "use_application_commands")),
            ROLE_PROFESSOR: (discord.Colour.blue(), True, True, ("view_channel", "read_message_history", "connect", "speak", "stream", "use_application_commands")),
            ROLE_PROFESSOR_FEMALE: (discord.Colour.blue(), True, True, ("view_channel", "read_message_history", "connect", "speak", "stream", "use_application_commands")),
            ROLE_STUDENT: (discord.Colour.green(), True, True, ("view_channel", "send_messages", "read_message_history", "create_public_threads", "send_messages_in_threads", "connect", "speak", "use_application_commands")),
        }
        roles: dict[str, discord.Role] = {}
        for name, (colour, hoist, mentionable, permission_names) in specs.items():
            role = discord.utils.get(self.guild.roles, name=name)
            if role is None:
                perms = discord.Permissions.none()
                for permission in permission_names:
                    setattr(perms, permission, True)
                role = await self._create_role(name, permissions=perms, colour=colour, hoist=hoist, mentionable=mentionable, reason="School manager managed role")
            else:
                print(f"[BUILD] reuse role: {name}", flush=True)
            roles[name] = role
            self._remember_role(role)
        return roles

    async def _ensure_stream_role(self, level_name: str, stream_name: str) -> discord.Role:
        name = _stream_role_name(level_name, stream_name)
        role = discord.utils.get(self.guild.roles, name=name)
        if role is None:
            role = await self._create_role(name, permissions=discord.Permissions.none(), colour=discord.Colour.teal(), mentionable=True, reason="School manager stream role")
        else:
            print(f"[BUILD] reuse role: {name}", flush=True)
        self._remember_role(role)
        return role

    async def _ensure_student_stream_role(self, level_name: str, stream_name: str) -> discord.Role:
        name = _student_stream_role_name(level_name, stream_name)
        role = discord.utils.get(self.guild.roles, name=name)
        if role is None:
            role = await self._create_role(name, permissions=discord.Permissions.none(), colour=discord.Colour.green(), mentionable=False, reason="School manager student stream role")
        else:
            print(f"[BUILD] reuse role: {name}", flush=True)
        self._remember_role(role)
        return role

    async def _get_or_create_category(self, name: str, overwrites=None) -> discord.CategoryChannel:
        category = self._find_category(name)
        if category is not None:
            self._remember_category(category)
            return category
        kwargs = {"name": name, "reason": "School manager automatic setup"}
        if overwrites is not None:
            kwargs["overwrites"] = overwrites
        category = await self.guild.create_category(**kwargs)
        self.stats.categories_created += 1
        self._remember_category(category)
        self._channel_snapshot.append(category)
        await self._pace_after_create()
        return category

    async def _get_or_create_text(self, category: discord.CategoryChannel, name: str, *, topic: str, overwrites: dict) -> discord.TextChannel:
        channel = self._find_text(category, name)
        if channel is not None:
            self._remember_channel(channel)
            return channel
        channel = await category.create_text_channel(name=name, topic=topic, overwrites=overwrites, reason="School manager automatic setup")
        self.stats.text_channels_created += 1
        self._remember_channel(channel)
        self._channel_snapshot.append(channel)
        await self._pace_after_create()
        return channel

    async def _get_or_create_voice(self, category: discord.CategoryChannel, name: str, overwrites: dict) -> discord.VoiceChannel:
        channel = self._find_voice(category, name)
        if channel is not None:
            self._remember_channel(channel)
            return channel
        channel = await category.create_voice_channel(name=name, overwrites=overwrites, reason="School manager automatic setup")
        self.stats.voice_channels_created += 1
        self._remember_channel(channel)
        self._channel_snapshot.append(channel)
        await self._pace_after_create()
        return channel

    async def _ensure_general_area(self, roles) -> None:
        overwrites = general_area_overwrites(self.guild.default_role, roles[ROLE_ADMIN], roles[ROLE_PROFESSOR], roles[ROLE_PROFESSOR_FEMALE], roles[ROLE_STUDENT])
        category = await self._get_or_create_category(CATEGORY_GENERAL, overwrites)
        topics = {"actualites": "Annonces et informations officielles de l'établissement.", "absences": "Informations concernant les absences des professeurs.", "results": "Résultats et communications scolaires importantes.", "post_bac": "Orientation, études supérieures, bourses et opportunités post-bac.", "contests": "Concours, clubs, activités et événements scolaires."}
        for key, name in GENERAL_CHANNELS.items():
            await self._get_or_create_text(category, name, topic=topics[key], overwrites=overwrites)

    async def _ensure_professor_area(self, roles) -> None:
        overwrites = teacher_area_overwrites(self.guild.default_role, roles[ROLE_ADMIN], roles[ROLE_PROFESSOR], roles[ROLE_PROFESSOR_FEMALE], roles[ROLE_STUDENT])
        category = await self._get_or_create_category(CATEGORY_PROFESSORS, overwrites)
        await self._get_or_create_text(category, PROFESSOR_CHANNELS["discussion"], topic="Espace privé de discussion et de coordination des professeurs.", overwrites=overwrites)
        await self._get_or_create_voice(category, PROFESSOR_CHANNELS["meeting"], overwrites)

    async def _build_level(self, level: dict, roles, voice_category) -> None:
        level_name = level["name"]
        for stream in level.get("streams", []):
            stream_name = stream["name"]
            code = stream.get("abbreviation") or get_stream_abbreviation(level_name, stream_name)
            subjects = list(stream.get("subjects", [])) or get_stream_subjects(level_name, stream_name)
            category = await self._get_or_create_category(_stream_category_name(level_name, stream_name, code))
            print(f"[BUILD] Stream start: {level_name}/{code} -> {category.name}", flush=True)
            teacher_stream_role = await self._ensure_stream_role(level_name, stream_name)
            student_stream_role = await self._ensure_student_stream_role(level_name, stream_name)
            announcement_overwrites = stream_announcement_overwrites(self.guild.default_role, roles[ROLE_ADMIN], roles[ROLE_PROFESSOR], roles[ROLE_PROFESSOR_FEMALE], roles[ROLE_STUDENT], teacher_stream_role, student_stream_role)
            await self._get_or_create_text(category, f"📌-{code}・informations", topic=f"{code} — {stream_name}. Informations générales et organisation de la filière.", overwrites=announcement_overwrites)
            await self._get_or_create_text(category, f"🗓️-{code}・emploi-du-temps", topic=f"Emplois du temps de {stream_name} ({level_name}).", overwrites=announcement_overwrites)
            await self._get_or_create_text(category, f"📝-{code}・examens", topic=f"Dates, horaires et consignes des examens pour {stream_name} — {level_name}.", overwrites=announcement_overwrites)
            subject_overwrites = _subject_channel_overwrites(self.guild.default_role, roles[ROLE_ADMIN], roles[ROLE_PROFESSOR], roles[ROLE_PROFESSOR_FEMALE], teacher_stream_role, student_stream_role, roles[ROLE_STUDENT])
            for subject in subjects:
                await self._get_or_create_text(category, _subject_channel_name(code, subject), topic=f"Cours, devoirs, exercices, examens blancs et ressources de {get_subject_display_name(subject)} pour {stream_name} ({level_name}).", overwrites=subject_overwrites)
            await self._get_or_create_voice(voice_category, f"🔊-{_safe_name(code, 30)}-à-distance", public_voice_overwrites(self.guild.default_role, roles[ROLE_ADMIN], roles[ROLE_PROFESSOR], roles[ROLE_PROFESSOR_FEMALE], roles[ROLE_STUDENT], teacher_stream_role, student_stream_role))
            self.stats.streams_processed += 1
            print(f"[BUILD] Stream done: {level_name}/{code}", flush=True)

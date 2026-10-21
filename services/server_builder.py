"""Simple, stable Discord server builder for School Manager."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from copy import copy

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
    student_subject_channel_overwrite,
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


class BuildPermissionError(RuntimeError):
    """A setup build cannot safely run with the bot's current permissions."""


# These permissions are needed to discover existing resources and create the
# requested Discord resources. Other role/channel permission bits are shaped
# to the bot's actual grants instead of requiring Administrator.
_REQUIRED_SETUP_PERMISSION_NAMES = (
    "view_channel",
    "manage_channels",
    "manage_roles",
)

_PERMISSION_LABELS = {
    "view_channel": "View Channel",
    "send_messages": "Send Messages",
    "read_message_history": "Read Message History",
    "manage_channels": "Manage Channels",
    "manage_roles": "Manage Roles",
    "manage_messages": "Manage Messages",
    "manage_threads": "Manage Threads",
    "connect": "Connect",
    "speak": "Speak",
    "stream": "Video",
    "use_application_commands": "Use Application Commands",
    "create_public_threads": "Create Public Threads",
    "create_private_threads": "Create Private Threads",
    "send_messages_in_threads": "Send Messages in Threads",
}


def _permission_names_for_bits(permission_bits: int) -> list[str]:
    """Map permission bits to stable, user-facing canonical names."""
    flags = getattr(discord.Permissions, "VALID_FLAGS", {})
    return sorted(
        name
        for name in _PERMISSION_LABELS
        if name in flags and (int(flags[name]) & int(permission_bits))
    )


def _required_setup_permissions() -> discord.Permissions:
    """Return the minimum effective guild permissions needed to build safely."""
    requested = discord.Permissions.none()
    for name in _REQUIRED_SETUP_PERMISSION_NAMES:
        setattr(requested, name, True)
    return requested


def _missing_requested_permissions(
    guild: discord.Guild,
    requested: discord.Permissions,
) -> list[str] | None:
    """Return requested permission bits the bot does not currently hold."""
    bot_member = getattr(guild, "me", None)
    current = getattr(bot_member, "guild_permissions", None)
    if current is None:
        return None
    if getattr(current, "administrator", False):
        return []

    missing_bits = int(requested.value) & ~int(current.value)
    return _permission_names_for_bits(missing_bits)


def _grantable_permissions(
    guild: discord.Guild,
    requested: discord.Permissions,
) -> tuple[discord.Permissions, list[str]]:
    """Keep only permission bits the bot is allowed to grant to another resource."""
    bot_member = getattr(guild, "me", None)
    current = getattr(bot_member, "guild_permissions", None)
    if current is None:
        raise BuildPermissionError(
            "Impossible d'identifier les permissions effectives du bot; aucun droit n'a été accordé."
        )
    if getattr(current, "administrator", False):
        return requested, []

    requested_bits = int(requested.value)
    current_bits = int(current.value)
    omitted_bits = requested_bits & ~current_bits
    return (
        discord.Permissions(requested_bits & current_bits),
        _permission_names_for_bits(omitted_bits),
    )


def _prepare_channel_overwrites(
    guild: discord.Guild,
    overwrites: dict | None,
) -> tuple[dict | None, list[str]]:
    """Fit allow/deny overwrites to the bot's grants without mutating the policy.

    Discord won't let a bot grant arbitrary permission bits through role/channel
    configuration when it does not possess those bits itself. Remove those bits
    consistently from role grants and channel overwrites; callers report any
    omitted capabilities explicitly rather than silently claiming full access.
    """
    if overwrites is None:
        return None, []

    bot_member = getattr(guild, "me", None)
    current = getattr(bot_member, "guild_permissions", None)
    if bot_member is None or current is None:
        raise BuildPermissionError(
            "Impossible d'identifier les permissions effectives du bot; aucun salon n'a été créé."
        )
    if not getattr(current, "view_channel", False) and not getattr(current, "administrator", False):
        raise BuildPermissionError(
            "View Channel est nécessaire pour garantir l'accès du bot aux salons privés gérés."
        )

    prepared: dict = {}
    omitted: set[str] = set()
    for target, overwrite in overwrites.items():
        if getattr(current, "administrator", False):
            prepared[target] = copy(overwrite)
            continue
        allow, deny = overwrite.pair()
        grantable_allow, removed_allow = _grantable_permissions(guild, allow)
        omitted.update(removed_allow)
        # Preserve explicit denies: dropping a deny can accidentally make a
        # restricted school channel writable or visible to a broader audience.
        prepared[target] = discord.PermissionOverwrite.from_pair(
            grantable_allow,
            deny,
        )

    # Keep only the narrow bot-member visibility grant. It is needed because
    # some managed areas intentionally hide @everyone from private channels.
    bot_overwrite = copy(
        prepared.get(bot_member, discord.PermissionOverwrite())
    )
    bot_overwrite.view_channel = True
    if getattr(current, "read_message_history", False):
        bot_overwrite.read_message_history = True
    if getattr(current, "send_messages", False):
        bot_overwrite.send_messages = True
    prepared[bot_member] = bot_overwrite
    return prepared, sorted(omitted)


def _safe_name(value: str, max_length: int = 80) -> str:
    value = value.lower().replace("’", "'").replace(" ", "-")
    value = re.sub(r"[^\w\-àâçéèêëîïôûùüÿñæœ']+", "-", value, flags=re.UNICODE)
    return re.sub(r"-+", "-", value).strip("-")[:max_length]


def _normalize_channel_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


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


def _subject_channel_overwrites(everyone, admin_role, professor_role, female_professor_role, teacher_stream_role, student_stream_role, student_role=None, bot_member=None):
    teacher = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, create_public_threads=True, send_messages_in_threads=True)
    professor = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)
    student = student_subject_channel_overwrite()
    admin = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, manage_permissions=True, manage_messages=True, manage_threads=True)
    overwrites = {everyone: discord.PermissionOverwrite(view_channel=False), admin_role: admin, professor_role: professor, female_professor_role: professor, teacher_stream_role: teacher, student_stream_role: student}
    if student_role is not None:
        overwrites[student_role] = student_view_overwrite()
    if bot_member is not None:
        # Subject channels are hidden from @everyone, so the bot needs a narrow
        # member overwrite to manage teacher threads in those managed channels.
        overwrites[bot_member] = discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            create_public_threads=True,
            send_messages_in_threads=True,
        )
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
        self.omitted_permissions: set[str] = set()


def permission_omission_notice(stats: BuildStats) -> str:
    """Format a visible notice when optional Discord capabilities were omitted."""
    names = sorted(getattr(stats, "omitted_permissions", set()))
    if not names:
        return ""
    labels = ", ".join(_PERMISSION_LABELS.get(name, name) for name in names)
    return (
        "\n\n⚠️ Permissions non accordées au bot et donc omises des rôles/salons : "
        f"**{labels}**. Le serveur a été construit sans Administrator, mais ces "
        "capacités ne sont pas attribuées. Pour les activer, ajoute uniquement les "
        "permissions granulaires correspondantes au rôle du bot."
    )


class ServerBuilder:
    """Create the managed structure with as few Discord API requests as possible."""

    def __init__(self, guild: discord.Guild) -> None:
        self.guild = guild
        self.stats = BuildStats()
        self.managed: dict[str, dict[str, int]] = {"roles": {}, "categories": {}, "channels": {}}
        self._channel_snapshot: list[discord.abc.GuildChannel] = []
        self._channel_snapshot_refreshed = False

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
        self._channel_snapshot_refreshed = True

    def _find_category(self, name: str) -> discord.CategoryChannel | None:
        normalized_name = _normalize_channel_name(name)
        category = discord.utils.find(
            lambda channel: isinstance(channel, discord.CategoryChannel) and _normalize_channel_name(channel.name) == normalized_name,
            self._channel_snapshot,
        )
        if category is not None or self._channel_snapshot_refreshed:
            return category
        return discord.utils.find(
            lambda channel: isinstance(channel, discord.CategoryChannel) and _normalize_channel_name(channel.name) == normalized_name,
            getattr(self.guild, "categories", []),
        )

    def _find_text(self, category: discord.CategoryChannel, name: str) -> discord.TextChannel | None:
        normalized_name = _normalize_channel_name(name)
        channel = discord.utils.find(
            lambda item: item is not category
            and _normalize_channel_name(getattr(item, "name", "")) == normalized_name
            and getattr(item, "category_id", None) == category.id,
            self._channel_snapshot,
        )
        if channel is not None and not isinstance(channel, discord.VoiceChannel):
            return channel
        if self._channel_snapshot_refreshed:
            return None
        return discord.utils.find(
            lambda item: _normalize_channel_name(getattr(item, "name", "")) == normalized_name,
            getattr(category, "text_channels", []),
        )

    def _find_voice(self, category: discord.CategoryChannel, name: str) -> discord.VoiceChannel | None:
        normalized_name = _normalize_channel_name(name)
        channel = discord.utils.find(
            lambda item: _normalize_channel_name(getattr(item, "name", "")) == normalized_name
            and getattr(item, "category_id", None) == category.id,
            self._channel_snapshot,
        )
        if channel is not None and isinstance(channel, discord.VoiceChannel):
            return channel
        if self._channel_snapshot_refreshed:
            return None
        return discord.utils.find(
            lambda item: _normalize_channel_name(getattr(item, "name", "")) == normalized_name,
            getattr(category, "voice_channels", []),
        )

    async def _pace_after_create(self) -> None:
        await asyncio.sleep(RESOURCE_CREATE_DELAY)

    def _category_count(self) -> int:
        return sum(isinstance(channel, discord.CategoryChannel) for channel in self._channel_snapshot)

    def _validate_capacity(self, selected: dict) -> None:
        streams = [
            stream
            for level in selected.get("levels", [])
            for stream in level.get("streams", [])
        ]
        for stream in streams:
            planned_count = self._stream_channel_count({}, stream)
            if planned_count > 50:
                code = stream.get("abbreviation") or stream.get("name", "stream")
                raise ValueError(
                    f"La filière `{code}` dépasse la limite de 50 salons dans sa catégorie."
                )

        fixed_category_specs = (
            (CATEGORY_GENERAL, set(GENERAL_CHANNELS.values()), set()),
            (
                CATEGORY_PROFESSORS,
                {PROFESSOR_CHANNELS["discussion"]},
                {PROFESSOR_CHANNELS["meeting"]},
            ),
            (CATEGORY_VOICE, set(), set()),
        )

        expected_by_category: dict[str, set[str]] = {
            category_name: set(text_names) | set(voice_names)
            for category_name, text_names, voice_names in fixed_category_specs
        }
        for level in selected.get("levels", []):
            for stream in level.get("streams", []):
                category_name = _stream_category_name(
                    level["name"],
                    stream["name"],
                    stream.get("abbreviation"),
                )
                expected_by_category[category_name] = self._planned_channel_names_for_stream(stream)

        stream_codes = {
            str(
                stream.get("abbreviation")
                or get_stream_abbreviation(level["name"], stream["name"])
            )
            for level in selected.get("levels", [])
            for stream in level.get("streams", [])
        }
        expected_by_category[CATEGORY_VOICE] = {
            f"🔊-{_safe_name(code, 30)}-à-distance"
            for code in stream_codes
        }

        required_category_names = set(expected_by_category)
        missing_categories = sum(
            1
            for name in required_category_names
            if self._find_category(name) is None
        )

        missing_channels = 0
        for category_name, expected_names in expected_by_category.items():
            category = self._find_category(category_name)
            if category is None:
                missing_channels += len(expected_names)
                continue

            existing_channels = list(getattr(category, "channels", []))
            existing_names = {
                _normalize_channel_name(getattr(channel, "name", ""))
                for channel in existing_channels
            }
            missing = sum(
                1
                for expected_name in expected_names
                if _normalize_channel_name(expected_name) not in existing_names
            )
            projected_category_channels = len(existing_channels) + missing
            if projected_category_channels > 50:
                raise ValueError(
                    f"La catégorie `{category_name}` dépasserait la limite de 50 salons "
                    f"({projected_category_channels})."
                )
            missing_channels += missing

        projected_channels = len(self._channel_snapshot) + missing_channels
        if projected_channels > 500:
            raise ValueError(
                f"La construction dépasserait la limite Discord de 500 salons ({projected_channels})."
            )
        projected_categories = self._category_count() + missing_categories
        if projected_categories > 50:
            raise ValueError(
                f"La construction dépasserait la limite Discord de 50 catégories ({projected_categories})."
            )

    async def build(self, selected: dict) -> BuildStats:
        print(f"[BUILD] Start guild={self.guild.id}", flush=True)
        print("[BUILD] Phase: refresh channels", flush=True)
        await self._refresh_channel_snapshot()
        print("[BUILD] Phase: capacity check", flush=True)
        self._validate_capacity(selected)
        print("[BUILD] Phase: permission preflight", flush=True)
        missing_permissions = _missing_requested_permissions(
            self.guild,
            _required_setup_permissions(),
        )
        if missing_permissions is None:
            raise BuildPermissionError(
                "Impossible de lire les permissions effectives du bot; aucun resource n'a été créé."
            )
        if missing_permissions:
            missing_labels = [
                f"{_PERMISSION_LABELS.get(name, name)} ({name})"
                for name in missing_permissions
            ]
            raise BuildPermissionError(
                "Permissions de base manquantes sur le rôle du bot : "
                + ", ".join(missing_labels)
                + ". Ajoute uniquement ces permissions au rôle du bot; "
                "Administrator n'est pas nécessaire. La construction a été arrêtée avant toute création."
            )
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
        grantable, omitted = _grantable_permissions(self.guild, permissions)
        self.stats.omitted_permissions.update(omitted)
        if omitted:
            print(
                f"[BUILD WARNING] role={name!r}; omitted_ungranted_permissions={', '.join(omitted)}",
                flush=True,
            )
        try:
            role = await self.guild.create_role(
                name=name,
                permissions=grantable,
                colour=colour,
                hoist=hoist,
                mentionable=mentionable,
                reason=reason,
            )
        except discord.Forbidden as exc:
            print(
                f"[BUILD PERMISSION ERROR] operation=create_role; resource_name={name!r}; "
                f"http_status={getattr(exc, 'status', 'unknown')}; "
                f"discord_code={getattr(exc, 'code', 'unknown')}; "
                f"discord_message={getattr(exc, 'text', '') or str(exc)}",
                flush=True,
            )
            raise

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
        kwargs = {"name": name, "reason": "School Manager automatic setup"}
        prepared, omitted = _prepare_channel_overwrites(self.guild, overwrites)
        self.stats.omitted_permissions.update(omitted)
        if omitted:
            print(
                f"[BUILD WARNING] category={name!r}; omitted_ungranted_overwrite_permissions={', '.join(omitted)}",
                flush=True,
            )
        if prepared is not None:
            kwargs["overwrites"] = prepared
        try:
            category = await self.guild.create_category(**kwargs)
        except discord.Forbidden as exc:
            print(
                f"[BUILD PERMISSION ERROR] operation=create_category; resource_name={name!r}; "
                f"http_status={getattr(exc, 'status', 'unknown')}; "
                f"discord_code={getattr(exc, 'code', 'unknown')}; "
                f"discord_message={getattr(exc, 'text', '') or str(exc)}",
                flush=True,
            )
            raise
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
        prepared, omitted = _prepare_channel_overwrites(self.guild, overwrites)
        self.stats.omitted_permissions.update(omitted)
        if omitted:
            print(
                f"[BUILD WARNING] text_channel={name!r}; omitted_ungranted_overwrite_permissions={', '.join(omitted)}",
                flush=True,
            )
        try:
            channel = await category.create_text_channel(
                name=name,
                topic=topic,
                overwrites=prepared,
                reason="School Manager automatic setup",
            )
        except discord.Forbidden as exc:
            print(
                f"[BUILD PERMISSION ERROR] operation=create_text_channel; resource_name={name!r}; "
                f"category={category.name!r}; "
                f"http_status={getattr(exc, 'status', 'unknown')}; "
                f"discord_code={getattr(exc, 'code', 'unknown')}; "
                f"discord_message={getattr(exc, 'text', '') or str(exc)}",
                flush=True,
            )
            raise
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
        prepared, omitted = _prepare_channel_overwrites(self.guild, overwrites)
        self.stats.omitted_permissions.update(omitted)
        if omitted:
            print(
                f"[BUILD WARNING] voice_channel={name!r}; omitted_ungranted_overwrite_permissions={', '.join(omitted)}",
                flush=True,
            )
        try:
            channel = await category.create_voice_channel(
                name=name,
                overwrites=prepared,
                reason="School Manager automatic setup",
            )
        except discord.Forbidden as exc:
            print(
                f"[BUILD PERMISSION ERROR] operation=create_voice_channel; resource_name={name!r}; "
                f"category={category.name!r}; "
                f"http_status={getattr(exc, 'status', 'unknown')}; "
                f"discord_code={getattr(exc, 'code', 'unknown')}; "
                f"discord_message={getattr(exc, 'text', '') or str(exc)}",
                flush=True,
            )
            raise
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
            subject_overwrites = _subject_channel_overwrites(
                self.guild.default_role,
                roles[ROLE_ADMIN],
                roles[ROLE_PROFESSOR],
                roles[ROLE_PROFESSOR_FEMALE],
                teacher_stream_role,
                student_stream_role,
                roles[ROLE_STUDENT],
                bot_member=getattr(self.guild, "me", None),
            )
            for subject in subjects:
                await self._get_or_create_text(category, _subject_channel_name(code, subject), topic=f"Cours, devoirs, exercices, examens blancs et ressources de {get_subject_display_name(subject)} pour {stream_name} ({level_name}).", overwrites=subject_overwrites)
            await self._get_or_create_voice(voice_category, f"🔊-{_safe_name(code, 30)}-à-distance", public_voice_overwrites(self.guild.default_role, roles[ROLE_ADMIN], roles[ROLE_PROFESSOR], roles[ROLE_PROFESSOR_FEMALE], roles[ROLE_STUDENT], teacher_stream_role, student_stream_role))
            self.stats.streams_processed += 1
            print(f"[BUILD] Stream done: {level_name}/{code}", flush=True)

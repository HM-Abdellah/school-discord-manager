"""Fail-closed security controls for high-impact School Manager operations."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import get_levels, get_stream_abbreviation, get_streams
from services.audit import record_event
from services.build_guard import get_build_lock
from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_PROFESSOR_FEMALE,
    ROLE_STUDENT,
    STUDENT_STREAM_ROLE_PREFIX,
    get_managed_role,
    management_check,
    owner_only_check,
)
from services.storage import (
    enroll_student_record,
    get_active_academic_year,
    get_guild_config,
    reset_guild_data,
)

# This module owns only security-sensitive management operations that are not
# delegated to a more specialized command cog.
OWNED_COMMANDS = {"assignstudent", "assignteacher", "resetserver"}


def _management_authorized(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    if guild is None:
        return False
    if interaction.user.id == guild.owner_id:
        return True
    role = get_managed_role(guild, ROLE_ADMIN)
    return role is not None and role in getattr(interaction.user, "roles", [])


def _student_staff_conflict(
    member: discord.Member,
    guild: discord.Guild,
) -> str | None:
    if member.bot:
        return "❌ Un bot ne peut pas recevoir un rôle scolaire."
    admin_role = get_managed_role(guild, ROLE_ADMIN)
    if admin_role is not None and admin_role in member.roles:
        return (
            "❌ Cet utilisateur possède le rôle **Administration**. "
            "Retire d'abord ce rôle avant une affectation scolaire."
        )
    professor_roles = {
        role
        for role in (
            get_managed_role(guild, ROLE_PROFESSOR),
            get_managed_role(guild, ROLE_PROFESSOR_FEMALE),
        )
        if role is not None
    }
    if any(role in member.roles for role in professor_roles):
        return (
            "❌ Cet utilisateur possède encore un rôle **Prof**. "
            "Retire d'abord son rôle professeur avant de l'affecter comme élève."
        )
    return None


def _teacher_target_conflict(
    member: discord.Member,
    guild: discord.Guild,
) -> str | None:
    if member.bot:
        return "❌ Un bot ne peut pas être enregistré comme professeur."
    admin_role = get_managed_role(guild, ROLE_ADMIN)
    if admin_role is not None and admin_role in member.roles:
        return (
            "❌ Un membre du rôle **Administration** ne peut pas recevoir un rôle professeur."
        )
    student_role = get_managed_role(guild, ROLE_STUDENT)
    if student_role is not None and student_role in member.roles:
        return "❌ Cet utilisateur possède encore le rôle **Élève**. Retire-le d'abord."
    if any(
        not role.managed and role.name.startswith(STUDENT_STREAM_ROLE_PREFIX)
        for role in member.roles
    ):
        return (
            "❌ Cet utilisateur possède encore un rôle de filière **Élève**. "
            "Retire-le d'abord."
        )
    return None


async def level_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=level, value=level)
        for level in get_levels()
        if current.casefold() in level.casefold()
    ][:25]


async def stream_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    level = str(getattr(interaction.namespace, "level", ""))
    if level not in get_levels():
        return []
    return [
        app_commands.Choice(name=stream, value=stream)
        for stream in get_streams(level)
        if current.casefold() in stream.casefold()
    ][:25]


def _managed_mapping(config: dict, section: str) -> dict:
    managed = config.get("managed", {}) if isinstance(config, dict) else {}
    value = managed.get(section, {}) if isinstance(managed, dict) else {}
    return value if isinstance(value, dict) else {}


class HardenedManagementCommands(commands.Cog):
    """Security-sensitive management commands with explicit fail-closed guards."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="assignstudent",
        description="Affecter un élève à une filière.",
    )
    @app_commands.describe(
        student="Élève",
        level="Niveau scolaire",
        stream="Filière scolaire",
    )
    @app_commands.autocomplete(level=level_autocomplete, stream=stream_autocomplete)
    @management_check()
    async def assign_student(
        self,
        interaction: discord.Interaction,
        student: discord.Member,
        level: str,
        stream: str,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if level not in get_levels() or stream not in get_streams(level):
            await interaction.response.send_message(
                "❌ Niveau ou filière invalide.",
                ephemeral=True,
            )
            return

        conflict = _student_staff_conflict(student, guild)
        if conflict:
            await interaction.response.send_message(conflict, ephemeral=True)
            return

        code = get_stream_abbreviation(level, stream)
        student_role = get_managed_role(guild, ROLE_STUDENT)
        student_stream_role = get_managed_role(
            guild,
            f"{STUDENT_STREAM_ROLE_PREFIX}{code}",
        )
        if student_role is None or student_stream_role is None:
            await interaction.response.send_message(
                "❌ Les rôles scolaires gérés ne sont pas prêts. Lance `/setup` puis `/build`.",
                ephemeral=True,
            )
            return

        year = get_active_academic_year(guild.id)
        if year is None:
            await interaction.response.send_message(
                "❌ Aucune année scolaire active.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        original_roles = list(student.roles)
        original_stream_roles = [
            role
            for role in student.roles
            if not role.managed and role.name.startswith(STUDENT_STREAM_ROLE_PREFIX)
        ]
        try:
            remove = [
                role
                for role in original_stream_roles
                if role != student_stream_role
            ]
            if remove:
                await student.remove_roles(
                    *remove,
                    reason="School Manager student stream normalization",
                )
            await student.add_roles(
                student_role,
                student_stream_role,
                reason="School Manager student stream assignment",
            )
            enroll_student_record(
                guild.id,
                student.id,
                student.display_name,
                int(year["id"]),
                level,
                stream,
            )
        except (discord.Forbidden, discord.HTTPException, OSError) as exc:
            try:
                current_added = [
                    role
                    for role in student.roles
                    if role not in original_roles and not role.managed
                ]
                if current_added:
                    await student.remove_roles(
                        *current_added,
                        reason="School Manager assignment rollback",
                    )
                restored = [
                    role
                    for role in original_roles
                    if role not in student.roles and not role.managed
                ]
                if restored:
                    await student.add_roles(
                        *restored,
                        reason="School Manager assignment rollback",
                    )
            except discord.HTTPException:
                pass
            await interaction.followup.send(
                f"❌ Affectation annulée : `{type(exc).__name__}`",
                ephemeral=True,
            )
            return

        record_event(
            guild.id,
            interaction.user.id,
            interaction.user.display_name,
            "assignstudent",
            student.display_name,
            f"{level}: {code}",
        )
        await interaction.followup.send(
            f"✅ {student.mention} est maintenant dans **{code}** ({level}).",
            ephemeral=True,
        )

    @app_commands.command(
        name="assignteacher",
        description="Donner le rôle Prof à un membre.",
    )
    @app_commands.describe(
        teacher="Membre qui doit recevoir le rôle professeur",
    )
    @app_commands.choices(
        gender=[
            app_commands.Choice(name="Prof", value="male"),
            app_commands.Choice(name="Prof (F)", value="female"),
        ]
    )
    @management_check()
    async def assign_teacher(
        self,
        interaction: discord.Interaction,
        teacher: discord.Member,
        gender: app_commands.Choice[str],
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return

        conflict = _teacher_target_conflict(teacher, guild)
        if conflict:
            await interaction.response.send_message(conflict, ephemeral=True)
            return

        role_name = (
            ROLE_PROFESSOR_FEMALE
            if gender.value == "female"
            else ROLE_PROFESSOR
        )
        role = get_managed_role(guild, role_name)
        if role is None:
            await interaction.response.send_message(
                f"❌ Le rôle géré `{role_name}` n'existe pas encore. Lance `/setup` puis `/build`.",
                ephemeral=True,
            )
            return

        other_role = get_managed_role(
            guild,
            ROLE_PROFESSOR if gender.value == "female" else ROLE_PROFESSOR_FEMALE,
        )
        await interaction.response.defer(ephemeral=True)
        try:
            if other_role is not None and other_role in teacher.roles:
                await teacher.remove_roles(
                    other_role,
                    reason="School Manager teacher gender normalization",
                )
            await teacher.add_roles(
                role,
                reason="School Manager teacher assignment",
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            await interaction.followup.send(
                f"❌ Impossible d'attribuer le rôle : `{type(exc).__name__}`",
                ephemeral=True,
            )
            return

        record_event(
            guild.id,
            interaction.user.id,
            interaction.user.display_name,
            "assignteacher",
            teacher.display_name,
            role_name,
        )
        await interaction.followup.send(
            f"✅ {teacher.mention} a reçu le rôle **{role_name}**.",
            ephemeral=True,
        )


class HardenedResetCommands(commands.Cog):
    """Owner-only scoped reset of resources recorded by School Manager."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="resetserver",
        description="Supprimer uniquement les ressources School Manager enregistrées.",
    )
    @app_commands.describe(
        confirm="Écris RESET SCHOOL MANAGER pour confirmer. Réservé au propriétaire."
    )
    @owner_only_check()
    async def reset_server(
        self,
        interaction: discord.Interaction,
        confirm: str,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if confirm.strip().upper() != "RESET SCHOOL MANAGER":
            await interaction.response.send_message(
                "❌ Confirmation exacte requise : `RESET SCHOOL MANAGER`.",
                ephemeral=True,
            )
            return

        lock = get_build_lock(guild.id)
        if lock.locked():
            await interaction.response.send_message(
                "⏳ Une construction est déjà en cours sur ce serveur.",
                ephemeral=True,
            )
            return

        config = get_guild_config(guild.id) or {}
        role_ids = {
            value
            for value in _managed_mapping(config, "roles").values()
            if isinstance(value, int) and value > 0
        }
        channel_ids = {
            value
            for value in _managed_mapping(config, "channels").values()
            if isinstance(value, int) and value > 0
        }
        category_ids = {
            value
            for value in _managed_mapping(config, "categories").values()
            if isinstance(value, int) and value > 0
        }

        await interaction.response.send_message(
            "🧹 **RESET SCHOOL MANAGER EN COURS...**",
            ephemeral=True,
        )
        deleted_channels = 0
        deleted_categories = 0
        deleted_roles = 0
        retained_categories = 0

        try:
            async with lock:
                for channel_id in sorted(channel_ids):
                    channel = guild.get_channel(channel_id)
                    if channel is None or not isinstance(channel, discord.abc.GuildChannel):
                        continue
                    await channel.delete(reason="School Manager scoped reset")
                    deleted_channels += 1

                for category_id in sorted(category_ids):
                    category = guild.get_channel(category_id)
                    if not isinstance(category, discord.CategoryChannel):
                        continue
                    remaining = [
                        channel
                        for channel in guild.channels
                        if getattr(channel, "category_id", None) == category.id
                    ]
                    if remaining:
                        retained_categories += 1
                        continue
                    await category.delete(reason="School Manager scoped reset")
                    deleted_categories += 1

                top_role = guild.me.top_role if guild.me is not None else None
                for role_id in sorted(role_ids):
                    role = guild.get_role(role_id)
                    if (
                        role is None
                        or role.managed
                        or role.is_default()
                        or (top_role is not None and role >= top_role)
                    ):
                        continue
                    await role.delete(reason="School Manager scoped reset")
                    deleted_roles += 1

                reset_guild_data(guild.id)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound, OSError) as exc:
            await interaction.followup.send(
                f"❌ Reset interrompu : `{type(exc).__name__}`. Aucun nom n'a été utilisé pour élargir le scope.",
                ephemeral=True,
            )
            return

        suffix = (
            f" Catégories conservées car elles contiennent des ressources non gérées : **{retained_categories}**."
            if retained_categories
            else ""
        )
        await interaction.followup.send(
            f"✅ Reset terminé. Channels: **{deleted_channels}** · Catégories: **{deleted_categories}** · Rôles: **{deleted_roles}**. Seuls les IDs gérés enregistrés ont été ciblés.{suffix}",
            ephemeral=True,
        )


def _patch_setup_build_callback() -> None:
    """Add the security preflight without creating another /build command."""
    from cogs.setup import SummaryView

    if getattr(SummaryView, "_security_hardening_applied", False):
        return

    original = SummaryView.build_callback

    async def guarded_build(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not _management_authorized(interaction):
            await interaction.response.send_message(
                "❌ Tes droits d'administration ne sont plus valides pour cette configuration.",
                ephemeral=True,
            )
            return
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Serveur requis.",
                ephemeral=True,
            )
            return

        from services.permissions import _preflight_message

        message = _preflight_message(
            interaction,
            needs_channels=True,
            needs_roles=True,
        )
        if message:
            await interaction.response.send_message(message, ephemeral=True)
            return
        await original(self, interaction)

    SummaryView.build_callback = guarded_build
    SummaryView._security_hardening_applied = True


async def setup(bot: commands.Bot) -> None:
    _patch_setup_build_callback()
    await bot.add_cog(HardenedManagementCommands(bot))
    await bot.add_cog(HardenedResetCommands(bot))

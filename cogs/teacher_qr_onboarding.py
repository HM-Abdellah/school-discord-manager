"""Teacher QR onboarding for one-time self-registration."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import GENERAL_CHANNELS
from services.audit import record_event
from services.discord_registry import resolve_registered_text_channel
from services.permissions import ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, ROLE_TEACHER_PENDING, get_managed_role, management_check
from services.qr_invites import DEFAULT_QR_MAX_AGE, create_role_invite, delete_invite, qr_file
from services.role_conflicts import teacher_target_conflict
from services.storage import get_guild_config, get_teacher_qr_invites, get_teacher_registration, mark_teacher_qr_invite_revoked, record_teacher_qr_invite, save_guild_config

async def _get_or_create_pending_role(guild: discord.Guild, config: dict) -> tuple[discord.Role, bool]:
    managed = config.setdefault("managed", {}).setdefault("roles", {})
    recorded_id = managed.get(ROLE_TEACHER_PENDING)
    if isinstance(recorded_id, int) and recorded_id > 0:
        role = guild.get_role(recorded_id)
        if role is None:
            raise RuntimeError("Le rôle d'attente professeur enregistré est absent de Discord.")
        if role.managed or role.name != ROLE_TEACHER_PENDING:
            raise RuntimeError("L'identité du rôle d'attente professeur est incohérente.")
        return role, False

    collision = discord.utils.get(guild.roles, name=ROLE_TEACHER_PENDING)
    if collision is not None:
        raise RuntimeError("Un rôle non géré utilise déjà le nom du rôle d'attente professeur.")

    role = await guild.create_role(
        name=ROLE_TEACHER_PENDING,
        permissions=discord.Permissions.none(),
        colour=discord.Colour.orange(),
        mentionable=False,
        reason="School Manager teacher onboarding pending role",
    )
    managed[ROLE_TEACHER_PENDING] = role.id
    return role, True


async def _revoke_teacher_qrs(bot: discord.Client, guild_id: int) -> int:
    rows = get_teacher_qr_invites(guild_id, include_revoked=False)
    revoked = 0
    for row in rows:
        code = str(row["invite_code"])
        try:
            await delete_invite(bot, code)
        except discord.HTTPException as exc:
            if exc.status != 404:
                raise
        mark_teacher_qr_invite_revoked(guild_id, code)
        revoked += 1
    return revoked


async def _send_teacher_onboarding_prompt(user: discord.abc.User) -> None:
    try:
        await user.send(
            "## 👨‍🏫 Inscription professeur\n\n"
            "Vous avez rejoint avec le QR d'inscription professeur.\n"
            "Utilisez maintenant **/assignteacherfull** pour vous enregistrer vous-même.\n\n"
            "Choisissez votre sexe, votre niveau, votre filière et vos matières. "
            "Après validation, votre rôle Prof sera attribué immédiatement et l'inscription QR sera définitivement consommée pour ce compte."
        )
    except discord.HTTPException:
        pass


class TeacherQROnboarding(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="createteacherqr", description="Créer un QR temporaire d'inscription professeur.")
    @app_commands.describe(max_uses="Nombre de professeurs autorisés à utiliser le QR (1 à 42)")
    @app_commands.default_permissions(manage_roles=True)
    @management_check()
    async def create_teacher_qr(
        self,
        interaction: discord.Interaction,
        max_uses: app_commands.Range[int, 1, 42],
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return

        bot_member = guild.me
        if bot_member is None or not bot_member.guild_permissions.create_instant_invite:
            await interaction.response.send_message(
                "❌ Le bot doit avoir Create Instant Invite pour générer le QR.",
                ephemeral=True,
            )
            return

        config = get_guild_config(guild.id) or {}
        working_config = deepcopy(config)
        await interaction.response.defer(ephemeral=True)

        pending_role: discord.Role | None = None
        created_role = False
        invite_code: str | None = None

        try:
            await _revoke_teacher_qrs(self.bot, guild.id)
            pending_role, created_role = await _get_or_create_pending_role(guild, working_config)

            channel = await resolve_registered_text_channel(
                guild,
                working_config,
                channel_name=GENERAL_CHANNELS["actualites"],
                category_name="🏢・INFORMATIONS & ADMINISTRATION",
            )
            if channel is None:
                raise RuntimeError("Le salon d'actualités institutionnelles est introuvable dans le registre.")

            invite_url = await create_role_invite(
                self.bot,
                channel,
                (pending_role,),
                max_uses=int(max_uses),
                reason="School Manager teacher QR onboarding",
            )
            invite_code = invite_url.rsplit("/", 1)[-1]
            now = discord.utils.utcnow()
            expires_at = now + timedelta(seconds=DEFAULT_QR_MAX_AGE)
            record_teacher_qr_invite(
                guild.id,
                invite_code,
                pending_role.id,
                interaction.user.id,
                now.isoformat(),
                expires_at.isoformat(),
                int(max_uses),
            )
            qr = qr_file(invite_url, "school-manager-teachers.png")
            save_guild_config(guild.id, working_config)
        except (discord.Forbidden, discord.HTTPException, OSError, RuntimeError, ValueError) as exc:
            if invite_code:
                try:
                    await delete_invite(self.bot, invite_code)
                except discord.HTTPException:
                    pass
                try:
                    mark_teacher_qr_invite_revoked(guild.id, invite_code)
                except OSError:
                    pass
            if created_role and pending_role is not None:
                try:
                    await pending_role.delete(reason="School Manager teacher QR setup rollback")
                except discord.HTTPException:
                    pass
            await interaction.followup.send(
                "❌ QR professeur non créé : " + type(exc).__name__ + ": " + str(exc),
                ephemeral=True,
            )
            return

        record_event(
            guild.id,
            interaction.user.id,
            interaction.user.display_name,
            "createteacherqr",
            "teacher-onboarding",
            f"expires={DEFAULT_QR_MAX_AGE}s;max_uses={int(max_uses)}",
        )
        await interaction.followup.send(
            "## ✅ QR professeur prêt\n\n"
            "Rôle temporaire : Professeur - En attente\n"
            "Expiration : 30 minutes\n"
            f"Utilisations max : {int(max_uses)}\n\n"
            "Le QR donne un accès temporaire pour permettre au professeur de lancer /assignteacherfull. "
            "Après une inscription réussie, le rôle temporaire est retiré et ce compte ne peut plus se réinscrire via QR.\n\n"
            + invite_url,
            file=qr,
            ephemeral=True,
        )

    @app_commands.command(name="revoketeacherqr", description="Révoquer le QR professeur actif.")
    @app_commands.default_permissions(manage_roles=True)
    @management_check()
    async def revoke_teacher_qr(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            count = await _revoke_teacher_qrs(self.bot, guild.id)
        except (discord.Forbidden, discord.HTTPException, OSError) as exc:
            await interaction.followup.send(
                "❌ Révocation impossible : " + type(exc).__name__ + ": " + str(exc),
                ephemeral=True,
            )
            return
        record_event(guild.id, interaction.user.id, interaction.user.display_name, "revoketeacherqr", "teacher-onboarding", f"count={count}")
        await interaction.followup.send(
            f"✅ {count} QR professeur actif(s) révoqué(s)." if count else "ℹ️ Aucun QR professeur actif.",
            ephemeral=True,
        )

    @app_commands.command(name="listteacherqr", description="Afficher le QR professeur actif.")
    @app_commands.default_permissions(manage_roles=True)
    @management_check()
    async def list_teacher_qr(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        rows = get_teacher_qr_invites(guild.id, include_revoked=False)
        now = discord.utils.utcnow()
        lines: list[str] = []
        for row in rows:
            expires_at = discord.utils.parse_time(str(row["expires_at"]))
            if expires_at <= now:
                continue
            lines.append(
                f"• {row['invite_code']} — {discord.utils.format_dt(expires_at, 'R')} — max {row['max_uses']}"
            )
        await interaction.followup.send(
            "## 🔐 QR professeur actif\n\n" + ("\n".join(lines) if lines else "ℹ️ Aucun QR professeur actif non expiré."),
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        pending_role = get_managed_role(member.guild, ROLE_TEACHER_PENDING)
        if pending_role is None or pending_role not in member.roles:
            return

        registration = get_teacher_registration(member.guild.id, member.id)
        professor_roles = {
            role
            for role in (
                get_managed_role(member.guild, ROLE_PROFESSOR),
                get_managed_role(member.guild, ROLE_PROFESSOR_FEMALE),
            )
            if role is not None
        }
        if registration is not None or any(role in member.roles for role in professor_roles):
            try:
                await member.remove_roles(
                    pending_role,
                    reason="School Manager teacher QR already consumed",
                )
                await member.send(
                    "ℹ️ Ce compte est déjà enregistré comme professeur. Le QR professeur ne peut pas être réutilisé pour ce compte."
                )
            except discord.HTTPException:
                pass
            return

        conflict = teacher_target_conflict(member, member.guild)
        if conflict:
            try:
                await member.send("⚠️ Votre compte possède déjà un état incompatible avec une inscription professeur. Contactez l'administration.")
            except discord.HTTPException:
                pass
            return
        await _send_teacher_onboarding_prompt(member)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TeacherQROnboarding(bot))
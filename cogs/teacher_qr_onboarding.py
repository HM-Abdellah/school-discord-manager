"""Teacher QR onboarding and profile request workflow."""

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
from services.role_transactions import restore_role_presence, snapshot_role_presence
from services.storage import (
    approve_teacher_onboarding_request,
    get_guild_config,
    get_teacher_onboarding_request,
    get_teacher_onboarding_requests,
    get_teacher_qr_invites,
    mark_teacher_qr_invite_revoked,
    record_teacher_qr_invite,
    reject_teacher_onboarding_request,
    save_guild_config,
    submit_teacher_onboarding_request,
)

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


class TeacherProfileModal(discord.ui.Modal):
    def __init__(self, guild_id: int, profile_type: str) -> None:
        super().__init__(title="Demande d'inscription professeur")
        self.guild_id = guild_id
        self.profile_type = profile_type

        self.full_name = discord.ui.TextInput(
            label="Nom complet",
            placeholder="Prénom NOM",
            min_length=2,
            max_length=100,
            required=True,
        )
        self.subjects = discord.ui.TextInput(
            label="Matières enseignées",
            placeholder="Ex. Mathématiques, Physique et Chimie",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=True,
        )
        self.streams = discord.ui.TextInput(
            label="Filières / niveaux enseignés",
            placeholder="Ex. 2BACPC, 1BACSE, Tronc Commun Scientifique",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=True,
        )
        self.notes = discord.ui.TextInput(
            label="Remarque complémentaire",
            placeholder="Facultatif",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=False,
        )
        self.add_item(self.full_name)
        self.add_item(self.subjects)
        self.add_item(self.streams)
        self.add_item(self.notes)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.client.get_guild(self.guild_id)
        if guild is None:
            await interaction.response.send_message("Serveur d'inscription introuvable.")
            return

        member = guild.get_member(interaction.user.id)
        if member is None:
            await interaction.response.send_message("Votre compte n'est plus membre du serveur.")
            return

        conflict = teacher_target_conflict(member, guild)
        if conflict:
            await interaction.response.send_message(conflict)
            return

        pending_role = get_managed_role(guild, ROLE_TEACHER_PENDING)
        if pending_role is None or pending_role not in member.roles:
            await interaction.response.send_message("Cette invitation professeur n'est plus reconnue.")
            return

        request_id = submit_teacher_onboarding_request(
            guild.id,
            member.id,
            self.full_name.value.strip(),
            self.profile_type,
            self.subjects.value.strip(),
            self.streams.value.strip(),
            self.notes.value.strip(),
        )
        await interaction.response.send_message(
            f"✅ Demande professeur #{request_id} enregistrée. L'administration doit maintenant la vérifier."
        )


class TeacherProfileView(discord.ui.View):
    def __init__(self, guild_id: int) -> None:
        super().__init__(timeout=15 * 60)
        self.guild_id = guild_id

    @discord.ui.button(label="Profil professeur", style=discord.ButtonStyle.primary, emoji="👨‍🏫")
    async def male_profile(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(TeacherProfileModal(self.guild_id, "male"))

    @discord.ui.button(label="Profil professeure", style=discord.ButtonStyle.secondary, emoji="👩‍🏫")
    async def female_profile(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(TeacherProfileModal(self.guild_id, "female"))


async def _send_teacher_onboarding_prompt(user: discord.abc.User, guild_id: int) -> None:
    try:
        await user.send(
            "## 👨‍🏫 Bienvenue dans School Discord Manager\n\n"
            "Vous avez rejoint avec une invitation d'inscription professeur. "
            "Vous êtes encore en attente et n'avez pas le rôle Prof.\n\n"
            "Choisissez votre profil puis remplissez le formulaire. "
            "L'administration vérifiera ensuite votre demande.",
            view=TeacherProfileView(guild_id),
        )
    except discord.HTTPException:
        pass


class TeacherQROnboarding(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="createteacherqr", description="Créer un QR temporaire d'inscription professeur.")
    @app_commands.describe(max_uses="Nombre de personnes autorisées à demander une inscription (1 à 42)")
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
            "Le QR donne uniquement un accès en attente. Le professeur remplit sa demande, "
            "puis l'administration doit l'approuver.\n\n"
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

    @app_commands.command(name="teacherprofile", description="Ouvrir le formulaire d'inscription professeur.")
    async def teacher_profile(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Lance cette commande depuis le serveur scolaire.")
            return
        member = guild.get_member(interaction.user.id)
        pending_role = get_managed_role(guild, ROLE_TEACHER_PENDING)
        if member is None or pending_role is None or pending_role not in member.roles:
            await interaction.response.send_message(
                "❌ Cette commande est réservée aux membres ayant rejoint via l'inscription professeur.",
                ephemeral=True,
            )
            return
        conflict = teacher_target_conflict(member, guild)
        if conflict:
            await interaction.response.send_message(conflict, ephemeral=True)
            return
        await interaction.response.send_message(
            "Choisissez votre type de profil puis remplissez le formulaire.",
            view=TeacherProfileView(guild.id),
            ephemeral=True,
        )

    @app_commands.command(name="teacherrequests", description="Afficher les demandes professeur en attente.")
    @app_commands.default_permissions(manage_roles=True)
    @management_check()
    async def teacher_requests(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        rows = get_teacher_onboarding_requests(guild.id, status="pending", limit=15)
        if not rows:
            await interaction.followup.send("ℹ️ Aucune demande professeur en attente.", ephemeral=True)
            return
        chunks: list[str] = []
        for row in rows:
            member = guild.get_member(int(row["discord_id"]))
            mention = member.mention if member is not None else f"<@{row['discord_id']}>"
            profile = "Prof" if row["profile_type"] == "male" else "Prof (F)"
            chunks.append(
                f"#{row['id']} — {row['full_name']} · {profile} · {mention}\n"
                f"Matières : {str(row['subjects_text'])[:300]}\n"
                f"Filières : {str(row['streams_text'])[:300]}"
            )
        await interaction.followup.send("## 👨‍🏫 Demandes en attente\n\n" + "\n\n".join(chunks), ephemeral=True)

    @app_commands.command(name="approveteacher", description="Approuver une demande professeur et activer son rôle.")
    @app_commands.describe(teacher="Membre dont la demande doit être approuvée")
    @app_commands.default_permissions(manage_roles=True)
    @management_check()
    async def approve_teacher(self, interaction: discord.Interaction, teacher: discord.Member) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        request = get_teacher_onboarding_request(guild.id, teacher.id, status="pending")
        if request is None:
            await interaction.response.send_message("❌ Aucune demande professeur en attente pour ce membre.", ephemeral=True)
            return
        conflict = teacher_target_conflict(teacher, guild)
        if conflict:
            await interaction.response.send_message(conflict, ephemeral=True)
            return

        role_name = ROLE_PROFESSOR_FEMALE if request["profile_type"] == "female" else ROLE_PROFESSOR
        desired_role = get_managed_role(guild, role_name)
        other_role = get_managed_role(guild, ROLE_PROFESSOR if role_name == ROLE_PROFESSOR_FEMALE else ROLE_PROFESSOR_FEMALE)
        pending_role = get_managed_role(guild, ROLE_TEACHER_PENDING)
        if desired_role is None:
            await interaction.response.send_message(
                f"❌ Le rôle {role_name} n'existe pas. Lance /setup puis /build.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        tracked_roles = [role for role in (desired_role, other_role, pending_role) if role is not None]
        original_presence = snapshot_role_presence(teacher, tracked_roles)
        try:
            if desired_role not in teacher.roles:
                await teacher.add_roles(desired_role, reason="School Manager teacher onboarding approval")
            if other_role is not None and other_role in teacher.roles:
                await teacher.remove_roles(other_role, reason="Teacher role normalization after onboarding approval")
            if pending_role is not None and pending_role in teacher.roles:
                await teacher.remove_roles(pending_role, reason="School Manager teacher onboarding activation")
            approve_teacher_onboarding_request(guild.id, teacher.id, interaction.user.id)
        except (discord.Forbidden, discord.HTTPException, OSError, RuntimeError) as exc:
            try:
                await restore_role_presence(
                    teacher,
                    tracked_roles,
                    original_presence,
                    reason="School Manager teacher onboarding approval rollback",
                )
            except discord.HTTPException:
                pass
            await interaction.followup.send(
                "❌ Approbation annulée : " + type(exc).__name__ + ": " + str(exc),
                ephemeral=True,
            )
            return

        record_event(
            guild.id,
            interaction.user.id,
            interaction.user.display_name,
            "approveteacher",
            teacher.display_name,
            f"request={request['id']};role={role_name}",
        )
        await interaction.followup.send(
            f"✅ {teacher.mention} est maintenant {role_name}. "
            "Les matières et filières du formulaire restent disponibles pour l'affectation détaillée via /assignteacherfull.",
            ephemeral=True,
        )

    @app_commands.command(name="rejectteacher", description="Rejeter une demande professeur.")
    @app_commands.describe(teacher="Membre dont la demande doit être rejetée", reason="Motif du rejet")
    @app_commands.default_permissions(manage_roles=True)
    @management_check()
    async def reject_teacher(self, interaction: discord.Interaction, teacher: discord.Member, reason: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        request = get_teacher_onboarding_request(guild.id, teacher.id, status="pending")
        if request is None:
            await interaction.response.send_message("❌ Aucune demande professeur en attente pour ce membre.", ephemeral=True)
            return

        pending_role = get_managed_role(guild, ROLE_TEACHER_PENDING)
        tracked_roles = [pending_role] if pending_role is not None else []
        original_presence = snapshot_role_presence(teacher, tracked_roles)
        await interaction.response.defer(ephemeral=True)
        try:
            if pending_role is not None and pending_role in teacher.roles:
                await teacher.remove_roles(pending_role, reason="School Manager teacher onboarding rejection")
            reject_teacher_onboarding_request(
                guild.id,
                teacher.id,
                interaction.user.id,
                reason.strip()[:1000] or "Non précisé",
            )
        except (discord.Forbidden, discord.HTTPException, OSError, RuntimeError) as exc:
            try:
                await restore_role_presence(
                    teacher,
                    tracked_roles,
                    original_presence,
                    reason="School Manager teacher onboarding rejection rollback",
                )
            except discord.HTTPException:
                pass
            await interaction.followup.send(
                "❌ Rejet annulé : " + type(exc).__name__ + ": " + str(exc),
                ephemeral=True,
            )
            return
        record_event(
            guild.id,
            interaction.user.id,
            interaction.user.display_name,
            "rejectteacher",
            teacher.display_name,
            f"request={request['id']};reason={reason.strip()[:200]}",
        )
        await interaction.followup.send(f"✅ Demande de {teacher.mention} rejetée.", ephemeral=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        pending_role = get_managed_role(member.guild, ROLE_TEACHER_PENDING)
        if pending_role is None or pending_role not in member.roles:
            return
        conflict = teacher_target_conflict(member, member.guild)
        if conflict:
            try:
                await member.send("⚠️ Votre compte possède déjà un état incompatible avec une inscription professeur. Contactez l'administration.")
            except discord.HTTPException:
                pass
            return
        await _send_teacher_onboarding_prompt(member, member.guild.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TeacherQROnboarding(bot))

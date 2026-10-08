"""Teacher QR onboarding for one-time self-registration."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import traceback

import discord
from discord import app_commands
from discord.ext import commands

from config.curriculum import (
    GENERAL_CHANNELS,
    get_levels,
    get_stream_abbreviation,
    get_stream_subjects,
    get_streams,
    get_subject_display_name,
)
from services.audit import record_event
from services.discord_registry import resolve_registered_text_channel
from services.build_guard import get_build_lock
from services.permissions import ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE, ROLE_TEACHER_PENDING, get_managed_role, management_check
from services.teacher_assignment import TeacherAssignmentError, execute_teacher_assignment
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


class TeacherOnboardingView(discord.ui.View):
    """Guided teacher self-registration using Discord select menus."""

    def __init__(self, bot: discord.Client, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=1800)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.message: discord.Message | None = None

        self.selected_gender: str | None = None
        self.selected_level: str | None = None
        self.selected_stream: str | None = None
        self.selected_subjects: list[str] = []
        self.submitting = False

        self.gender_select = discord.ui.Select(
            placeholder="1️⃣ Choisissez votre type de professeur",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Prof", value="male", description="Rôle professeur"),
                discord.SelectOption(label="Prof (F)", value="female", description="Rôle professeur féminin"),
            ],
        )
        self.gender_select.callback = self._gender_selected
        self.add_item(self.gender_select)

        self.level_select = discord.ui.Select(
            placeholder="2️⃣ Choisissez votre niveau scolaire",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=level, value=level)
                for level in get_levels()
            ],
        )
        self.level_select.callback = self._level_selected
        self.add_item(self.level_select)

        self.stream_select = discord.ui.Select(
            placeholder="3️⃣ Choisissez d'abord votre niveau",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label="Choisissez un niveau d'abord",
                    value="__disabled__",
                )
            ],
            disabled=True,
        )
        self.stream_select.callback = self._stream_selected
        self.add_item(self.stream_select)

        self.subject_select = discord.ui.Select(
            placeholder="4️⃣ Choisissez une ou plusieurs matières",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label="Choisissez une filière d'abord",
                    value="__disabled__",
                )
            ],
            disabled=True,
        )
        self.subject_select.callback = self._subjects_selected
        self.add_item(self.subject_select)

        self.confirm_button = discord.ui.Button(
            label="Valider mon inscription",
            style=discord.ButtonStyle.success,
            emoji="✅",
            disabled=True,
        )
        self.confirm_button.callback = self._confirm_registration
        self.add_item(self.confirm_button)

    async def _respond(self, interaction: discord.Interaction, content: str) -> None:
        if interaction.response.is_done():
            message = getattr(interaction, "message", None)
            if message is not None:
                await message.edit(content=content, view=self)
            else:
                await self._update_interaction_message(interaction, content=content, view=self)
        else:
            await interaction.response.send_message(content)

    async def _update_interaction_message(
        self,
        interaction: discord.Interaction,
        *,
        content: str,
        view: discord.ui.View | None = None,
    ) -> None:
        message = getattr(interaction, "message", None)
        if message is not None:
            await message.edit(content=content, view=self if view is None else view)
        else:
            await interaction.edit_original_response(
                content=content,
                view=self if view is None else view,
            )

    async def _ensure_eligible(self, interaction: discord.Interaction) -> discord.Member | None:
        if interaction.user.id != self.user_id:
            await self._respond(
                interaction,
                "❌ Ce formulaire est réservé au professeur concerné.",
            )
            return None

        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            await self._respond(
                interaction,
                "❌ Le serveur n'est plus accessible. Contactez l'administration.",
            )
            return None

        member = guild.get_member(self.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(self.user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                await self._respond(
                    interaction,
                    "❌ Impossible de retrouver votre compte dans le serveur.",
                )
                return None

        pending_role = get_managed_role(guild, ROLE_TEACHER_PENDING)
        professor_roles = {
            role
            for role in (
                get_managed_role(guild, ROLE_PROFESSOR),
                get_managed_role(guild, ROLE_PROFESSOR_FEMALE),
            )
            if role is not None
        }
        if pending_role is None or pending_role not in member.roles:
            await self._respond(interaction, "❌ Cette inscription QR n'est plus active.")
            return None

        if get_teacher_registration(guild.id, member.id) is not None or any(
            role in member.roles for role in professor_roles
        ):
            await self._respond(
                interaction,
                "ℹ️ Cette inscription est déjà terminée. Le QR ne peut pas être réutilisé.",
            )
            return None

        return member

    async def _edit_form(self, interaction: discord.Interaction) -> None:
        if self.message is None:
            self.message = getattr(interaction, "message", None)
        await interaction.response.edit_message(content=self._summary_text(), view=self)

    def _refresh_control_state(self) -> None:
        self.gender_select.disabled = False
        self.level_select.disabled = False
        self.stream_select.disabled = self.selected_level is None
        self.subject_select.disabled = self.selected_stream is None
        self.confirm_button.disabled = not (
            self.selected_gender
            and self.selected_level
            and self.selected_stream
            and self.selected_subjects
        )

    def _summary_text(self) -> str:
        gender = {"male": "Prof", "female": "Prof (F)"}.get(self.selected_gender, "—")
        level = self.selected_level or "—"
        stream = self.selected_stream or "—"
        subjects = ", ".join(get_subject_display_name(s) for s in self.selected_subjects) or "—"
        ready = (
            "✅ Vous pouvez valider votre inscription."
            if self.selected_gender and self.selected_level and self.selected_stream and self.selected_subjects
            else "➡️ Complétez les choix ci-dessus."
        )
        return (
            "## 👨‍🏫 Inscription professeur\n\n"
            "Sélectionnez uniquement les valeurs proposées par le système. "
            "Cela évite toute erreur de nom ou d'affectation.\n\n"
            f"**Type :** {gender}\n"
            f"**Niveau :** {level}\n"
            f"**Filière :** {stream}\n"
            f"**Matière(s) :** {subjects}\n\n"
            f"{ready}"
        )

    async def _gender_selected(self, interaction: discord.Interaction) -> None:
        if await self._ensure_eligible(interaction) is None:
            return
        self.selected_gender = self.gender_select.values[0]
        await self._edit_form(interaction)

    async def _level_selected(self, interaction: discord.Interaction) -> None:
        if await self._ensure_eligible(interaction) is None:
            return
        self.selected_level = self.level_select.values[0]
        self.selected_stream = None
        self.selected_subjects = []

        streams = get_streams(self.selected_level)
        self.stream_select.options = [
            discord.SelectOption(
                label=stream,
                value=stream,
                description=get_stream_abbreviation(self.selected_level, stream),
            )
            for stream in streams
        ]
        self.stream_select.disabled = False
        self.subject_select.disabled = True
        self.subject_select.options = [
            discord.SelectOption(
                label="Choisissez une filière d'abord",
                value="__disabled__",
            )
        ]
        self.confirm_button.disabled = True
        await self._edit_form(interaction)

    async def _stream_selected(self, interaction: discord.Interaction) -> None:
        if await self._ensure_eligible(interaction) is None:
            return
        if self.selected_level is None:
            await interaction.response.send_message("❌ Choisissez d'abord un niveau.")
            return

        stream = self.stream_select.values[0]
        if stream not in get_streams(self.selected_level):
            await interaction.response.send_message("❌ Filière invalide pour ce niveau.")
            return

        self.selected_stream = stream
        self.selected_subjects = []
        subjects = get_stream_subjects(self.selected_level, stream)
        self.subject_select.options = [
            discord.SelectOption(
                label=get_subject_display_name(subject)[:100],
                value=subject,
                description=subject[:100] if get_subject_display_name(subject) != subject else None,
            )
            for subject in subjects
        ]
        self.subject_select.min_values = 1
        self.subject_select.max_values = len(subjects)
        self.subject_select.disabled = False
        self.confirm_button.disabled = True
        await self._edit_form(interaction)

    async def _subjects_selected(self, interaction: discord.Interaction) -> None:
        if await self._ensure_eligible(interaction) is None:
            return
        if self.selected_level is None or self.selected_stream is None:
            await interaction.response.send_message("❌ Choisissez d'abord le niveau et la filière.")
            return

        allowed = set(get_stream_subjects(self.selected_level, self.selected_stream))
        selected = [value for value in self.subject_select.values if value in allowed]
        if not selected:
            await interaction.response.send_message("❌ Choisissez au moins une matière valide.")
            return

        self.selected_subjects = selected
        self.confirm_button.disabled = False
        await self._edit_form(interaction)

    async def _confirm_registration(self, interaction: discord.Interaction) -> None:
        if self.submitting:
            await interaction.response.send_message("⏳ Une inscription est déjà en cours.")
            return

        if not (
            self.selected_gender
            and self.selected_level
            and self.selected_stream
            and self.selected_subjects
        ):
            await interaction.response.send_message("❌ Complétez tous les choix avant de valider.")
            return

        self.submitting = True
        for item in self.children:
            item.disabled = True

        try:
            await interaction.response.edit_message(
                content="⏳ **Inscription en cours...**\n\n"
                "Le bot vérifie vos choix et attribue vos rôles. Ne cliquez pas plusieurs fois.",
                view=self,
            )
        except discord.HTTPException:
            self.submitting = False
            raise

        try:
            member = await self._ensure_eligible(interaction)
            if member is None:
                self.submitting = False
                self._refresh_control_state()
                return

            async with get_build_lock(self.guild_id):
                pending_role = get_managed_role(member.guild, ROLE_TEACHER_PENDING)
                if pending_role is None or pending_role not in member.roles:
                    await self._update_interaction_message(
                        interaction,
                        content="❌ Cette inscription QR n'est plus active.",
                        view=self,
                    )
                    self.submitting = False
                    self._refresh_control_state()
                    return
                if get_teacher_registration(member.guild.id, member.id) is not None:
                    await self._update_interaction_message(
                        interaction,
                        content="ℹ️ Votre inscription est déjà terminée. Le QR ne peut plus être réutilisé.",
                        view=self,
                    )
                    self.submitting = False
                    self._refresh_control_state()
                    return

                try:
                    result = await execute_teacher_assignment(
                        guild=member.guild,
                        teacher=member,
                        gender_value=self.selected_gender,
                        level=self.selected_level,
                        stream=self.selected_stream,
                        subjects=", ".join(self.selected_subjects),
                        actor_id=member.id,
                        actor_display_name=member.display_name,
                        self_registration=True,
                    )
                except TeacherAssignmentError as exc:
                    self.submitting = False
                    self._refresh_control_state()
                    await self._update_interaction_message(
                        interaction,
                        content=f"{exc}\n\nVous pouvez corriger vos choix puis réessayer.",
                        view=self,
                    )
                    return

            for item in self.children:
                item.disabled = True
            self.submitting = True
            await interaction.edit_original_response(
                content=(
                    f"✅ **Inscription terminée !**\n\n"
                    f"Vous êtes maintenant professeur et affecté à **{result['stream_code']}** "
                    f"pour : **{result['subject_names']}**.\n\n"
                    "Le rôle temporaire **Professeur - En attente** a été retiré."
                ),
                view=self,
            )
        except Exception as exc:
            self.submitting = False
            self._refresh_control_state()
            try:
                await self._update_interaction_message(
                    interaction,
                    content=(
                        "❌ **Erreur technique pendant l'inscription.**\n\n"
                        "L'inscription n'a pas été finalisée. Vous pouvez réessayer."
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass
            print(
                f"[TEACHER QR UI ERROR] guild={self.guild_id} user={self.user_id} "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            traceback.print_exc()
            return

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        self.submitting = False
        self._refresh_control_state()
        print(
            f"[TEACHER QR VIEW ERROR] guild={self.guild_id} user={self.user_id} "
            f"item={type(item).__name__} {type(error).__name__}: {error}",
            flush=True,
        )
        try:
            if interaction.response.is_done():
                await self._update_interaction_message(
                    interaction,
                    content=(
                        "❌ **Erreur technique.**\n\n"
                        "L'inscription n'a pas pu être finalisée. Vérifiez les logs du bot."
                    ),
                    view=self,
                )
            else:
                await interaction.response.send_message(
                    "❌ **Erreur technique.** L'inscription n'a pas pu être finalisée."
                )
        except discord.HTTPException:
            pass

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(
                content="⏰ Cette invitation d'inscription a expiré. Demandez à l'administration un nouveau QR.",
                view=self,
            )
        except discord.HTTPException:
            pass

    async def complete(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is None:
            return
        try:
            await self.message.edit(
                content="✅ Inscription professeur terminée. Ce QR ne peut plus être utilisé pour ce compte.",
                view=self,
            )
        except discord.HTTPException:
            pass


async def _send_teacher_onboarding_prompt(
    bot: discord.Client,
    user: discord.Member,
) -> None:
    view = TeacherOnboardingView(bot, user.guild.id, user.id)
    try:
        view.message = await user.send(
            "## 👨‍🏫 Inscription professeur\n\n"
            "Bienvenue ! Utilisez les menus ci-dessous pour compléter votre inscription.\n\n"
            "Tous les choix proviennent directement du programme scolaire configuré. "
            "Après validation, le rôle professeur sera attribué immédiatement et le rôle temporaire sera retiré.",
            view=view,
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
            "Le QR donne un accès temporaire pour compléter l'inscription avec les menus ci-dessous. "
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
        await _send_teacher_onboarding_prompt(self.bot, member)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TeacherQROnboarding(bot))

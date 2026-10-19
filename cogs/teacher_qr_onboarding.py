"""Teacher QR onboarding for one-time self-registration."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import logging
import sqlite3
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
from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_PROFESSOR_FEMALE,
    ROLE_TEACHER_PENDING,
    SUBJECT_ROLE_PREFIX,
    STREAM_ROLE_PREFIX,
    get_managed_role,
    management_check,
    owner_only_check,
)
from services.teacher_assignment import TeacherAssignmentError, execute_teacher_assignment
from services.teacher_threads import ensure_teacher_threads
from services.qr_invites import DEFAULT_QR_MAX_AGE, create_role_invite, delete_invite, qr_file
from services.role_conflicts import teacher_target_conflict
from services.storage import (
    delete_teacher_registration,
    get_guild_config,
    get_active_sections_for_stream,
    get_teacher_assignments,
    get_teacher_qr_invites,
    get_teacher_registration,
    mark_teacher_qr_invite_revoked,
    record_teacher_qr_invite,
    save_guild_config,
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
        self.selected_sections: list[int] = []
        self.available_sections: list[int] = []
        self.selected_subjects: list[str] = []
        # One entry per class section; subjects are scoped to that section.
        self.assignments: list[dict[str, object]] = []
        self.stage = "class"
        self.submitting = False

        self.gender_select = discord.ui.Select(
            placeholder="1️⃣ Choisissez votre type de professeur",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Prof", value="male", description="Rôle professeur"),
                discord.SelectOption(label="Prof (F)", value="female", description="Rôle professeur féminin"),
            ],
            row=0,
        )
        self.gender_select.callback = self._gender_selected

        self.level_select = discord.ui.Select(
            placeholder="2️⃣ Choisissez votre niveau scolaire",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label=level, value=level) for level in get_levels()],
            row=1,
        )
        self.level_select.callback = self._level_selected

        self.stream_select = discord.ui.Select(
            placeholder="3️⃣ Choisissez d'abord votre niveau",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label="Choisissez un niveau d'abord", value="__disabled__")],
            disabled=True,
            row=2,
        )
        self.stream_select.callback = self._stream_selected

        self.section_select = discord.ui.Select(
            placeholder="4️⃣ Choisissez vos sections (1 à 8)",
            min_values=1,
            max_values=8,
            options=[
                discord.SelectOption(label=f"Section {section}", value=str(section))
                for section in range(1, 9)
            ],
            disabled=True,
            row=3,
        )
        self.section_select.callback = self._sections_selected

        self.continue_button = discord.ui.Button(
            label="Choisir les matières",
            style=discord.ButtonStyle.primary,
            emoji="➡️",
            disabled=True,
            row=4,
        )
        self.continue_button.callback = self._continue_to_subjects

        self.subject_select = discord.ui.Select(
            placeholder="Choisissez une ou plusieurs matières",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label="Choisissez une filière d'abord", value="__disabled__")],
            disabled=True,
            row=0,
        )
        self.subject_select.callback = self._subjects_selected

        self.add_assignment_button = discord.ui.Button(
            label="Ajouter une autre classe",
            style=discord.ButtonStyle.secondary,
            emoji="➕",
            disabled=True,
            row=1,
        )
        self.add_assignment_button.callback = self._add_assignment

        self.confirm_button = discord.ui.Button(
            label="Valider mon inscription",
            style=discord.ButtonStyle.success,
            emoji="✅",
            disabled=True,
            row=1,
        )
        self.confirm_button.callback = self._confirm_registration

        self.back_button = discord.ui.Button(
            label="Retour à la classe",
            style=discord.ButtonStyle.secondary,
            emoji="⬅️",
            row=1,
        )
        self.back_button.callback = self._back_to_class

        self.reset_button = discord.ui.Button(
            label="Recommencer",
            style=discord.ButtonStyle.danger,
            emoji="🔄",
            row=4,
        )
        self.reset_button.callback = self._reset_form
        self._show_class_stage()

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

        registry = getattr(self.bot, "_teacher_onboarding_views", {})
        active_view = registry.get((self.guild_id, self.user_id)) if isinstance(registry, dict) else None
        if active_view is not None and active_view is not self:
            await self._respond(
                interaction,
                "ℹ️ Ce formulaire a été remplacé par un formulaire plus récent. Utilisez le dernier message privé reçu.",
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
        if pending_role is None:
            await self._respond(interaction, "❌ Le rôle temporaire d'inscription est introuvable. Contactez l'administration.")
            return None

        # A role-change REST request and its gateway cache update can race with
        # the teacher clicking the form. Refresh only when the cached role state
        # is inconsistent with an active onboarding session.
        if pending_role not in member.roles or any(role in member.roles for role in professor_roles):
            fetch_member = getattr(guild, "fetch_member", None)
            if not callable(fetch_member):
                await self._respond(
                    interaction,
                    "❌ Impossible de vérifier vos rôles actuels. Réessayez dans un instant ou contactez l'administration.",
                )
                return None
            try:
                member = await fetch_member(self.user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                await self._respond(
                    interaction,
                    "❌ Impossible de vérifier votre accès actuel au serveur. Contactez l'administration.",
                )
                return None

        if pending_role not in member.roles:
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

    def _show_class_stage(self) -> None:
        for item in list(self.children):
            self.remove_item(item)
        self.stage = "class"
        for item in (
            self.gender_select,
            self.level_select,
            self.stream_select,
            self.section_select,
            self.continue_button,
            self.reset_button,
        ):
            if item is self.reset_button:
                item.row = 4
            self.add_item(item)
        self._refresh_control_state()

    def _show_subject_stage(self) -> None:
        for item in list(self.children):
            self.remove_item(item)
        self.stage = "subjects"
        self.back_button.row = 1
        self.reset_button.row = 2
        for item in (
            self.subject_select,
            self.back_button,
            self.add_assignment_button,
            self.confirm_button,
            self.reset_button,
        ):
            self.add_item(item)
        self._refresh_control_state()

    def _refresh_control_state(self) -> None:
        if self.stage == "class":
            self.gender_select.disabled = bool(self.assignments)
            self.level_select.disabled = False
            self.stream_select.disabled = self.selected_level is None
            self.section_select.disabled = not self.available_sections
            self.continue_button.disabled = not (
                self.selected_gender
                and self.selected_level
                and self.selected_stream
                and self.selected_sections
                and all(section in self.available_sections for section in self.selected_sections)
            )
            self.subject_select.disabled = True
            self.back_button.disabled = True
            self.add_assignment_button.disabled = True
            self.confirm_button.disabled = True
        else:
            self.subject_select.disabled = self.selected_stream is None
            self.back_button.disabled = False
            self.add_assignment_button.disabled = not bool(self.selected_subjects)
            self.confirm_button.disabled = not bool(self.selected_subjects)
            self.continue_button.disabled = True

    def _assigned_subject_keys(self, assignments: list[dict[str, object]] | None = None) -> set[str]:
        source = self.assignments if assignments is None else assignments
        keys: set[str] = set()
        for item in source:
            subjects = item.get("subjects", [])
            if not isinstance(subjects, (list, tuple, set)):
                continue
            keys.update(get_subject_display_name(str(subject)).casefold() for subject in subjects)
        return keys

    def _assignment_lines(self) -> list[str]:
        lines: list[str] = []
        for item in self.assignments:
            subjects = ", ".join(
                get_subject_display_name(str(subject))
                for subject in item.get("subjects", [])
            )
            lines.append(
                f"• {get_stream_abbreviation(str(item['level']), str(item['stream']))} "
                f"— Section {item['section']}: {subjects}"
            )
        if len(lines) > 8:
            return lines[:8] + [f"… et {len(lines) - 8} autre(s) affectation(s)."]
        return lines

    def _summary_text(self) -> str:
        gender = {"male": "Prof", "female": "Prof (F)"}.get(self.selected_gender, "—")
        level = self.selected_level or "—"
        stream = self.selected_stream or "—"
        sections = ", ".join(str(section) for section in self.selected_sections) or "—"
        subjects = ", ".join(get_subject_display_name(subject) for subject in self.selected_subjects) or "—"
        lines = [
            "## 👨‍🏫 Inscription professeur",
            "",
            "Choisissez toutes les classes et matières que vous enseignez. Vous pouvez ajouter plusieurs niveaux.",
            "",
            f"**Type :** {gender}",
        ]
        if self.assignments:
            lines.extend(["", "**Affectations déjà ajoutées :**", *self._assignment_lines()])
        if self.stage == "class":
            lines.extend([
                "",
                "**Étape 1/2 — Classe**",
                f"**Niveau :** {level}",
                f"**Filière :** {stream}",
                f"**Sections :** {sections}",
                "",
                "Les matières seront choisies à l'étape suivante. Les sections sélectionnées recevront les mêmes matières.",
                "➡️ Choisissez le type, le niveau, la filière et une ou plusieurs sections.",
            ])
        else:
            lines.extend([
                "",
                "**Étape 2/2 — Matières**",
                f"**Classe :** {get_stream_abbreviation(level, stream)}",
                f"**Sections :** {sections}",
                f"**Matière(s) :** {subjects}",
                "",
                "Choisissez les matières enseignées dans ces sections, puis ajoutez une autre classe ou validez l'inscription.",
            ])
        text = "\n".join(lines)
        return text if len(text) <= 1900 else text[:1890] + "\n…"

    def _merged_assignments(
        self,
        existing: list[dict[str, object]],
        sections: list[int],
        subjects: list[str],
    ) -> list[dict[str, object]]:
        merged: list[dict[str, object]] = [
            {
                "level": str(item["level"]),
                "stream": str(item["stream"]),
                "section": int(item["section"]),
                "subjects": list(item.get("subjects", [])),
            }
            for item in existing
        ]
        if self.selected_level is None or self.selected_stream is None:
            return merged
        for section in sections:
            current = next(
                (
                    item for item in merged
                    if item["level"] == self.selected_level
                    and item["stream"] == self.selected_stream
                    and item["section"] == section
                ),
                None,
            )
            if current is None:
                current = {
                    "level": self.selected_level,
                    "stream": self.selected_stream,
                    "section": section,
                    "subjects": [],
                }
                merged.append(current)
            current_subjects = current["subjects"]
            if not isinstance(current_subjects, list):
                current_subjects = []
                current["subjects"] = current_subjects
            for subject in subjects:
                if subject not in current_subjects:
                    current_subjects.append(subject)
        return merged

    async def _gender_selected(self, interaction: discord.Interaction) -> None:
        if await self._ensure_eligible(interaction) is None:
            return
        if self.assignments:
            await self._respond(interaction, "❌ Le type de professeur ne peut plus être modifié après l'ajout d'une affectation.")
            return
        value = self.gender_select.values[0]
        if value not in {"male", "female"}:
            await interaction.response.send_message("❌ Type de professeur invalide.")
            return
        self.selected_gender = value
        self._refresh_control_state()
        await self._edit_form(interaction)

    async def _level_selected(self, interaction: discord.Interaction) -> None:
        if await self._ensure_eligible(interaction) is None:
            return
        level = self.level_select.values[0]
        if level not in get_levels():
            await interaction.response.send_message("❌ Niveau invalide.")
            return
        self.selected_level = level
        self.selected_stream = None
        self.selected_sections = []
        self.available_sections = []
        self.selected_subjects = []
        self.level_select.options = [
            discord.SelectOption(label=item, value=item, default=item == level)
            for item in get_levels()
        ]
        self.section_select.options = [
            discord.SelectOption(label="Choisissez une filière d'abord", value="__disabled__")
        ]
        self.section_select.min_values = 1
        self.section_select.max_values = 1
        self.section_select.disabled = True
        self.stream_select.options = [
            discord.SelectOption(
                label=stream,
                value=stream,
                description=get_stream_abbreviation(level, stream),
            )
            for stream in get_streams(level)
        ]
        self.stream_select.disabled = False
        self.section_select.disabled = True
        self._refresh_control_state()
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
        self.selected_sections = []
        self.available_sections = []
        self.selected_subjects = []
        self.section_select.options = [
            discord.SelectOption(label="Chargement des sections…", value="__disabled__")
        ]
        self.section_select.min_values = 1
        self.section_select.max_values = 1
        self.section_select.disabled = True
        self._refresh_control_state()
        try:
            self.available_sections = get_active_sections_for_stream(
                self.guild_id, self.selected_level, self.selected_stream
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            logging.getLogger(__name__).exception(
                "Unable to load active sections for teacher onboarding (guild=%s level=%s stream=%s)",
                self.guild_id, self.selected_level, self.selected_stream,
            )
            await interaction.response.edit_message(
                content=(
                    "❌ Impossible de lire les sections issues de l'import scolaire. Réessayez ou contactez l'administration.\n\n"
                    + self._summary_text()
                ),
                view=self,
            )
            return
        if self.available_sections:
            self.section_select.options = [
                discord.SelectOption(label=f"Section {section}", value=str(section))
                for section in self.available_sections
            ]
            self.section_select.min_values = 1
            self.section_select.max_values = len(self.available_sections)
            self.section_select.disabled = False
        else:
            self.section_select.options = [
                discord.SelectOption(label="Aucune section active importée", value="__disabled__")
            ]
            self.section_select.min_values = 1
            self.section_select.max_values = 1
            self.section_select.disabled = True
        self.stream_select.options = [
            discord.SelectOption(
                label=item,
                value=item,
                description=get_stream_abbreviation(self.selected_level, item),
                default=item == stream,
            )
            for item in get_streams(self.selected_level)
        ]
        self._refresh_control_state()
        await self._edit_form(interaction)

    async def _sections_selected(self, interaction: discord.Interaction) -> None:
        if await self._ensure_eligible(interaction) is None:
            return
        if self.selected_level is None or self.selected_stream is None:
            await interaction.response.send_message("❌ Choisissez d'abord le niveau et la filière.")
            return
        try:
            sections = sorted({int(value) for value in self.section_select.values})
        except (TypeError, ValueError):
            sections = []
        if (
            not sections
            or any(section < 1 or section > 8 for section in sections)
            or any(section not in self.available_sections for section in sections)
        ):
            await interaction.response.send_message(
                "❌ Sélectionnez uniquement les sections réellement disponibles dans la liste. "
                "Les sections non importées ne peuvent pas être affectées."
            )
            return
        self.selected_sections = sections
        self.section_select.options = [
            discord.SelectOption(
                label=f"Section {section}",
                value=str(section),
                default=section in sections,
            )
            for section in self.available_sections
        ]
        self._refresh_control_state()
        await self._edit_form(interaction)

    async def _continue_to_subjects(self, interaction: discord.Interaction) -> None:
        if await self._ensure_eligible(interaction) is None:
            return
        if not (
            self.selected_gender in {"male", "female"}
            and self.selected_level in get_levels()
            and self.selected_stream in get_streams(self.selected_level)
            and self.selected_sections
        ):
            await interaction.response.send_message("❌ Choisissez le type, le niveau, la filière et au moins une section.")
            return
        if not self.available_sections or any(
            section not in self.available_sections for section in self.selected_sections
        ):
            await interaction.response.send_message(
                "❌ Cette filière ne contient aucune section active issue de l'import scolaire. "
                "Changez de filière ou demandez à l'administration de vérifier l'import XLSX."
            )
            return
        subjects = get_stream_subjects(self.selected_level, self.selected_stream)
        if not subjects:
            await interaction.response.send_message("❌ Aucune matière n'est configurée pour cette filière.")
            return
        assigned_subjects = self._assigned_subject_keys()
        eligible_subjects = [
            subject for subject in subjects
            if get_subject_display_name(subject).casefold() in assigned_subjects or len(assigned_subjects) < 2
        ]
        if not eligible_subjects:
            await interaction.response.send_message(
                "❌ Vous avez déjà choisi deux matières pour cette inscription. "
                "Pour ajouter une autre classe, elle doit proposer au moins une de ces deux matières; "
                "sinon utilisez **Recommencer** pour corriger vos choix non validés."
            )
            return
        self.subject_select.options = [
            discord.SelectOption(
                label=get_subject_display_name(subject)[:100],
                value=subject,
                description=subject[:100] if get_subject_display_name(subject) != subject else None,
            )
            for subject in eligible_subjects
        ]
        self.subject_select.min_values = 1
        self.subject_select.max_values = min(2, len(eligible_subjects))
        self.subject_select.placeholder = "Choisissez au maximum 2 matières"
        self.selected_subjects = []
        self._show_subject_stage()
        await self._edit_form(interaction)

    async def _subjects_selected(self, interaction: discord.Interaction) -> None:
        if await self._ensure_eligible(interaction) is None:
            return
        if self.stage != "subjects" or self.selected_level is None or self.selected_stream is None:
            await interaction.response.send_message("❌ Choisissez d'abord une classe.")
            return
        allowed = set(get_stream_subjects(self.selected_level, self.selected_stream))
        selected = [value for value in self.subject_select.values if value in allowed]
        if not selected:
            await interaction.response.send_message("❌ Choisissez au moins une matière valide.")
            return
        if len(selected) > 2:
            await interaction.response.send_message("❌ Vous pouvez sélectionner deux matières au maximum.")
            return
        all_subject_keys = self._assigned_subject_keys() | {
            get_subject_display_name(value).casefold() for value in selected
        }
        if len(all_subject_keys) > 2:
            await interaction.response.send_message(
                "❌ Vous pouvez choisir au maximum **deux matières au total** pour toute l'inscription, "
                "même si vous ajoutez plusieurs classes. Gardez une matière déjà choisie ou appuyez sur Recommencer."
            )
            return
        self.selected_subjects = selected
        self.subject_select.options = [
            discord.SelectOption(
                label=get_subject_display_name(str(option.value))[:100],
                value=str(option.value),
                description=str(option.value)[:100] if get_subject_display_name(str(option.value)) != str(option.value) else None,
                default=str(option.value) in selected,
            )
            for option in self.subject_select.options
        ]
        self._refresh_control_state()
        await self._edit_form(interaction)

    async def _add_assignment(self, interaction: discord.Interaction) -> None:
        if await self._ensure_eligible(interaction) is None:
            return
        if self.stage != "subjects" or not (
            self.selected_level and self.selected_stream and self.selected_sections and self.selected_subjects
        ):
            await interaction.response.send_message("❌ Complétez la classe et ses matières avant de l'ajouter.")
            return
        self.assignments = self._merged_assignments(
            self.assignments,
            self.selected_sections,
            self.selected_subjects,
        )
        self.selected_level = None
        self.selected_stream = None
        self.selected_sections = []
        self.available_sections = []
        self.selected_subjects = []
        self.stream_select.options = [
            discord.SelectOption(label="Choisissez un niveau d'abord", value="__disabled__")
        ]
        self.section_select.options = [
            discord.SelectOption(label="Choisissez un niveau et une filière", value="__disabled__")
        ]
        self.section_select.min_values = 1
        self.section_select.max_values = 1
        self.section_select.disabled = True
        self.subject_select.options = [
            discord.SelectOption(label="Choisissez une filière d'abord", value="__disabled__")
        ]
        self._show_class_stage()
        await self._edit_form(interaction)

    async def _back_to_class(self, interaction: discord.Interaction) -> None:
        if await self._ensure_eligible(interaction) is None:
            return
        if self.stage != "subjects":
            await self._respond(interaction, "ℹ️ Vous êtes déjà à l'étape de sélection de la classe.")
            return
        self.selected_subjects = []
        self.subject_select.options = [
            discord.SelectOption(
                label=get_subject_display_name(subject)[:100],
                value=subject,
            )
            for subject in (
                get_stream_subjects(self.selected_level, self.selected_stream)
                if self.selected_level and self.selected_stream
                else []
            )
        ]
        self._show_class_stage()
        await interaction.response.edit_message(content=self._summary_text(), view=self)

    async def _reset_form(self, interaction: discord.Interaction) -> None:
        if await self._ensure_eligible(interaction) is None:
            return
        if self.submitting:
            await self._respond(interaction, "⏳ L'inscription est en cours; attendez le résultat avant de réinitialiser.")
            return
        self.selected_gender = None
        self.selected_level = None
        self.selected_stream = None
        self.selected_sections = []
        self.available_sections = []
        self.selected_subjects = []
        self.assignments = []
        self.gender_select.options = [
            discord.SelectOption(label="Prof", value="male", description="Rôle professeur"),
            discord.SelectOption(label="Prof (F)", value="female", description="Rôle professeur féminin"),
        ]
        self.level_select.options = [
            discord.SelectOption(label=level, value=level) for level in get_levels()
        ]
        self.stream_select.options = [
            discord.SelectOption(label="Choisissez un niveau d'abord", value="__disabled__")
        ]
        self.section_select.options = [
            discord.SelectOption(label="Choisissez un niveau et une filière", value="__disabled__")
        ]
        self.section_select.min_values = 1
        self.section_select.max_values = 1
        self.subject_select.options = [
            discord.SelectOption(label="Choisissez une filière d'abord", value="__disabled__")
        ]
        self.subject_select.min_values = 1
        self.subject_select.max_values = 1
        self.subject_select.placeholder = "Choisissez au maximum 2 matières"
        self._show_class_stage()
        await interaction.response.edit_message(
            content="🔄 **Formulaire réinitialisé.** Vos choix non validés ont été effacés.\n\n"
            + self._summary_text(),
            view=self,
        )

    async def _confirm_registration(self, interaction: discord.Interaction) -> None:
        stage = "validation"
        registration_completed = False
        if self.submitting:
            await interaction.response.send_message("⏳ Une inscription est déjà en cours.")
            return
        if self.stage != "subjects" or not (
            self.selected_gender in {"male", "female"}
            and self.selected_level
            and self.selected_stream
            and self.selected_sections
            and self.selected_subjects
        ):
            await interaction.response.send_message("❌ Complétez la classe et sélectionnez au moins une matière avant de valider.")
            return

        assignment_payload = self._merged_assignments(
            self.assignments,
            self.selected_sections,
            self.selected_subjects,
        )
        if not assignment_payload:
            await interaction.response.send_message("❌ Ajoutez au moins une affectation valide.")
            return
        all_subject_keys = self._assigned_subject_keys(assignment_payload)
        if self.selected_subjects:
            all_subject_keys.update(
                get_subject_display_name(subject).casefold() for subject in self.selected_subjects
            )
        if len(all_subject_keys) > 2:
            await interaction.response.send_message(
                "❌ Une inscription professeur ne peut contenir que deux matières distinctes au maximum. "
                "Appuyez sur Recommencer pour corriger les choix."
            )
            return

        try:
            available_by_stream: dict[tuple[str, str], list[int]] = {}
            for item in assignment_payload:
                pair = (str(item["level"]), str(item["stream"]))
                if pair not in available_by_stream:
                    available_by_stream[pair] = get_active_sections_for_stream(
                        self.guild_id, pair[0], pair[1]
                    )
                if int(item["section"]) not in available_by_stream[pair]:
                    await interaction.response.send_message(
                        "❌ Une des sections choisies n'existe plus dans l'import scolaire actif "
                        f"({get_stream_abbreviation(str(item['level']), str(item['stream']))}-S{item['section']}). "
                        "Utilisez **Retour à la classe** ou **Recommencer** pour corriger les choix."
                    )
                    return
        except (OSError, sqlite3.Error, ValueError):
            logging.getLogger(__name__).exception(
                "Unable to revalidate teacher sections before registration (guild=%s member=%s)",
                self.guild_id, self.user_id,
            )
            await interaction.response.send_message(
                "❌ Impossible de revérifier les sections de l'import scolaire. Aucune inscription n'a été enregistrée; réessayez plus tard."
            )
            return

        primary = assignment_payload[0]
        primary_subjects = primary["subjects"]
        if not isinstance(primary_subjects, list) or not primary_subjects:
            await interaction.response.send_message("❌ La première affectation ne contient aucune matière.")
            return

        self.submitting = True
        for item in self.children:
            item.disabled = True
        try:
            await interaction.response.edit_message(
                content=(
                    "⏳ **Inscription en cours...**\n\n"
                    "Le bot vérifie toutes vos affectations, attribue vos rôles et prépare les threads publics. "
                    "Ne cliquez pas plusieurs fois."
                ),
                view=self,
            )
        except discord.HTTPException:
            self.submitting = False
            self._refresh_control_state()
            raise

        try:
            stage = "member-validation"
            member = await self._ensure_eligible(interaction)
            if member is None:
                self.submitting = False
                self._refresh_control_state()
                return

            stage = "assignment"
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
                        level=str(primary["level"]),
                        stream=str(primary["stream"]),
                        subjects=", ".join(str(subject) for subject in primary_subjects),
                        actor_id=member.id,
                        actor_display_name=member.display_name,
                        self_registration=True,
                        assignments=assignment_payload,
                    )
                    registration_completed = True
                except TeacherAssignmentError as exc:
                    self.submitting = False
                    self._refresh_control_state()
                    await self._update_interaction_message(
                        interaction,
                        content=f"{exc}\n\nVous pouvez corriger vos choix puis réessayer.",
                        view=self,
                    )
                    return

            # Registration and assignments are now committed. Thread provisioning
            # is separate: its failure must not misreport a completed registration.
            stage = "thread-provisioning"
            thread_sync_interrupted = False
            try:
                async with get_build_lock(self.guild_id):
                    thread_result = await ensure_teacher_threads(member.guild, member.id)
            except Exception as exc:
                thread_sync_interrupted = True
                thread_result = {
                    "created": 0,
                    "existing": 0,
                    "failed": max(
                        1,
                        sum(
                            len(item.get("subjects", []))
                            for item in assignment_payload
                            if isinstance(item.get("subjects"), list)
                        ),
                    ),
                    "errors": [f"{type(exc).__name__}: {exc}"],
                }
                print(
                    f"[TEACHER THREAD PROVISIONING ERROR] guild={self.guild_id} user={self.user_id} "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
                traceback.print_exc()

            created = int(thread_result.get("created", 0))
            existing = int(thread_result.get("existing", 0))
            failed = int(thread_result.get("failed", 0))
            thread_errors = thread_result.get("errors", [])
            if failed and isinstance(thread_errors, list):
                print(
                    f"[TEACHER THREAD PROVISIONING PARTIAL] guild={self.guild_id} user={self.user_id} "
                    f"failed={failed} errors={thread_errors[:10]}",
                    flush=True,
                )
            assignment_count = int(result.get("assignments_count", 0))
            assignment_summary = str(result.get("assignment_summary", ""))
            if len(assignment_summary) > 1050:
                assignment_summary = assignment_summary[:1020].rstrip() + "…\n(Le détail complet des affectations est enregistré.)"
            if thread_sync_interrupted:
                thread_status = (
                    "⚠️ **La synchronisation des threads a été interrompue.** Certains threads peuvent déjà exister; "
                    "l'administration doit relancer /sync-teacher-threads pour vérifier et terminer la synchronisation."
                )
            elif failed:
                thread_status = (
                    f"⚠️ **{failed} thread(s) à vérifier.** Votre inscription reste enregistrée. "
                    "L'administration peut relancer la synchronisation avec /sync-teacher-threads."
                )
            else:
                thread_status = (
                    f"✅ **Threads publics prêts : {created + existing}.** "
                    f"Nouveaux : {created} · Déjà existants : {existing}."
                )

            stage = "final-response"
            for item in self.children:
                item.disabled = True
            self.submitting = True
            await self._update_interaction_message(
                interaction,
                content=(
                    "✅ **Inscription terminée !**\n\n"
                    f"**Affectations enregistrées :** {assignment_count}\n"
                    f"{assignment_summary}\n\n"
                    f"{thread_status}\n\n"
                    "Le rôle temporaire **Professeur - En attente** a été retiré."
                ),
                view=self,
            )
        except Exception as exc:
            self.submitting = False
            self._refresh_control_state()
            try:
                if registration_completed:
                    error_content = (
                        "✅ **Votre inscription est enregistrée.**\n\n"
                        "La mise à jour du message de confirmation a échoué. Ne recommencez pas l'inscription QR. "
                        "L'administration peut vérifier les affectations et relancer /sync-teacher-threads."
                    )
                else:
                    error_content = (
                        "❌ **Erreur technique pendant l'inscription.**\n\n"
                        f"Étape concernée : **{stage}**\n"
                        "Vérifiez les logs du bot avant de relancer l'opération."
                    )
                await self._update_interaction_message(
                    interaction,
                    content=error_content,
                    view=self,
                )
            except discord.HTTPException:
                pass
            print(
                f"[TEACHER QR UI ERROR] guild={self.guild_id} user={self.user_id} "
                f"stage={stage} {type(exc).__name__}: {exc}",
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
        traceback.print_exception(error)
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
) -> bool:
    view = TeacherOnboardingView(bot, user.guild.id, user.id)
    try:
        view.message = await user.send(
            "## 👨‍🏫 Inscription professeur\n\n"
            "Bienvenue ! Sélectionnez tous les niveaux, filières, sections et matières que vous enseignez.\n\n"
            "Vous pouvez choisir au maximum deux matières distinctes pour toute l'inscription, même si vous enseignez plusieurs classes. Les sections proposées viennent des élèves "
            "importés pour l'année scolaire active. Utilisez **Retour à la classe** pour corriger un choix ou "
            "**Recommencer** pour effacer vos choix non validés. Vous pouvez aussi lancer `/restartteacherregistration` "
            "dans le serveur pour recevoir un nouveau formulaire.\n\n"
            "Les choix proviennent du programme scolaire configuré. Après validation, le bot attribuera vos rôles "
            "et préparera automatiquement les threads publics de vos sections dans les salons des matières.",
            view=view,
        )
    except discord.HTTPException:
        return False

    registry = getattr(bot, "_teacher_onboarding_views", None)
    if not isinstance(registry, dict):
        registry = {}
        setattr(bot, "_teacher_onboarding_views", registry)
    key = (user.guild.id, user.id)
    previous = registry.get(key)
    registry[key] = view
    if previous is not None and previous is not view:
        previous.stop()
        for item in previous.children:
            item.disabled = True
        previous_message = getattr(previous, "message", None)
        if previous_message is not None:
            try:
                await previous_message.edit(
                    content="ℹ️ Ce formulaire a été remplacé par un formulaire plus récent. Utilisez le dernier message privé reçu.",
                    view=previous,
                )
            except discord.HTTPException:
                pass
    return True


async def _restore_registered_teacher_access(
    member: discord.Member,
    registration,
) -> tuple[bool, str]:
    """Reconcile Discord roles from the trusted persisted teacher registration."""
    conflict = teacher_target_conflict(member, member.guild)
    if conflict:
        return False, conflict

    try:
        stored_assignments = get_teacher_assignments(member.guild.id, member.id)
        assignments = [
            {
                "level": str(row["level_name"]),
                "stream": str(row["stream_name"]),
                "section": int(row["section"]),
                "subjects": [str(row["subject_name"])],
            }
            for row in stored_assignments
        ] or None
        await execute_teacher_assignment(
            guild=member.guild,
            teacher=member,
            gender_value=str(registration["profile_type"]),
            level=str(registration["level_name"]),
            stream=str(registration["stream_name"]),
            subjects=str(registration["subjects_text"]),
            actor_id=member.id,
            actor_display_name=member.display_name,
            self_registration=False,
            assignments=assignments,
            event_action=None,
        )
    except (TeacherAssignmentError, sqlite3.Error, OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        return False, str(exc)

    pending_role = get_managed_role(member.guild, ROLE_TEACHER_PENDING)
    if pending_role is not None and pending_role in member.roles:
        try:
            await member.remove_roles(
                pending_role,
                reason="School Manager restored registered teacher access",
            )
        except discord.HTTPException as exc:
            logging.getLogger(__name__).warning(
                "Teacher access restored but pending role could not be removed (guild=%s member=%s): %s",
                member.guild.id,
                member.id,
                exc,
            )
            return True, "Les accès ont été rétablis, mais le rôle temporaire n'a pas pu être retiré automatiquement."

    return True, "Les rôles professeur ont été rétablis."


async def _ensure_pending_teacher_role(member: discord.Member) -> discord.Role:
    """Ensure the member has the managed pending role required by the onboarding form."""
    guild = member.guild
    working_config = deepcopy(get_guild_config(guild.id) or {})
    pending_role = get_managed_role(guild, ROLE_TEACHER_PENDING)
    if pending_role is None:
        pending_role, created_role = await _get_or_create_pending_role(guild, working_config)
        if created_role:
            save_guild_config(guild.id, working_config)
    if pending_role not in member.roles:
        await member.add_roles(
            pending_role,
            reason="School Manager teacher re-enrollment",
        )
    return pending_role


async def _remove_registered_teacher_roles(
    member: discord.Member,
    registration,
    assignments,
) -> int:
    """Remove only managed teacher roles from the old profile before re-enrollment."""
    role_names = {ROLE_PROFESSOR, ROLE_PROFESSOR_FEMALE}
    stream_pairs: set[tuple[str, str]] = set()
    subjects: set[str] = {str(row["subject_name"]) for row in assignments}

    primary_level = str(registration["level_name"])
    primary_stream = str(registration["stream_name"])
    stream_pairs.add((primary_level, primary_stream))
    for row in assignments:
        stream_pairs.add((str(row["level_name"]), str(row["stream_name"])))

    # Older registrations may have only the primary subjects_text field and no
    # teacher_assignments rows. Reconstruct those canonical subject roles too.
    selected_labels = {
        value.strip().casefold()
        for value in str(registration["subjects_text"]).split(",")
        if value.strip()
    }
    for subject in get_stream_subjects(primary_level, primary_stream):
        display_name = get_subject_display_name(subject)
        if subject.casefold() in selected_labels or display_name.casefold() in selected_labels:
            subjects.add(subject)

    for level_name, stream_name in stream_pairs:
        role_names.add(f"{STREAM_ROLE_PREFIX}{get_stream_abbreviation(level_name, stream_name)}")
    for subject in subjects:
        role_names.add(f"{SUBJECT_ROLE_PREFIX}{get_subject_display_name(subject)}"[:100])

    roles_to_remove = [
        role
        for name in sorted(role_names)
        if (role := get_managed_role(member.guild, name)) is not None and role in member.roles
    ]
    if roles_to_remove:
        await member.remove_roles(
            *roles_to_remove,
            reason="School Manager: remove old teacher profile before re-enrollment",
        )
    return len(roles_to_remove)


async def _prepare_teacher_reenrollment(
    bot: discord.Client,
    member: discord.Member,
) -> dict[str, int | bool]:
    """Reset any old teacher profile and get the member ready for a fresh form."""
    guild = member.guild
    async with get_build_lock(guild.id):
        registration = get_teacher_registration(guild.id, member.id)
        assignments = get_teacher_assignments(guild.id, member.id) if registration is not None else []

        # Assign the pending role before destructive changes. If Discord refuses,
        # the old registration remains intact and an administrator can retry.
        await _ensure_pending_teacher_role(member)

        removed_roles = 0
        if registration is not None:
            removed_roles = await _remove_registered_teacher_roles(member, registration, assignments)
            delete_teacher_registration(guild.id, member.id)

        # The rows are already captured, so clean up linked threads after the DB
        # reset. Thread API failures are reported but do not restore stale DB data.
        archived_count, thread_failures = await _archive_teacher_assignment_threads(
            bot, guild, member.id, assignments
        )

    return {
        "had_registration": registration is not None,
        "assignments": len(assignments),
        "roles_removed": removed_roles,
        "threads_archived": archived_count,
        "thread_failures": thread_failures,
    }


async def _sync_registered_teacher_threads(guild: discord.Guild, teacher_id: int) -> str:
    """Ensure the current persisted assignments have usable public threads."""
    try:
        async with get_build_lock(guild.id):
            result = await ensure_teacher_threads(guild, teacher_id)
    except Exception as exc:
        logging.getLogger(__name__).exception(
            "Unable to synchronize teacher threads (guild=%s member=%s)",
            guild.id,
            teacher_id,
        )
        return (
            "⚠️ La synchronisation des threads n'a pas pu être terminée. "
            "L'administration peut vérifier puis relancer /sync-teacher-threads. "
            f"Détail : {type(exc).__name__}."
        )

    created = int(result.get("created", 0))
    existing = int(result.get("existing", 0))
    failed = int(result.get("failed", 0))
    if failed:
        return (
            f"⚠️ {failed} thread(s) n'ont pas pu être préparés. "
            "L'administration peut vérifier puis relancer /sync-teacher-threads."
        )
    if created + existing == 0:
        return "ℹ️ Aucun thread à synchroniser : aucune affectation de section/matière n'est enregistrée."
    return f"✅ Threads professeur prêts : {created + existing} (nouveaux : {created}, existants : {existing})."


async def _archive_teacher_assignment_threads(
    bot: discord.Client,
    guild: discord.Guild,
    teacher_id: int,
    assignments,
) -> tuple[int, int]:
    """Archive and lock only the public threads recorded for this teacher."""
    archived_count = 0
    failure_count = 0
    seen: set[int] = set()
    logger = logging.getLogger(__name__)

    for assignment in assignments:
        raw_thread_id = assignment["thread_id"]
        if not isinstance(raw_thread_id, int) or raw_thread_id <= 0 or raw_thread_id in seen:
            continue
        seen.add(raw_thread_id)
        try:
            thread = guild.get_thread(raw_thread_id) if hasattr(guild, "get_thread") else None
            if thread is None and hasattr(bot, "get_channel"):
                thread = bot.get_channel(raw_thread_id)
            if thread is None and hasattr(bot, "fetch_channel"):
                thread = await bot.fetch_channel(raw_thread_id)
            if thread is None:
                failure_count += 1
                logger.warning(
                    "Teacher thread unavailable during departure cleanup (guild=%s member=%s thread=%s)",
                    guild.id, teacher_id, raw_thread_id,
                )
                continue
            if getattr(thread, "type", None) != discord.ChannelType.public_thread:
                logger.warning(
                    "Stored teacher thread ID is not a public thread (guild=%s thread=%s)",
                    guild.id, raw_thread_id,
                )
                failure_count += 1
                continue
            if not getattr(thread, "archived", False) or not getattr(thread, "locked", False):
                await thread.edit(
                    archived=True,
                    locked=True,
                    reason="School Manager: archive teacher assignment after leaving the server",
                )
            archived_count += 1
        except discord.NotFound:
            # A deleted Discord thread is already cleaned up; deleting its DB row is safe.
            continue
        except Exception as exc:
            failure_count += 1
            logger.warning(
                "Could not archive teacher thread during departure cleanup (guild=%s thread=%s): %s: %s",
                guild.id, raw_thread_id, type(exc).__name__, exc,
            )
    return archived_count, failure_count


class TeacherQROnboarding(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="restartteacherregistration",
        description="Renvoyer votre formulaire professeur si vous avez fait une erreur avant de valider.",
    )
    @app_commands.guild_only()
    async def restart_teacher_registration(self, interaction: discord.Interaction) -> None:
        """Resend onboarding only for the invoking member's incomplete registration."""
        guild = interaction.guild
        member = interaction.user
        if guild is None or getattr(member, "guild", None) is None:
            await interaction.response.send_message(
                "❌ Utilisez cette commande dans le serveur, pas dans les messages privés.",
                ephemeral=True,
            )
            return
        if member.bot:
            await interaction.response.send_message("❌ Un bot ne peut pas démarrer une inscription professeur.", ephemeral=True)
            return
        try:
            registration = get_teacher_registration(guild.id, member.id)
        except (OSError, sqlite3.Error):
            logging.getLogger(__name__).exception(
                "Unable to check teacher registration before self-restart (guild=%s member=%s)",
                guild.id, member.id,
            )
            await interaction.response.send_message(
                "❌ Impossible de vérifier votre dossier. Contactez l'administration avant de réessayer.",
                ephemeral=True,
            )
            return
        if registration is not None:
            await interaction.response.send_message(
                "ℹ️ Votre inscription est déjà enregistrée. Pour protéger vos affectations et threads, "
                "seule l'administration peut réinitialiser un dossier terminé avec "
                "/startteacherregistration en activant `restart`.",
                ephemeral=True,
            )
            return

        pending_role = get_managed_role(guild, ROLE_TEACHER_PENDING)
        professor_roles = {
            role
            for role in (
                get_managed_role(guild, ROLE_PROFESSOR),
                get_managed_role(guild, ROLE_PROFESSOR_FEMALE),
            )
            if role is not None
        }
        if pending_role is None or pending_role not in member.roles or any(
            role in member.roles for role in professor_roles
        ):
            await interaction.response.send_message(
                "❌ Votre compte n'est pas dans un état d'inscription incomplète. "
                "Demandez à l'administration de vérifier le rôle `Professeur - En attente`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        dm_sent = await _send_teacher_onboarding_prompt(self.bot, member)
        record_event(
            guild.id,
            member.id,
            member.display_name,
            "selfrestartteacherregistration",
            member.display_name,
            "dm_sent=" + str(dm_sent).lower(),
        )
        if dm_sent:
            await interaction.followup.send(
                "✅ Je vous ai renvoyé un formulaire propre en message privé. Vous pouvez aussi utiliser "
                "les boutons **Retour à la classe** et **Recommencer** dans le formulaire. "
                "Vos choix n'avaient pas été enregistrés; aucun départ du serveur n'est nécessaire.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "⚠️ Je n'ai pas pu vous envoyer le formulaire en privé. Autorisez les messages privés du serveur "
                "puis relancez `/restartteacherregistration`. Votre inscription reste en attente.",
                ephemeral=True,
            )

    @app_commands.command(
        name="startteacherregistration",
        description="Envoyer le formulaire professeur à un membre déjà présent dans le serveur.",
    )
    @app_commands.describe(
        teacher="Membre du serveur à inscrire ou dont le dossier professeur doit être réinitialisé",
        restart="Réinitialiser son ancien profil professeur et renvoyer le formulaire",
    )
    @app_commands.default_permissions(manage_roles=True)
    @management_check()
    async def start_teacher_registration(
        self,
        interaction: discord.Interaction,
        teacher: discord.Member,
        restart: bool = False,
    ) -> None:
        """Start onboarding for a selected existing member without requiring a leave/rejoin."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return
        if teacher.bot:
            await interaction.response.send_message("❌ Un bot ne peut pas être inscrit comme professeur.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            registration = get_teacher_registration(guild.id, teacher.id)
        except sqlite3.Error as exc:
            await interaction.followup.send(
                "❌ Impossible de lire le dossier professeur dans la base de données : "
                + type(exc).__name__ + ". Réessayez après vérification des logs.",
                ephemeral=True,
            )
            return
        if restart:
            try:
                summary = await _prepare_teacher_reenrollment(self.bot, teacher)
            except (discord.Forbidden, discord.HTTPException, OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
                logging.getLogger(__name__).exception(
                    "Unable to restart teacher registration (guild=%s member=%s)",
                    guild.id, teacher.id,
                )
                await interaction.followup.send(
                    "❌ La préparation de la réinscription a échoué : "
                    f"{type(exc).__name__}: {exc}. Certains changements peuvent déjà avoir été appliqués; "
                    "consultez les logs et vérifiez le dossier avant de réessayer.",
                    ephemeral=True,
                )
                return

            dm_sent = await _send_teacher_onboarding_prompt(self.bot, teacher)
            record_event(
                guild.id,
                interaction.user.id,
                interaction.user.display_name,
                "restartteacherregistration",
                teacher.display_name,
                "had_registration=" + str(bool(summary["had_registration"])).lower()
                + ";assignments=" + str(summary["assignments"])
                + ";threads_archived=" + str(summary["threads_archived"])
                + ";thread_failures=" + str(summary["thread_failures"]),
            )
            if dm_sent:
                detail = (
                    " Anciennes affectations supprimées : " + str(summary["assignments"]) + "."
                    + " Rôles professeur retirés : " + str(summary["roles_removed"]) + "."
                    + " Threads archivés : " + str(summary["threads_archived"]) + "."
                )
                if int(summary["thread_failures"]):
                    detail += " Attention : certains threads n'ont pas pu être archivés; consultez les logs."
                await interaction.followup.send(
                    f"✅ Le formulaire de réinscription a été envoyé à {teacher.mention}. "
                    "L'ancienne inscription a été réinitialisée. Aucun départ du serveur n'est nécessaire."
                    + detail,
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"⚠️ Le profil a été réinitialisé et le rôle temporaire préparé pour {teacher.mention}, "
                    "mais l'envoi du DM a échoué. Autorisez les messages privés du serveur puis relancez "
                    "/startteacherregistration avec restart activé.",
                    ephemeral=True,
                )
            return

        if registration is not None:
            restored, detail = await _restore_registered_teacher_access(teacher, registration)
            thread_detail = await _sync_registered_teacher_threads(guild, teacher.id) if restored else ""
            if restored:
                dm_sent = True
                try:
                    await teacher.send(
                        "✅ Votre inscription professeur existante a été retrouvée. "
                        "Les accès de votre compte ont été vérifiés et rétablis par l'administration."
                    )
                except discord.HTTPException:
                    dm_sent = False
                message = "✅ L'inscription existe déjà et les rôles professeur ont été rétablis."
                if "mais" in detail:
                    message += "\n⚠️ " + detail
                if thread_detail:
                    message += "\n" + thread_detail
                if not dm_sent:
                    message += "\nℹ️ Les accès ont été rétablis, mais le DM du professeur est fermé."
            else:
                message = (
                    "⚠️ Un dossier professeur existe déjà, mais les accès n'ont pas été modifiés automatiquement. "
                    "Vérifiez le conflit ou les rôles gérés avant toute correction.\n\n" + detail
                )
            record_event(
                guild.id,
                interaction.user.id,
                interaction.user.display_name,
                "startteacherregistration",
                teacher.display_name,
                "existing-registration;restored=" + str(restored).lower(),
            )
            await interaction.followup.send(message, ephemeral=True)
            return

        professor_roles = {
            role
            for role in (
                get_managed_role(guild, ROLE_PROFESSOR),
                get_managed_role(guild, ROLE_PROFESSOR_FEMALE),
            )
            if role is not None
        }
        if any(role in teacher.roles for role in professor_roles):
            await interaction.followup.send(
                "❌ Ce membre possède déjà un rôle professeur, mais aucun dossier d'inscription QR n'a été trouvé. "
                "Aucune nouvelle inscription n'a été lancée; vérifiez son affectation existante.",
                ephemeral=True,
            )
            return

        conflict = teacher_target_conflict(teacher, guild)
        if conflict:
            await interaction.followup.send(conflict, ephemeral=True)
            return

        working_config = deepcopy(get_guild_config(guild.id) or {})
        pending_role: discord.Role | None = None
        try:
            pending_role = get_managed_role(guild, ROLE_TEACHER_PENDING)
            if pending_role is None:
                pending_role, created_role = await _get_or_create_pending_role(guild, working_config)
                if created_role:
                    save_guild_config(guild.id, working_config)
            if pending_role not in teacher.roles:
                await teacher.add_roles(
                    pending_role,
                    reason="School Manager administrator-triggered teacher onboarding",
                )
        except (discord.Forbidden, discord.HTTPException, OSError, RuntimeError, ValueError) as exc:
            await interaction.followup.send(
                "❌ Impossible de préparer l'inscription de ce membre : " + type(exc).__name__ + ": " + str(exc),
                ephemeral=True,
            )
            return

        dm_sent = await _send_teacher_onboarding_prompt(self.bot, teacher)
        record_event(
            guild.id,
            interaction.user.id,
            interaction.user.display_name,
            "startteacherregistration",
            teacher.display_name,
            "dm_sent=" + str(dm_sent).lower(),
        )
        if dm_sent:
            await interaction.followup.send(
                f"✅ Le formulaire d'inscription a été envoyé à {teacher.mention}. Aucun départ/réintégration du serveur n'est nécessaire.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"⚠️ Le rôle temporaire a été préparé pour {teacher.mention}, mais son DM a échoué. "
                "Demandez-lui d'autoriser les messages privés du serveur, puis relancez cette commande.",
                ephemeral=True,
            )

    @app_commands.command(
        name="createadminqr",
        description="Créer un QR d'invitation à usage unique pour le directeur.",
    )
    @owner_only_check(lock=False)
    async def create_admin_qr(self, interaction: discord.Interaction) -> None:
        """Create a short-lived one-use invite that grants the configured Administration role."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Serveur requis.", ephemeral=True)
            return

        bot_member = guild.me
        if bot_member is None or not bot_member.guild_permissions.create_instant_invite:
            await interaction.response.send_message(
                "❌ Le bot doit avoir la permission **Create Instant Invite**.",
                ephemeral=True,
            )
            return
        if not bot_member.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "❌ Le bot doit avoir la permission **Manage Roles** pour l'invitation avec rôle.",
                ephemeral=True,
            )
            return

        admin_role = get_managed_role(guild, ROLE_ADMIN)
        if admin_role is None or admin_role.managed or admin_role.is_default():
            await interaction.response.send_message(
                "❌ Le rôle Administration géré par School Manager est introuvable. Lance /setup puis /build avant de créer ce QR.",
                ephemeral=True,
            )
            return
        if admin_role >= bot_member.top_role:
            await interaction.response.send_message(
                "❌ Le rôle du bot doit être placé au-dessus du rôle Administration dans la hiérarchie Discord.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        config = get_guild_config(guild.id) or {}
        try:
            channel = await resolve_registered_text_channel(
                guild,
                config,
                channel_name=GENERAL_CHANNELS["actualites"],
                category_name="🏢・INFORMATIONS & ADMINISTRATION",
            )
            if channel is None:
                raise RuntimeError("Le salon d'actualités institutionnelles est introuvable dans le registre.")

            # One use limits exposure; two hours allows time to travel to the school and present it.
            max_age = 2 * 60 * 60
            invite_url = await create_role_invite(
                self.bot,
                channel,
                (admin_role,),
                max_age=max_age,
                max_uses=1,
                reason="School Manager one-time Administration invite for school director",
            )
            qr = qr_file(invite_url, "school-manager-administration.png")
        except (discord.Forbidden, discord.HTTPException, OSError, RuntimeError, ValueError) as exc:
            await interaction.followup.send(
                "❌ QR Administration non créé : " + type(exc).__name__ + ": " + str(exc),
                ephemeral=True,
            )
            return

        record_event(
            guild.id,
            interaction.user.id,
            interaction.user.display_name,
            "createadminqr",
            "administration-onboarding",
            "expires=7200s;max_uses=1",
        )
        await interaction.followup.send(
            "## ✅ QR Administration prêt\n\n"
            "Le lien attribue automatiquement le rôle `Administration` quand une personne rejoint le serveur.\n"
            "**Expiration : 2 heures · Utilisation : une seule fois.**\n\n"
            "⚠️ Le QR est un lien secret : montre-le uniquement au directeur. La première personne qui l'utilise recevra le rôle Administration.\n\n"
            f"Lien de secours : {invite_url}",
            file=qr,
            ephemeral=True,
        )

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
            "Si le professeur est déjà membre du serveur, utilisez `/startteacherregistration` avec son compte : "
            "il n'a pas besoin de quitter puis de revenir.\n\n"
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

        # A stale registration row must never cause automatic restoration after
        # departure; use it only to clean the old profile and start fresh onboarding.
        try:
            registration = get_teacher_registration(member.guild.id, member.id)
        except sqlite3.Error:
            logging.getLogger(__name__).exception(
                "Unable to inspect teacher registration during member join (guild=%s member=%s)",
                member.guild.id,
                member.id,
            )
            return

        logger = logging.getLogger(__name__)
        pending_role = get_managed_role(member.guild, ROLE_TEACHER_PENDING)

        # A teacher who leaves and rejoins must register again even if the
        # temporary role was not present in the join event. Never silently restore
        # an old profile just because Discord did not attach that role to the invite.
        if registration is not None:
            try:
                summary = await _prepare_teacher_reenrollment(self.bot, member)
            except (discord.Forbidden, discord.HTTPException, OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
                logger.exception(
                    "Unable to restart stale teacher registration during member join (guild=%s member=%s)",
                    member.guild.id, member.id,
                )
                try:
                    await member.send(
                        "❌ Votre ancien dossier professeur existe toujours, mais le bot n'a pas pu "
                        "préparer la réinscription. Contactez l'administration sans quitter le serveur. "
                        f"Détail : {type(exc).__name__}."
                    )
                except discord.HTTPException:
                    pass
                return

            record_event(
                member.guild.id,
                member.id,
                member.display_name,
                "teacher_registration_reset_on_rejoin",
                str(member.id),
                "assignments=" + str(summary["assignments"])
                + ";roles_removed=" + str(summary["roles_removed"])
                + ";threads_archived=" + str(summary["threads_archived"])
                + ";thread_failures=" + str(summary["thread_failures"]),
            )
            logger.warning(
                "Reset existing teacher profile on rejoin and restarting onboarding "
                "(guild=%s member=%s had_registration=%s assignments=%s threads_archived=%s thread_failures=%s)",
                member.guild.id, member.id, summary["had_registration"], summary["assignments"],
                summary["threads_archived"], summary["thread_failures"],
            )
            if not await _send_teacher_onboarding_prompt(self.bot, member):
                logger.warning(
                    "Teacher re-enrollment form DM failed (guild=%s member=%s)",
                    member.guild.id, member.id,
                )
            return

        if pending_role is None or pending_role not in member.roles:
            return

        professor_roles = {
            role
            for role in (
                get_managed_role(member.guild, ROLE_PROFESSOR),
                get_managed_role(member.guild, ROLE_PROFESSOR_FEMALE),
            )
            if role is not None
        }
        if any(role in member.roles for role in professor_roles):
            try:
                await member.remove_roles(
                    pending_role,
                    reason="School Manager teacher QR conflicts with an existing professor role",
                )
                await member.send(
                    "⚠️ Ce compte possède déjà un rôle professeur mais aucun dossier QR correspondant. "
                    "Contactez l'administration pour vérifier l'affectation existante."
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

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Forget teacher onboarding data when the teacher leaves this guild."""
        logger = logging.getLogger(__name__)
        logger.info(
            "Discord member-remove event received (guild=%s member=%s bot=%s)",
            member.guild.id, member.id, member.bot,
        )
        if member.bot:
            return

        guild_id = member.guild.id
        try:
            async with get_build_lock(guild_id):
                registration = get_teacher_registration(guild_id, member.id)
                if registration is None:
                    return
                assignments = get_teacher_assignments(guild_id, member.id)

                # The member has already left; Discord has stripped guild roles.
                # Delete their identity/assignments first, then archive linked work.
                delete_teacher_registration(guild_id, member.id)
                archived_count, thread_failures = await _archive_teacher_assignment_threads(
                    self.bot, member.guild, member.id, assignments
                )
        except sqlite3.Error:
            logger.exception(
                "Unable to clear teacher registration during member departure (guild=%s member=%s)",
                guild_id, member.id,
            )
            return
        except Exception:
            logger.exception(
                "Unexpected teacher departure cleanup failure (guild=%s member=%s)",
                guild_id, member.id,
            )
            return

        record_event(
            guild_id,
            member.id,
            member.display_name,
            "teacher_registration_cleared_on_leave",
            str(member.id),
            f"assignments={len(assignments)};threads_archived={archived_count};thread_failures={thread_failures}",
        )
        logger.info(
            "Cleared teacher registration after departure (guild=%s member=%s assignments=%s "
            "threads_archived=%s thread_failures=%s)",
            guild_id, member.id, len(assignments), archived_count, thread_failures,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TeacherQROnboarding(bot))

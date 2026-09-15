"""Shared school-role conflict validation."""

from __future__ import annotations

import discord

from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_PROFESSOR_FEMALE,
    ROLE_STUDENT,
    STUDENT_STREAM_ROLE_PREFIX,
    get_managed_role,
)


def student_staff_conflict(member: discord.Member, guild: discord.Guild) -> str | None:
    """Return a user-facing conflict when staff roles block student assignment."""
    if member.bot:
        return "❌ Un bot ne peut pas recevoir un rôle scolaire."
    admin_role = get_managed_role(guild, ROLE_ADMIN)
    if admin_role is not None and admin_role in member.roles:
        return "❌ Cet utilisateur possède le rôle **Administration**. Retire d'abord ce rôle avant une affectation scolaire."
    professor_roles = {
        role
        for role in (
            get_managed_role(guild, ROLE_PROFESSOR),
            get_managed_role(guild, ROLE_PROFESSOR_FEMALE),
        )
        if role is not None
    }
    if any(role in member.roles for role in professor_roles):
        return "❌ Cet utilisateur possède encore un rôle **Prof**. Retire d'abord son rôle professeur avant de l'affecter comme élève."
    return None


def teacher_target_conflict(member: discord.Member, guild: discord.Guild) -> str | None:
    """Return a user-facing conflict when student/admin/bot state blocks teacher assignment."""
    if member.bot:
        return "❌ Un bot ne peut pas être enregistré comme professeur."
    admin_role = get_managed_role(guild, ROLE_ADMIN)
    if admin_role is not None and admin_role in member.roles:
        return "❌ Un membre du rôle **Administration** ne peut pas recevoir un rôle professeur."
    student_role = get_managed_role(guild, ROLE_STUDENT)
    if student_role is not None and student_role in member.roles:
        return "❌ Cet utilisateur possède encore le rôle **Élève**. Retire-le d'abord."
    if any(
        not role.managed and role.name.startswith(STUDENT_STREAM_ROLE_PREFIX)
        for role in member.roles
    ):
        return "❌ Cet utilisateur possède encore un rôle de filière **Élève**. Retire-le d'abord."
    return None

"""Shared school-role conflict validation."""

from __future__ import annotations

import discord

from config.curriculum import get_stream_abbreviation
from services.permissions import (
    ROLE_ADMIN,
    ROLE_PROFESSOR,
    ROLE_PROFESSOR_FEMALE,
    ROLE_STUDENT,
    STUDENT_STREAM_ROLE_PREFIX,
    get_managed_role,
)
from services.storage import get_guild_config


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

    config = get_guild_config(guild.id) or {}
    configured_student_stream_roles: set[int] = set()
    for level in config.get("levels", []) if isinstance(config, dict) else []:
        if not isinstance(level, dict) or not isinstance(level.get("name"), str):
            continue
        for stream in level.get("streams", []) or []:
            if not isinstance(stream, dict) or not isinstance(stream.get("name"), str):
                continue
            code = str(stream.get("abbreviation") or get_stream_abbreviation(level["name"], stream["name"]))
            role = get_managed_role(guild, f"{STUDENT_STREAM_ROLE_PREFIX}{code}")
            if role is not None:
                configured_student_stream_roles.add(role.id)

    if any(role.id in configured_student_stream_roles for role in member.roles):
        return "❌ Cet utilisateur possède encore un rôle de filière **Élève**. Retire-le d'abord."
    return None

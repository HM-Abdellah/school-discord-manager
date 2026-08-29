"""Centralized Discord permission-overwrite builders."""

from __future__ import annotations

import discord


ROLE_ADMIN = "Administration"
ROLE_PROFESSOR = "Professeur"
ROLE_STUDENT = "Élève"


def administrator_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        manage_messages=True,
        manage_channels=True,
        manage_permissions=True,
        manage_threads=True,
        connect=True,
        speak=True,
        stream=True,
    )


def professor_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        manage_messages=True,
        manage_threads=True,
        create_public_threads=True,
        create_private_threads=True,
        send_messages_in_threads=True,
        connect=True,
        speak=True,
        stream=True,
    )


def student_overwrite(*, can_send: bool = True) -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=can_send,
        read_message_history=True,
        create_public_threads=True,
        send_messages_in_threads=True,
        connect=True,
        speak=True,
    )


def hidden_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=False,
    )


def general_area_overwrites(
    everyone: discord.Role,
    admin_role: discord.Role,
    professor_role: discord.Role,
    student_role: discord.Role,
) -> dict[discord.Role, discord.PermissionOverwrite]:
    return {
        everyone: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            read_message_history=True,
        ),
        student_role: student_overwrite(can_send=False),
        professor_role: professor_overwrite(),
        admin_role: administrator_overwrite(),
    }


def teacher_area_overwrites(
    everyone: discord.Role,
    admin_role: discord.Role,
    professor_role: discord.Role,
    student_role: discord.Role,
) -> dict[discord.Role, discord.PermissionOverwrite]:
    return {
        everyone: hidden_overwrite(),
        student_role: hidden_overwrite(),
        professor_role: professor_overwrite(),
        admin_role: administrator_overwrite(),
    }


def class_area_overwrites(
    everyone: discord.Role,
    admin_role: discord.Role,
    professor_role: discord.Role,
    student_role: discord.Role,
    class_role: discord.Role,
) -> dict[discord.Role, discord.PermissionOverwrite]:
    return {
        everyone: hidden_overwrite(),
        student_role: hidden_overwrite(),
        class_role: student_overwrite(can_send=True),
        professor_role: professor_overwrite(),
        admin_role: administrator_overwrite(),
    }


def read_only_class_overwrites(
    everyone: discord.Role,
    admin_role: discord.Role,
    professor_role: discord.Role,
    student_role: discord.Role,
    class_role: discord.Role,
) -> dict[discord.Role, discord.PermissionOverwrite]:
    overwrites = class_area_overwrites(
        everyone,
        admin_role,
        professor_role,
        student_role,
        class_role,
    )
    overwrites[class_role] = student_overwrite(can_send=False)
    return overwrites


def virtual_classroom_overwrites(
    everyone: discord.Role,
    admin_role: discord.Role,
    professor_role: discord.Role,
    student_role: discord.Role,
    class_role: discord.Role,
) -> dict[discord.Role, discord.PermissionOverwrite]:
    return {
        everyone: discord.PermissionOverwrite(
            view_channel=False,
            connect=False,
        ),
        student_role: discord.PermissionOverwrite(
            view_channel=False,
            connect=False,
        ),
        class_role: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
        ),
        professor_role: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
        ),
        admin_role: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
        ),
    }

"""Owner-authorized repair for the persisted school-administration role registry."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import discord

from services.audit import record_event
from services.permissions import (
    ROLE_DIRECTION,
    ROLE_PERSONNEL_ADMINISTRATIF,
    ROLE_SUPPORT_ENGINEER,
    ROLE_SURVEILLANT_GENERAL,
)
from services.storage import get_guild_config, save_guild_config

_REQUIRED_ROLE_NAMES = (
    ROLE_DIRECTION,
    ROLE_SURVEILLANT_GENERAL,
    ROLE_PERSONNEL_ADMINISTRATIF,
)
_OPTIONAL_ROLE_NAMES = (ROLE_SUPPORT_ENGINEER,)


def _is_regular_role(role: Any) -> bool:
    if getattr(role, "managed", False):
        return False
    is_default = getattr(role, "is_default", None)
    return not (callable(is_default) and is_default())


def sync_admin_role_registry(
    guild: discord.Guild,
    *,
    actor_id: int,
    actor_name: str,
) -> tuple[str, ...]:
    """Register existing canonical admin roles after an explicit server-owner action.

    This updates only the managed role ID registry in the persisted guild config.
    It does not create, rename, reorder, delete, or change permissions on Discord
    roles, and it never changes member assignments or channel overwrites.
    """
    if guild is None or type(getattr(guild, "id", None)) is not int or guild.id <= 0:
        raise ValueError("Serveur Discord invalide.")

    current = get_guild_config(guild.id)
    if not isinstance(current, dict) or not current.get("levels"):
        raise ValueError(
            "Aucune configuration scolaire enregistrée. Le propriétaire doit d'abord "
            "lancer /setup et valider le bouton Construire le serveur."
        )

    managed = current.get("managed", {})
    if managed is None:
        managed = {}
    if not isinstance(managed, dict):
        raise ValueError("Le registre managed est invalide; aucune modification n'a été faite.")
    role_registry = managed.get("roles", {})
    if role_registry is None:
        role_registry = {}
    if not isinstance(role_registry, dict):
        raise ValueError("Le registre des rôles est invalide; aucune modification n'a été faite.")

    resolved: dict[str, Any] = {}
    for name in (*_REQUIRED_ROLE_NAMES, *_OPTIONAL_ROLE_NAMES):
        matches = [role for role in getattr(guild, "roles", []) if getattr(role, "name", None) == name]
        if not matches:
            if name in _OPTIONAL_ROLE_NAMES:
                continue
            raise ValueError(
                f"Le rôle {name} est absent de Discord. Aucun registre n'a été modifié."
            )
        if len(matches) != 1:
            raise ValueError(
                f"Plusieurs rôles portent le nom {name}. Renomme les doublons après vérification; "
                "aucun registre n'a été modifié."
            )

        role = matches[0]
        role_id = getattr(role, "id", None)
        if type(role_id) is not int or role_id <= 0 or not _is_regular_role(role):
            raise ValueError(
                f"Le rôle {name} n'est pas un rôle Discord classique valide. Aucun registre n'a été modifié."
            )

        registered_id = role_registry.get(name)
        if registered_id is not None:
            if type(registered_id) is not int or registered_id <= 0:
                raise ValueError(
                    f"L'identité enregistrée pour {name} est invalide. Aucun registre n'a été modifié."
                )
            registered_role = guild.get_role(registered_id)
            if registered_role is not None and registered_role.id != role_id:
                raise ValueError(
                    f"{name} est déjà associé à un autre rôle Discord encore présent. "
                    "Aucun remplacement automatique n'a été effectué."
                )

        for other_name, other_id in role_registry.items():
            if other_name != name and other_id == role_id:
                raise ValueError(
                    f"Le rôle {name} est déjà enregistré sous l'identité {other_name}. "
                    "Aucun registre n'a été modifié."
                )
        resolved[name] = role

    updated = deepcopy(current)
    updated_managed = updated.get("managed")
    if not isinstance(updated_managed, dict):
        updated_managed = {}
        updated["managed"] = updated_managed
    updated_roles = updated_managed.get("roles")
    if not isinstance(updated_roles, dict):
        updated_roles = {}
        updated_managed["roles"] = updated_roles

    for name, role in resolved.items():
        updated_roles[name] = role.id

    save_guild_config(guild.id, updated)
    synced_names = tuple(resolved)
    record_event(
        guild.id,
        actor_id,
        actor_name,
        "sync_admin_role_registry",
        target=", ".join(synced_names),
        details=(
            "Server owner registered existing canonical school-administration role IDs. "
            "No Discord roles, members, permissions, or channel overwrites were changed."
        ),
    )
    return synced_names

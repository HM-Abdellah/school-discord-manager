"""Transactional academic-year state transitions."""

from __future__ import annotations

from copy import deepcopy

from services.storage import activate_academic_year, get_guild_config, list_academic_years



def rollback_guild_config_year(guild_id: int, year: str) -> tuple[str | None, bool]:
    """Switch the logical active academic year without rebuilding Discord."""
    config = get_guild_config(guild_id)
    if not config:
        raise ValueError("Configuration absente.")

    target = next((row for row in list_academic_years(guild_id) if row["name"] == year), None)
    if target is None:
        raise ValueError(f"L'année {year} n'est pas enregistrée.")

    previous_year = config.get("academic_year")
    if previous_year == year:
        return year, False

    new_config = deepcopy(config)
    new_config["academic_year"] = year

    # Only logical state changes. Current Discord roles/channels are untouched.
    activate_academic_year(guild_id, year, config=new_config)
    return previous_year, True


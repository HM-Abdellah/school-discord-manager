"""Transactional academic-year state transitions."""

from __future__ import annotations

from copy import deepcopy

from services.storage import _connect, _refresh_json_cache, get_guild_config, list_academic_years


def rollback_guild_config_year(guild_id: int, year: str) -> tuple[str | None, bool]:
    """Activate a previously recorded year and update its guild configuration atomically."""
    config = get_guild_config(guild_id)
    if not config:
        raise ValueError("Configuration absente.")

    target = next((row for row in list_academic_years(guild_id) if row["name"] == year), None)
    if target is None:
        raise ValueError(f"L'année {year} n'est pas enregistrée.")

    previous_year = config.get("academic_year")
    if previous_year == year:
        return year, False

    target_streams = set()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT level_name, stream_name, role_name FROM streams WHERE guild_id=? AND academic_year_id=?",
            (guild_id, int(target["id"])),
        ).fetchall()
    for row in rows:
        target_streams.add(
            (str(row["level_name"]), str(row["stream_name"]), str(row["role_name"]))
        )

    current_streams = set()
    for level in config.get("levels", []) or []:
        if not isinstance(level, dict) or not isinstance(level.get("name"), str):
            continue
        for stream in level.get("streams", []) or []:
            if not isinstance(stream, dict) or not isinstance(stream.get("name"), str):
                continue
            code = str(stream.get("abbreviation") or "")
            if not code:
                from config.curriculum import get_stream_abbreviation
                code = get_stream_abbreviation(level["name"], stream["name"])
            current_streams.add(
                (str(level["name"]), str(stream["name"]), f"Filière - {code}")
            )

    if target_streams != current_streams:
        raise ValueError(
            f"Impossible de basculer vers {year} : la structure des filières enregistrée pour cette année "
            "diffère de la configuration actuellement déployée. Réconcilie la structure avant le rollback."
        )

    new_config = deepcopy(config)
    new_config["academic_year"] = year

    with _connect() as conn:
        try:
            conn.execute("UPDATE academic_years SET is_active=0 WHERE guild_id=?", (guild_id,))
            updated = conn.execute(
                "UPDATE academic_years SET is_active=1 WHERE guild_id=? AND name=?",
                (guild_id, year),
            ).rowcount
            if updated != 1:
                raise OSError(f"Impossible d'activer l'année {year} dans la base de données.")

            import json
            conn.execute(
                """
                INSERT INTO guild_configs(guild_id, config_json, is_deleted, updated_at)
                VALUES(?,?,0,date('now'))
                ON CONFLICT(guild_id) DO UPDATE SET
                    config_json=excluded.config_json,
                    is_deleted=0,
                    updated_at=excluded.updated_at
                """,
                (guild_id, json.dumps(new_config, ensure_ascii=False, separators=(",", ":"))),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    _refresh_json_cache()
    return previous_year, True

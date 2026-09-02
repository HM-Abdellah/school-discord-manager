"""Pure helpers for the visual stream layout.

The actual Discord build logic lives in ``services.server_builder``.  This
module intentionally contains no monkey-patching and can safely be imported by
helpers or tests without changing builder behavior.
"""

from __future__ import annotations

from config.curriculum import get_stream_abbreviation
from services.server_builder import STREAM_EMOJIS, _subject_channel_name

HEADER_PREFIX = "🔹・"


def stream_header_name(stream_name: str, stream_code: str) -> str:
    return f"{HEADER_PREFIX}{STREAM_EMOJIS.get(stream_name, '🎓')}・{stream_code}"


def _stream_channel_count(level: dict, stream: dict) -> int:
    return 4 + len(list(stream.get("subjects", [])))


def _planned_channel_names_for_stream(stream: dict) -> set[str]:
    code = stream.get("abbreviation") or ""
    return {
        stream_header_name(stream.get("name", ""), code),
        f"📌-{code}・informations",
        f"🗓️-{code}・emploi-du-temps",
        f"📝-{code}・examens",
        *{_subject_channel_name(code, subject) for subject in stream.get("subjects", [])},
    }


__all__ = [
    "stream_header_name",
    "_stream_channel_count",
    "_planned_channel_names_for_stream",
]

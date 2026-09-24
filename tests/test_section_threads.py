import asyncio
from types import SimpleNamespace

import discord
import pytest

from cogs.section_threads import _all_public_threads, _resolve_subject_channel
from config.curriculum import get_stream_abbreviation, get_streams, get_stream_subjects
from services.server_builder import _stream_category_name, _subject_channel_name


def test_resolve_managed_subject_channel():
    level = "2ème Année Bac"
    stream = "2ème Année Bac Sciences Physiques"
    subject = get_stream_subjects(level, stream)[0]
    code = get_stream_abbreviation(level, stream)
    category = SimpleNamespace(name=_stream_category_name(level, stream, code))
    channel = SimpleNamespace(
        name=_subject_channel_name(code, subject),
        category=category,
    )

    assert _resolve_subject_channel(channel) == (level, stream, subject)


def test_every_configured_stream_has_resolvable_subject_names():
    for level in ["Tronc Commun", "1ère Année Bac", "2ème Année Bac"]:
        for stream in get_streams(level):
            code = get_stream_abbreviation(level, stream)
            for subject in get_stream_subjects(level, stream):
                assert _subject_channel_name(code, subject).startswith(f"📚-{code}・")
                assert _stream_category_name(level, stream, code)


def test_all_public_threads_excludes_private_active_threads_and_keeps_archived_public_threads():
    public_active = SimpleNamespace(id=1, name="Section 1", type=discord.ChannelType.public_thread)
    private_active = SimpleNamespace(id=2, name="Section 2", type=discord.ChannelType.private_thread)
    archived_public = SimpleNamespace(id=3, name="Section 3", type=discord.ChannelType.public_thread)
    archived_private = SimpleNamespace(id=4, name="Section 4", type=discord.ChannelType.private_thread)

    async def archived_threads(limit=None):
        assert limit is None
        yield archived_public
        yield archived_private

    channel = SimpleNamespace(
        threads=[public_active, private_active],
        archived_threads=archived_threads,
    )

    result = asyncio.run(_all_public_threads(channel))

    assert {thread.id for thread in result} == {1, 3}


def test_all_public_threads_fails_closed_when_archive_fetch_fails():
    response = SimpleNamespace(status=500, reason="Internal Server Error")

    async def archived_threads(limit=None):
        assert limit is None
        raise discord.HTTPException(response, "archive unavailable")
        yield  # pragma: no cover

    channel = SimpleNamespace(threads=[], archived_threads=archived_threads)

    with pytest.raises(discord.HTTPException):
        asyncio.run(_all_public_threads(channel))

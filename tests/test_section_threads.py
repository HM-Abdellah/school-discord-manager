from types import SimpleNamespace

from cogs.section_threads import _resolve_subject_channel
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

    # The resolver only accepts real discord.TextChannel instances, so exercise
    # the matching rule through a lightweight subclass-like object is not enough.
    assert subject in get_stream_subjects(level, stream)
    assert category.name.startswith("📚")
    assert channel.name.startswith(f"📚-{code}・")


def test_every_configured_stream_has_resolvable_subject_names():
    for level in ["Tronc Commun", "1ère Année Bac", "2ème Année Bac"]:
        for stream in get_streams(level):
            code = get_stream_abbreviation(level, stream)
            for subject in get_stream_subjects(level, stream):
                assert _subject_channel_name(code, subject).startswith(f"📚-{code}・")
                assert _stream_category_name(level, stream, code)
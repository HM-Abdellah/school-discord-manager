from types import SimpleNamespace

from services.stream_layout import stream_header_name, _planned_channel_names_for_stream, _stream_channel_count


def test_stream_header_is_distinct_per_stream():
    assert stream_header_name("Tronc Commun Scientifique", "TCS") != stream_header_name("Tronc Commun Lettres", "TCL")
    assert stream_header_name("Tronc Commun Scientifique", "TCS").startswith("🔹・🔬・TCS")


def test_stream_channel_count_includes_read_only_title():
    stream = {"name": "Tronc Commun Scientifique", "abbreviation": "TCS", "subjects": ["math", "phys"]}
    assert _stream_channel_count({}, stream) == 6


def test_planned_channels_include_stream_title():
    stream = {"name": "Tronc Commun Lettres", "abbreviation": "TCL", "subjects": ["fr", "ar"]}
    names = _planned_channel_names_for_stream(stream)
    assert "🔹・📩・TCL" in names
    assert "📌-TCL・informations" in names
    assert len(names) == 6

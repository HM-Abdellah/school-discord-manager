from services.server_builder import _stream_category_name


def test_stream_titles_are_categories_not_text_headers():
    assert _stream_category_name("Tronc Commun", "Tronc Commun Scientifique", "TCS") == "📘・TC・🔬 TCS"
    assert _stream_category_name("Tronc Commun", "Tronc Commun Lettres", "TCL") == "📘・TC・📩 TCL"

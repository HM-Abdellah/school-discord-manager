from bot import SchoolBot


def test_edge_case_hardening_is_not_a_runtime_dependency():
    assert "cogs.edge_case_hardening" not in SchoolBot.EXTENSIONS


def test_removestream_has_a_single_runtime_owner():
    assert SchoolBot.EXTENSIONS.count("cogs.removestream_fix") == 1

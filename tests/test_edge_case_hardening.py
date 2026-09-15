from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_edge_case_hardening_is_not_a_runtime_dependency():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert '"cogs.edge_case_hardening"' not in source


def test_removestream_has_a_single_runtime_owner():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert source.count('"cogs.removestream_fix"') == 1

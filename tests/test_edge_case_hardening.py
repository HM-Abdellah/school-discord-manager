from cogs.edge_case_hardening import _repair_removed_stream_config


def test_removestream_persists_removal_from_the_requested_level():
    config = {
        "academic_year": "2026/2027",
        "levels": [
            {
                "name": "Tronc Commun",
                "streams": [
                    {"name": "Tronc Commun Scientifique", "abbreviation": "TCS"},
                    {"name": "Tronc Commun Lettres", "abbreviation": "TCL"},
                ],
            },
            {
                "name": "1ère Bac",
                "streams": [{"name": "Sciences Expérimentales", "abbreviation": "1SE"}],
            },
        ],
        "managed": {
            "roles": {"Filière - TCS": 101},
            "channels": {"📌-TCS・informations": 201},
        },
    }

    repaired = _repair_removed_stream_config(config, "Tronc Commun", "Tronc Commun Scientifique")

    assert repaired["academic_year"] == "2026/2027"
    assert repaired["levels"][0]["streams"] == [
        {"name": "Tronc Commun Lettres", "abbreviation": "TCL"}
    ]
    assert repaired["levels"][1]["streams"] == [
        {"name": "Sciences Expérimentales", "abbreviation": "1SE"}
    ]
    assert config["levels"][0]["streams"][0]["name"] == "Tronc Commun Scientifique"


def test_removestream_drops_empty_level_after_last_stream_is_removed():
    config = {
        "levels": [
            {"name": "Tronc Commun", "streams": [{"name": "Tronc Commun Scientifique"}]},
            {"name": "1ère Bac", "streams": [{"name": "Sciences Expérimentales"}]},
        ]
    }

    repaired = _repair_removed_stream_config(config, "Tronc Commun", "Tronc Commun Scientifique")

    assert [level["name"] for level in repaired["levels"]] == ["1ère Bac"]


def test_removestream_repair_is_noop_for_non_matching_level_or_malformed_streams():
    config = {
        "levels": [
            {"name": "Tronc Commun", "streams": "invalid"},
            {"name": "1ère Bac", "streams": [{"name": "Sciences Expérimentales"}]},
        ]
    }

    repaired = _repair_removed_stream_config(config, "Tronc Commun", "Unknown")

    assert repaired == config
    assert repaired is not config

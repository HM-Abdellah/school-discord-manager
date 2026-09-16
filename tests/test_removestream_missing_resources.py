from config.curriculum import get_stream_subjects
from cogs.removestream_fix import CATEGORY_VOICE, _registry_removal_journal
from services.server_builder import _stream_category_name
from services.permissions import STREAM_ROLE_PREFIX, STUDENT_STREAM_ROLE_PREFIX


class EmptyGuild:
    def get_channel(self, _resource_id):
        return None

    def get_role(self, _resource_id):
        return None


def _config() -> tuple[dict, str, str, dict]:
    level = "1ère Année Bac"
    stream = "1ère Année Bac Lettres et Sciences Humaines"
    subjects = get_stream_subjects(level, stream)
    stream_item = {"name": stream, "subjects": subjects, "abbreviation": "1BACSH"}
    category_name = _stream_category_name(level, stream, "1BACSH")

    managed_channels = {}
    next_id = 1000
    for name in (
        "📌-1BACSH・informations",
        "🗓️-1BACSH・emploi-du-temps",
        "📝-1BACSH・examens",
        *[f"📚-1BACSH・{subject}" for subject in subjects],
    ):
        managed_channels[name] = next_id
        next_id += 1
    managed_channels["🔊-1BACSH-à-distance"] = next_id

    config = {
        "levels": [{"name": level, "streams": [stream_item]}],
        "managed": {
            "categories": {
                category_name: 2000,
                CATEGORY_VOICE: 2001,
            },
            "channels": managed_channels,
            "roles": {
                f"{STREAM_ROLE_PREFIX}1BACSH": 3000,
                f"{STUDENT_STREAM_ROLE_PREFIX}1BACSH": 3001,
            },
        },
    }
    return config, level, stream, stream_item


def test_registry_journal_keeps_persisted_ids_when_live_resources_are_gone():
    config, level, stream, stream_item = _config()
    journal, missing = _registry_removal_journal(
        config,
        level=level,
        stream=stream,
        stream_item=stream_item,
    )

    assert missing == []
    assert journal is not None
    assert journal["level"] == level
    assert journal["stream"] == stream
    assert journal["resources"]


def test_missing_registry_identity_still_fails_closed():
    config, level, stream, stream_item = _config()
    del config["managed"]["channels"]["📌-1BACSH・informations"]

    journal, missing = _registry_removal_journal(
        config,
        level=level,
        stream=stream,
        stream_item=stream_item,
    )

    assert journal is None
    assert any("informations" in item and "ID géré manquant" in item for item in missing)

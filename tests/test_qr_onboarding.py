from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import storage

from cogs.qr_onboarding import (
    _class_key,
    _class_role_name,
    _get_or_create_class_role,
)
from services.qr_invites import (
    DEFAULT_QR_MAX_AGE,
    DEFAULT_QR_MAX_USES,
    create_role_invite,
)


def test_class_identity_is_stream_plus_section():
    assert _class_key("2BACPC", 2) == "2BACPC-2"
    assert _class_role_name("2BACPC", 2) == "Élèves - 2BACPC-2"


def test_qr_capacity_policy_is_capped_at_42():
    assert DEFAULT_QR_MAX_USES == 42


@pytest.mark.asyncio
async def test_class_role_is_created_and_registered():
    role = SimpleNamespace(id=900, name="Élèves - 2BACPC-2", managed=False)
    guild = SimpleNamespace(
        roles=[],
        get_role=lambda role_id: None,
        create_role=AsyncMock(return_value=role),
    )
    config = {"class_roles": {}}

    created, was_created = await _get_or_create_class_role(
        guild,
        config,
        level="2ème Année Bac",
        stream="2ème Année Bac Sciences Physiques",
        code="2BACPC",
        section=2,
    )

    assert created is role
    assert was_created is True
    assert config["class_roles"]["2BACPC-2"]["role_id"] == 900


@pytest.mark.asyncio
async def test_class_role_reuses_registered_role():
    role = SimpleNamespace(id=900, name="Élèves - 2BACPC-2", managed=False)
    guild = SimpleNamespace(
        roles=[role],
        get_role=lambda role_id: role if role_id == 900 else None,
        create_role=AsyncMock(),
    )
    config = {
        "class_roles": {
            "2BACPC-2": {
                "role_id": 900,
                "level_name": "2ème Année Bac",
                "stream_name": "2ème Année Bac Sciences Physiques",
                "stream_code": "2BACPC",
                "section": 2,
            }
        }
    }

    created, was_created = await _get_or_create_class_role(
        guild,
        config,
        level="2ème Année Bac",
        stream="2ème Année Bac Sciences Physiques",
        code="2BACPC",
        section=2,
    )

    assert created is role
    assert was_created is False
    guild.create_role.assert_not_awaited()


@pytest.mark.asyncio
async def test_class_role_rejects_same_name_unmanaged_collision():
    collision = SimpleNamespace(id=901, name="Élèves - 2BACPC-2", managed=False)
    guild = SimpleNamespace(
        roles=[collision],
        get_role=lambda role_id: None,
        create_role=AsyncMock(),
    )

    with pytest.raises(RuntimeError, match="collision"):
        await _get_or_create_class_role(
            guild,
            {"class_roles": {}},
            level="2ème Année Bac",
            stream="2ème Année Bac Sciences Physiques",
            code="2BACPC",
            section=2,
        )

    guild.create_role.assert_not_awaited()


@pytest.mark.asyncio
async def test_role_invite_payload_accepts_admin_selected_capacity():
    request = AsyncMock(return_value={"code": "abc123"})
    bot = SimpleNamespace(http=SimpleNamespace(request=request))
    channel = SimpleNamespace(id=123)
    roles = [SimpleNamespace(id=10), SimpleNamespace(id=20)]

    url = await create_role_invite(bot, channel, roles, max_uses=36)

    assert url == "https://discord.gg/abc123"
    payload = request.await_args.kwargs["json"]
    assert payload["max_age"] == DEFAULT_QR_MAX_AGE
    assert payload["max_uses"] == 36
    assert payload["unique"] is True
    assert payload["temporary"] is False
    assert payload["role_ids"] == ["10", "20"]


def test_qr_defaults_are_bounded():
    assert 0 < DEFAULT_QR_MAX_AGE <= 604800
    assert 0 < DEFAULT_QR_MAX_USES <= 42


@pytest.mark.asyncio
async def test_role_invite_rejects_capacity_above_42():
    bot = SimpleNamespace(http=SimpleNamespace(request=AsyncMock()))
    channel = SimpleNamespace(id=123)
    roles = [SimpleNamespace(id=10)]

    with pytest.raises(ValueError, match="between 1 and 42"):
        await create_role_invite(bot, channel, roles, max_uses=43)

    bot.http.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_class_role_access_copies_explicit_stream_student_overwrites():
    from cogs.qr_onboarding import _grant_class_role_access

    class FakeChannel:
        def __init__(self, source):
            self.overwrites = {}
            self._source = source
            self.set_permissions = AsyncMock()

        def overwrites_for(self, role):
            if role is stream_role:
                return self._source
            return SimpleNamespace(is_empty=lambda: True)

    class Role:
        def __init__(self, name):
            self.name = name

        def __hash__(self):
            return hash(self.name)

    stream_role = Role("Élèves - 2BACPC")
    class_role = Role("Élèves - 2BACPC-2")
    source = SimpleNamespace(is_empty=lambda: False)

    channel = FakeChannel(source)
    await _grant_class_role_access(
        [channel],
        class_role=class_role,
        student_stream_role=stream_role,
    )

    channel.set_permissions.assert_awaited_once()
    assert channel.set_permissions.await_args.kwargs["overwrite"] is source


def test_managed_stream_channels_follow_deployed_stream_config_subjects(monkeypatch):
    from cogs.qr_onboarding import _managed_stream_channels
    from services.server_builder import _stream_category_name

    code = "2BACPC"
    level = "2ème Année Bac"
    stream = "2ème Année Bac Sciences Physiques"
    category_name = _stream_category_name(level, stream, code)
    subject_name = "📚-2BACPC・math"

    class Channel:
        def __init__(self, channel_id, name, category_id):
            self.id = channel_id
            self.name = name
            self.category_id = category_id

    channels = {
        100: Channel(100, category_name, None),
        101: Channel(101, "📌-2BACPC・informations", 100),
        102: Channel(102, "🗓️-2BACPC・emploi-du-temps", 100),
        103: Channel(103, "📝-2BACPC・examens", 100),
        104: Channel(104, subject_name, 100),
    }

    class Guild:
        def get_channel(self, channel_id):
            return channels.get(channel_id)

    config = {
        "levels": [
            {
                "name": level,
                "streams": [
                    {
                        "name": stream,
                        "abbreviation": code,
                        "subjects": ["Mathématiques"],
                    }
                ],
            }
        ],
        "managed": {
            "categories": {category_name: 100},
            "channels": {
                item.name: item.id
                for item in channels.values()
                if item.id != 100
            },
        },
    }

    monkeypatch.setattr(
        "cogs.qr_onboarding.get_stream_subjects",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("QR must use the deployed stream config when subjects are present.")
        ),
    )

    result = _managed_stream_channels(
        Guild(),
        config,
        level=level,
        stream=stream,
        code=code,
    )

    assert {channel.name for channel in result} == {
        "📌-2BACPC・informations",
        "🗓️-2BACPC・emploi-du-temps",
        "📝-2BACPC・examens",
        subject_name,
    }


def test_class_qr_registry_records_and_filters_active_invites(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_FILE", tmp_path / "guild_config.json")
    monkeypatch.setattr(storage, "DATABASE_FILE", tmp_path / "school.db")

    storage.initialize_database()
    storage.record_class_qr_invite(
        1,
        "abc123",
        "2BACPC-2",
        "2ème Année Bac",
        "2ème Année Bac Sciences Physiques",
        "2BACPC",
        2,
        900,
        42,
        "2026-10-06T10:00:00+00:00",
        "2026-10-06T10:30:00+00:00",
        36,
    )
    rows = storage.get_class_qr_invites(1, "2BACPC-2")
    assert len(rows) == 1
    assert rows[0]["invite_code"] == "abc123"

    storage.mark_class_qr_invite_revoked(1, "abc123")
    assert storage.get_class_qr_invites(1, "2BACPC-2") == []
    assert len(storage.get_class_qr_invites(1, "2BACPC-2", include_revoked=True)) == 1


def test_class_qr_registry_is_cleared_by_guild_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_FILE", tmp_path / "guild_config.json")
    monkeypatch.setattr(storage, "DATABASE_FILE", tmp_path / "school.db")

    storage.initialize_database()
    storage.record_class_qr_invite(
        1,
        "abc123",
        "TCS-1",
        "Tronc Commun",
        "Tronc Commun Scientifique",
        "TCS",
        1,
        901,
        42,
        "2026-10-06T10:00:00+00:00",
        "2026-10-06T10:30:00+00:00",
        36,
    )

    storage.reset_guild_data(1)
    assert storage.get_class_qr_invites(1, include_revoked=True) == []


@pytest.mark.asyncio
async def test_delete_invite_uses_invite_route():
    from services.qr_invites import delete_invite

    request = AsyncMock(return_value=None)
    bot = SimpleNamespace(http=SimpleNamespace(request=request))
    await delete_invite(bot, "abc123")

    route = request.await_args.args[0]
    assert route.method == "DELETE"
    assert route.path == "/invites/{invite_code}"
    assert request.await_args.kwargs["reason"] == "School Manager class QR revoked"


@pytest.mark.asyncio
async def test_member_join_persists_the_class_from_its_registered_role(monkeypatch):
    from cogs.qr_onboarding import ClassQROnboarding

    class Role:
        def __init__(self, role_id, name):
            self.id = role_id
            self.name = name

        def __hash__(self):
            return hash(self.id)

    class Guild:
        id = 123

    class Member:
        bot = False
        id = 700
        display_name = "Student"
        guild = Guild()

        def __init__(self):
            self.class_role = Role(900, "Élèves - 2BACPC-2")
            self.roles = [self.class_role]
            self.add_roles = AsyncMock()

    member = Member()
    bot = SimpleNamespace(
        user=SimpleNamespace(id=999, display_name="School Manager"),
    )
    cog = ClassQROnboarding(bot)

    config = {
        "class_roles": {
            "2BACPC-2": {
                "role_id": 900,
                "level_name": "2ème Année Bac",
                "stream_name": "2ème Année Bac Sciences Physiques",
                "stream_code": "2BACPC",
                "section": 2,
            }
        }
    }
    student_role = Role(901, "Élève")
    captured = {}

    monkeypatch.setattr("cogs.qr_onboarding.get_guild_config", lambda _guild_id: config)
    monkeypatch.setattr(
        "cogs.qr_onboarding.get_active_academic_year",
        lambda _guild_id: {"id": 42},
    )
    monkeypatch.setattr(
        "cogs.qr_onboarding.get_managed_role",
        lambda _guild, name: student_role if name == "Élève" else None,
    )
    monkeypatch.setattr(
        "cogs.qr_onboarding.enroll_student_record",
        lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs),
    )
    monkeypatch.setattr("cogs.qr_onboarding.record_event", lambda *args, **kwargs: None)

    await cog.on_member_join(member)

    member.add_roles.assert_awaited_once_with(
        student_role,
        reason="School Manager class QR onboarding",
    )
    assert captured["args"][-1] == "2ème Année Bac Sciences Physiques"
    assert captured["kwargs"]["section"] == 2


def test_class_qr_registry_rejects_two_active_qrs_for_same_class(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_FILE", tmp_path / "guild_config.json")
    monkeypatch.setattr(storage, "DATABASE_FILE", tmp_path / "school.db")

    storage.initialize_database()
    args = (
        1,
        "abc123",
        "2BACPC-2",
        "2ème Année Bac",
        "2ème Année Bac Sciences Physiques",
        "2BACPC",
        2,
        900,
        42,
        "2026-10-06T10:00:00+00:00",
        "2026-10-06T10:30:00+00:00",
        36,
    )
    storage.record_class_qr_invite(*args)

    with pytest.raises(RuntimeError, match="QR actif existe déjà"):
        storage.record_class_qr_invite(*args[:1], "def456", *args[2:])


def test_revoked_class_qr_allows_a_new_active_qr_for_same_class(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_FILE", tmp_path / "guild_config.json")
    monkeypatch.setattr(storage, "DATABASE_FILE", tmp_path / "school.db")

    storage.initialize_database()
    base = (
        1,
        "abc123",
        "2BACPC-2",
        "2ème Année Bac",
        "2ème Année Bac Sciences Physiques",
        "2BACPC",
        2,
        900,
        42,
        "2026-10-06T10:00:00+00:00",
        "2026-10-06T10:30:00+00:00",
        36,
    )
    storage.record_class_qr_invite(*base)
    storage.mark_class_qr_invite_revoked(1, "abc123")

    replacement = (base[0], "def456", *base[2:])
    storage.record_class_qr_invite(*replacement)

    rows = storage.get_class_qr_invites(1, "2BACPC-2")
    assert [row["invite_code"] for row in rows] == ["def456"]


def test_existing_duplicate_active_class_qrs_are_deduplicated_on_database_initialization(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_FILE", tmp_path / "guild_config.json")
    monkeypatch.setattr(storage, "DATABASE_FILE", tmp_path / "school.db")

    with storage._connect() as conn:
        conn.execute(
            """
            CREATE TABLE class_qr_invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                invite_code TEXT NOT NULL UNIQUE,
                class_key TEXT NOT NULL,
                level_name TEXT NOT NULL,
                stream_name TEXT NOT NULL,
                stream_code TEXT NOT NULL,
                section INTEGER NOT NULL,
                class_role_id INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                max_uses INTEGER NOT NULL,
                revoked_at TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO class_qr_invites(
                guild_id,invite_code,class_key,level_name,stream_name,stream_code,
                section,class_role_id,created_by,created_at,expires_at,max_uses
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (1, "old123", "2BACPC-2", "2ème Année Bac", "PC", "2BACPC", 2, 900, 42, "2026-10-06T10:00:00+00:00", "2026-10-06T10:30:00+00:00", 36),
                (1, "new456", "2BACPC-2", "2ème Année Bac", "PC", "2BACPC", 2, 900, 42, "2026-10-06T10:01:00+00:00", "2026-10-06T10:31:00+00:00", 36),
            ],
        )
        conn.commit()

    storage.initialize_database()
    rows = storage.get_class_qr_invites(1, "2BACPC-2", include_revoked=True)
    active = [row for row in rows if row["revoked_at"] is None]
    revoked = [row for row in rows if row["revoked_at"] is not None]

    assert [row["invite_code"] for row in active] == ["new456"]
    assert [row["invite_code"] for row in revoked] == ["old123"]


def test_class_qr_registry_rejects_invalid_capacity(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_FILE", tmp_path / "guild_config.json")
    monkeypatch.setattr(storage, "DATABASE_FILE", tmp_path / "school.db")

    storage.initialize_database()
    with pytest.raises(ValueError, match="max_uses must be between 1 and 42"):
        storage.record_class_qr_invite(
            1,
            "abc123",
            "2BACPC-2",
            "2ème Année Bac",
            "2ème Année Bac Sciences Physiques",
            "2BACPC",
            2,
            900,
            42,
            "2026-10-06T10:00:00+00:00",
            "2026-10-06T10:30:00+00:00",
            43,
        )


@pytest.mark.asyncio
async def test_revoke_class_qrs_deletes_discord_invites_and_marks_them_revoked(monkeypatch):
    from cogs.qr_onboarding import _revoke_class_qrs

    rows = [
        {"invite_code": "abc123"},
        {"invite_code": "def456"},
    ]
    deleted = []

    monkeypatch.setattr(
        "cogs.qr_onboarding.get_class_qr_invites",
        lambda _guild_id, _class_key, include_revoked=False: rows,
    )

    async def fake_delete(_bot, code):
        deleted.append(code)

    marked = []
    monkeypatch.setattr("cogs.qr_onboarding.delete_invite", fake_delete)
    monkeypatch.setattr(
        "cogs.qr_onboarding.mark_class_qr_invite_revoked",
        lambda guild_id, code: marked.append((guild_id, code)),
    )

    result = await _revoke_class_qrs(SimpleNamespace(), 123, "2BACPC-2")

    assert result == 2
    assert deleted == ["abc123", "def456"]
    assert marked == [(123, "abc123"), (123, "def456")]


"""Linking multiple platform identities into one natural person."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, PlayerActionLog, PlayerServerStats, Server
from app.services.identity_links import (
    get_group_id,
    link_identities,
    linked_identities,
    unlink_identity,
)
from app.services.player_leaderboard import build_player_leaderboard
from app.services.player_records import batch_has_records, get_dossier

STEAM = "76561198000000001"
GDK = "gdk_2535470764765514"
XBOX_ID = "2535470764765514"
T0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _server(db, sid: int = 1, name: str = "Alpha") -> Server:
    row = Server(
        id=sid,
        name=name,
        host="127.0.0.1",
        query_port=27131,
        rcon_port=27015,
        server_type="sandstorm",
    )
    db.add(row)
    db.flush()
    return row


def test_link_and_unlink(db):
    group = link_identities(db, a=("steam", STEAM), b=("xbox", XBOX_ID))
    db.commit()
    assert group.id is not None
    members = linked_identities(db, "steam", STEAM)
    assert set(members) == {("steam", STEAM), ("xbox", XBOX_ID)}
    assert get_group_id(db, "xbox", XBOX_ID) == group.id

    assert unlink_identity(db, platform="xbox", external_id=XBOX_ID) is True
    db.commit()
    # Solo again - no group.
    assert get_group_id(db, "steam", STEAM) is None
    assert linked_identities(db, "steam", STEAM) == [("steam", STEAM)]


def test_cannot_link_to_self(db):
    with pytest.raises(ValueError):
        link_identities(db, a=("steam", STEAM), b=("steam", STEAM))


def test_merge_two_groups(db):
    link_identities(db, a=("steam", STEAM), b=("xbox", XBOX_ID))
    link_identities(db, a=("psn", "psn-user-1"), b=("eos", "eos-user-1"))
    db.flush()
    group = link_identities(
        db, a=("steam", STEAM), b=("psn", "psn-user-1")
    )
    db.commit()
    members = set(linked_identities(db, "steam", STEAM))
    assert members == {
        ("steam", STEAM),
        ("xbox", XBOX_ID),
        ("psn", "psn-user-1"),
        ("eos", "eos-user-1"),
    }
    assert get_group_id(db, "eos", "eos-user-1") == group.id


def test_leaderboard_sums_linked_accounts(db):
    _server(db, 1, "Sandstorm")
    _server(db, 2, "Palworld")
    db.add(
        PlayerServerStats(
            server_id=1,
            steam_id=STEAM,
            last_name="SteamName",
            first_seen_at=T0,
            last_seen_at=T0,
            total_seconds=1000,
            visit_count=2,
        )
    )
    db.add(
        PlayerServerStats(
            server_id=2,
            steam_id=GDK,
            last_name="XboxName",
            first_seen_at=T0,
            last_seen_at=T0,
            total_seconds=500,
            visit_count=1,
        )
    )
    db.flush()

    before = build_player_leaderboard(db, allowed_server_ids=None)
    assert before["total"] == 2

    link_identities(db, a=("steam", STEAM), b=("xbox", XBOX_ID))
    db.commit()

    after = build_player_leaderboard(db, allowed_server_ids=None)
    assert after["total"] == 1
    row = after["players"][0]
    assert row["total_seconds"] == 1500
    assert row["rank"] == 1
    platforms = {li["platform"] for li in row["linked_identities"]}
    assert platforms == {"steam", "xbox"}
    assert len(row["servers"]) == 2


def test_dossier_has_one_section_per_profile(db):
    link_identities(db, a=("steam", STEAM), b=("xbox", XBOX_ID))
    db.add(
        PlayerActionLog(
            platform="steam",
            external_id=STEAM,
            action="kick",
            server_name="S1",
            player_name="SteamName",
            ok=True,
            created_at=T0,
        )
    )
    db.commit()

    dossier = get_dossier(db, "steam", STEAM)
    assert dossier["link_group_id"] is not None
    assert len(dossier["profiles"]) == 2
    by_plat = {p["platform"]: p for p in dossier["profiles"]}
    assert len(by_plat["steam"]["actions"]) == 1
    assert by_plat["xbox"]["actions"] == []
    # Top-level still reflects the requested identity.
    assert len(dossier["actions"]) == 1


def test_flags_true_for_any_linked_member(db):
    link_identities(db, a=("steam", STEAM), b=("xbox", XBOX_ID))
    db.add(
        PlayerActionLog(
            platform="steam",
            external_id=STEAM,
            action="ban",
            server_name="S1",
            ok=True,
            created_at=T0,
        )
    )
    db.commit()

    flags = batch_has_records(db, [("xbox", XBOX_ID), ("steam", STEAM)])
    assert flags[f"steam:{STEAM}"] is True
    assert flags[f"xbox:{XBOX_ID}"] is True

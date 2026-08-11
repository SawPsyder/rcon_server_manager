"""Global player leaderboard: aggregation, ranks, scope, search, pagination."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, PlayerServerStats, Server
from app.services.player_leaderboard import build_player_leaderboard

STEAM_A = "76561198000000001"
STEAM_B = "76561198000000002"
# Same person as STEAM_A, Palworld-style prefix — must merge overall.
STEAM_A_PREFIXED = f"steam_{STEAM_A}"
GDK = "gdk_2535470764765514"

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


def _server(db, sid: int, name: str, server_type: str = "sandstorm") -> Server:
    row = Server(
        id=sid,
        name=name,
        host="127.0.0.1",
        query_port=27131 + sid,
        rcon_port=27015 + sid,
        server_type=server_type,
    )
    db.add(row)
    db.flush()
    return row


def _stats(
    db,
    *,
    server_id: int,
    steam_id: str,
    name: str,
    total: int,
    visits: int = 1,
    first: datetime | None = None,
    last: datetime | None = None,
    online: bool = False,
) -> PlayerServerStats:
    first = first or T0
    last = last or T0
    row = PlayerServerStats(
        server_id=server_id,
        steam_id=steam_id,
        last_name=name,
        first_seen_at=first,
        last_seen_at=last,
        total_seconds=total,
        visit_count=visits,
        session_started_at=last if online else None,
    )
    db.add(row)
    db.flush()
    return row


def test_overall_rank_sums_across_servers(db):
    _server(db, 1, "Alpha")
    _server(db, 2, "Beta")
    _stats(db, server_id=1, steam_id=STEAM_A, name="Alice", total=100)
    _stats(db, server_id=2, steam_id=STEAM_A, name="Alice", total=50)
    _stats(db, server_id=1, steam_id=STEAM_B, name="Bob", total=120)

    out = build_player_leaderboard(db, allowed_server_ids=None)
    assert out["total"] == 2
    assert out["ranked_players"] == 2
    by_name = {p["display_name"]: p for p in out["players"]}
    assert by_name["Alice"]["total_seconds"] == 150
    assert by_name["Alice"]["rank"] == 1
    assert by_name["Bob"]["total_seconds"] == 120
    assert by_name["Bob"]["rank"] == 2
    assert len(by_name["Alice"]["servers"]) == 2


def test_prefixed_and_bare_steam_merge(db):
    _server(db, 1, "Sandstorm")
    _server(db, 2, "Palworld", server_type="palworld")
    _stats(db, server_id=1, steam_id=STEAM_A, name="Lyra", total=1000)
    _stats(db, server_id=2, steam_id=STEAM_A_PREFIXED, name="Lyra", total=500)

    out = build_player_leaderboard(db, allowed_server_ids=None)
    assert out["total"] == 1
    row = out["players"][0]
    assert row["platform"] == "steam"
    assert row["external_id"] == STEAM_A
    assert row["total_seconds"] == 1500
    assert row["rank"] == 1
    assert len(row["servers"]) == 2


def test_server_filter_uses_per_server_rank(db):
    _server(db, 1, "Alpha")
    _server(db, 2, "Beta")
    _stats(db, server_id=1, steam_id=STEAM_A, name="Alice", total=10)
    _stats(db, server_id=2, steam_id=STEAM_A, name="Alice", total=5000)
    _stats(db, server_id=1, steam_id=STEAM_B, name="Bob", total=100)

    out = build_player_leaderboard(db, allowed_server_ids=None, server_id=1)
    assert out["total"] == 2
    by_name = {p["display_name"]: p for p in out["players"]}
    # On server 1 Bob has more time.
    assert by_name["Bob"]["rank"] == 1
    assert by_name["Bob"]["total_seconds"] == 100
    assert by_name["Alice"]["rank"] == 2
    assert by_name["Alice"]["total_seconds"] == 10
    # Overall still available for context.
    assert by_name["Alice"]["overall_seconds"] == 5010


def test_grant_scope_hides_other_servers(db):
    _server(db, 1, "Alpha")
    _server(db, 2, "Beta")
    _stats(db, server_id=1, steam_id=STEAM_A, name="Alice", total=100)
    _stats(db, server_id=2, steam_id=STEAM_B, name="Bob", total=9999)

    out = build_player_leaderboard(db, allowed_server_ids={1})
    assert out["total"] == 1
    assert out["players"][0]["display_name"] == "Alice"
    assert out["players"][0]["total_seconds"] == 100


def test_search_by_name_and_id(db):
    _server(db, 1, "Alpha")
    _stats(db, server_id=1, steam_id=STEAM_A, name="Kurudos", total=10)
    _stats(db, server_id=1, steam_id=STEAM_B, name="Junior", total=20)
    _stats(db, server_id=1, steam_id=GDK, name="Jay", total=30)

    by_name = build_player_leaderboard(db, allowed_server_ids=None, q="kuru")
    assert by_name["total"] == 1
    assert by_name["players"][0]["display_name"] == "Kurudos"

    by_id = build_player_leaderboard(db, allowed_server_ids=None, q=STEAM_B[-6:])
    assert by_id["total"] == 1
    assert by_id["players"][0]["external_id"] == STEAM_B

    by_gdk = build_player_leaderboard(db, allowed_server_ids=None, q="gdk_")
    assert by_gdk["total"] == 1
    assert by_gdk["players"][0]["platform"] == "xbox"


def test_pagination(db):
    _server(db, 1, "Alpha")
    for i in range(5):
        _stats(
            db,
            server_id=1,
            steam_id=f"7656119800000000{i}",
            name=f"P{i}",
            total=100 - i,
        )

    page1 = build_player_leaderboard(
        db, allowed_server_ids=None, page=1, page_size=2, sort="total_seconds"
    )
    page2 = build_player_leaderboard(
        db, allowed_server_ids=None, page=2, page_size=2, sort="total_seconds"
    )
    assert page1["total"] == 5
    assert len(page1["players"]) == 2
    assert page1["players"][0]["rank"] == 1
    assert len(page2["players"]) == 2
    assert page2["players"][0]["rank"] == 3


def test_online_flag_from_open_session(db):
    _server(db, 1, "Alpha")
    _stats(
        db,
        server_id=1,
        steam_id=STEAM_A,
        name="OnlineOne",
        total=10,
        last=T0 + timedelta(minutes=1),
        online=True,
    )
    _stats(
        db,
        server_id=1,
        steam_id=STEAM_B,
        name="OfflineOne",
        total=20,
        last=T0,
        online=False,
    )

    out = build_player_leaderboard(db, allowed_server_ids=None, now=T0 + timedelta(minutes=2))
    by_name = {p["display_name"]: p for p in out["players"]}
    assert by_name["OnlineOne"]["online"] is True
    assert by_name["OnlineOne"]["last_seen_pretty"] == "Online"
    assert 1 in by_name["OnlineOne"]["online_server_ids"]
    assert by_name["OfflineOne"]["online"] is False


def test_empty_grants_returns_empty(db):
    _server(db, 1, "Alpha")
    _stats(db, server_id=1, steam_id=STEAM_A, name="Alice", total=10)
    out = build_player_leaderboard(db, allowed_server_ids=set())
    assert out["total"] == 0
    assert out["players"] == []


def test_competition_rank_ties(db):
    _server(db, 1, "Alpha")
    _stats(db, server_id=1, steam_id=STEAM_A, name="A", total=100)
    _stats(db, server_id=1, steam_id=STEAM_B, name="B", total=100)
    _stats(db, server_id=1, steam_id="76561198000000003", name="C", total=50)

    out = build_player_leaderboard(db, allowed_server_ids=None)
    ranks = {p["display_name"]: p["rank"] for p in out["players"]}
    assert ranks["A"] == 1
    assert ranks["B"] == 1
    assert ranks["C"] == 3

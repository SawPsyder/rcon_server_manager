"""Crossplay net ids: parsing, presence tracking, and Sandstorm regression.

Palworld reports platform-prefixed user ids (``gdk_2535…`` for Game Pass,
``steam_7656…`` for Steam). Presence used to accept bare 17-digit SteamID64s
only, so every non-Steam player silently got no rank, session, total, visits or
last seen. These tests pin the widened contract and that Source games keep
behaving exactly as before.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, IdentityCache, PlayerServerStats
from app.services.identity import parse_net_id
from app.services.player_records import normalize_identity
from app.services.presence import enrich_player_list, update_presence

STEAM = "76561198084350159"
GDK = "gdk_2535470764765514"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


# --- parsing --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (STEAM, ("steam", STEAM)),
        (f"SteamNWI:{STEAM}", ("steam", STEAM)),
        (f"STEAMNWI:{STEAM}", ("steam", STEAM)),
        ("EOS:abc123def", ("eos", "abc123def")),
        (f"steam_{STEAM}", ("steam", STEAM)),
        (GDK, ("xbox", "2535470764765514")),
        ("xsx_2535412345678901", ("xbox", "2535412345678901")),
        ("psn_someplayer", ("psn", "someplayer")),
        (f"Player <{STEAM}> connected", ("steam", STEAM)),
    ],
)
def test_recognised_ids(raw, expected):
    assert parse_net_id(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "NULL", "n/a", "12345", "zz_9", "x" * 40])
def test_junk_is_rejected(raw):
    # Unrecognisable ids must never become presence rows
    assert parse_net_id(raw) is None


def test_ids_too_long_to_key_a_row_are_skipped_by_presence(db):
    """parse_net_id is about semantics; the length cap is a storage concern.

    An embedded SteamID64 still parses (moderation accepts free-text input), but
    presence keys rows on the raw string, so it must fit VARCHAR(32).
    """
    long_raw = f"Player <{STEAM}> connected"
    assert parse_net_id(long_raw) == ("steam", STEAM)
    assert len(long_raw) > 32

    _sample(db, [{"steamid": long_raw, "name": "Noise"}], at=datetime(2026, 8, 7, tzinfo=timezone.utc))
    assert db.query(PlayerServerStats).count() == 0


def test_unknown_platform_prefix_is_rejected_not_guessed():
    assert parse_net_id("wii_2535470764765514") is None


def test_prefix_wins_over_the_loose_17_digit_search():
    """The bug this ordering prevents.

    A 17-digit Xbox id would otherwise match the "find a SteamID64 anywhere"
    fallback, get filed as a Steam account, and send the dossier to the Steam
    Web API for a profile that cannot exist.
    """
    assert parse_net_id("gdk_25354707647655141") == ("xbox", "25354707647655141")


def test_moderation_logs_agree_with_presence():
    # Same person must resolve identically in both subsystems
    assert normalize_identity(net_id=GDK) == parse_net_id(GDK)
    assert normalize_identity(net_id=STEAM) == parse_net_id(STEAM)


# --- presence -------------------------------------------------------------


def _sample(db, players, *, at):
    update_presence(db, server_id=1, online_players=players, now=at)
    db.commit()


def test_game_pass_player_accrues_playtime(db):
    t0 = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    player = [{"steamid": GDK, "name": "Jay", "ip": "10.0.0.5"}]

    _sample(db, player, at=t0)
    _sample(db, player, at=t0 + timedelta(seconds=60))

    rows = db.query(PlayerServerStats).all()
    assert len(rows) == 1
    # Keyed by the id exactly as the adapter emitted it, so kick/ban round-trips
    assert rows[0].steam_id == GDK
    assert rows[0].total_seconds == 60
    assert rows[0].visit_count == 1
    assert rows[0].last_ip == "10.0.0.5"


def test_enriched_row_fills_every_column_that_was_empty(db):
    t0 = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    player = [{"steamid": GDK, "name": "Jay", "ip": "10.0.0.5"}]
    _sample(db, player, at=t0)
    _sample(db, player, at=t0 + timedelta(seconds=90))

    out = enrich_player_list(db, 1, player, now=t0 + timedelta(seconds=90))[0]
    assert out["rank"] == 1 and out["ranked_players"] == 1
    assert out["session_seconds"] == 90
    assert out["total_seconds"] == 90
    assert out["visit_count"] == 1
    assert out["last_seen_pretty"] == "Online"
    assert out["ip"] == "10.0.0.5"


def test_steam_and_game_pass_players_rank_against_each_other(db):
    t0 = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    both = [
        {"steamid": STEAM, "name": "Lyra"},
        {"steamid": GDK, "name": "Jay"},
    ]
    _sample(db, both, at=t0)
    _sample(db, both, at=t0 + timedelta(seconds=30))
    # Jay leaves: the sample that notices credits his final slice (30s → 60s
    # total) and closes the session, so only Lyra accrues from here on.
    _sample(db, [both[0]], at=t0 + timedelta(seconds=60))
    _sample(db, [both[0]], at=t0 + timedelta(seconds=120))

    enriched = enrich_player_list(db, 1, both, now=t0 + timedelta(seconds=120))
    by_name = {p["name"]: p for p in enriched}
    assert by_name["Lyra"]["rank"] == 1
    assert by_name["Jay"]["rank"] == 2
    assert by_name["Lyra"]["total_seconds"] > by_name["Jay"]["total_seconds"]


def test_only_steam_ids_get_a_steam_profile_url(db):
    t0 = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    _sample(
        db,
        [{"steamid": STEAM, "name": "Lyra"}, {"steamid": GDK, "name": "Jay"}],
        at=t0,
    )
    cache = {(r.platform, r.external_id): r for r in db.query(IdentityCache).all()}

    steam_row = cache[("steam", STEAM)]
    assert steam_row.display_name == "Lyra"
    assert "steamcommunity.com" in steam_row.profile_url

    # A Game Pass id filed as steam would send the dossier to a page that
    # cannot exist, and would burn Steam Web API quota resolving nothing.
    xbox_row = cache[("xbox", "2535470764765514")]
    assert xbox_row.display_name == "Jay"
    assert not xbox_row.profile_url


def test_junk_ids_still_never_create_rows(db):
    _sample(
        db,
        [{"steamid": "", "name": "Blank"}, {"steamid": "NULL", "name": "Junk"}],
        at=datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc),
    )
    assert db.query(PlayerServerStats).count() == 0


# --- last visit -----------------------------------------------------------


def test_first_visit_has_no_previous_session(db):
    t0 = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    player = [{"steamid": GDK, "name": "Jay"}]
    _sample(db, player, at=t0)

    out = enrich_player_list(db, 1, player, now=t0)[0]
    assert out["previous_seen_at"] is None
    assert out["previous_seen_pretty"] == "First visit"


def test_rejoining_records_when_the_previous_session_ended(db):
    """The value the player table shows while someone is online.

    last_seen_at is overwritten on every sample, so the previous leave time has
    to be captured at the moment a new visit starts or it is lost.
    """
    t0 = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    player = [{"steamid": GDK, "name": "Jay"}]

    _sample(db, player, at=t0)
    left_at = t0 + timedelta(minutes=5)
    _sample(db, player, at=left_at)
    # Gone for two hours (max_tick_seconds keeps the gap uncredited)
    _sample(db, [], at=t0 + timedelta(hours=2))
    back_at = t0 + timedelta(hours=2, minutes=5)
    _sample(db, player, at=back_at)

    row = db.query(PlayerServerStats).one()
    assert row.previous_seen_at is not None
    assert row.visit_count == 2

    out = enrich_player_list(db, 1, player, now=back_at)[0]
    # Still online, so the old column would have said "Online" here
    assert out["last_seen_pretty"] == "Online"
    assert out["previous_seen_pretty"] == "2h ago"


def test_a_past_visit_seconds_ago_is_not_reported_as_online(db):
    t0 = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    player = [{"steamid": GDK, "name": "Jay"}]
    _sample(db, player, at=t0)
    _sample(db, [], at=t0 + timedelta(seconds=10))
    _sample(db, player, at=t0 + timedelta(seconds=20))

    out = enrich_player_list(db, 1, player, now=t0 + timedelta(seconds=20))[0]
    # format_last_seen would have said "Online" for a <90s delta
    assert out["previous_seen_pretty"] == "just now"


def test_existing_rows_predating_the_column_do_not_invent_a_time(db):
    t0 = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    player = [{"steamid": GDK, "name": "Jay"}]
    _sample(db, player, at=t0)
    # Simulate an upgraded install: several visits recorded, column still NULL
    row = db.query(PlayerServerStats).one()
    row.visit_count = 4
    row.previous_seen_at = None
    db.commit()

    out = enrich_player_list(db, 1, player, now=t0)[0]
    assert out["previous_seen_at"] is None
    # Not "First visit" — we know they've been here before, just not when
    assert out["previous_seen_pretty"] == "—"


# --- Sandstorm regression -------------------------------------------------


def test_source_ids_are_stored_exactly_as_before(db):
    """Widening the filter must not change the key Sandstorm rows use.

    Existing player_server_stats rows are keyed on the bare SteamID64; a
    different key here would orphan every historical playtime record.
    """
    t0 = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    _sample(db, [{"steamid": STEAM, "name": "Lyra", "ip": "1.2.3.4"}], at=t0)
    row = db.query(PlayerServerStats).one()
    assert row.steam_id == STEAM


# --- leaving must be observed ---------------------------------------------


def test_a_read_but_empty_roster_closes_the_session(db):
    """The bug that made "Last visit" unreachable for Palworld.

    Both presence call sites used to run only when player_list was non-empty
    (Sandstorm escaped via a `source == "rcon"` check). So when the last player
    on a REST-API server left, their session was never closed, no new visit was
    ever counted, and previous_seen_at was never captured.
    """
    t0 = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    player = [{"steamid": GDK, "name": "Jay"}]
    _sample(db, player, at=t0)
    _sample(db, [], at=t0 + timedelta(seconds=30))  # empty, but read

    row = db.query(PlayerServerStats).one()
    assert row.session_started_at is None, "leaving was not observed"

    _sample(db, player, at=t0 + timedelta(hours=1))
    row = db.query(PlayerServerStats).one()
    assert row.visit_count == 2
    assert row.previous_seen_at is not None

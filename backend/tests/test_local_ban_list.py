"""Ban list for games that cannot enumerate their own bans.

Palworld keeps bans in a ``banlist.txt`` the REST API never exposes, so the only
bans knowable are the ones this app issued. rebuild_local_bans folds the
moderation log into the same ServerBanEntry cache a live ``listbans`` fills, so
pagination and name resolution reuse one path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, PlayerActionLog, PlayerServerStats, Server, ServerBanEntry
from app.server_types import get_adapter
from app.services.ban_cache import load_cached_bans, rebuild_local_bans

GDK_NET_ID = "gdk_2535470764765514"
XBOX_ID = "2535470764765514"
STEAM = "76561198084350159"
T0 = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(
        Server(id=1, name="Pal", host="h", query_port=8212, rcon_port=8212,
               server_type="palworld")
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _log(db, action, *, platform="xbox", external_id=XBOX_ID, net_id=GDK_NET_ID,
         name="Jay", reason="", at=T0, ok=True):
    db.add(
        PlayerActionLog(
            platform=platform, external_id=external_id, net_id=net_id, action=action,
            server_id=1, server_name="Pal", player_name=name, reason=reason,
            ok=ok, created_at=at,
        )
    )
    db.commit()


def _entries(db):
    return db.query(ServerBanEntry).filter(ServerBanEntry.server_id == 1).all()


# --- capability -----------------------------------------------------------


def test_palworld_declares_a_locally_sourced_ban_list():
    info = get_adapter("palworld").info
    assert info.features.ban_list is True
    assert info.ban_list_source == "local"


def test_sandstorm_still_queries_the_server():
    assert get_adapter("sandstorm").info.ban_list_source == "live"


# --- folding the log ------------------------------------------------------


def test_a_ban_appears_in_the_list(db):
    _log(db, "ban", reason="griefing")
    assert rebuild_local_bans(db, 1) == 1
    row = _entries(db)[0]
    assert row.raw_id == GDK_NET_ID
    assert row.display_id == XBOX_ID
    assert row.reason == "griefing"
    # No Palworld endpoint takes a duration
    assert row.permanent is True and row.duration == "Permanent"


def test_a_later_unban_removes_them(db):
    _log(db, "ban", at=T0)
    _log(db, "unban", at=T0 + timedelta(minutes=5))
    assert rebuild_local_bans(db, 1) == 0
    assert _entries(db) == []


def test_a_re_ban_after_an_unban_counts_again(db):
    _log(db, "ban", at=T0)
    _log(db, "unban", at=T0 + timedelta(minutes=5))
    _log(db, "ban", at=T0 + timedelta(minutes=10), reason="again")
    assert rebuild_local_bans(db, 1) == 1
    assert _entries(db)[0].reason == "again"


def test_kicks_are_not_bans(db):
    _log(db, "kick", reason="afk")
    assert rebuild_local_bans(db, 1) == 0


def test_failed_bans_are_not_listed(db):
    _log(db, "ban", ok=False, reason="server was down")
    assert rebuild_local_bans(db, 1) == 0


def test_rebuilding_is_idempotent(db):
    _log(db, "ban")
    rebuild_local_bans(db, 1)
    rebuild_local_bans(db, 1)
    assert len(_entries(db)) == 1


def test_bans_on_another_server_are_not_mixed_in(db):
    db.add(
        Server(id=2, name="Other", host="h", query_port=8212, rcon_port=8212,
               server_type="palworld")
    )
    db.commit()
    _log(db, "ban")
    assert rebuild_local_bans(db, 2) == 0


# --- recovering the id /unban needs ---------------------------------------


def test_the_exact_platform_id_is_preserved_for_unban(db):
    """(platform, external_id) is lossy: gdk_ and xsx_ both mean "xbox"."""
    _log(db, "ban")
    rebuild_local_bans(db, 1)
    # Sending the canonical form back would not match anything on the server
    assert _entries(db)[0].net_id == GDK_NET_ID
    assert _entries(db)[0].net_id != XBOX_ID


def test_bans_logged_before_net_id_existed_recover_it_from_presence(db):
    """Rows written before the net_id column have only the canonical pair.

    Presence stores the raw id we last saw the player under, which puts the
    platform prefix back so their unban button still works.
    """
    _log(db, "ban", net_id="")
    db.add(
        PlayerServerStats(
            server_id=1, steam_id=GDK_NET_ID, last_name="Jay",
            first_seen_at=T0, last_seen_at=T0, total_seconds=60, visit_count=1,
        )
    )
    db.commit()

    rebuild_local_bans(db, 1)
    assert _entries(db)[0].raw_id == GDK_NET_ID


def test_an_unrecoverable_id_still_lists_rather_than_vanishing(db):
    # Never seen online and logged before net_id - show what we know
    _log(db, "ban", net_id="")
    rebuild_local_bans(db, 1)
    assert _entries(db)[0].raw_id == XBOX_ID


def test_steam_bans_round_trip_unchanged(db):
    _log(db, "ban", platform="steam", external_id=STEAM, net_id=STEAM, name="Lyra")
    rebuild_local_bans(db, 1)
    assert _entries(db)[0].raw_id == STEAM


# --- rendering ------------------------------------------------------------


def test_the_list_paginates_through_the_shared_cache_path(db):
    for i in range(7):
        _log(
            db, "ban", platform="steam", external_id=f"7656119800000{i:04d}",
            net_id=f"7656119800000{i:04d}", name=f"P{i}", at=T0 + timedelta(minutes=i),
        )
    assert rebuild_local_bans(db, 1) == 7

    page1 = load_cached_bans(db, 1, page=1, page_size=5)
    assert page1["total"] == 7 and page1["total_pages"] == 2
    assert len(page1["bans"]) == 5
    page2 = load_cached_bans(db, 1, page=2, page_size=5)
    assert len(page2["bans"]) == 2
    # No overlap between pages
    ids1 = {b["raw_id"] for b in page1["bans"]}
    ids2 = {b["raw_id"] for b in page2["bans"]}
    assert not ids1 & ids2


def test_most_recent_ban_is_listed_first(db):
    _log(db, "ban", platform="steam", external_id=STEAM, net_id=STEAM,
         name="Old", at=T0)
    _log(db, "ban", name="New", at=T0 + timedelta(hours=1))
    rebuild_local_bans(db, 1)
    first = sorted(_entries(db), key=lambda r: r.sort_index)[0]
    assert first.raw_id == GDK_NET_ID


def test_crossplay_bans_show_the_players_name(db):
    """resolve_names only knows Steam and EOS, so a gdk_ id resolved to nothing.

    Presence and moderation already cache the name under (xbox, external_id);
    a ban list of opaque platform ids is far less useful than one with names.
    """
    from app.models import IdentityCache

    _log(db, "ban", reason="griefing")
    db.add(
        IdentityCache(platform="xbox", external_id=XBOX_ID, display_name="Jay",
                      profile_url="", avatar_url="", source="presence")
    )
    db.commit()
    rebuild_local_bans(db, 1)

    entry = load_cached_bans(db, 1)["bans"][0]
    assert entry["display_name"] == "Jay"
    # No Steam profile page exists for an Xbox account
    assert entry["profile_url"] == ""


def test_ban_numbering_is_one_based_with_no_gaps(db):
    """Live listbans numbers from 1; an unbanned row must not leave a hole."""
    _log(db, "ban", platform="steam", external_id=STEAM, net_id=STEAM,
         name="Lyra", at=T0)
    # Most recent action is an unban, so this identity drops out entirely
    _log(db, "ban", at=T0 + timedelta(minutes=1))
    _log(db, "unban", at=T0 + timedelta(minutes=2))
    _log(db, "ban", platform="psn", external_id="abc123", net_id="psn_abc123",
         name="Kes", at=T0 + timedelta(minutes=3))

    assert rebuild_local_bans(db, 1) == 2
    indexes = sorted(r.sort_index for r in _entries(db))
    assert indexes == [1, 2]

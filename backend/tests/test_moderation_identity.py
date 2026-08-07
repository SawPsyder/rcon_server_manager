"""Moderation history must reach the dossier for crossplay identities.

A kick on a Game Pass player used to be written as ``platform='unknown'`` with
the ``gdk_`` prefix left on the external_id, while the dossier popup looks up
``(xbox, 2535…)`` — so the action was stored but never displayed, and the
history read as empty.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.migrate import _renormalize_unknown_identities
from app.models import Base, IdentityCache, PlayerActionLog, Server
from app.services.player_records import get_dossier, log_player_action

GDK_NET_ID = "gdk_2535470764765514"
XBOX_ID = "2535470764765514"
STEAM = "76561198084350159"


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db(engine):
    session = sessionmaker(bind=engine)()
    server = Server(id=1, name="Pal", host="h", query_port=8212, rcon_port=8212,
                    server_type="palworld")
    session.add(server)
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _server(db):
    return db.get(Server, 1)


# --- logging --------------------------------------------------------------


def test_kicking_a_game_pass_player_is_filed_under_xbox(db):
    row = log_player_action(
        db, server=_server(db), action="kick", net_id=GDK_NET_ID,
        player_name="Jay", reason="haha", ok=True,
    )
    db.commit()
    assert row is not None
    assert (row.platform, row.external_id) == ("xbox", XBOX_ID)


def test_the_dossier_popup_finds_that_kick(db):
    log_player_action(
        db, server=_server(db), action="kick", net_id=GDK_NET_ID,
        player_name="Jay", reason="haha", ok=True,
    )
    db.commit()

    # Exactly what the frontend requests after parseIdentity("gdk_2535…")
    dossier = get_dossier(db, "xbox", XBOX_ID)
    assert len(dossier["actions"]) == 1
    assert dossier["actions"][0].action == "kick"
    assert dossier["display_name"] == "Jay"


def test_names_are_cached_for_non_steam_platforms_too(db):
    """Without this the dossier has no name unless presence saw them first."""
    log_player_action(
        db, server=_server(db), action="ban", net_id=GDK_NET_ID,
        player_name="Jay", ok=True,
    )
    db.commit()
    row = (
        db.query(IdentityCache)
        .filter(IdentityCache.platform == "xbox", IdentityCache.external_id == XBOX_ID)
        .one()
    )
    assert row.display_name == "Jay"
    # No public profile page exists for an Xbox id
    assert not row.profile_url


def test_steam_moderation_is_unchanged(db):
    row = log_player_action(
        db, server=_server(db), action="kick", net_id=STEAM,
        player_name="Lyra", ok=True,
    )
    db.commit()
    assert (row.platform, row.external_id) == ("steam", STEAM)
    cached = (
        db.query(IdentityCache)
        .filter(IdentityCache.platform == "steam", IdentityCache.external_id == STEAM)
        .one()
    )
    assert "steamcommunity.com" in cached.profile_url


# --- migration ------------------------------------------------------------


def test_migration_refiles_rows_written_before_crossplay_support(engine, db):
    """Rows already in the DB are otherwise invisible forever."""
    db.add(
        PlayerActionLog(
            platform="unknown", external_id=GDK_NET_ID, action="kick",
            server_id=1, server_name="Pal", player_name="Jay", ok=True,
        )
    )
    db.commit()
    assert get_dossier(db, "xbox", XBOX_ID)["actions"] == []

    _renormalize_unknown_identities(engine)
    db.expire_all()

    row = db.query(PlayerActionLog).one()
    assert (row.platform, row.external_id) == ("xbox", XBOX_ID)
    assert len(get_dossier(db, "xbox", XBOX_ID)["actions"]) == 1


def test_migration_is_idempotent_and_leaves_real_unknowns_alone(engine, db):
    db.add(
        PlayerActionLog(
            platform="unknown", external_id="some-opaque-handle", action="kick",
            server_id=1, server_name="Pal", player_name="Ghost", ok=True,
        )
    )
    db.add(
        PlayerActionLog(
            platform="unknown", external_id=GDK_NET_ID, action="kick",
            server_id=1, server_name="Pal", player_name="Jay", ok=True,
        )
    )
    db.commit()

    for _ in range(2):
        _renormalize_unknown_identities(engine)
    db.expire_all()

    by_name = {r.player_name: r for r in db.query(PlayerActionLog).all()}
    assert (by_name["Jay"].platform, by_name["Jay"].external_id) == ("xbox", XBOX_ID)
    # Nothing to resolve it to, so it must not be mangled
    assert by_name["Ghost"].platform == "unknown"
    assert by_name["Ghost"].external_id == "some-opaque-handle"


def test_migration_skips_tables_that_do_not_exist():
    empty = create_engine("sqlite://")
    _renormalize_unknown_identities(empty)  # must not raise

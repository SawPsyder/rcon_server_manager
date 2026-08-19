"""Dune adapter: registry flags, roster mapping, status/sample contracts."""

from __future__ import annotations

import pytest

from app.server_types import get_adapter
from app.server_types.dune import (
    apply_steam_personas,
    dune_adapter,
    enrich_dune_roster,
    kick_target,
    map_label,
    normalize_player,
    roster_entry,
    sorted_map_labels,
    status_extra,
)
from app.services import dune_api
from app.services.dune_api import DuneApiError, DuneAuthError

RAW_STATUS = {
    "ok": True,
    "totalPlayers": 2,
    "totalServers": 5,
    "uptimeSeconds": 3661,
    "maps": [
        {"map": "Survival_1", "status": "healthy", "players": 1},
        {"map": "DeepDesert_1", "status": "healthy", "players": 1},
        {"map": "SH_Arrakeen", "status": "starting", "players": 0},
    ],
    "pool": {"size": 25, "used": 5, "free": 20},
}

RAW_PLAYERS = [
    {
        "fls_id": "DE0BCCAA2501BF22",
        "character": "Sergentval",
        "steam_id": "76561198041278656",
        "platform_name": "Steam",
        "life": "Alive",
        "online": "Online",
        "last_avatar_activity": "2026-05-28 07:22:05.861+00",
    },
    {
        "fls_id": "AABBCCDDEEFF0011",
        "character": "",
        "steam_id": "",
        "platform_name": "EOS",
        "life": "Alive",
        "online": "Online",
        "last_avatar_activity": "",
    },
]


class FakeClient:
    def __init__(
        self, *, status=None, players=None, partitions=None, info=None, fail=None
    ):
        self._status = RAW_STATUS if status is None else status
        self._players = RAW_PLAYERS if players is None else players
        self._partitions = partitions if partitions is not None else {"ok": True, "partitions": []}
        self._info = info if info is not None else {"display_name": "", "player_hard_cap": None}
        self._fail = fail or {}
        self.calls: list[tuple[str, tuple]] = []

    def _maybe_fail(self, name):
        if name in self._fail:
            raise self._fail[name]

    def status(self):
        self._maybe_fail("status")
        self.calls.append(("status", ()))
        return self._status

    def server_info(self, **kwargs):
        self.calls.append(("server_info", ()))
        return dict(self._info)

    def players(self, filter="online"):
        self._maybe_fail("players")
        self.calls.append(("players", (filter,)))
        return self._players

    def partitions(self):
        self._maybe_fail("partitions")
        self.calls.append(("partitions", ()))
        return self._partitions

    def broadcast(self, title, body, duration=30):
        self._maybe_fail("broadcast")
        self.calls.append(("broadcast", (title, body, duration)))
        return {"ok": True, "stdout": "publish=ok", "stderr": ""}

    def kick(self, player_id):
        self._maybe_fail("kick")
        self.calls.append(("kick", (player_id,)))
        return {"ok": True, "stdout": "publish=ok", "stderr": ""}


@pytest.fixture
def fake_pool(monkeypatch):
    holder: dict[str, object] = {}

    def install(client=None, *, error=None):
        holder["client"] = client
        holder["error"] = error

        def _client(endpoint, *, timeout=15.0):
            holder["endpoint"] = endpoint
            if error is not None:
                raise error
            return client

        monkeypatch.setattr(dune_api.dune_pool, "client", _client)
        return holder

    return install


def test_registered_with_expected_capabilities():
    adapter = get_adapter("dune")
    assert adapter is dune_adapter
    info = adapter.info
    assert info.default_query_port == 8090 and info.default_rcon_port == 8090
    assert info.endpoint_style == "single_port"
    assert info.secret_label == "Admin UI password"
    assert info.features.structured_player_list is True
    assert info.features.kick_ban is True
    assert info.features.timed_ban is False
    assert info.features.perm_ban is False
    assert info.features.ban_list is False
    assert info.features.admin_say is True
    assert info.features.a2s_query is False
    assert info.features.admin_api is True
    assert info.features.console is True
    assert info.features.tls_optional is True
    assert info.features.player_score is False
    assert info.features.tick_rate_history is False


def test_steam_roster_uses_bare_steamid64():
    entry = roster_entry(RAW_PLAYERS[0])
    assert entry["name"] == "Sergentval"
    assert entry["steamid"] == "76561198041278656"
    assert entry["extra"]["fls_id"] == "DE0BCCAA2501BF22"
    assert "online" not in entry["extra"]


def test_apply_steam_personas_keeps_character_and_adds_steam():
    rows = [roster_entry(RAW_PLAYERS[0])]
    apply_steam_personas(
        rows, {"76561198041278656": {"display_name": "sergent_val"}}
    )
    assert rows[0]["name"] == "Sergentval"
    assert rows[0]["extra"]["steam_name"] == "sergent_val"


def test_apply_steam_personas_promotes_when_character_missing():
    nameless = {**RAW_PLAYERS[0], "character": ""}
    rows = [roster_entry(nameless)]
    assert rows[0]["name"] == "DE0BCCAA2501BF22"
    apply_steam_personas(
        rows, {"76561198041278656": {"display_name": "sergent_val"}}
    )
    assert rows[0]["name"] == "sergent_val"
    assert rows[0]["extra"]["steam_name"] == "sergent_val"


def test_enrich_dune_roster_fetches_community_when_cache_is_only_presence(monkeypatch):
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.services.identity import remember_identity

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    remember_identity(
        session,
        platform="steam",
        external_id="76561198041278656",
        display_name="Sergentval",
        source="presence",
        commit=True,
    )

    def fake_community(ids):
        assert ids == ["76561198041278656"]
        return {
            "76561198041278656": {
                "display_name": "sergent_val",
                "profile_url": "https://steamcommunity.com/profiles/76561198041278656",
                "avatar_url": "",
                "source": "steam_community",
            }
        }

    monkeypatch.setattr(
        "app.services.identity.fetch_steam_community_names", fake_community
    )
    rows = [roster_entry(RAW_PLAYERS[0])]
    enrich_dune_roster(session, rows)
    assert rows[0]["name"] == "Sergentval"
    assert rows[0]["extra"]["steam_name"] == "sergent_val"
    session.close()


def test_apply_steam_personas_ignores_presence_echoing_the_character():
    rows = [roster_entry(RAW_PLAYERS[0])]
    apply_steam_personas(
        rows,
        {"76561198041278656": {"display_name": "Sergentval", "source": "presence"}},
    )
    assert rows[0]["name"] == "Sergentval"
    assert "steam_name" not in rows[0]["extra"]


def test_non_steam_roster_falls_back_to_fls():
    entry = roster_entry(RAW_PLAYERS[1])
    assert entry["name"] == "AABBCCDDEEFF0011"
    assert entry["steamid"] == "AABBCCDDEEFF0011"
    assert entry["extra"]["fls_id"] == "AABBCCDDEEFF0011"


def test_kick_target_prefers_steam_prefix():
    assert kick_target(player_name="Sergentval", net_id="76561198041278656") == (
        "steam:76561198041278656"
    )
    assert kick_target(player_name="x", net_id="de0bccaa2501bf22") == "DE0BCCAA2501BF22"


def test_query_status_from_grid(fake_pool):
    fake_pool(FakeClient())
    snap = dune_adapter.query_status("quantumrabbit", 8090, secret="pw")
    assert snap["online"] is True
    assert snap["players"] == 2
    assert snap["extra"]["instances"] == 5
    assert snap["extra"]["live_maps"] == 2
    # Region names, A-Z; the starting instance is not live yet.
    assert snap["map"] == "Deep Desert, Hagga Basin"
    assert snap["extra"]["maps"] == "Deep Desert, Hagga Basin"


def test_query_status_reads_name_and_cap_from_settings(fake_pool):
    fake_pool(FakeClient(info={"display_name": "Arrakis", "player_hard_cap": 40}))
    snap = dune_adapter.query_status("quantumrabbit", 8090, secret="pw")
    assert snap["hostname"] == "Arrakis"
    assert snap["max_players"] == 40


def test_query_status_leaves_cap_unset_when_not_configured(fake_pool):
    fake_pool(FakeClient())
    snap = dune_adapter.query_status("quantumrabbit", 8090, secret="pw")
    assert snap["hostname"] is None
    assert snap["max_players"] is None


def test_query_status_auth_error_is_reachable(fake_pool):
    fake_pool(error=DuneAuthError("bad password", status=401))
    snap = dune_adapter.query_status("quantumrabbit", 8090, secret="wrong")
    assert snap["online"] is True
    assert "password" in (snap["error"] or "").lower()


def test_sample_players_marks_roster_known(fake_pool):
    fake_pool(FakeClient())
    snap = dune_adapter.sample_players("quantumrabbit", 8090, rcon_password="pw")
    assert snap["online"] is True
    assert snap["roster_known"] is True
    assert snap["players"] == 2
    assert snap["player_list"][0]["steamid"] == "76561198041278656"
    assert snap["source"] == "admin_http"


def test_say_and_kick_commands(fake_pool):
    client = FakeClient()
    fake_pool(client)
    out = dune_adapter.execute_command(
        "quantumrabbit", port=8090, secret="pw", command='say "spice must flow"'
    )
    assert "Broadcast" in "".join(str(c) for c in client.calls) or client.calls[0][0] == "broadcast"
    assert client.calls[0] == ("broadcast", ("Broadcast", "spice must flow", 30))
    assert "spice must flow" in out or "ok" in out.lower() or "Broadcast" in out

    dune_adapter.execute_command(
        "quantumrabbit", port=8090, secret="pw", command="kick steam:76561198041278656"
    )
    assert client.calls[-1] == ("kick", ("steam:76561198041278656",))


def test_build_kick_uses_steam_form():
    cmd = dune_adapter.build_kick_command(
        player_name="Sergentval", net_id="76561198041278656", reason="out"
    )
    assert cmd == "kick steam:76561198041278656"


def test_ban_builders_refuse():
    with pytest.raises(DuneApiError):
        dune_adapter.build_permban_command(player_name="x", net_id="1", reason="no")


def test_status_extra_counts_healthy_maps():
    extra = status_extra(RAW_STATUS)
    assert extra["live_maps"] == 2
    assert extra["instances"] == 5
    assert extra["uptime"] == "1h 1m"


def test_map_label_maps_instance_names_to_regions():
    assert map_label("Survival_1") == "Hagga Basin"
    assert map_label("SH_HarkoVillage") == "Harko Village"
    assert map_label("DeepDesert_1") == "Deep Desert"
    # Unknown instances still lose the prefix rather than leaking raw keys.
    assert map_label("CB_Dungeon_ThePit") == "Dungeon ThePit"


def test_sorted_map_labels_are_alphabetical_and_healthy_only():
    grid = {
        "maps": [
            {"map": "SH_HarkoVillage", "status": "healthy", "players": 0},
            {"map": "Survival_1", "status": "healthy", "players": 0},
            {"map": "DeepDesert_1", "status": "healthy", "players": 0},
            {"map": "SH_Arrakeen", "status": "starting", "players": 0},
        ]
    }
    assert sorted_map_labels(grid["maps"]) == [
        "Deep Desert",
        "Hagga Basin",
        "Harko Village",
    ]
    assert sorted_map_labels(None) == []


def test_sample_players_reports_cap_zero_when_unset(fake_pool):
    fake_pool(FakeClient())
    snap = dune_adapter.sample_players("quantumrabbit", 8090, rcon_password="pw")
    assert snap["max_players"] == 0
    fake_pool(FakeClient(info={"display_name": "", "player_hard_cap": 40}))
    snap = dune_adapter.sample_players("quantumrabbit", 8090, rcon_password="pw")
    assert snap["max_players"] == 40


def test_normalize_player_exported():
    assert normalize_player(RAW_PLAYERS[0])["fls_id"] == "DE0BCCAA2501BF22"

"""Palworld adapter: field mapping onto the generic status/sample contracts."""

from __future__ import annotations

import pytest

from app.api.palworld import summarize_game_data
from app.server_types import get_adapter
from app.server_types.palworld import (
    normalize_info,
    normalize_metrics,
    normalize_player,
    palworld_adapter,
    roster_entry,
    to_api_user_id,
    to_local_id,
)
from app.services import palworld_api
from app.services.palworld_api import PalworldApiError, PalworldAuthError

RAW_INFO = {
    "version": "v1.0.2",
    "servername": "Pal Prime",
    "description": "A server.",
    "worldguid": "A7E97BAA767DB9029EF013BB71E993A0",
}

RAW_METRICS = {
    "serverfps": 57,
    "currentplayernum": 3,
    "serverframetime": 17.5439,
    "maxplayernum": 32,
    "uptime": 3600,
    "basecampnum": 5,
    "days": 4,
}

RAW_PLAYERS = [
    {
        "name": "Lyra",
        "accountName": "lyra_builds",
        "playerId": "AFAFD830000000000000000000000000",
        "userId": "steam_76561198084350159",
        "ip": "10.0.0.20",
        "ping": 3.14,
        "location_x": 123.45,
        "location_y": 67.89,
        "level": 21,
        "building_count": 119,
    },
    {
        "name": "XboxPal",
        "accountName": "xpal",
        "playerId": "BFAFD830000000000000000000000000",
        "userId": "xsx_2535412345678901",
        "ip": "10.0.0.21",
        "ping": 44.0,
        "level": 7,
        "building_count": 3,
    },
]


class FakeClient:
    """Stands in for a pooled PalworldClient."""

    def __init__(self, *, info=None, metrics=None, players=None, settings=None,
                 game_data=None, fail=None):
        self._info = RAW_INFO if info is None else info
        self._metrics = RAW_METRICS if metrics is None else metrics
        self._players = RAW_PLAYERS if players is None else players
        self._settings = settings if settings is not None else {"Difficulty": "None"}
        self._game_data = game_data or {"Time": "2026-08-07 12:00:00", "ActorData": []}
        self._fail = fail or {}
        self.calls: list[tuple[str, tuple]] = []

    def _maybe_fail(self, name):
        if name in self._fail:
            raise self._fail[name]

    def info(self):
        self._maybe_fail("info")
        self.calls.append(("info", ()))
        return self._info

    def metrics(self):
        self._maybe_fail("metrics")
        self.calls.append(("metrics", ()))
        return self._metrics

    def players(self):
        self._maybe_fail("players")
        self.calls.append(("players", ()))
        return self._players

    def settings(self):
        self._maybe_fail("settings")
        self.calls.append(("settings", ()))
        return self._settings

    def game_data(self):
        self._maybe_fail("game_data")
        self.calls.append(("game_data", ()))
        return self._game_data

    def announce(self, message):
        self._maybe_fail("announce")
        self.calls.append(("announce", (message,)))
        return "The message was announced."

    def kick(self, userid, message=""):
        self._maybe_fail("kick")
        self.calls.append(("kick", (userid, message)))
        return "The player was kicked."

    def ban(self, userid, message=""):
        self._maybe_fail("ban")
        self.calls.append(("ban", (userid, message)))
        return "The player was banned."

    def unban(self, userid):
        self._maybe_fail("unban")
        self.calls.append(("unban", (userid,)))
        return "The player was unbanned."

    def save(self):
        self._maybe_fail("save")
        self.calls.append(("save", ()))
        return "Successfully saved the world."

    def shutdown(self, waittime, message=""):
        self._maybe_fail("shutdown")
        self.calls.append(("shutdown", (waittime, message)))
        return "The server will shutdown."

    def stop(self):
        self._maybe_fail("stop")
        self.calls.append(("stop", ()))
        return "The server force stopped."


@pytest.fixture
def fake_pool(monkeypatch):
    """Replace the pool so adapter tests never touch the network."""
    holder: dict[str, object] = {}

    def install(client=None, *, error=None):
        holder["client"] = client
        holder["error"] = error

        def _client(endpoint, *, timeout=10.0):
            holder["endpoint"] = endpoint
            if error is not None:
                raise error
            return client

        monkeypatch.setattr(palworld_api.palworld_pool, "client", _client)
        return holder

    return install


# --- registry -------------------------------------------------------------


def test_registered_with_expected_capabilities():
    adapter = get_adapter("palworld")
    assert adapter is palworld_adapter
    info = adapter.info
    # The REST API port, not the game port (8211) and not RCON (25575)
    assert info.default_query_port == 8212 and info.default_rcon_port == 8212
    assert info.endpoint_style == "single_port"
    assert info.secret_label == "Admin password"
    # Unlike Satisfactory, /v1/api/players gives a real roster
    assert info.features.structured_player_list is True
    assert info.features.kick_ban is True
    # /v1/api/ban has no duration — only permanent bans (Permaban in the UI)
    assert info.features.timed_ban is False
    assert info.features.perm_ban is True
    assert info.features.admin_say is True
    # There is no ban-list endpoint (bans live in banlist.txt on the host), so
    # the list is shown but sourced from our own moderation history
    assert info.features.ban_list is True
    assert info.ban_list_source == "local"
    assert info.features.map_travel is False
    assert info.features.a2s_query is False
    assert info.features.admin_api is True
    assert info.features.console is True
    assert info.features.tick_rate_history is True
    assert info.features.tls_optional is True
    # The tick_rate series is server FPS here, so the chart must relabel
    assert (info.tick_rate_label, info.tick_rate_unit, info.tick_rate_target) == (
        "Server FPS",
        "fps",
        60,
    )


# --- identifiers ----------------------------------------------------------


def test_steam_user_ids_become_bare_steamid64():
    # presence.py only tracks 17 numeric digits, so the prefix has to go
    assert to_local_id("steam_76561198084350159") == "76561198084350159"
    assert to_api_user_id("76561198084350159") == "steam_76561198084350159"


def test_non_steam_user_ids_pass_through_untouched():
    for raw in ("xsx_2535412345678901", "psn_someplayer", ""):
        assert to_local_id(raw) == raw
        assert to_api_user_id(raw) == raw


def test_malformed_steam_prefix_is_not_stripped():
    # Only strip when what follows really is a numeric ID
    assert to_local_id("steam_not_a_number") == "steam_not_a_number"


# --- parsing --------------------------------------------------------------


def test_metrics_absent_fields_are_none_not_zero():
    # basecampnum is 1.x-only and days post-0.2.x; a missing field must not
    # render as a real reading of 0
    parsed = normalize_metrics({"serverfps": 60, "currentplayernum": 0})
    assert parsed["server_fps"] == 60
    assert parsed["current_players"] == 0  # a legitimate zero survives
    assert parsed["base_camps"] is None
    assert parsed["days"] is None
    assert parsed["uptime"] is None


def test_player_mapping_keeps_generic_and_palworld_fields():
    parsed = normalize_player(RAW_PLAYERS[0])
    # Generic roster shape the shared table / presence / roster snapshots read
    assert parsed["name"] == "Lyra"
    assert parsed["steamid"] == "76561198084350159"
    assert parsed["ip"] == "10.0.0.20"
    # Palworld has no score. Level used to be mapped here, which showed the same
    # number twice once Level became a column of its own.
    assert parsed["score"] == 0
    assert palworld_adapter.info.features.player_score is False
    # Palworld-only detail, served in full by GET /palworld/players
    assert parsed["user_id"] == "steam_76561198084350159"
    assert parsed["account_name"] == "lyra_builds"
    assert parsed["building_count"] == 119
    # Documented as a double, not an int
    assert parsed["ping"] == pytest.approx(3.14)


def test_fields_are_read_regardless_of_key_casing():
    """Palworld contradicts its own docs on casing between endpoints.

    A miss here is silent and expensive: an unread ``userId`` empties steamid,
    which drops the player out of presence and blanks rank / session / total /
    visits / last seen in the shared player table.
    """
    parsed = normalize_player(
        {"Name": "Lyra", "UserId": "steam_76561198084350159", "IP": "10.0.0.5", "Level": 9}
    )
    assert parsed["name"] == "Lyra"
    assert parsed["steamid"] == "76561198084350159"
    assert parsed["ip"] == "10.0.0.5"
    assert parsed["level"] == 9

    assert normalize_metrics({"ServerFPS": 60})["server_fps"] == 60
    assert normalize_info({"ServerName": "Pal"})["server_name"] == "Pal"


def test_player_mapping_tolerates_missing_optional_fields():
    parsed = normalize_player({"name": "Bare", "userId": "steam_76561198084350159"})
    assert parsed["level"] is None and parsed["ping"] is None
    assert parsed["location_x"] is None and parsed["building_count"] is None
    assert parsed["score"] == 0


# --- shared player table (PlayerInfo.extra) -------------------------------


def test_roster_entry_splits_generic_keys_from_palworld_extras():
    entry = roster_entry(RAW_PLAYERS[0])
    # Flat keys are what presence and the roster snapshot read
    assert entry["name"] == "Lyra"
    assert entry["steamid"] == "76561198084350159"
    assert entry["ip"] == "10.0.0.20"
    # Everything Palworld-specific is namespaced so the table stays game-agnostic
    assert entry["extra"] == {
        "account_name": "lyra_builds",
        "level": 21,
        # Rounded here so the frontend never formats a 3.14159-style float
        "ping_ms": 3,
    }


def test_roster_entry_omits_coordinates_and_building_count():
    """Deliberately dropped when the panel's Players tab was merged away.

    Coordinates are too niche for a column and building_count is missing from
    several server builds. Both stay on GET /palworld/players.
    """
    entry = roster_entry(RAW_PLAYERS[0])
    assert not {"location_x", "location_y", "building_count"} & set(entry["extra"])
    full = normalize_player(RAW_PLAYERS[0])
    assert full["location_x"] == 123.45 and full["building_count"] == 119


def test_roster_entry_drops_fields_a_server_does_not_report():
    # Jan's live server sends no building_count and no accountName on some builds
    entry = roster_entry({"name": "Jay", "userId": "gdk_2535470764765514", "level": 4})
    assert entry["extra"] == {"level": 4}
    assert "ping_ms" not in entry["extra"]


def test_sample_players_roster_carries_extras(fake_pool):
    fake_pool(FakeClient())
    snap = palworld_adapter.sample_players("host", 8212, rcon_password="pw")
    assert snap["player_list"][0]["extra"]["level"] == 21
    assert snap["player_list"][0]["extra"]["account_name"] == "lyra_builds"


# --- query_status ---------------------------------------------------------


def test_query_status_maps_info_and_metrics(fake_pool):
    fake_pool(FakeClient())
    snap = palworld_adapter.query_status("host", 8212, secret="pw")
    assert snap["online"] is True
    assert snap["hostname"] == "Pal Prime"
    assert snap["version"] == "v1.0.2"
    assert snap["players"] == 3 and snap["max_players"] == 32
    # Palworld has no concept of these - None, never zeroed
    assert snap["map"] is None and snap["gamemode"] is None
    assert snap["bots"] is None and snap["ping_ms"] is None
    assert snap["extra"]["server_fps"] == 57
    assert snap["extra"]["in_game_days"] == 4
    assert snap["extra"]["base_camps"] == 5
    # The admin panel has no overview tab, so every at-a-glance value has to
    # reach the status cards through extra - including the version string,
    # which nothing renders from the top-level ServerStatus field.
    assert snap["extra"]["version"] == "v1.0.2"
    assert snap["extra"]["world_guid"] == RAW_INFO["worldguid"]


def test_query_status_offline_when_unreachable(fake_pool):
    fake_pool(None, error=PalworldApiError("Could not connect"))
    snap = palworld_adapter.query_status("host", 8212, secret="pw")
    assert snap["online"] is False
    assert "Could not connect" in snap["error"]


def test_query_status_reports_reachable_but_rejected(fake_pool):
    # Every Palworld endpoint needs auth, so a 401 proves the server is up.
    # Saying "offline" would send the operator debugging the wrong thing.
    fake_pool(FakeClient(fail={"info": PalworldAuthError("password rejected")}))
    snap = palworld_adapter.query_status("host", 8212, secret="wrong")
    assert snap["online"] is True
    assert "rejected" in snap["error"]


def test_query_status_survives_metrics_failure(fake_pool):
    fake_pool(FakeClient(fail={"metrics": PalworldApiError("boom")}))
    snap = palworld_adapter.query_status("host", 8212, secret="pw")
    assert snap["online"] is True
    assert snap["hostname"] == "Pal Prime"
    assert snap["players"] is None


# --- sample_players -------------------------------------------------------


def test_sample_players_reports_counts_fps_and_roster(fake_pool):
    fake_pool(FakeClient())
    snap = palworld_adapter.sample_players("host", 8212, rcon_password="pw")
    assert snap["online"] is True
    assert snap["players"] == 3 and snap["max_players"] == 32
    assert snap["source"] == "rest_api"
    assert snap["tick_rate"] == 57
    assert [p["steamid"] for p in snap["player_list"]] == [
        "76561198084350159",
        "xsx_2535412345678901",
    ]


def test_sample_players_never_raises(fake_pool):
    # The stats collector loop depends on always getting a snapshot back
    fake_pool(None, error=PalworldApiError("connection refused"))
    snap = palworld_adapter.sample_players("host", 8212, rcon_password="pw")
    assert snap["online"] is False
    assert snap["players"] == 0
    assert "connection refused" in snap["api_error"]
    # None, not 0.0 - the chart must show a gap, not a crash to zero
    assert snap["tick_rate"] is None


def test_sample_players_keeps_counts_when_roster_fails(fake_pool):
    fake_pool(FakeClient(fail={"players": PalworldApiError("players broke")}))
    snap = palworld_adapter.sample_players("host", 8212, rcon_password="pw")
    assert snap["online"] is True
    assert snap["players"] == 3
    assert snap["player_list"] == []
    assert "players broke" in snap["api_error"]


def test_sample_players_zero_fps_is_a_gap(fake_pool):
    fake_pool(FakeClient(metrics={**RAW_METRICS, "serverfps": 0}))
    snap = palworld_adapter.sample_players("host", 8212, rcon_password="pw")
    assert snap["online"] is True
    assert snap["tick_rate"] is None


def test_options_reach_the_endpoint(fake_pool):
    holder = fake_pool(FakeClient())
    palworld_adapter.sample_players(
        "host",
        8212,
        rcon_password="pw",
        options={"use_https": True, "verify_tls": True, "cert_fingerprint": "AA:BB"},
    )
    endpoint = holder["endpoint"]
    assert endpoint.use_https is True and endpoint.verify_tls is True
    assert endpoint.cert_fingerprint == "AA:BB"
    assert endpoint.base_url == "https://host:8212/v1/api"


# --- commands -------------------------------------------------------------


def _run(adapter, client, command):
    return adapter.execute_command("host", port=8212, secret="pw", command=command)


def test_moderation_builders_round_trip_through_execute_command(fake_pool):
    client = FakeClient()
    fake_pool(client)
    adapter = palworld_adapter

    _run(adapter, client, adapter.build_kick_command(
        player_name="Lyra", net_id="76561198084350159", reason="afk"
    ))
    _run(adapter, client, adapter.build_unban_command("76561198084350159"))
    _run(adapter, client, adapter.build_say_command("back in 5"))

    assert ("kick", ("steam_76561198084350159", "afk")) in client.calls
    assert ("unban", ("steam_76561198084350159",)) in client.calls
    assert ("announce", ("back in 5",)) in client.calls


def test_ban_ignores_duration_and_says_so(fake_pool):
    client = FakeClient()
    fake_pool(client)
    # /v1/api/ban has no duration parameter, so a temp ban becomes permanent
    command = palworld_adapter.build_ban_command(
        player_name="Tomo", net_id="76561198012345678", reason="grief", minutes=60
    )
    assert "60" not in command
    out = _run(palworld_adapter, client, command)
    assert ("ban", ("steam_76561198012345678", "grief")) in client.calls
    assert "permanent" in out


# --- response wording -----------------------------------------------------


def test_commands_are_quoted_like_the_rest_of_the_app(fake_pool):
    """POSIX single quotes read as foreign next to Sandstorm's kick "Bob" "x"."""
    a = palworld_adapter
    assert (
        a.build_ban_command(
            player_name="Jay", net_id="gdk_2535470764765514", reason="Just a test", minutes=60
        )
        == 'ban gdk_2535470764765514 "Just a test"'
    )
    # Nothing to quote - stay bare
    assert a.build_unban_command("gdk_2535470764765514") == "unban gdk_2535470764765514"
    assert (
        a.build_kick_command(player_name="Jay", net_id="76561198012345678", reason="afk")
        == "kick steam_76561198012345678 afk"
    )


def test_an_empty_server_response_becomes_a_real_sentence(fake_pool):
    """Real Palworld servers answer some writes with an empty 2xx body.

    "ok" tells an operator nothing about what happened.
    """
    class Silent(FakeClient):
        def ban(self, userid, message=""):
            self.calls.append(("ban", (userid, message)))
            return "ok"

        def save(self):
            self.calls.append(("save", ()))
            return "ok"

    client = Silent()
    fake_pool(client)
    out = _run(palworld_adapter, client, "ban gdk_2535470764765514 spam")
    assert out.startswith("Banned gdk_2535470764765514.")
    assert "permanent" in out
    assert "ok" not in out.lower().split()

    assert _run(palworld_adapter, client, "save") == "World saved."


def test_a_server_that_does_reply_keeps_its_own_wording(fake_pool):
    client = FakeClient()
    fake_pool(client)
    # FakeClient returns the documented sentences
    assert _run(palworld_adapter, client, "save") == "Successfully saved the world."
    assert _run(palworld_adapter, client, "kick gdk_2535470764765514") == (
        "The player was kicked."
    )


def test_quoted_reasons_still_round_trip(fake_pool):
    client = FakeClient()
    fake_pool(client)
    cmd = palworld_adapter.build_ban_command(
        player_name="Jay", net_id="gdk_2535470764765514",
        reason='he said "hi" then left', minutes=0,
    )
    _run(palworld_adapter, client, cmd)
    assert ("ban", ("gdk_2535470764765514", 'he said "hi" then left')) in client.calls


def test_reasons_with_spaces_survive_the_round_trip(fake_pool):
    client = FakeClient()
    fake_pool(client)
    command = palworld_adapter.build_kick_command(
        player_name="Lyra", net_id="76561198084350159", reason='griefing "spawn"'
    )
    _run(palworld_adapter, client, command)
    assert ("kick", ("steam_76561198084350159", 'griefing "spawn"')) in client.calls


def test_read_commands_render_text(fake_pool):
    client = FakeClient()
    fake_pool(client)
    assert "Pal Prime" in _run(palworld_adapter, client, "info")
    assert "Server FPS" in _run(palworld_adapter, client, "metrics")
    assert "Lyra" in _run(palworld_adapter, client, "players")
    assert "Difficulty" in _run(palworld_adapter, client, "settings")


def test_lifecycle_commands_reach_the_api(fake_pool):
    client = FakeClient()
    fake_pool(client)
    _run(palworld_adapter, client, "save")
    _run(palworld_adapter, client, "shutdown 30 going down")
    _run(palworld_adapter, client, "stop")
    assert ("save", ()) in client.calls
    assert ("shutdown", (30, "going down")) in client.calls
    assert ("stop", ()) in client.calls


def test_unknown_commands_are_rejected(fake_pool):
    client = FakeClient()
    fake_pool(client)
    for command in ("rm -rf /", "exec payload", "listbans"):
        with pytest.raises(PalworldApiError):
            _run(palworld_adapter, client, command)
    assert client.calls == []


def test_commands_needing_arguments_fail_clearly(fake_pool):
    client = FakeClient()
    fake_pool(client)
    for command in ("kick", "unban", "say", "shutdown"):
        with pytest.raises(PalworldApiError):
            _run(palworld_adapter, client, command)


# --- hints ----------------------------------------------------------------


def test_missing_secret_hint_names_the_setting():
    hint = palworld_adapter.player_count_hint(has_rcon_password=False, snap={})
    assert "AdminPassword" in hint


def test_no_hint_when_healthy():
    assert (
        palworld_adapter.player_count_hint(
            has_rcon_password=True, snap={"online": True, "api_error": None}
        )
        is None
    )


# --- game-data summary ----------------------------------------------------


def test_game_data_summary_links_pals_to_their_owner():
    payload = {
        "Time": "2026-08-07 12:00:00",
        "FPS": 61.0,
        "AverageFPS": 57.5,
        "InGameTime": "03:50",
        "InGameDays": 12,
        "ActorData": [
            {
                "Type": "Character",
                "InstanceID": "OWNER1",
                "UnitType": "Player",
                "NickName": "Lyra",
                "userid": "steam_76561198084350159",
                "level": 21,
                "HP": 500,
                "MaxHP": 900,
                "GuildName": "Lyra's Guild",
                "GuildID": "G1",
                "LocationX": 1.0,
                "LocationY": 2.0,
                "LocationZ": 3.0,
                "RotationZ": 90.0,
            },
            {
                "Type": "Character",
                "InstanceID": "OTOMO1",
                "UnitType": "OtomoPal",
                "NickName": "Lamball",
                "Class": "BP_SheepBall_C",
                "TrainerInstanceID": "OWNER1",
                "LocationX": 1.5,
                "LocationY": 2.5,
                "LocationZ": 0.0,
                "RotationZ": 10.0,
            },
            {
                "Type": "Character",
                "InstanceID": "WORKER1",
                "UnitType": "BaseCampPal",
                "NickName": "Anubis",
                "Class": "BP_Anubis_C",
                "TrainerInstanceID": "OWNER1",
                "GuildName": "Lyra's Guild",
                "GuildID": "G1",
                "level": 40,
                "LocationX": 4.0,
                "LocationY": 5.0,
                "LocationZ": 0.0,
                "AI_Action": "BP_AIAction_Worker_Working",
            },
            {
                "Type": "Character",
                "InstanceID": "WILD1",
                "UnitType": "WildPal",
                "NickName": "Cattiva",
                "Class": "BP_PinkCat_C",
                "level": 3,
                "LocationX": 10.0,
                "LocationY": 11.0,
                "LocationZ": 0.0,
            },
            {
                "Type": "Character",
                "InstanceID": "NPC1",
                "UnitType": "NPC",
                "NickName": "Scouting Party Survivor",
                "level": 22,
                "LocationX": 20.0,
                "LocationY": 21.0,
                "LocationZ": 0.0,
            },
            {
                "Type": "PalBox",
                "GuildID": "G1",
                "GuildName": "Lyra's Guild",
                "Name": "Base A",
                "LocationX": 9.0,
                "LocationY": 8.0,
                "LocationZ": 7.0,
            },
        ],
    }
    out = summarize_game_data(payload)
    assert out.enabled is True
    # Not ISO 8601 - passed through as the server wrote it
    assert out.snapshot_time == "2026-08-07 12:00:00"
    assert out.fps == pytest.approx(61.0)
    assert out.in_game_time == "03:50" and out.in_game_days == 12
    assert out.actor_counts == {
        "BaseCampPal": 1,
        "NPC": 1,
        "OtomoPal": 1,
        "PalBox": 1,
        "Player": 1,
        "WildPal": 1,
    }
    assert len(out.players) == 1
    player = out.players[0]
    assert player.name == "Lyra" and player.hp == 500 and player.max_hp == 900
    # game-data spells it lowercase, unlike /players' userId
    assert player.user_id == "steam_76561198084350159"
    assert player.pal_count == 2
    assert player.rotation_z == pytest.approx(90.0)
    assert player.guild_id == "G1"
    assert len(out.base_camps) == 1 and out.base_camps[0].guild_id == "G1"
    assert out.base_camps[0].name == "Base A"
    assert len(out.workers) == 1 and out.workers[0].species == "Anubis"
    assert "Worker" in out.workers[0].activity or "Working" in out.workers[0].activity
    assert len(out.wild_pals) == 1 and out.wild_pals[0].species == "Cattiva"
    assert len(out.npcs) == 1 and "Survivor" in out.npcs[0].name
    assert len(out.otomo_pals) == 1 and out.otomo_pals[0].species == "Lamball"


def test_game_data_summary_tolerates_junk():
    out = summarize_game_data({"ActorData": ["not a dict", None, {}]})
    assert out.players == [] and out.base_camps == []
    assert out.workers == [] and out.wild_pals == [] and out.npcs == []
    assert out.fps is None

"""Sandstorm must behave exactly as before the adapter contract was widened.

The generic layer no longer calls A2S / Source RCON directly, so these tests pin
that the Sandstorm adapter still routes to them with the same arguments, and that
its pure parsers are untouched.
"""

from __future__ import annotations

import pytest

from app.server_types import DEFAULT_SERVER_TYPE, get_adapter
from app.server_types.sandstorm import (
    build_travel_command,
    parse_listbans,
    parse_listplayers,
    sandstorm_adapter,
)


def test_sandstorm_is_still_the_default_type():
    assert DEFAULT_SERVER_TYPE == "sandstorm"
    assert get_adapter(None) is sandstorm_adapter
    assert get_adapter("SANDSTORM") is sandstorm_adapter


def test_unknown_type_still_raises():
    with pytest.raises(KeyError):
        get_adapter("minecraft")


def test_capabilities_unchanged():
    info = sandstorm_adapter.info
    assert (info.default_query_port, info.default_rcon_port) == (27131, 27015)
    assert info.endpoint_style == "query_rcon"
    assert info.secret_label == "RCON password"
    features = info.features
    assert features.map_travel is True
    assert features.structured_player_list is True
    # Source games do report a score, so that column must stay
    assert features.player_score is True
    assert features.kick_ban is True
    # Source RCON supports duration-based bans
    assert features.timed_ban is True
    assert features.perm_ban is True
    assert features.admin_say is True
    assert features.a2s_query is True
    # Sandstorm's listbans is the reason ban_list exists; it must stay on
    assert features.ban_list is True
    # New flags must not silently switch a game into another type's admin panel
    assert features.admin_api is False
    assert features.console is True
    assert features.tls_optional is False
    # The tick-rate chart labels default to what Sandstorm/Satisfactory showed
    assert (info.tick_rate_label, info.tick_rate_unit, info.tick_rate_target) == (
        "Tick rate",
        "tps",
        30,
    )


def test_moderation_commands_are_byte_identical_to_the_inlined_versions():
    """These strings used to be f-strings inside api/rcon.py.

    Moving them behind adapter hooks (so Palworld can address players by user ID)
    must not change a single character of what Sandstorm sends over RCON.
    """
    a = sandstorm_adapter
    assert a.build_say_command("hello world") == "say hello world"
    assert (
        a.build_kick_command(player_name="Bob", net_id="7656119", reason="Kicked by admin")
        == 'kick "Bob" "Kicked by admin"'
    )
    assert (
        a.build_ban_command(
            player_name="Bob", net_id="7656119", reason="Banned by admin", minutes=60
        )
        == 'ban "Bob" "60" "Banned by admin"'
    )
    assert (
        a.build_permban_command(
            player_name="Bob", net_id="7656119", reason="Permanently banned by admin"
        )
        == 'permban "Bob" "Permanently banned by admin"'
    )
    assert a.build_unban_command("7656119") == 'unban "7656119"'


# --- transport routing ----------------------------------------------------


def test_query_status_still_goes_through_a2s(monkeypatch):
    calls: list[tuple] = []

    def fake_query(host, port, timeout=2.0):
        calls.append((host, port, timeout))
        return {"online": True, "hostname": "srv"}

    monkeypatch.setattr("app.services.query.query_server_status", fake_query)

    raw = sandstorm_adapter.query_status(
        "1.2.3.4", 27131, timeout=3.5, rcon_port=27015, secret="pw"
    )
    assert raw == {"online": True, "hostname": "srv"}
    assert calls == [("1.2.3.4", 27131, 3.5)]
    # A2S games must not emit game-specific extras
    assert "extra" not in raw


def test_execute_command_still_goes_through_rcon_with_the_allowlist(monkeypatch):
    seen: dict = {}

    def fake_run_rcon(host, port, password, command, timeout=5.0, allowed_prefixes=None, **kw):
        seen.update(
            host=host,
            port=port,
            password=password,
            command=command,
            timeout=timeout,
            allowed_prefixes=allowed_prefixes,
        )
        return "1 | Alice | SteamNWI:76561190000000001 | 1.2.3.4 | 5"

    monkeypatch.setattr("app.services.rcon.run_rcon", fake_run_rcon)

    out = sandstorm_adapter.execute_command(
        "1.2.3.4", port=27015, secret="pw", command="listplayers", timeout=9.0
    )
    assert "Alice" in out
    assert seen["host"] == "1.2.3.4"
    assert seen["port"] == 27015
    assert seen["password"] == "pw"
    assert seen["command"] == "listplayers"
    assert seen["timeout"] == 9.0
    assert "listplayers" in seen["allowed_prefixes"]


def test_invalidate_connections_hits_the_rcon_pool(monkeypatch):
    dropped: list[tuple] = []
    monkeypatch.setattr(
        "app.services.rcon_pool.rcon_pool.invalidate_endpoint",
        lambda host, port: dropped.append((host, port)),
    )
    sandstorm_adapter.invalidate_connections("1.2.3.4", 27015)
    assert dropped == [("1.2.3.4", 27015)]


def test_command_allowlist_behaviour():
    assert sandstorm_adapter.is_command_allowed("listplayers") is True
    assert sandstorm_adapter.is_command_allowed("BAN foo") is True
    assert sandstorm_adapter.is_command_allowed("") is False
    assert sandstorm_adapter.is_command_allowed("shutdown") is False


# --- pure parsers ---------------------------------------------------------


def test_parse_listplayers_keeps_humans_only():
    raw = (
        "ID | Name | NetID | IP | Score\n"
        "1 | Alice | SteamNWI:76561190000000001 | 10.0.0.1 | 42\n"
        "2 | BotBob | None:INVALID | | 0\n"
        "3 | Carol | 76561190000000003 | 10.0.0.3 | -7\n"
    )
    players = parse_listplayers(raw)
    assert [p["name"] for p in players] == ["Alice", "Carol"]
    assert players[0]["steamid"] == "76561190000000001"
    assert players[1]["score"] == -7
    assert players[0]["ip"] == "10.0.0.1"


def test_parse_listplayers_empty_input():
    assert parse_listplayers("") == []


def test_parse_listbans_handles_concatenated_entries():
    raw = (
        "SteamNWI:76561190000000001 Permanent (griefing)"
        "EOS:0002abcd Permanent (cheating)"
        "76561190000000009 30 minutes (spam)"
    )
    bans = parse_listbans(raw)
    assert [b["display_id"] for b in bans] == [
        "76561190000000001",
        "0002abcd",
        "76561190000000009",
    ]
    assert bans[0]["platform"] == "Steam (NWI)"
    assert bans[0]["permanent"] is True
    assert bans[1]["platform"] == "Epic (EOS)"
    assert bans[2]["permanent"] is False
    assert bans[2]["reason"] == "spam"


def test_adapter_parse_bans_delegates_to_the_parser():
    raw = "SteamNWI:76561190000000001 Permanent (griefing)"
    assert sandstorm_adapter.parse_bans(raw) == parse_listbans(raw)


def test_travel_command_is_byte_identical():
    expected = "travel Farmhouse?Scenario=Scenario_Farmhouse_Checkpoint_Security?Lighting=Day?game=checkpoint"
    assert (
        build_travel_command(
            "Farmhouse", "Scenario_Farmhouse_Checkpoint_Security", "Day", "checkpoint"
        )
        == expected
    )
    assert (
        sandstorm_adapter.build_travel_command(
            map_name="Farmhouse",
            scenario="Scenario_Farmhouse_Checkpoint_Security",
            lighting="Day",
            gamemode_key="checkpoint",
        )
        == expected
    )


def test_hardcore_insurgents_still_collapses_to_the_base_gamemode():
    command = sandstorm_adapter.build_travel_command(
        map_name="Crossing",
        scenario="Scenario_Crossing_Checkpoint_Insurgents",
        lighting="Night",
        gamemode_key="checkpointhardcore_ins",
    )
    assert command.endswith("?game=checkpointhardcore")


def test_gamemode_labels_are_exposed_through_the_adapter():
    labels = sandstorm_adapter.gamemode_labels()
    assert labels["checkpoint"] == "CheckPoint Security"
    assert len(labels) == 15


def test_map_helpers_read_the_map_row():
    class Row:
        checkpoint = "Scenario_A_Checkpoint_Security"
        checkpoint_ins = ""
        push = "  "
        day = True
        night = False
        fog = True

    gamemodes = sandstorm_adapter.map_gamemodes(Row())
    assert gamemodes == {"checkpoint": "Scenario_A_Checkpoint_Security"}
    assert sandstorm_adapter.map_lightings(Row()) == ["Day", "Fog"]


def test_player_count_hint_still_nudges_towards_rcon():
    hint = sandstorm_adapter.player_count_hint(
        has_rcon_password=False, snap={"online": True, "players": 0}
    )
    assert "RCON password" in hint
    assert (
        sandstorm_adapter.player_count_hint(
            has_rcon_password=True, snap={"online": True, "players": 4}
        )
        is None
    )

"""Satisfactory adapter: field mapping onto the generic status/sample contracts."""

from __future__ import annotations

import pytest

from app.server_types import get_adapter
from app.server_types.satisfactory import normalize_state, satisfactory_adapter
from app.services import satisfactory_api
from app.services.satisfactory_api import SatisfactoryApiError, SatisfactoryAuthError

RAW_STATE = {
    "activeSessionName": "Ficsit Prime",
    "numConnectedPlayers": 3,
    "playerLimit": 8,
    "techTier": 6,
    "activeSchematic": "/Game/FactoryGame/Schematics/Schematic_6-1.Schematic_6-1",
    "gamePhase": "/Game/FactoryGame/GamePhase/GP_Project_Assembly_Phase_3.GP_Project_Assembly_Phase_3",
    "isGameRunning": True,
    "totalGameDuration": 93784,
    "isGamePaused": False,
    "averageTickRate": 29.63,
    "autoLoadSessionName": "Ficsit Prime",
}


class FakeClient:
    """Stands in for a pooled SatisfactoryClient."""

    def __init__(self, *, state=None, options=None, health="healthy", fail=None):
        self._state = RAW_STATE if state is None else state
        self._options = options if options is not None else {"ServerName": "FICSIT HQ"}
        self._health = health
        self._fail = fail or {}
        self.commands: list[str] = []

    def _maybe_fail(self, name):
        if name in self._fail:
            raise self._fail[name]

    def health_check(self):
        self._maybe_fail("health_check")
        return {"health": self._health}

    def query_server_state(self):
        self._maybe_fail("query_server_state")
        return self._state

    def get_server_options(self):
        self._maybe_fail("get_server_options")
        return {"server_options": self._options, "pending_server_options": {}}

    def run_command(self, command):
        self._maybe_fail("run_command")
        self.commands.append(command)
        return {"result": f"ran {command}", "return_value": True}


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

        monkeypatch.setattr(satisfactory_api.satisfactory_pool, "client", _client)
        return holder

    return install


# --- registry -------------------------------------------------------------


def test_registered_with_expected_capabilities():
    adapter = get_adapter("satisfactory")
    assert adapter is satisfactory_adapter
    info = adapter.info
    assert info.default_query_port == 7777 and info.default_rcon_port == 7777
    assert info.endpoint_style == "single_port"
    assert info.secret_label == "Admin password or API token"
    # No player list exists in the API, so per-player features stay off
    assert info.features.structured_player_list is False
    assert info.features.kick_ban is False
    assert info.features.admin_say is False
    assert info.features.map_travel is False
    assert info.features.a2s_query is False
    assert info.features.admin_api is True
    assert info.features.console is False
    assert info.features.tick_rate_history is True


def test_travel_and_ban_hooks_stay_inert():
    assert satisfactory_adapter.parse_bans("anything") == []
    assert satisfactory_adapter.gamemode_labels() == {}
    assert satisfactory_adapter.map_gamemodes(object()) == {}
    assert satisfactory_adapter.map_lightings(object()) == []
    with pytest.raises(NotImplementedError):
        satisfactory_adapter.build_travel_command(
            map_name="m", scenario="s", lighting="Day", gamemode_key="g"
        )


# --- state normalisation --------------------------------------------------


def test_normalize_state_coerces_types():
    state = normalize_state(RAW_STATE)
    assert state["num_connected_players"] == 3
    assert state["player_limit"] == 8
    assert state["tech_tier"] == 6
    assert state["average_tick_rate"] == pytest.approx(29.63)
    assert state["is_game_running"] is True
    assert state["is_game_paused"] is False


def test_normalize_state_keeps_legitimate_zeroes():
    state = normalize_state(
        {"numConnectedPlayers": 0, "techTier": 0, "averageTickRate": 0.0}
    )
    assert state["num_connected_players"] == 0
    assert state["tech_tier"] == 0
    assert state["average_tick_rate"] == 0.0


def test_normalize_state_survives_missing_and_junk_fields():
    state = normalize_state({"numConnectedPlayers": "bad", "playerLimit": None})
    assert state["num_connected_players"] == 0
    assert state["player_limit"] == 0
    assert state["active_session_name"] == ""
    assert state["average_tick_rate"] == 0.0


# --- query_status ---------------------------------------------------------


def test_query_status_maps_onto_the_generic_shape(fake_pool):
    fake_pool(FakeClient())
    raw = satisfactory_adapter.query_status("h", 7777, secret="tok.aa11")

    assert raw["online"] is True
    assert raw["error"] is None
    assert raw["hostname"] == "FICSIT HQ"
    assert raw["map"] == "Ficsit Prime"
    # Unreal asset paths are trimmed to something readable
    assert raw["gamemode"] == "GP_Project_Assembly_Phase_3"
    assert raw["players"] == 3
    assert raw["max_players"] == 8
    assert raw["player_list"] == []
    # Fields this game has no concept of must stay absent, not zeroed
    assert raw["lighting"] is None and raw["vac"] is None and raw["bots"] is None

    extra = raw["extra"]
    assert extra["average_tick_rate"] == pytest.approx(29.6)
    assert extra["tech_tier"] == 6
    assert extra["active_schematic"] == "Schematic_6-1"
    assert extra["total_game_duration"] == "1d 2h"
    assert extra["health"] == "healthy"


def test_query_status_falls_back_to_session_name_without_server_options(fake_pool):
    fake_pool(FakeClient(fail={"get_server_options": SatisfactoryApiError("nope")}))
    raw = satisfactory_adapter.query_status("h", 7777, secret="tok.aa11")
    assert raw["online"] is True
    assert raw["hostname"] == "Ficsit Prime"


def test_query_status_reports_offline_when_unreachable(fake_pool):
    fake_pool(FakeClient(fail={"health_check": SatisfactoryApiError("refused")}))
    raw = satisfactory_adapter.query_status("h", 7777, secret="tok.aa11")
    assert raw["online"] is False
    assert "refused" in raw["error"]
    assert raw["player_list"] == []


def test_query_status_distinguishes_unauthenticated_from_offline(fake_pool):
    fake_pool(FakeClient(fail={"query_server_state": SatisfactoryAuthError("bad token")}))
    raw = satisfactory_adapter.query_status("h", 7777, secret="nope")
    # Reachable: HealthCheck answered, only the authenticated call failed
    assert raw["online"] is True
    assert "bad token" in raw["error"]


def test_query_status_survives_a_failed_pool_construction(fake_pool):
    fake_pool(None, error=SatisfactoryApiError("pin mismatch"))
    raw = satisfactory_adapter.query_status("h", 7777, secret="tok.aa11")
    assert raw["online"] is False and "pin mismatch" in raw["error"]


def test_tls_options_reach_the_endpoint(fake_pool):
    holder = fake_pool(FakeClient())
    satisfactory_adapter.query_status(
        "h",
        7777,
        secret="s",
        options={"verify_tls": True, "cert_fingerprint": "AA:BB"},
    )
    endpoint = holder["endpoint"]
    assert endpoint.verify_tls is True
    assert endpoint.cert_fingerprint == "AA:BB"


# --- sample_players ------------------------------------------------------


def test_sample_players_returns_counts_without_a_roster(fake_pool):
    fake_pool(FakeClient())
    snap = satisfactory_adapter.sample_players("h", 7777, rcon_password="tok.aa11")
    assert snap["online"] is True
    assert snap["players"] == 3
    assert snap["max_players"] == 8
    assert snap["player_list"] == []
    assert snap["source"] == "https_api"
    assert snap["api_error"] is None


def test_sample_players_never_raises_so_the_collector_keeps_going(fake_pool):
    fake_pool(FakeClient(fail={"query_server_state": SatisfactoryApiError("boom")}))
    snap = satisfactory_adapter.sample_players("h", 7777, rcon_password="tok.aa11")
    assert snap["online"] is False
    assert snap["players"] == 0
    assert "boom" in snap["api_error"]


def test_hints_are_actionable():
    hint = satisfactory_adapter.player_count_hint(has_rcon_password=False, snap={})
    assert "admin password or API token" in hint

    # Offline servers already surface the transport error via query_status
    assert (
        satisfactory_adapter.player_count_hint(
            has_rcon_password=True, snap={"online": False, "api_error": "refused"}
        )
        is None
    )
    paused = satisfactory_adapter.player_count_hint(
        has_rcon_password=True, snap={"online": True, "paused": True}
    )
    assert "paused" in paused.lower()


# --- commands ------------------------------------------------------------


def test_execute_command_runs_allowed_commands(fake_pool):
    client = FakeClient()
    fake_pool(client)
    out = satisfactory_adapter.execute_command(
        "h", port=7777, secret="tok.aa11", command="FG.AutosaveInterval 600"
    )
    assert client.commands == ["FG.AutosaveInterval 600"]
    assert "ran FG.AutosaveInterval" in out


def test_execute_command_rejects_commands_outside_the_allowlist(fake_pool):
    client = FakeClient()
    fake_pool(client)
    with pytest.raises(SatisfactoryApiError) as exc:
        satisfactory_adapter.execute_command(
            "h", port=7777, secret="tok.aa11", command="kick Alice"
        )
    assert "not allowed" in str(exc.value)
    assert client.commands == []


def test_failed_command_result_is_reported(fake_pool):
    class Failing(FakeClient):
        def run_command(self, command):
            return {"result": "unknown command", "return_value": False}

    fake_pool(Failing())
    out = satisfactory_adapter.execute_command(
        "h", port=7777, secret="tok.aa11", command="help"
    )
    assert "unknown command" in out and "reported failure" in out

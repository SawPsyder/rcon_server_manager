"""Satisfactory dedicated server type adapter (HTTPS API transport).

Satisfactory has no A2S query and no Source RCON. Everything runs through the
HTTPS API on the game port, so this adapter overrides both transport hooks of
:class:`~app.server_types.base.DefaultAdapter`.

One consequence shapes the whole feature set: **no API function returns a
player list.** ``QueryServerState`` gives ``numConnectedPlayers`` and
``playerLimit`` only, so player-count history and charts work while rosters,
presence tracking, identity lookups and kick/ban do not — those features are
switched off rather than faked.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Mapping

from app.server_types.base import DefaultAdapter, QuickButton, ServerFeatures, ServerTypeInfo
from app.services.satisfactory_api import (
    ApiEndpoint,
    SatisfactoryApiError,
    SatisfactoryClient,
    satisfactory_pool,
)
from app.services.server_options import coerce_bool, coerce_str, option_bool, option_str

if TYPE_CHECKING:
    from app.models import Server

logger = logging.getLogger(__name__)

DEFAULT_API_PORT = 7777

# Per-server option keys stored in servers.options_json
OPTION_VERIFY_TLS = "verify_tls"
OPTION_CERT_FINGERPRINT = "cert_fingerprint"

# RunCommand reaches the dedicated server console, which exposes cvars rather
# than a moderation command set. Keep the allowlist narrow and additive.
ALLOWED_COMMAND_PREFIXES = (
    "help",
    "fg.",
    "server.",
    "stat",
)

QUICK_BUTTONS = (
    QuickButton("Help", "help"),
    QuickButton("Frame stats", "stat fps"),
)

# Human-readable labels for the QueryServerState fields we surface as "extra"
STATE_EXTRA_LABELS: dict[str, str] = {
    "tech_tier": "Tech tier",
    "active_schematic": "Active milestone",
    "average_tick_rate": "Tick rate",
    "total_game_duration": "Play time",
    "is_game_running": "Game running",
    "is_game_paused": "Paused",
    "auto_load_session_name": "Auto-load session",
    "health": "Health",
}


def endpoint_for_server(server: Server, secret: str, *, port: int | None = None) -> ApiEndpoint:
    """Build an :class:`ApiEndpoint` from a server row plus its decrypted secret."""
    return ApiEndpoint(
        host=(server.host or "").strip(),
        port=int(port or server.query_port or DEFAULT_API_PORT),
        secret=secret or "",
        verify_tls=option_bool(server, OPTION_VERIFY_TLS, False),
        cert_fingerprint=option_str(server, OPTION_CERT_FINGERPRINT, ""),
    )


def client_for_server(
    server: Server,
    secret: str,
    *,
    timeout: float = 10.0,
    port: int | None = None,
) -> SatisfactoryClient:
    return satisfactory_pool.client(
        endpoint_for_server(server, secret, port=port), timeout=timeout
    )


def _pretty_duration(seconds: Any) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return "0m"
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def state_extra(state: dict[str, Any], health: str = "") -> dict[str, Any]:
    """Satisfactory-only scalars for ServerStatus.extra (display order matters)."""
    extra: dict[str, Any] = {}
    if health:
        extra["health"] = health
    tick = state.get("average_tick_rate")
    if tick is not None:
        extra["average_tick_rate"] = round(float(tick), 1)
    for key in ("tech_tier", "active_schematic", "auto_load_session_name"):
        value = state.get(key)
        if value not in (None, ""):
            extra[key] = value
    duration = state.get("total_game_duration")
    if duration:
        extra["total_game_duration"] = _pretty_duration(duration)
    if state.get("is_game_running") is not None:
        extra["is_game_running"] = bool(state.get("is_game_running"))
    if state.get("is_game_paused") is not None:
        extra["is_game_paused"] = bool(state.get("is_game_paused"))
    return extra


def normalize_state(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten a ``serverGameState`` object into snake_case python values."""
    from app.services.satisfactory_api import pick

    # Coerce without `or default` — that would rewrite legitimate zeroes
    def _int(name: str, default: int = 0) -> int:
        try:
            return int(pick(raw, name, default))
        except (TypeError, ValueError):
            return default

    def _float(name: str, default: float = 0.0) -> float:
        try:
            return float(pick(raw, name, default))
        except (TypeError, ValueError):
            return default

    return {
        "active_session_name": str(pick(raw, "activeSessionName", "") or ""),
        "num_connected_players": _int("numConnectedPlayers"),
        "player_limit": _int("playerLimit"),
        "tech_tier": _int("techTier"),
        "active_schematic": str(pick(raw, "activeSchematic", "") or ""),
        "game_phase": str(pick(raw, "gamePhase", "") or ""),
        "is_game_running": bool(pick(raw, "isGameRunning", False)),
        "total_game_duration": _int("totalGameDuration"),
        "is_game_paused": bool(pick(raw, "isGamePaused", False)),
        "average_tick_rate": _float("averageTickRate"),
        "auto_load_session_name": str(pick(raw, "autoLoadSessionName", "") or ""),
    }


def _short_name(value: str) -> str:
    """Trim Unreal-style asset paths (``/Game/.../Schematic_X.Schematic_X``)."""
    text = (value or "").strip()
    if "." in text and "/" in text:
        text = text.rsplit(".", 1)[-1]
    return text


class SatisfactoryAdapter(DefaultAdapter):
    info = ServerTypeInfo(
        id="satisfactory",
        label="Satisfactory",
        default_query_port=DEFAULT_API_PORT,
        default_rcon_port=DEFAULT_API_PORT,
        features=ServerFeatures(
            map_travel=False,
            structured_player_list=False,
            kick_ban=False,
            admin_say=False,
            a2s_query=False,
            admin_api=True,
            # RunCommand / free-text console disabled for now (admin panel covers ops)
            console=False,
            tick_rate_history=True,
        ),
        quick_buttons=QUICK_BUTTONS,
        secret_label="Admin password or API token",
        endpoint_style="single_port",
    )
    allowed_rcon_prefixes = ALLOWED_COMMAND_PREFIXES

    # --- transport ---------------------------------------------------------

    def _endpoint(
        self,
        host: str,
        port: int,
        secret: str,
        options: Mapping[str, Any] | None = None,
    ) -> ApiEndpoint:
        opts = options or {}
        return ApiEndpoint(
            host=host,
            port=int(port or DEFAULT_API_PORT),
            secret=secret or "",
            verify_tls=coerce_bool(opts.get(OPTION_VERIFY_TLS), False),
            cert_fingerprint=coerce_str(opts.get(OPTION_CERT_FINGERPRINT)),
        )

    def execute_command(
        self,
        host: str,
        *,
        port: int,
        secret: str,
        command: str,
        timeout: float = 5.0,
        options: Mapping[str, Any] | None = None,
    ) -> str:
        if not self.is_command_allowed(command):
            raise SatisfactoryApiError(
                "Command not allowed. Allowed prefixes: "
                + ", ".join(self.allowed_rcon_prefixes)
            )
        client = satisfactory_pool.client(
            self._endpoint(host, port, secret, options), timeout=max(timeout, 10.0)
        )
        outcome = client.run_command(command)
        result = outcome["result"]
        if not outcome["return_value"]:
            suffix = "command reported failure"
            result = f"{result}\n{suffix}" if result else suffix
        return result

    def invalidate_connections(self, host: str, port: int) -> None:
        satisfactory_pool.invalidate_endpoint(host, port)

    # --- status ------------------------------------------------------------

    def query_status(
        self,
        host: str,
        query_port: int,
        *,
        timeout: float = 2.0,
        rcon_port: int | None = None,
        secret: str = "",
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        endpoint = self._endpoint(host, query_port, secret, options)
        offline = {
            "online": False,
            "host": host,
            "query_port": query_port,
            "player_list": [],
            "error": None,
        }

        try:
            client = satisfactory_pool.client(endpoint, timeout=max(timeout, 10.0))
        except SatisfactoryApiError as exc:
            return {**offline, "error": str(exc)}

        health = ""
        try:
            health = str(client.health_check().get("health") or "")
        except SatisfactoryApiError as exc:
            # HealthCheck needs no auth: failing it means unreachable
            return {**offline, "error": str(exc)}

        try:
            state = normalize_state(client.query_server_state())
        except SatisfactoryApiError as exc:
            # Reachable but not authenticated — say so instead of "offline"
            return {
                **offline,
                "online": True,
                "error": str(exc),
                "extra": {"health": health} if health else {},
            }

        hostname = ""
        try:
            hostname = str(
                client.get_server_options()["server_options"].get("ServerName") or ""
            )
        except SatisfactoryApiError:
            logger.debug("GetServerOptions failed for %s", host, exc_info=True)

        session = state["active_session_name"]
        return {
            "online": True,
            "host": host,
            "query_port": query_port,
            "hostname": hostname or session or None,
            "map": session or None,
            "lighting": None,
            "gamemode": _short_name(state["game_phase"]) or None,
            "coop_or_versus": None,
            "players": state["num_connected_players"],
            "max_players": state["player_limit"],
            "bots": None,
            "ping_ms": None,
            "password_protected": None,
            "vac": None,
            "ranked": None,
            "game_port": int(query_port),
            "version": None,
            "player_list": [],
            "error": None,
            "extra": state_extra(
                {**state, "active_schematic": _short_name(state["active_schematic"])},
                health,
            ),
        }

    def sample_players(
        self,
        host: str,
        query_port: int,
        rcon_port: int | None = None,
        rcon_password: str = "",
        timeout: float = 3.0,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Player *count* only — the API exposes no per-player data."""
        endpoint = self._endpoint(host, query_port, rcon_password, options)
        snap: dict[str, Any] = {
            "online": False,
            "players": 0,
            "max_players": 0,
            "bots": 0,
            "player_list": [],
            "source": "offline",
            "a2s_players": 0,
            "a2s_error": None,
            "rcon_error": None,
            "api_error": None,
            "paused": False,
            # None rather than 0.0 while offline: the chart must show a gap, not
            # a crash to zero the server never reported.
            "tick_rate": None,
            "sampled_at": time.time(),
        }
        try:
            client = satisfactory_pool.client(endpoint, timeout=max(timeout, 10.0))
            state = normalize_state(client.query_server_state())
        except SatisfactoryApiError as exc:
            snap["api_error"] = str(exc)
            logger.info("Satisfactory API sample failed for %s:%s: %s", host, query_port, exc)
            return snap

        tick = state["average_tick_rate"]
        snap.update(
            online=True,
            players=state["num_connected_players"],
            max_players=state["player_limit"],
            source="https_api",
            paused=state["is_game_paused"],
            # A paused server still answers, but its tick rate is meaningless
            tick_rate=None if state["is_game_paused"] or tick <= 0 else round(tick, 2),
        )
        return snap

    def player_count_hint(self, *, has_rcon_password: bool, snap: dict[str, Any]) -> str | None:
        if not has_rcon_password:
            return (
                "The HTTPS API needs credentials: set an admin password or API token "
                "under Servers."
            )
        # An unreachable server already reports the transport error via query_status;
        # only add a hint when the API answered but the count is still suspect.
        if snap.get("online") and snap.get("api_error"):
            return f"Satisfactory API error: {snap['api_error']}"
        if snap.get("online") and snap.get("paused"):
            return "Game is paused — no players are connected."
        return None


satisfactory_adapter = SatisfactoryAdapter()

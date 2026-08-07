"""Palworld dedicated server type adapter (REST API transport).

Palworld has no A2S query and no Source RCON — or rather, it has RCON but
Pocketpair deprecated it in favour of the REST API, which is strictly more
capable. Everything here runs over ``http://<host>:8212/v1/api``, so this
adapter overrides both transport hooks of
:class:`~app.server_types.base.DefaultAdapter`.

Unlike Satisfactory, ``/v1/api/players`` returns a real roster, so this is the
first API-backed type where presence tracking, identity resolution, playtime
and moderation all engage. Two shapes make that work:

* ``userId`` arrives platform-prefixed (``steam_7656…``). The adapter emits the
  **bare** SteamID64 so the existing presence filter and identity parser accept
  it unchanged, and re-applies the prefix when calling kick/ban.
* Moderation goes through the generic command path, so the adapter overrides the
  command builders and implements a small text command language over the REST
  endpoints. That keeps ``command_history``, the console and the quick buttons
  working exactly as they do for RCON games.

Two capabilities the API simply does not have, switched off rather than faked:
there is no ban-list endpoint (bans live in ``banlist.txt`` on disk), and
settings are read-only.
"""

from __future__ import annotations

import logging
import shlex
import time
from typing import TYPE_CHECKING, Any, Mapping

from app.server_types.base import DefaultAdapter, QuickButton, ServerFeatures, ServerTypeInfo
from app.services.palworld_api import (
    ApiEndpoint,
    PalworldApiError,
    PalworldAuthError,
    PalworldClient,
    palworld_pool,
    pick,
)
from app.services.server_options import coerce_bool, coerce_str, option_bool, option_str

if TYPE_CHECKING:
    from app.models import Server

logger = logging.getLogger(__name__)

DEFAULT_API_PORT = 8212

# Per-server option keys stored in servers.options_json
OPTION_USE_HTTPS = "use_https"
OPTION_VERIFY_TLS = "verify_tls"
OPTION_CERT_FINGERPRINT = "cert_fingerprint"

# The REST API has no free-text console, so "commands" are a thin text layer over
# the endpoints. Keeping them as text is what lets command_history, the console
# and the quick buttons treat Palworld like every other type.
ALLOWED_COMMAND_PREFIXES = (
    "announce",
    "ban",
    "info",
    "kick",
    "metrics",
    "players",
    "save",
    "say",
    "settings",
    "shutdown",
    "stop",
    "unban",
)

QUICK_BUTTONS = (
    QuickButton("Server info", "info"),
    QuickButton("Metrics", "metrics"),
    QuickButton("Players", "players"),
    QuickButton("Save world", "save"),
)

STEAM_PREFIX = "steam_"


def quote_arg(value: str) -> str:
    """Quote a command argument the way the rest of the app writes commands.

    ``shlex.quote`` is correct but emits POSIX single quotes, which read as
    foreign next to Sandstorm's ``kick "Bob" "reason"`` in the same console and
    audit log. Prefer double quotes and fall back to shlex only when the value
    contains something double quoting cannot carry safely.
    """
    text = value or ""
    if not text:
        return '""'
    if any(c in text for c in '"\\\n\r\t') or "'" in text:
        return shlex.quote(text)
    if not any(c.isspace() for c in text):
        return text
    return f'"{text}"'


def confirm_text(result: str, fallback: str) -> str:
    """Prefer what the server said, but never surface an empty body as "ok".

    Palworld documents a sentence for each write endpoint, yet real servers
    answer some of them with an empty 2xx body. "ok" tells an operator nothing
    about what actually happened.
    """
    text = (result or "").strip()
    return fallback if not text or text.lower() == "ok" else text


# --- identifiers -----------------------------------------------------------


def to_local_id(user_id: str) -> str:
    """``steam_7656…`` → ``7656…``; anything else passes through untouched.

    The bare SteamID64 is what ``services.presence`` (17 numeric digits) and the
    frontend's ``parseIdentity`` expect, so stripping the prefix here wires
    Palworld into playtime tracking, the identity cache and the dossier without
    touching any shared code. Xbox/PlayStation user IDs keep their own prefix and
    are skipped by presence, exactly as non-Steam Sandstorm players are.
    """
    text = (user_id or "").strip()
    if text.lower().startswith(STEAM_PREFIX):
        candidate = text[len(STEAM_PREFIX) :]
        if candidate.isdigit():
            return candidate
    return text


def to_api_user_id(net_id: str) -> str:
    """Inverse of :func:`to_local_id` — what ``/kick``, ``/ban``, ``/unban`` want."""
    text = (net_id or "").strip()
    if text.isdigit() and len(text) == 17:
        return f"{STEAM_PREFIX}{text}"
    return text


# --- parsing ---------------------------------------------------------------


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_metrics(raw: Mapping[str, Any]) -> dict[str, Any]:
    """``/v1/api/metrics`` → snake_case, with absent fields left as ``None``.

    ``basecampnum`` is 1.x-only and ``days`` post-0.2.x, so nothing is defaulted
    to zero — a missing field must not render as a real reading of 0.
    """
    return {
        "server_fps": _int_or_none(pick(raw, "serverfps")),
        "current_players": _int_or_none(pick(raw, "currentplayernum")),
        "max_players": _int_or_none(pick(raw, "maxplayernum")),
        "frame_time_ms": _float_or_none(pick(raw, "serverframetime")),
        "uptime": _int_or_none(pick(raw, "uptime")),
        "days": _int_or_none(pick(raw, "days")),
        "base_camps": _int_or_none(pick(raw, "basecampnum")),
    }


def normalize_info(raw: Mapping[str, Any]) -> dict[str, Any]:
    """``/v1/api/info`` → snake_case. ``worldguid`` is post-0.2.x."""
    return {
        "version": str(pick(raw, "version", "") or ""),
        "server_name": str(pick(raw, "servername", "") or ""),
        "description": str(pick(raw, "description", "") or ""),
        "world_guid": str(pick(raw, "worldguid", "") or ""),
    }


# Palworld-only per-player fields merged into the shared player table as extra
# columns. Location and building count are deliberately not here: coordinates
# are too niche to spend a column on, and building_count is absent from several
# server builds. Both stay available on GET /palworld/players for API callers.
ROSTER_EXTRA_KEYS = ("account_name", "level", "ping_ms")


def normalize_player(raw: Mapping[str, Any]) -> dict[str, Any]:
    """One ``/v1/api/players`` entry → the generic roster shape plus extras.

    ``name``/``steamid``/``ip`` are what the shared player table, roster
    snapshots and presence read. The remaining keys are Palworld-only: a subset
    (:data:`ROSTER_EXTRA_KEYS`) is merged into the shared table via
    ``PlayerInfo.extra``, and the full set is served by the game-specific route.
    """
    user_id = str(pick(raw, "userId", "") or "")
    level = _int_or_none(pick(raw, "level"))
    ping = _float_or_none(pick(raw, "ping"))
    return {
        "name": str(pick(raw, "name", "") or "").strip(),
        "steamid": to_local_id(user_id),
        "ip": str(pick(raw, "ip", "") or "").strip(),
        # Palworld has no score. Mapping level here would show the same number
        # twice under two names, so the column is switched off for this type.
        "score": 0,
        # Rounded for display; the raw float stays on the game-specific route
        "ping_ms": None if ping is None else round(ping),
        "user_id": user_id,
        "account_name": str(pick(raw, "accountName", "") or ""),
        "player_id": str(pick(raw, "playerId", "") or ""),
        "level": level,
        # Documented as a double, not an int
        "ping": _float_or_none(pick(raw, "ping")),
        "building_count": _int_or_none(pick(raw, "building_count")),
        "location_x": _float_or_none(pick(raw, "location_x")),
        "location_y": _float_or_none(pick(raw, "location_y")),
    }


def roster_entry(raw: Mapping[str, Any]) -> dict[str, Any]:
    """A ``/players`` entry shaped for the shared player table.

    Generic keys stay flat (presence and the roster snapshot read those); the
    Palworld-only subset goes under ``extra``, which the table renders as extra
    columns without knowing anything about Palworld.
    """
    player = normalize_player(raw)
    extra = {k: player[k] for k in ROSTER_EXTRA_KEYS if player.get(k) not in (None, "")}
    return {
        "name": player["name"],
        "steamid": player["steamid"],
        "ip": player["ip"],
        "score": player["score"],
        "extra": extra,
    }


def _pretty_uptime(seconds: Any) -> str:
    total = _int_or_none(seconds)
    if total is None or total <= 0:
        return "0m"
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def status_extra(info: Mapping[str, Any], metrics: Mapping[str, Any]) -> dict[str, Any]:
    """Palworld-only scalars for ServerStatus.extra (display order matters).

    ``version`` is repeated from the top-level status field on purpose: nothing
    renders that field today, and ``extra`` is the only path to a stat card.
    """
    extra: dict[str, Any] = {}
    if info.get("version"):
        extra["version"] = info["version"]
    if metrics.get("server_fps") is not None:
        extra["server_fps"] = metrics["server_fps"]
    if metrics.get("frame_time_ms") is not None:
        extra["frame_time_ms"] = round(float(metrics["frame_time_ms"]), 2)
    if metrics.get("uptime"):
        extra["uptime"] = _pretty_uptime(metrics["uptime"])
    if metrics.get("days") is not None:
        extra["in_game_days"] = metrics["days"]
    if metrics.get("base_camps") is not None:
        extra["base_camps"] = metrics["base_camps"]
    if info.get("description"):
        extra["description"] = info["description"]
    if info.get("world_guid"):
        extra["world_guid"] = info["world_guid"]
    return extra


# --- server row helpers -----------------------------------------------------


def endpoint_for_server(server: Server, secret: str, *, port: int | None = None) -> ApiEndpoint:
    """Build an :class:`ApiEndpoint` from a server row plus its decrypted secret."""
    return ApiEndpoint(
        host=(server.host or "").strip(),
        port=int(port or server.query_port or DEFAULT_API_PORT),
        secret=secret or "",
        use_https=option_bool(server, OPTION_USE_HTTPS, False),
        verify_tls=option_bool(server, OPTION_VERIFY_TLS, False),
        cert_fingerprint=option_str(server, OPTION_CERT_FINGERPRINT, ""),
    )


def client_for_server(
    server: Server,
    secret: str,
    *,
    timeout: float = 10.0,
    port: int | None = None,
) -> PalworldClient:
    return palworld_pool.client(endpoint_for_server(server, secret, port=port), timeout=timeout)


# --- console rendering ------------------------------------------------------


def _render_info(info: Mapping[str, Any]) -> str:
    lines = [
        f"Name:    {info.get('server_name') or '—'}",
        f"Version: {info.get('version') or '—'}",
    ]
    if info.get("description"):
        lines.append(f"About:   {info['description']}")
    if info.get("world_guid"):
        lines.append(f"World:   {info['world_guid']}")
    return "\n".join(lines)


def _render_metrics(metrics: Mapping[str, Any]) -> str:
    rows = [
        ("Server FPS", metrics.get("server_fps")),
        ("Frame time", metrics.get("frame_time_ms")),
        ("Players", metrics.get("current_players")),
        ("Max players", metrics.get("max_players")),
        ("Uptime", _pretty_uptime(metrics.get("uptime"))),
        ("In-game days", metrics.get("days")),
        ("Base camps", metrics.get("base_camps")),
    ]
    return "\n".join(f"{label:<13}{'—' if value is None else value}" for label, value in rows)


def _render_players(players: list[dict[str, Any]]) -> str:
    if not players:
        return "No players online."
    header = f"{'Name':<24}{'Level':>6}  {'Ping':>7}  User ID"
    lines = [header, "-" * len(header)]
    for p in players:
        ping = "—" if p.get("ping") is None else f"{p['ping']:.0f}ms"
        level = "—" if p.get("level") is None else p["level"]
        lines.append(f"{p['name'][:23]:<24}{level:>6}  {ping:>7}  {p.get('user_id') or '—'}")
    return "\n".join(lines)


def _render_settings(settings: Mapping[str, Any]) -> str:
    if not settings:
        return "No settings returned."
    width = max(len(str(k)) for k in settings)
    return "\n".join(f"{str(k):<{width}}  {v}" for k, v in sorted(settings.items()))


class PalworldAdapter(DefaultAdapter):
    info = ServerTypeInfo(
        id="palworld",
        label="Palworld",
        # The REST API port (RESTAPIPort), not the game port (8211)
        default_query_port=DEFAULT_API_PORT,
        default_rcon_port=DEFAULT_API_PORT,
        features=ServerFeatures(
            map_travel=False,
            structured_player_list=True,
            # Palworld reports a level, not a score — Level is an extra column
            player_score=False,
            kick_ban=True,
            # The API cannot enumerate bans (they live in banlist.txt on the
            # host), so the list is derived from our own moderation history.
            ban_list=True,
            admin_say=True,
            a2s_query=False,
            admin_api=True,
            console=True,
            tick_rate_history=True,
            tls_optional=True,
        ),
        quick_buttons=QUICK_BUTTONS,
        secret_label="Admin password",
        endpoint_style="single_port",
        ban_list_source="local",
        tick_rate_label="Server FPS",
        tick_rate_unit="fps",
        tick_rate_target=60,
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
            use_https=coerce_bool(opts.get(OPTION_USE_HTTPS), False),
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
        text = (command or "").strip()
        if not self.is_command_allowed(text):
            raise PalworldApiError(
                "Command not allowed. Supported: " + ", ".join(self.allowed_rcon_prefixes)
            )
        try:
            verb, *args = shlex.split(text)
        except ValueError as exc:
            # Unbalanced quotes — shlex is what parses the builders' output
            raise PalworldApiError(f"Could not parse command: {exc}") from exc

        client = palworld_pool.client(
            self._endpoint(host, port, secret, options), timeout=max(timeout, 10.0)
        )
        return self._dispatch(client, verb.lower(), args)

    def _dispatch(self, client: PalworldClient, verb: str, args: list[str]) -> str:
        if verb == "info":
            return _render_info(normalize_info(client.info()))
        if verb == "metrics":
            return _render_metrics(normalize_metrics(client.metrics()))
        if verb == "players":
            return _render_players([normalize_player(p) for p in client.players()])
        if verb == "settings":
            return _render_settings(client.settings())

        if verb in ("say", "announce"):
            message = " ".join(args).strip()
            if not message:
                raise PalworldApiError("say needs a message")
            return confirm_text(client.announce(message), "Announced to everyone online.")

        if verb == "kick":
            userid, reason = self._player_args(verb, args)
            return confirm_text(client.kick(userid, reason), f"Kicked {userid}.")

        if verb == "ban":
            userid, reason = self._player_args(verb, args)
            result = confirm_text(client.ban(userid, reason), f"Banned {userid}.")
            # /v1/api/ban takes no duration, so a timed ban from the generic UI
            # silently becomes permanent. Say so rather than let it surprise.
            return f"{result} Palworld bans are permanent — no duration was applied."

        if verb == "unban":
            if not args:
                raise PalworldApiError("unban needs a user ID")
            userid = to_api_user_id(args[0])
            return confirm_text(client.unban(userid), f"Unbanned {userid}.")

        if verb == "save":
            return confirm_text(client.save(), "World saved.")

        if verb == "shutdown":
            waittime = _int_or_none(args[0]) if args else None
            if waittime is None:
                raise PalworldApiError("shutdown needs a wait time in seconds")
            message = " ".join(args[1:]).strip()
            return confirm_text(
                client.shutdown(waittime, message),
                f"Server will shut down in {waittime}s.",
            )

        if verb == "stop":
            return confirm_text(
                client.stop(), "Server force stopped — the world was not saved."
            )

        raise PalworldApiError(f"Unsupported command: {verb}")

    @staticmethod
    def _player_args(verb: str, args: list[str]) -> tuple[str, str]:
        if not args:
            raise PalworldApiError(f"{verb} needs a user ID")
        return to_api_user_id(args[0]), " ".join(args[1:]).strip()

    def invalidate_connections(self, host: str, port: int) -> None:
        palworld_pool.invalidate_endpoint(host, port)

    # --- moderation command builders ---------------------------------------

    # The REST API addresses players by user ID, never by display name, so these
    # diverge from the Source RCON defaults in base.DefaultAdapter.

    def build_kick_command(self, *, player_name: str, net_id: str, reason: str) -> str:
        return f"kick {quote_arg(to_api_user_id(net_id))} {quote_arg(reason)}"

    def build_ban_command(
        self, *, player_name: str, net_id: str, reason: str, minutes: int
    ) -> str:
        # minutes is deliberately dropped: /v1/api/ban has no duration parameter
        return f"ban {quote_arg(to_api_user_id(net_id))} {quote_arg(reason)}"

    def build_permban_command(self, *, player_name: str, net_id: str, reason: str) -> str:
        return f"ban {quote_arg(to_api_user_id(net_id))} {quote_arg(reason)}"

    def build_unban_command(self, net_id: str) -> str:
        return f"unban {quote_arg(to_api_user_id(net_id))}"

    def build_say_command(self, message: str) -> str:
        return f"say {quote_arg(message)}"

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
            client = palworld_pool.client(endpoint, timeout=max(timeout, 10.0))
            info = normalize_info(client.info())
        except PalworldAuthError as exc:
            # Every Palworld endpoint requires auth, so a 401 is proof the server
            # is up — report "reachable but rejected", not "offline".
            return {**offline, "online": True, "error": str(exc)}
        except PalworldApiError as exc:
            return {**offline, "error": str(exc)}

        metrics: dict[str, Any] = {}
        try:
            metrics = normalize_metrics(client.metrics())
        except PalworldApiError:
            logger.debug("Palworld /metrics failed for %s", host, exc_info=True)

        return {
            "online": True,
            "host": host,
            "query_port": query_port,
            "hostname": info["server_name"] or None,
            "map": None,
            "lighting": None,
            "gamemode": None,
            "coop_or_versus": None,
            "players": metrics.get("current_players"),
            "max_players": metrics.get("max_players"),
            "bots": None,
            "ping_ms": None,
            "password_protected": None,
            "vac": None,
            "ranked": None,
            "game_port": int(query_port),
            "version": info["version"] or None,
            "player_list": [],
            "error": None,
            "extra": status_extra(info, metrics),
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
        """Counts from ``/metrics`` plus the roster from ``/players``.

        Never raises — the stats collector loop depends on getting a snapshot
        back even when the server is unreachable.
        """
        endpoint = self._endpoint(host, query_port, rcon_password, options)
        snap: dict[str, Any] = {
            "online": False,
            "players": 0,
            "max_players": 0,
            "bots": 0,
            "player_list": [],
            # Only true once /players actually answered — an empty roster we
            # read is "everyone left", an unread one is "we don't know".
            "roster_known": False,
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
            client = palworld_pool.client(endpoint, timeout=max(timeout, 10.0))
            metrics = normalize_metrics(client.metrics())
        except PalworldApiError as exc:
            snap["api_error"] = str(exc)
            logger.info("Palworld API sample failed for %s:%s: %s", host, query_port, exc)
            return snap

        fps = metrics.get("server_fps")
        snap.update(
            online=True,
            players=metrics.get("current_players") or 0,
            max_players=metrics.get("max_players") or 0,
            source="rest_api",
            tick_rate=fps if fps and fps > 0 else None,
        )

        # A roster failure must not lose the count we already have
        try:
            snap["player_list"] = [roster_entry(p) for p in client.players()]
            snap["roster_known"] = True
        except PalworldApiError as exc:
            snap["api_error"] = str(exc)
            logger.info("Palworld /players failed for %s:%s: %s", host, query_port, exc)

        return snap

    def player_count_hint(self, *, has_rcon_password: bool, snap: dict[str, Any]) -> str | None:
        if not has_rcon_password:
            return (
                "The Palworld REST API needs the server's AdminPassword: set it "
                "under Servers."
            )
        # An unreachable server already reports the transport error via query_status;
        # only add a hint when the API answered but the count is still suspect.
        if snap.get("online") and snap.get("api_error"):
            return f"Palworld API error: {snap['api_error']}"
        return None


palworld_adapter = PalworldAdapter()

"""Dune: Awakening dedicated server type adapter (egg admin-HTTP transport).

Dune has no A2S query and no Source RCON. Live admin actions reach the
UE5 Sietches through the Sergentval Pelican/Pterodactyl egg's sidecar
HTTP on a configurable port (default 8090). This adapter overrides both
transport hooks of :class:`~app.server_types.base.DefaultAdapter`.

Roster rows carry a Funcom FLS id plus, when the account is Steam, a
bare SteamID64 so presence, identity and the dossier work unchanged.
Kick addresses the egg as ``steam:<id>`` or the 16-hex FLS id — never
the encrypted character name.

The egg has kick but no ban command, so ``perm_ban`` / ``ban_list`` stay
off. Game-only surfaces (map, 195-key INI, sietch scale) live in
``app.api.dune``, not on this adapter.
"""

from __future__ import annotations

import logging
import shlex
import time
from typing import TYPE_CHECKING, Any, Mapping

from app.server_types.base import DefaultAdapter, QuickButton, ServerFeatures, ServerTypeInfo
from app.services.dune_api import (
    ApiEndpoint,
    DuneApiError,
    DuneAuthError,
    DuneClient,
    dune_pool,
    pretty_uptime,
    publish_detail,
)
from app.services.server_options import coerce_bool, coerce_str, option_bool, option_str

if TYPE_CHECKING:
    from app.models import Server

logger = logging.getLogger(__name__)

DEFAULT_API_PORT = 8090

OPTION_USE_HTTPS = "use_https"
OPTION_VERIFY_TLS = "verify_tls"
OPTION_CERT_FINGERPRINT = "cert_fingerprint"

ALLOWED_COMMAND_PREFIXES = (
    "broadcast",
    "kick",
    "listplayers",
    "partitions",
    "players",
    "say",
    "status",
)

QUICK_BUTTONS = (
    QuickButton("Status", "status"),
    QuickButton("Players", "players"),
)

# steam_name is filled later from the Steam Web API / identity cache.
ROSTER_EXTRA_KEYS = ("steam_name", "fls_id", "life", "platform")

# Battlegroup instance names → the region an operator recognises. The grid
# reports the raw UE map name; the panel, the console table and the Map /
# Maps status fields all read better with the in-game name.
MAP_LABELS = {
    "Survival_1": "Hagga Basin",
    "Overmap": "Overland",
    "DeepDesert_1": "Deep Desert",
    "SH_Arrakeen": "Arrakeen",
    "SH_HarkoVillage": "Harko Village",
    "SH_FallenLight": "Fallen Light",
}


def map_label(raw: str) -> str:
    """``SH_HarkoVillage`` → ``Harko Village``; unknown names de-prefixed."""
    name = (raw or "").strip()
    if not name:
        return ""
    known = MAP_LABELS.get(name)
    if known:
        return known
    for prefix in ("DLC_", "CB_", "SH_"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    return name.replace("_", " ")


def sorted_map_labels(maps: Any) -> list[str]:
    """Healthy grid rows → region names, A-Z. Empty when nothing is live."""
    if not isinstance(maps, list):
        return []
    labels = {
        map_label(str(row.get("map") or ""))
        for row in maps
        if isinstance(row, Mapping) and row.get("status") == "healthy"
    }
    return sorted(label for label in labels if label)


def quote_arg(value: str) -> str:
    text = value or ""
    if not text:
        return '""'
    if any(c in text for c in '"\\\n\r\t') or "'" in text:
        return shlex.quote(text)
    if not any(c.isspace() for c in text):
        return text
    return f'"{text}"'


def confirm_text(result: str, fallback: str) -> str:
    text = (result or "").strip()
    return fallback if not text or text.lower() == "ok" else text


def kick_target(*, player_name: str, net_id: str) -> str:
    """Pick the egg player-id form the generic kick button can supply.

    ``net_id`` is the roster ``steamid`` (bare SteamID64, or the FLS id when
    the account has no Steam id). Character names cannot be used — Funcom
    stores them encrypted.
    """
    steam = (net_id or "").strip()
    if steam.isdigit() and len(steam) >= 15:
        return f"steam:{steam}"
    if len(steam) == 16 and all(c in "0123456789abcdefABCDEF" for c in steam):
        return steam.upper()
    name = (player_name or "").strip()
    if len(name) == 16 and all(c in "0123456789abcdefABCDEF" for c in name):
        return name.upper()
    return steam or name


def normalize_player(raw: Mapping[str, Any]) -> dict[str, Any]:
    fls = str(raw.get("fls_id") or "").strip().upper()
    steam = str(raw.get("steam_id") or "").strip()
    character = str(raw.get("character") or "").strip()
    return {
        "name": character or fls,
        # Bare SteamID64 when we have one, else the FLS id so kick/identity
        # still have a stable handle.
        "steamid": steam if steam.isdigit() else fls,
        "ip": "",
        "score": 0,
        "fls_id": fls,
        "life": str(raw.get("life") or "").strip(),
        "platform": str(raw.get("platform_name") or "").strip(),
        "online": str(raw.get("online") or "").strip(),
    }


def roster_entry(raw: Mapping[str, Any]) -> dict[str, Any]:
    player = normalize_player(raw)
    extra = {k: player[k] for k in ROSTER_EXTRA_KEYS if player.get(k) not in (None, "")}
    return {
        "name": player["name"],
        "steamid": player["steamid"],
        "ip": player["ip"],
        "score": player["score"],
        "extra": extra,
    }


def enrich_dune_roster(db: Any, player_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduped roster → attach Steam personas (API cache, then public profiles)."""
    if not player_list:
        return player_list
    from app.services.identity import (
        fetch_steam_community_names,
        remember_community_personas,
        resolve_names,
    )

    steam_ids = [str(p.get("steamid") or "") for p in player_list]
    resolved = resolve_names(db, steam_ids)
    need_persona = [
        sid
        for sid in steam_ids
        if sid
        and (resolved.get(sid) or {}).get("source") not in {"steam_api", "steam_community"}
    ]
    if need_persona:
        community = fetch_steam_community_names(need_persona)
        if community:
            remember_community_personas(db, community)
            resolved.update(community)
            try:
                db.commit()
            except Exception:
                db.rollback()
    return apply_steam_personas(player_list, resolved)


def apply_steam_personas(
    player_list: list[dict[str, Any]],
    resolved: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Attach Steam persona names without dropping the in-game character.

    ``name`` stays the character when the egg decoded one. The persona goes
    under ``extra.steam_name`` so the shared player table can show both.
    When the character is missing (empty leftover player_state row), promote
    the persona so the Name column is not a raw FLS id.
    """
    for player in player_list:
        steam = str(player.get("steamid") or "").strip()
        info = resolved.get(steam) if steam else None
        source = str((info or {}).get("source") or "")
        persona = str((info or {}).get("display_name") or "").strip()
        extra = dict(player.get("extra") or {})
        name = str(player.get("name") or "").strip()
        fls = str(extra.get("fls_id") or "").strip()
        # Presence caches the character name under the Steam id; that is not
        # a persona. Only treat Web API / community-profile names as Steam.
        trusted = source in {"steam_api", "steam_community"}
        if not trusted and persona in {"", name, steam, fls}:
            continue
        if not persona:
            continue
        extra["steam_name"] = persona
        player["extra"] = extra
        if not name or name == steam or name == fls:
            player["name"] = persona
    return player_list


def status_extra(grid: Mapping[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    maps = grid.get("maps")
    if isinstance(maps, list):
        live = sorted_map_labels(maps)
        extra["live_maps"] = len(live)
        extra["maps"] = ", ".join(live) if live else "-"
    if grid.get("totalServers") is not None:
        extra["instances"] = grid.get("totalServers")
    if grid.get("uptimeSeconds") is not None:
        extra["uptime"] = pretty_uptime(grid.get("uptimeSeconds"))
    pool = grid.get("pool")
    if isinstance(pool, Mapping) and pool.get("used") is not None:
        extra["port_pool"] = f"{pool.get('used')}/{pool.get('size') or '?'}"
    if grid.get("warning"):
        extra["warning"] = grid["warning"]
    return extra


def endpoint_for_server(server: Server, secret: str, *, port: int | None = None) -> ApiEndpoint:
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
    timeout: float = 15.0,
    port: int | None = None,
) -> DuneClient:
    return dune_pool.client(endpoint_for_server(server, secret, port=port), timeout=timeout)


def _render_status(grid: Mapping[str, Any]) -> str:
    maps = grid.get("maps") if isinstance(grid.get("maps"), list) else []
    lines = [
        f"Players:    {grid.get('totalPlayers', 0)}",
        f"Instances:  {grid.get('totalServers', 0)}",
        f"Uptime:     {pretty_uptime(grid.get('uptimeSeconds'))}",
    ]
    pool = grid.get("pool")
    if isinstance(pool, Mapping):
        lines.append(f"Port pool:  {pool.get('used')}/{pool.get('size')}")
    if grid.get("warning"):
        lines.append(f"Warning:    {grid['warning']}")
    if maps:
        lines.append("")
        lines.append(f"{'Map':<22}{'Status':<12}Players")
        for row in sorted(
            (row for row in maps if isinstance(row, Mapping)),
            key=lambda row: map_label(str(row.get("map") or "")),
        ):
            lines.append(
                f"{map_label(str(row.get('map') or '')) or '-':<22}"
                f"{str(row.get('status') or '-'):<12}"
                f"{row.get('players', 0)}"
            )
    return "\n".join(lines)


def _render_players(players: list[Mapping[str, Any]]) -> str:
    if not players:
        return "No players online."
    header = f"{'Character':<20}{'FLS':<18}{'Steam':<20}Life"
    lines = [header, "-" * len(header)]
    for raw in players:
        player = normalize_player(raw)
        steam = player["steamid"] if player["steamid"] != player["fls_id"] else "-"
        lines.append(
            f"{player['name'][:19]:<20}"
            f"{player['fls_id']:<18}"
            f"{steam:<20}"
            f"{player['life'] or '-'}"
        )
    return "\n".join(lines)


def _render_partitions(payload: Mapping[str, Any]) -> str:
    parts = payload.get("partitions")
    if not isinstance(parts, list) or not parts:
        return str(payload.get("error") or "No partitions reported.")
    lines = [f"{'ID':<6}{'Map':<28}{'Dim':<6}State  Players"]
    for row in parts:
        if not isinstance(row, Mapping):
            continue
        if row.get("parked"):
            state = "parked"
        elif row.get("ready"):
            state = "live"
        elif row.get("server_id"):
            state = "start"
        else:
            state = "cold"
        lines.append(
            f"{str(row.get('partition_id') or '-'):<6}"
            f"{str(row.get('map') or '-'):<28}"
            f"{str(row.get('dimension') if row.get('dimension') is not None else '-'):<6}"
            f"{state:<7}{row.get('players', 0)}"
        )
    return "\n".join(lines)


class DuneAdapter(DefaultAdapter):
    info = ServerTypeInfo(
        id="dune",
        label="Dune: Awakening",
        default_query_port=DEFAULT_API_PORT,
        default_rcon_port=DEFAULT_API_PORT,
        features=ServerFeatures(
            map_travel=False,
            structured_player_list=True,
            player_score=False,
            kick_ban=True,
            timed_ban=False,
            perm_ban=False,
            ban_list=False,
            admin_say=True,
            a2s_query=False,
            admin_api=True,
            console=True,
            tick_rate_history=False,
            tls_optional=True,
        ),
        quick_buttons=QUICK_BUTTONS,
        secret_label="Admin UI password",
        endpoint_style="single_port",
        ban_list_source="local",
    )
    allowed_rcon_prefixes = ALLOWED_COMMAND_PREFIXES

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
            raise DuneApiError(
                "Command not allowed. Supported: " + ", ".join(self.allowed_rcon_prefixes)
            )
        try:
            verb, *args = shlex.split(text)
        except ValueError as exc:
            raise DuneApiError(f"Could not parse command: {exc}") from exc

        client = dune_pool.client(
            self._endpoint(host, port, secret, options), timeout=max(timeout, 15.0)
        )
        return self._dispatch(client, verb.lower(), args)

    def _dispatch(self, client: DuneClient, verb: str, args: list[str]) -> str:
        if verb in ("players", "listplayers"):
            filt = "all" if args and args[0].lower() == "all" else "online"
            return _render_players(client.players(filt))
        if verb == "status":
            return _render_status(client.status())
        if verb == "partitions":
            return _render_partitions(client.partitions())
        if verb in ("say", "broadcast"):
            if verb == "broadcast" and len(args) >= 2:
                title, body = args[0], " ".join(args[1:])
            else:
                title, body = "Broadcast", " ".join(args).strip()
            if not body:
                raise DuneApiError("say needs a message")
            return confirm_text(
                publish_detail(client.broadcast(title, body)),
                "Broadcast sent.",
            )
        if verb == "kick":
            if not args:
                raise DuneApiError("kick needs a player id (FLS hex or steam:…)")
            return confirm_text(publish_detail(client.kick(args[0])), f"Kicked {args[0]}.")
        raise DuneApiError(f"Unsupported command: {verb}")

    def invalidate_connections(self, host: str, port: int) -> None:
        dune_pool.invalidate_endpoint(host, port)

    def build_kick_command(self, *, player_name: str, net_id: str, reason: str) -> str:
        target = kick_target(player_name=player_name, net_id=net_id)
        return f"kick {quote_arg(target)}"

    def build_ban_command(
        self, *, player_name: str, net_id: str, reason: str, minutes: int
    ) -> str:
        raise DuneApiError("Dune: Awakening has no ban command — kick only.")

    def build_permban_command(self, *, player_name: str, net_id: str, reason: str) -> str:
        raise DuneApiError("Dune: Awakening has no ban command — kick only.")

    def build_unban_command(self, net_id: str) -> str:
        raise DuneApiError("Dune: Awakening has no ban command — kick only.")

    def build_say_command(self, message: str) -> str:
        return f"say {quote_arg(message)}"

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
            client = dune_pool.client(endpoint, timeout=max(timeout, 15.0))
            grid = client.status()
        except DuneAuthError as exc:
            return {**offline, "online": True, "error": str(exc)}
        except DuneApiError as exc:
            return {**offline, "error": str(exc)}

        extra = status_extra(grid)
        maps = extra.get("maps")
        info = client.server_info()
        return {
            "online": True,
            "host": host,
            "query_port": query_port,
            "hostname": str(info.get("display_name") or "") or None,
            "map": maps if isinstance(maps, str) and maps != "-" else None,
            "lighting": None,
            "gamemode": None,
            "coop_or_versus": None,
            "players": grid.get("totalPlayers"),
            "max_players": info.get("player_hard_cap"),
            "bots": None,
            "ping_ms": None,
            "password_protected": None,
            "vac": None,
            "ranked": None,
            "game_port": int(query_port),
            "version": None,
            "player_list": [],
            "error": None,
            "extra": extra,
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
        endpoint = self._endpoint(host, query_port, rcon_password, options)
        snap: dict[str, Any] = {
            "online": False,
            "players": 0,
            "max_players": 0,
            "bots": 0,
            "player_list": [],
            "roster_known": False,
            "source": "offline",
            "a2s_players": 0,
            "a2s_error": None,
            "rcon_error": None,
            "api_error": None,
            "paused": False,
            "tick_rate": None,
            "sampled_at": time.time(),
        }
        try:
            client = dune_pool.client(endpoint, timeout=max(timeout, 15.0))
            grid = client.status()
        except DuneApiError as exc:
            snap["api_error"] = str(exc)
            logger.info("Dune admin HTTP sample failed for %s:%s: %s", host, query_port, exc)
            return snap

        snap.update(
            online=True,
            players=int(grid.get("totalPlayers") or 0),
            # 0 when Bgd.ServerPlayerHardCap is unset — Dune ships no default
            # cap, so the UI hides the slot count rather than inventing one.
            max_players=int(client.server_info().get("player_hard_cap") or 0),
            source="admin_http",
        )
        try:
            snap["player_list"] = [roster_entry(p) for p in client.players("online")]
            snap["roster_known"] = True
        except DuneApiError as exc:
            snap["api_error"] = str(exc)
            logger.info("Dune /api/players failed for %s:%s: %s", host, query_port, exc)
        return snap

    def player_count_hint(self, *, has_rcon_password: bool, snap: dict[str, Any]) -> str | None:
        if not has_rcon_password:
            return (
                "The Dune admin HTTP needs the admin UI password: set it under Servers."
            )
        if snap.get("online") and snap.get("api_error"):
            return f"Dune admin HTTP error: {snap['api_error']}"
        return None


dune_adapter = DuneAdapter()

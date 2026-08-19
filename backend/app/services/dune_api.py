"""Dune: Awakening admin-HTTP client (Sergentval egg sidecar).

The dedicated server has no Source RCON. Funcom admin actions are AMQP
``ServerCommand`` envelopes that must be published from inside the game
broker. The Pelican/Pterodactyl egg exposes that pipeline as an HTTP SPA
on a configurable port (default 8090).

This client talks to that sidecar the same way Palworld talks to its REST
API: one pooled session per endpoint, TLS optional for reverse-proxied
deploys. Authentication is the admin UI password: ``POST /api/login``
issues a 7-day HMAC token which we send as ``Authorization: Bearer``.
Login is rate-limited (5 / 900s), so the token is cached and a rejected
password is put on cooldown rather than retried on every status poll.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import quote

import httpx

from app.services.errors import CommandError
from app.services.tls_pins import (
    CertFetchError,
    fetch_cert_fingerprint,
    normalize_fingerprint,
    pin_mismatch_message,
)

logger = logging.getLogger(__name__)

DEFAULT_API_PORT = 8090
DEFAULT_TIMEOUT = 15.0
# Login is rate-limited on the egg; do not hammer a wrong password.
DEFAULT_AUTH_COOLDOWN_SECONDS = 30.0
# Refresh a minute before the token's advertised expiry.
TOKEN_REFRESH_SKEW_SECONDS = 60.0

MAP_KEYS = ("HaggaBasin", "DeepDesert", "Arrakeen", "HarkoVillage")
SCALABLE_MAPS = ("DeepDesert_1", "SH_Arrakeen", "SH_HarkoVillage")
SCALE_REPLICAS_MAX = 4

# Two of the 195 INI keys carry status the grid does not: the advertised
# server name and the player cap. Read from /api/settings and TTL-cached so a
# status poll does not re-read the whole catalogue every time.
# /api/login answers with Set-Cookie for both of these. We deliberately do not
# ride the cookie session — see DuneClient._send.
SESSION_COOKIE_NAME = "dune_session"
CSRF_COOKIE_NAME = "dune_csrf"

SETTING_DISPLAY_NAME = "Bgd.ServerDisplayName"
SETTING_PLAYER_HARD_CAP = "Bgd.ServerPlayerHardCap"
SERVER_INFO_TTL_SECONDS = 120.0


class DuneApiError(CommandError):
    """An admin-HTTP call failed (HTTP status, error body, or transport)."""

    def __init__(self, message: str, *, code: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class DuneAuthError(DuneApiError):
    """The admin UI password was missing, rejected, or rate-limited."""


class DuneTimeoutError(DuneApiError):
    """The admin HTTP did not answer in time."""


class DuneTlsError(DuneApiError):
    """Certificate verification or fingerprint pinning failed."""

    def __init__(self, message: str, *, observed_fingerprint: str = "") -> None:
        super().__init__(message, code="tls_error")
        self.observed_fingerprint = observed_fingerprint


@dataclass(frozen=True)
class ApiEndpoint:
    host: str
    port: int = DEFAULT_API_PORT
    secret: str = ""
    use_https: bool = False
    verify_tls: bool = False
    cert_fingerprint: str = ""

    @property
    def scheme(self) -> str:
        return "https" if self.use_https else "http"

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{int(self.port)}"

    @property
    def key(self) -> tuple[str, int, str, bool, bool, str]:
        return (
            self.host.strip(),
            int(self.port),
            self.secret,
            bool(self.use_https),
            bool(self.verify_tls),
            normalize_fingerprint(self.cert_fingerprint),
        )


def parse_psql_table(stdout: str) -> list[dict[str, str]]:
    """Parse a ``psql`` ASCII table (headers + ``---+---`` divider + rows)."""
    if not stdout:
        return []
    lines = stdout.splitlines()
    divider_idx = next(
        (i for i, line in enumerate(lines) if _is_psql_divider(line)),
        -1,
    )
    if divider_idx <= 0:
        return []
    headers = [cell.strip() for cell in lines[divider_idx - 1].split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[divider_idx + 1 :]:
        stripped = line.strip()
        if not stripped or stripped.startswith("("):
            break
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) < len(headers):
            continue
        rows.append({headers[i]: cells[i] if i < len(cells) else "" for i in range(len(headers))})
    return rows


def _is_psql_divider(line: str) -> bool:
    text = line.strip()
    if not text or "+" not in text:
        return False
    return all(ch in "-+|= " for ch in text) and "-" in text


def parse_player_table(stdout: str) -> list[dict[str, str]]:
    """``admin players`` stdout → one dict per account (duplicates collapsed).

    Funcom keeps more than one ``encrypted_player_state`` row per account —
    typically a nameless leftover plus the live character. The egg's SQL is a
    plain JOIN, so the same FLS/Steam id appears twice. Collapse those here
    so every consumer (roster, console, map labels) sees one person.
    """
    out: list[dict[str, str]] = []
    for raw in parse_psql_table(stdout):
        fls = (raw.get("fls_id") or "").strip()
        if not fls:
            continue
        character = (raw.get("character") or "").strip()
        steam = (raw.get("steam_id") or "").strip()
        out.append(
            {
                "fls_id": fls.upper(),
                "character": "" if character in {"", "-"} else character,
                "steam_id": "" if steam in {"", "-"} else steam,
                "platform_name": (raw.get("platform_name") or "").strip(),
                "life": (raw.get("life") or "").strip(),
                "online": (raw.get("online") or "").strip(),
                "last_avatar_activity": (raw.get("last_avatar_activity") or "").strip(),
            }
        )
    return dedupe_player_rows(out)


def dedupe_player_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """One row per FLS id (then Steam id). Prefer a real character name."""
    by_key: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for raw in rows:
        fls = (raw.get("fls_id") or "").strip().upper()
        steam = (raw.get("steam_id") or "").strip()
        key = f"fls:{fls}" if fls else (f"steam:{steam}" if steam else "")
        if not key:
            continue
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = dict(raw)
            by_key[key]["fls_id"] = fls
            order.append(key)
            continue
        by_key[key] = _merge_player_row(existing, raw)
    return [by_key[key] for key in order]


def _merge_player_row(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    left_name = bool((left.get("character") or "").strip())
    right_name = bool((right.get("character") or "").strip())
    if right_name and not left_name:
        winner, other = dict(right), left
    elif left_name and not right_name:
        winner, other = dict(left), right
    elif (right.get("last_avatar_activity") or "") > (left.get("last_avatar_activity") or ""):
        winner, other = dict(right), left
    else:
        winner, other = dict(left), right
    if winner.get("fls_id"):
        winner["fls_id"] = winner["fls_id"].upper()
    for key in ("character", "steam_id", "platform_name", "life", "online", "last_avatar_activity"):
        if not (winner.get(key) or "").strip() and (other.get(key) or "").strip():
            winner[key] = other[key]
    if not (winner.get("fls_id") or "").strip() and (other.get("fls_id") or "").strip():
        winner["fls_id"] = other["fls_id"].upper()
    return winner


def row_is_online(row: Mapping[str, Any]) -> bool:
    """Is this *resolved* roster row a connected player?

    Only meaningful after :func:`dedupe_player_rows` has merged an account's
    rows — a raw ``online`` cell straight out of the SQL cannot be trusted.
    See :meth:`DuneClient.players`.
    """
    return str(row.get("online") or "").strip().lower() == "online"


def settings_server_info(payload: Mapping[str, Any]) -> dict[str, Any]:
    """``/api/settings`` → the two keys that belong on the status card.

    ``Bgd.ServerDisplayName`` is the name the server advertises and
    ``Bgd.ServerPlayerHardCap`` its player cap. Both are plain INI keys with
    no grid equivalent; an unset cap stays ``None`` so callers can hide the
    slot count rather than print a made-up one.
    """
    info: dict[str, Any] = {"display_name": "", "player_hard_cap": None}
    fields = {
        SETTING_DISPLAY_NAME: "display_name",
        SETTING_PLAYER_HARD_CAP: "player_hard_cap",
    }
    categories = payload.get("categories")
    if not isinstance(categories, Mapping):
        return info
    for items in categories.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            field_name = fields.get(str(item.get("key") or ""))
            if not field_name:
                continue
            value = item.get("value")
            if value is None or not str(value).strip():
                value = item.get("default")
            text = "" if value is None else str(value).strip()
            if field_name == "display_name":
                info["display_name"] = text
            else:
                info["player_hard_cap"] = _positive_int(text)
    return info


def _positive_int(text: str) -> int | None:
    try:
        value = int(float(text))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def pretty_uptime(seconds: Any) -> str:
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return "0m"
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


def publish_detail(payload: Any) -> str:
    """Human-readable result of an ``/admin/<sub>`` publish."""
    if payload is None:
        return "ok"
    if isinstance(payload, str):
        return payload.strip() or "ok"
    if not isinstance(payload, Mapping):
        return str(payload)
    stdout = str(payload.get("stdout") or "").strip()
    stderr = str(payload.get("stderr") or "").strip()
    if stdout:
        return stdout
    if stderr:
        return stderr
    if payload.get("ok") is False:
        return str(payload.get("error") or "command failed")
    return "ok"


class DuneClient:
    """One authenticated conversation with an egg admin-HTTP process.

    Not created directly in app code — use :data:`dune_pool`.
    """

    def __init__(
        self,
        endpoint: ApiEndpoint,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        auth_cooldown_seconds: float = DEFAULT_AUTH_COOLDOWN_SECONDS,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.auth_cooldown_seconds = auth_cooldown_seconds
        self._lock = threading.RLock()
        self._auth_blocked_until = 0.0
        self._auth_error = ""
        self._token = ""
        self._token_expires_at = 0.0
        self._csrf = ""
        self._info: dict[str, Any] = {"display_name": "", "player_hard_cap": None}
        self._info_read_at = 0.0
        self.created_at = time.time()
        self.last_used = 0.0
        self.call_count = 0

        if endpoint.cert_fingerprint and endpoint.use_https and transport is None:
            self._verify_pinned_certificate()

        if transport is not None:
            self._http = httpx.Client(timeout=timeout, transport=transport)
        else:
            self._http = httpx.Client(timeout=timeout, verify=bool(endpoint.verify_tls))

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:  # noqa: BLE001
            pass

    def _verify_pinned_certificate(self) -> None:
        expected = normalize_fingerprint(self.endpoint.cert_fingerprint)
        try:
            observed = fetch_cert_fingerprint(
                self.endpoint.host, self.endpoint.port, self.timeout
            )
        except CertFetchError as exc:
            if exc.kind == "tls":
                raise DuneTlsError(str(exc)) from exc
            if exc.kind == "timeout":
                raise DuneTimeoutError(str(exc)) from exc
            raise DuneApiError(str(exc)) from exc
        if observed != expected:
            raise DuneTlsError(
                pin_mismatch_message(
                    self.endpoint.host, self.endpoint.port, expected, observed
                ),
                observed_fingerprint=observed,
            )

    # --- transport ---------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        json_body: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
        auth: bool = True,
    ) -> Any:
        with self._lock:
            self._check_auth_cooldown()
            if auth:
                self._ensure_token(timeout)
            response = self._send(method, path, json_body, timeout, auth=auth)
            if auth and response.status_code == 401:
                # Token expired / revoked — login once and retry.
                self._forget_session()
                self._ensure_token(timeout)
                response = self._send(method, path, json_body, timeout, auth=True)
            result = self._parse(response, f"{method} {path}")
            self.last_used = time.time()
            self.call_count += 1
            return result

    def _check_auth_cooldown(self) -> None:
        now = time.time()
        if self._auth_blocked_until > now:
            wait = int(self._auth_blocked_until - now)
            raise DuneAuthError(
                f"Dune admin login for {self.endpoint.host}:{self.endpoint.port} "
                f"is in cooldown ({self._auth_error or 'rejected'}; retry in ~{wait}s)",
                status=401,
            )

    def _ensure_token(self, timeout: float | None) -> None:
        if not (self.endpoint.secret or "").strip():
            raise DuneAuthError(
                "Dune admin HTTP needs the admin UI password: set it under Servers.",
                status=401,
            )
        if self._token and time.time() < self._token_expires_at - TOKEN_REFRESH_SKEW_SECONDS:
            return
        payload = self._login(timeout)

        token = str(payload.get("token") or "").strip()
        if not token:
            raise DuneAuthError(
                "Dune admin login returned no token. Check the admin UI password.",
                status=401,
            )
        try:
            expires_in = float(payload.get("expires_in") or 604800)
        except (TypeError, ValueError):
            expires_in = 604800.0
        self._token = token
        self._token_expires_at = time.time() + max(expires_in, 60.0)
        self._auth_blocked_until = 0.0
        self._auth_error = ""

    def _forget_session(self) -> None:
        self._token = ""
        self._token_expires_at = 0.0
        self._csrf = ""

    def _login(self, timeout: float | None) -> dict[str, Any]:
        response = self._send(
            "POST",
            "/api/login",
            {"password": self.endpoint.secret},
            timeout,
            auth=False,
        )
        # The login body strips the csrf value; only the cookie carries it.
        # We stay on Bearer auth (so the egg exempts us from the CSRF check),
        # but keep the value so a reverse proxy that re-attaches the session
        # cookie cannot push our mutations back behind the gate.
        self._csrf = response.cookies.get(CSRF_COOKIE_NAME) or self._csrf
        status = response.status_code
        if status == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else self.auth_cooldown_seconds
            except (TypeError, ValueError):
                wait = self.auth_cooldown_seconds
            self._auth_error = "login rate-limited"
            self._auth_blocked_until = time.time() + max(wait, 5.0)
            raise DuneAuthError(
                f"Dune admin login is rate-limited for {self.endpoint.host}:"
                f"{self.endpoint.port} (retry in ~{int(wait)}s)",
                status=429,
            )
        if status == 401:
            self._auth_error = "admin UI password rejected"
            self._auth_blocked_until = time.time() + self.auth_cooldown_seconds
            raise DuneAuthError(
                "Dune admin login was rejected: check the admin UI password "
                "(DUNE_ADMIN_UI_PASSWORD on the egg).",
                status=401,
            )
        parsed = self._parse(response, "POST /api/login")
        return parsed if isinstance(parsed, dict) else {}

    def _send(
        self,
        method: str,
        path: str,
        json_body: Mapping[str, Any] | None,
        timeout: float | None,
        *,
        auth: bool,
    ) -> httpx.Response:
        url = f"{self.endpoint.base_url}{path}"
        method = method.upper()
        headers = {"Accept": "application/json"}
        if auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._csrf and method != "GET":
            headers["X-CSRF-Token"] = self._csrf
        # The egg resolves the session from the cookie BEFORE the Authorization
        # header, and a cookie-borne session must also carry X-CSRF-Token.
        # /api/login sets that cookie, so httpx's jar would replay it on every
        # later call, silently downgrading us from Bearer to cookie auth — which
        # made every mutation ("/admin/*", POST /api/settings, instance and
        # sietch scale) fail with "csrf token missing or invalid" while reads,
        # which skip the CSRF gate, kept working. Bearer-only is the documented
        # path for non-browser clients, so drop the jar before every request.
        self._http.cookies.clear()
        kwargs: dict[str, Any] = {"headers": headers}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if json_body is not None:
            kwargs["json"] = dict(json_body)
        elif method == "POST":
            kwargs["content"] = b""
        try:
            return self._http.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise DuneTimeoutError(
                f"Dune admin HTTP timed out after "
                f"{timeout if timeout is not None else self.timeout:g}s calling "
                f"{method} {path} on {self.endpoint.host}:{self.endpoint.port}"
            ) from exc
        except httpx.ConnectError as exc:
            message = str(exc)
            lowered = message.lower()
            if "certificate" in lowered or "ssl" in lowered or "tls" in lowered:
                raise DuneTlsError(
                    f"TLS handshake failed for {url}: {message}. The egg serves "
                    f"plain HTTP by default — turn off 'Use HTTPS' unless a reverse "
                    f"proxy terminates TLS, or turn off 'Verify TLS' and pin its "
                    f"fingerprint."
                ) from exc
            raise DuneApiError(f"Could not connect to {url}: {message}") from exc
        except httpx.HTTPError as exc:
            raise DuneApiError(f"Dune admin HTTP request failed: {exc}") from exc

    def _parse(self, response: httpx.Response, what: str) -> Any:
        status = response.status_code
        text = response.text or ""

        if status == 401:
            self._auth_error = "admin UI password rejected"
            self._auth_blocked_until = time.time() + self.auth_cooldown_seconds
            self._forget_session()
            raise DuneAuthError(
                f"{what} was rejected: check the admin UI password "
                "(DUNE_ADMIN_UI_PASSWORD on the egg).",
                status=status,
            )

        if status >= 400:
            snippet = _error_snippet(text)
            raise DuneApiError(
                f"{what} failed: {snippet or f'HTTP {status}'}",
                status=status,
            )

        if status == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return text.strip()

    # --- reads -------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return _as_dict(self.request("GET", "/api/status"))

    def players(self, filter: str = "online") -> list[dict[str, str]]:
        """Deduped roster. ``filter="online"`` is resolved here, not in SQL.

        ``admin players online`` pushes ``online_status='Online'`` into the
        JOIN, and that keeps the wrong row. Funcom leaves a nameless
        ``encrypted_player_state`` leftover next to the live character row, and
        the leftover's ``online_status`` is stuck at 'Online' forever — so the
        SQL filter reports every account that ever played as connected, with
        no character name attached. Live-verified on an idle server: the named
        row read 'Offline' with a logout timestamp while the leftover still
        read 'Online', and ``/api/map/markers`` (which reads the plaintext
        ``dune.player_state`` instead) agreed the player was offline.

        So ask for every row and let :func:`dedupe_player_rows` resolve the
        account first — it prefers the row that owns the character name, the
        same authority the egg's own ``assert_player_offline`` write guard
        uses. Accounts that the ``all`` window missed (both reads are
        ``LIMIT 100``) are still carried over from the SQL-filtered list, so
        this only ever drops false positives, never a real player.
        """
        rows = self._player_rows("all")
        if filter != "online":
            return rows
        online = [row for row in rows if row_is_online(row)]
        seen = {row["fls_id"] for row in rows}
        online.extend(
            row for row in self._player_rows("online") if row["fls_id"] not in seen
        )
        return online

    def _player_rows(self, kind: str) -> list[dict[str, str]]:
        payload = _as_dict(self.request("GET", f"/api/players?filter={kind}"))
        if payload.get("ok") is False:
            raise DuneApiError(
                publish_detail(payload) or "player list failed",
                status=502,
            )
        return parse_player_table(str(payload.get("stdout") or ""))

    def partitions(self) -> dict[str, Any]:
        return _as_dict(self.request("GET", "/api/partitions"))

    def settings(self) -> dict[str, Any]:
        return _as_dict(self.request("GET", "/api/settings"))

    def server_info(self, *, max_age: float = SERVER_INFO_TTL_SECONDS) -> dict[str, Any]:
        """Cached ``{display_name, player_hard_cap}`` for the status card.

        Settings change only when an operator edits them, so a status poll
        reads the catalogue at most once per ``max_age``. A failed read keeps
        the last good answer instead of blanking the card — the grid poll that
        called us has already proven the endpoint is up.
        """
        with self._lock:
            if self._info_read_at and time.time() - self._info_read_at < max_age:
                return dict(self._info)
        try:
            payload = self.settings()
        except DuneApiError as exc:
            logger.info(
                "Dune /api/settings read failed for %s:%s: %s",
                self.endpoint.host,
                self.endpoint.port,
                exc,
            )
            with self._lock:
                return dict(self._info)
        info = settings_server_info(payload)
        with self._lock:
            self._info = info
            self._info_read_at = time.time()
        return dict(info)

    def map_markers(self, map_key: str) -> dict[str, Any]:
        key = (map_key or "").strip()
        if key not in MAP_KEYS:
            raise DuneApiError(
                f"Unknown map '{map_key}'. Allowed: {', '.join(MAP_KEYS)}"
            )
        return _as_dict(
            self.request("GET", f"/api/map/markers?map={quote(key, safe='')}")
        )

    def locations(self) -> dict[str, Any]:
        return _as_dict(self.request("GET", "/api/map/locations"))

    # --- writes ------------------------------------------------------------

    def publish(self, sub: str, body: Mapping[str, Any]) -> dict[str, Any]:
        payload = _as_dict(self.request("POST", f"/admin/{sub}", body))
        if payload.get("ok") is False:
            raise DuneApiError(publish_detail(payload) or f"{sub} failed", status=502)
        return payload

    def broadcast(self, title: str, body: str, duration: int = 30) -> dict[str, Any]:
        return self.publish(
            "broadcast",
            {"title": title, "body": body, "duration": int(duration)},
        )

    def kick(self, player_id: str) -> dict[str, Any]:
        return self.publish("kick", {"player_id": player_id})

    def save_settings(self, settings: Mapping[str, str]) -> dict[str, Any]:
        return _as_dict(
            self.request("POST", "/api/settings", {"settings": dict(settings)})
        )

    def add_location(self, location: Mapping[str, Any]) -> dict[str, Any]:
        return _as_dict(
            self.request(
                "POST",
                "/api/map/locations",
                {"action": "add", "location": dict(location)},
            )
        )

    def remove_location(self, name: str) -> dict[str, Any]:
        return _as_dict(
            self.request("POST", "/api/map/locations", {"action": "remove", "name": name})
        )

    def teleport(self, player: str, location: str) -> dict[str, Any]:
        payload = _as_dict(
            self.request(
                "POST",
                "/api/map/teleport",
                {"player": player, "location": location},
            )
        )
        if payload.get("ok") is False:
            raise DuneApiError(publish_detail(payload) or "teleport failed", status=502)
        return payload

    def scale_instance(
        self, map_name: str, replicas: int, *, force: bool = False
    ) -> dict[str, Any]:
        return _as_dict(
            self.request(
                "POST",
                f"/api/instances/{quote(map_name, safe='')}/scale",
                {"replicas": int(replicas), "force": bool(force)},
            )
        )

    def dimension_up(self, partition_id: int) -> dict[str, Any]:
        return _as_dict(
            self.request("POST", f"/api/instances/dimension/{int(partition_id)}/up", {})
        )

    def dimension_down(self, partition_id: int, *, force: bool = False) -> dict[str, Any]:
        return _as_dict(
            self.request(
                "POST",
                f"/api/instances/dimension/{int(partition_id)}/down",
                {"force": bool(force)},
            )
        )

    def park_sietch(self, partition_id: int, *, force: bool = False) -> dict[str, Any]:
        return _as_dict(
            self.request(
                "POST",
                f"/api/sietches/{int(partition_id)}/park",
                {"force": bool(force)},
            )
        )

    def unpark_sietch(self, partition_id: int) -> dict[str, Any]:
        return _as_dict(
            self.request("POST", f"/api/sietches/{int(partition_id)}/unpark", {})
        )

    def remove_sietch(self, partition_id: int, *, force: bool = False) -> dict[str, Any]:
        return _as_dict(
            self.request(
                "POST",
                f"/api/sietches/{int(partition_id)}/remove",
                {"force": bool(force)},
            )
        )


def _as_dict(payload: Any) -> dict[str, Any]:
    return dict(payload) if isinstance(payload, Mapping) else {}


def _error_snippet(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except ValueError:
        return raw[:200]
    if isinstance(data, Mapping):
        for key in ("error", "detail", "message"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:200]
    return raw[:200]


@dataclass
class _PoolEntry:
    client: DuneClient
    lock: threading.RLock = field(default_factory=threading.RLock)


class DuneSessionPool:
    """Process-wide cache of admin-HTTP clients, keyed by endpoint."""

    def __init__(self) -> None:
        self._guard = threading.RLock()
        self._entries: dict[tuple[str, int, str, bool, bool, str], _PoolEntry] = {}

    def client(
        self,
        endpoint: ApiEndpoint,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> DuneClient:
        key = endpoint.key
        with self._guard:
            entry = self._entries.get(key)
            if entry is not None:
                entry.client.timeout = timeout
                return entry.client
        client = DuneClient(endpoint, timeout=timeout)
        with self._guard:
            existing = self._entries.get(key)
            if existing is not None:
                client.close()
                return existing.client
            self._entries[key] = _PoolEntry(client=client)
            logger.info("Opened Dune admin HTTP session to %s:%s", endpoint.host, endpoint.port)
        return client

    def invalidate_endpoint(self, host: str, port: int) -> None:
        host = (host or "").strip()
        port = int(port)
        with self._guard:
            keys = [k for k in self._entries if k[0] == host and k[1] == port]
            entries = [self._entries.pop(k) for k in keys]
        for entry in entries:
            entry.client.close()
        if entries:
            logger.info("Invalidated Dune admin HTTP session %s:%s", host, port)

    def invalidate_all(self) -> None:
        with self._guard:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            entry.client.close()
        if entries:
            logger.info("Closed all Dune admin HTTP sessions (%s)", len(entries))

    def stats(self) -> list[dict[str, Any]]:
        with self._guard:
            items = list(self._entries.items())
        return [
            {
                "host": key[0],
                "port": key[1],
                "https": key[3],
                "verify_tls": key[4],
                "pinned": bool(key[5]),
                "call_count": entry.client.call_count,
                "created_at": entry.client.created_at or None,
                "last_used": entry.client.last_used or None,
            }
            for key, entry in items
        ]


dune_pool = DuneSessionPool()

"""Palworld Dedicated Server REST API client.

The dedicated server exposes a small REST API, enabled in ``PalWorldSettings.ini``
with ``RESTAPIEnabled=True`` / ``RESTAPIPort=8212``. There is no launch argument
for it, and it listens on a port of its own — separate from the game port (8211)
and from RCON (25575), which Pocketpair has deprecated in favour of this API::

    GET  http://<host>:8212/v1/api/metrics
    Authorization: Basic base64("admin:<AdminPassword>")

Differences from the Satisfactory transport that shaped this module:

* **Auth** — HTTP Basic, and stateless: the username is the literal string
  ``admin`` and the password is ``AdminPassword`` from the INI. There is no
  token to obtain, cache or refresh. What *is* worth caching is the failure —
  a rejected password would otherwise be retried on every status poll.
* **TLS** — the server speaks plain HTTP and has no TLS support at all. The
  upstream docs warn these endpoints "are not designed to be exposed directly
  to the Internet", so reverse-proxied deployments are common; ``use_https``
  switches the scheme and re-uses the shared verification / pinning helpers.
* **Errors** — the API documents only 200/400/401 and defines **no** error body
  schema. POST success bodies are undocumented too, so every call keys off the
  status code and treats the body as best-effort.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

from app.services.errors import CommandError
from app.services.tls_pins import (
    CertFetchError,
    fetch_cert_fingerprint,
    normalize_fingerprint,
    pin_mismatch_message,
)

logger = logging.getLogger(__name__)

API_PATH = "/v1/api"
DEFAULT_API_PORT = 8212
# Not configurable server-side: the API only accepts this account name
DEFAULT_USERNAME = "admin"
DEFAULT_TIMEOUT = 10.0
# /save is synchronous and blocks until the world is written
SAVE_TIMEOUT = 30.0

# Don't re-attempt a rejected password on every status poll
DEFAULT_AUTH_COOLDOWN_SECONDS = 30.0

# /game-data answers with this instead of a 4xx when the launch flag is missing
GAMEDATA_DISABLED_MARKER = "gamedata api is not enabled"
GAMEDATA_DISABLED_CODE = "gamedata_disabled"


class PalworldApiError(CommandError):
    """An API call failed (HTTP status, error body, or transport problem)."""

    def __init__(self, message: str, *, code: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class PalworldAuthError(PalworldApiError):
    """The admin password was missing or rejected."""


class PalworldTimeoutError(PalworldApiError):
    """The server did not answer in time."""


class PalworldTlsError(PalworldApiError):
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
    username: str = DEFAULT_USERNAME

    @property
    def scheme(self) -> str:
        return "https" if self.use_https else "http"

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{int(self.port)}{API_PATH}"

    @property
    def key(self) -> tuple[str, int, str, str, bool, bool, str]:
        """Pool identity — every field, so any change opens a fresh session."""
        return (
            self.host.strip(),
            int(self.port),
            self.username,
            self.secret,
            bool(self.use_https),
            bool(self.verify_tls),
            normalize_fingerprint(self.cert_fingerprint),
        )


def pick(data: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a response mapping, ignoring key capitalisation.

    Palworld contradicts its own documentation on casing — ``/players`` returns
    ``userId`` while ``/game-data`` returns ``userid`` for the same value — so
    matching a single spelling exactly is how a field silently reads as empty.
    (``satisfactory_api`` carries its own copy of this for the same reason; the
    two transports stay decoupled deliberately.)
    """
    if not isinstance(data, Mapping):
        return default
    if name in data:
        return data[name]
    target = name.lower()
    for key, value in data.items():
        if str(key).lower() == target:
            return value
    return default


def basic_auth_header(username: str, password: str) -> str:
    raw = f"{username}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _looks_like_gamedata_disabled(payload: Any, text: str) -> bool:
    if isinstance(payload, str) and GAMEDATA_DISABLED_MARKER in payload.lower():
        return True
    return GAMEDATA_DISABLED_MARKER in (text or "").lower()


def _gamedata_disabled() -> PalworldApiError:
    return PalworldApiError(
        "The game-data API is disabled. Add -enable-gamedata-api to the dedicated "
        "server's launch arguments and restart it.",
        code=GAMEDATA_DISABLED_CODE,
    )


class PalworldClient:
    """One authenticated conversation with a server's REST API.

    Not created directly in app code — use :data:`palworld_pool` so the
    underlying connection pool is reused across polls.
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
        self.created_at = time.time()
        self.last_used = 0.0
        self.call_count = 0

        # Pin verification does network I/O, so skip it when a test transport is
        # injected — there is no real socket to read a certificate from.
        if endpoint.cert_fingerprint and endpoint.use_https and transport is None:
            self._verify_pinned_certificate()

        if transport is not None:
            self._http = httpx.Client(timeout=timeout, transport=transport)
        else:
            self._http = httpx.Client(timeout=timeout, verify=bool(endpoint.verify_tls))

        self._headers = {
            "Accept": "application/json",
            "Authorization": basic_auth_header(endpoint.username, endpoint.secret or ""),
        }

    # --- lifecycle ---------------------------------------------------------

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
                raise PalworldTlsError(str(exc)) from exc
            if exc.kind == "timeout":
                raise PalworldTimeoutError(str(exc)) from exc
            raise PalworldApiError(str(exc)) from exc
        if observed != expected:
            raise PalworldTlsError(
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
    ) -> Any:
        """Call an endpoint. Returns the decoded body, or ``None`` for empty ones."""
        with self._lock:
            self._check_auth_cooldown()
            response = self._send(method, path, json_body, timeout)
            result = self._parse(response, f"{method} {path}")
            self.last_used = time.time()
            self.call_count += 1
            return result

    def _check_auth_cooldown(self) -> None:
        now = time.time()
        if self._auth_blocked_until > now:
            wait = int(self._auth_blocked_until - now)
            raise PalworldAuthError(
                f"Palworld API authentication for {self.endpoint.host}:"
                f"{self.endpoint.port} is in cooldown "
                f"({self._auth_error or 'rejected'}; retry in ~{wait}s)",
                status=401,
            )

    def _send(
        self,
        method: str,
        path: str,
        json_body: Mapping[str, Any] | None,
        timeout: float | None,
    ) -> httpx.Response:
        url = f"{self.endpoint.base_url}{path}"
        kwargs: dict[str, Any] = {"headers": dict(self._headers)}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if json_body is not None:
            kwargs["json"] = dict(json_body)
        elif method.upper() == "POST":
            # Bodyless POSTs still need an explicit Content-Length: 0
            kwargs["content"] = b""
        try:
            return self._http.request(method.upper(), url, **kwargs)
        except httpx.TimeoutException as exc:
            raise PalworldTimeoutError(
                f"Palworld API timed out after "
                f"{timeout if timeout is not None else self.timeout:g}s calling "
                f"{method.upper()} {path} on {self.endpoint.host}:{self.endpoint.port}"
            ) from exc
        except httpx.ConnectError as exc:
            message = str(exc)
            lowered = message.lower()
            if "certificate" in lowered or "ssl" in lowered or "tls" in lowered:
                raise PalworldTlsError(
                    f"TLS handshake failed for {url}: {message}. Palworld itself serves "
                    f"plain HTTP — turn off 'Use HTTPS' unless the server sits behind a "
                    f"reverse proxy, or turn off 'Verify TLS' and pin its fingerprint."
                ) from exc
            raise PalworldApiError(f"Could not connect to {url}: {message}") from exc
        except httpx.HTTPError as exc:
            raise PalworldApiError(f"Palworld API request failed: {exc}") from exc

    def _parse(self, response: httpx.Response, what: str) -> Any:
        status = response.status_code
        text = response.text or ""

        if status == 401:
            self._auth_error = "admin password rejected"
            self._auth_blocked_until = time.time() + self.auth_cooldown_seconds
            raise PalworldAuthError(
                f"{what} was rejected: check the server's AdminPassword "
                f"(the REST API signs in as '{self.endpoint.username}')",
                status=status,
            )

        if status >= 400:
            snippet = text.strip()[:200]
            raise PalworldApiError(
                f"{what} failed: {snippet or f'HTTP {status}'}",
                status=status,
            )

        # A successful call clears any earlier cooldown
        self._auth_blocked_until = 0.0
        self._auth_error = ""

        if status == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            # POST success bodies are undocumented and not always JSON
            return text.strip()

    # --- reads -------------------------------------------------------------

    def info(self) -> dict[str, Any]:
        return _as_dict(self.request("GET", "/info"))

    def players(self) -> list[dict[str, Any]]:
        payload = _as_dict(self.request("GET", "/players"))
        raw = payload.get("players")
        return [p for p in raw if isinstance(p, Mapping)] if isinstance(raw, list) else []

    def settings(self) -> dict[str, Any]:
        return _as_dict(self.request("GET", "/settings"))

    def metrics(self) -> dict[str, Any]:
        return _as_dict(self.request("GET", "/metrics"))

    def game_data(self) -> dict[str, Any]:
        """World actor snapshot. Needs the server launched with ``-enable-gamedata-api``.

        Without the flag the server answers with a plain-text refusal rather than
        a status code, and reportedly not always with 200 — so the marker is
        checked on both the success and the failure path and re-raised with a
        code the router can turn into setup instructions.
        """
        try:
            payload = self.request("GET", "/game-data")
        except PalworldAuthError:
            raise
        except PalworldApiError as exc:
            if _looks_like_gamedata_disabled(None, str(exc)):
                raise _gamedata_disabled() from exc
            raise
        if _looks_like_gamedata_disabled(payload, ""):
            raise _gamedata_disabled()
        return _as_dict(payload)

    # --- writes ------------------------------------------------------------

    def announce(self, message: str) -> str:
        return _as_text(self.request("POST", "/announce", {"message": message}))

    def kick(self, userid: str, message: str = "") -> str:
        body: dict[str, Any] = {"userid": userid}
        if message:
            body["message"] = message
        return _as_text(self.request("POST", "/kick", body))

    def ban(self, userid: str, message: str = "") -> str:
        body: dict[str, Any] = {"userid": userid}
        if message:
            body["message"] = message
        return _as_text(self.request("POST", "/ban", body))

    def unban(self, userid: str) -> str:
        return _as_text(self.request("POST", "/unban", {"userid": userid}))

    def save(self) -> str:
        return _as_text(self.request("POST", "/save", timeout=SAVE_TIMEOUT))

    def shutdown(self, waittime: int, message: str = "") -> str:
        body: dict[str, Any] = {"waittime": int(waittime)}
        if message:
            body["message"] = message
        return _as_text(self.request("POST", "/shutdown", body))

    def stop(self) -> str:
        return _as_text(self.request("POST", "/stop"))


def _as_dict(payload: Any) -> dict[str, Any]:
    return dict(payload) if isinstance(payload, Mapping) else {}


def _as_text(payload: Any) -> str:
    """POST responses are undocumented — surface whatever came back, or 'ok'."""
    if payload is None:
        return "ok"
    if isinstance(payload, str):
        return payload.strip() or "ok"
    if isinstance(payload, Mapping):
        for key in ("message", "Message", "result", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return str(payload)


@dataclass
class _PoolEntry:
    client: PalworldClient
    lock: threading.RLock = field(default_factory=threading.RLock)


class PalworldSessionPool:
    """Process-wide cache of API clients, keyed by endpoint.

    Mirrors :class:`app.services.satisfactory_api.SatisfactorySessionPool` so
    every transport is invalidated the same way when a server row changes.
    """

    def __init__(self) -> None:
        self._guard = threading.RLock()
        self._entries: dict[tuple[str, int, str, str, bool, bool, str], _PoolEntry] = {}

    def client(
        self,
        endpoint: ApiEndpoint,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> PalworldClient:
        key = endpoint.key
        with self._guard:
            entry = self._entries.get(key)
            if entry is not None:
                entry.client.timeout = timeout
                return entry.client
        # Construct outside the guard: pin verification does network I/O
        client = PalworldClient(endpoint, timeout=timeout)
        with self._guard:
            existing = self._entries.get(key)
            if existing is not None:
                client.close()
                return existing.client
            self._entries[key] = _PoolEntry(client=client)
            logger.info("Opened Palworld API session to %s:%s", endpoint.host, endpoint.port)
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
            logger.info("Invalidated Palworld API session %s:%s", host, port)

    def invalidate_all(self) -> None:
        with self._guard:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            entry.client.close()
        if entries:
            logger.info("Closed all Palworld API sessions (%s)", len(entries))

    def stats(self) -> list[dict[str, Any]]:
        with self._guard:
            items = list(self._entries.items())
        return [
            {
                "host": key[0],
                "port": key[1],
                "https": key[4],
                "verify_tls": key[5],
                "pinned": bool(key[6]),
                "call_count": entry.client.call_count,
                "created_at": entry.client.created_at or None,
                "last_used": entry.client.last_used or None,
            }
            for key, entry in items
        ]


# Process-wide pool
palworld_pool = PalworldSessionPool()

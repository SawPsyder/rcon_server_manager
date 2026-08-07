"""Satisfactory Dedicated Server HTTPS API client.

The dedicated server exposes a JSON-over-HTTPS API on its game port (7777 by
default)::

    POST https://<host>:<port>/api/v1
    Authorization: Bearer <token>
    {"function": "QueryServerState", "data": {...}}

Responses are ``200`` with ``{"data": {...}}``, ``204`` with no body, or an
error object ``{"errorCode": "...", "errorMessage": "..."}``. Request field
names are sent as lowerCamelCase; responses are read case-insensitively via
:func:`pick`, because the shipped documentation and the actual wire format
disagree on capitalisation.

Two things differ from the RCON transport this app started with:

* **Auth** — a bearer token, obtained either from a long-lived API token
  (``server.GenerateAPIToken`` in the server console) or by exchanging the admin
  password via ``PasswordLogin``. Tokens are cached per endpoint.
* **TLS** — the server generates a self-signed certificate unless the operator
  installs their own, so verification is opt-in per server and can instead be
  anchored on a pinned SHA-256 fingerprint.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

from app.services.errors import CommandError
from app.services.tls_pins import (
    CertFetchError,
    format_fingerprint,
    normalize_fingerprint,
    pin_mismatch_message,
)
from app.services.tls_pins import fetch_cert_fingerprint as _fetch_cert_fingerprint

logger = logging.getLogger(__name__)

API_PATH = "/api/v1"
DEFAULT_API_PORT = 7777
DEFAULT_TIMEOUT = 10.0
PRIVILEGE_ADMINISTRATOR = "Administrator"
PRIVILEGE_INITIAL_ADMIN = "InitialAdmin"

# Don't re-attempt a rejected login on every status poll
DEFAULT_AUTH_COOLDOWN_SECONDS = 30.0

# Tokens are "<base64 JSON payload>.<hex fingerprint>" — this shape check plus a
# payload decode distinguishes a pasted API token from an admin password without
# asking the user which one they gave us.
TOKEN_RE = re.compile(r"^[A-Za-z0-9+/_\-=]{8,}\.[0-9a-fA-F]{8,}$")

_AUTH_ERROR_CODES = {
    "unauthorized",
    "forbidden",
    "invalid_token",
    "expired_token",
    "wrong_password",
    "insufficient_privilege",
    "insufficient_privileges",
    "passwordless_login_not_possible",
}


class SatisfactoryApiError(CommandError):
    """An API call failed (HTTP status, error body, or transport problem)."""

    def __init__(self, message: str, *, code: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class SatisfactoryAuthError(SatisfactoryApiError):
    """Missing, wrong or expired credentials."""


class SatisfactoryTimeoutError(SatisfactoryApiError):
    """The server did not answer in time."""


class SatisfactoryTlsError(SatisfactoryApiError):
    """Certificate verification or fingerprint pinning failed."""

    def __init__(self, message: str, *, observed_fingerprint: str = "") -> None:
        super().__init__(message, code="tls_error")
        self.observed_fingerprint = observed_fingerprint


def pick(data: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a response mapping, ignoring key capitalisation."""
    if not isinstance(data, Mapping):
        return default
    if name in data:
        return data[name]
    for variant in (name[:1].lower() + name[1:], name[:1].upper() + name[1:]):
        if variant in data:
            return data[variant]
    target = name.lower()
    for key, value in data.items():
        if str(key).lower() == target:
            return value
    return default


def fetch_cert_fingerprint(host: str, port: int, timeout: float = DEFAULT_TIMEOUT) -> str:
    """SHA-256 of the server's presented certificate (DER), as lowercase hex.

    A server that is simply down raises :class:`SatisfactoryApiError`, not a TLS
    error — "certificate problem" would send the operator chasing the wrong fix.
    """
    try:
        return _fetch_cert_fingerprint(host, port, timeout)
    except CertFetchError as exc:
        if exc.kind == "tls":
            raise SatisfactoryTlsError(str(exc)) from exc
        if exc.kind == "timeout":
            raise SatisfactoryTimeoutError(str(exc)) from exc
        raise SatisfactoryApiError(str(exc)) from exc


@dataclass(frozen=True)
class ApiEndpoint:
    host: str
    port: int = DEFAULT_API_PORT
    secret: str = ""
    verify_tls: bool = False
    cert_fingerprint: str = ""

    @property
    def url(self) -> str:
        return f"https://{self.host}:{int(self.port)}{API_PATH}"

    @property
    def key(self) -> tuple[str, int, str, bool, str]:
        return (
            self.host.strip(),
            int(self.port),
            self.secret,
            bool(self.verify_tls),
            normalize_fingerprint(self.cert_fingerprint),
        )


def looks_like_api_token(secret: str) -> bool:
    """True when a stored secret is a Satisfactory bearer token, not a password.

    Shape alone is too loose — a password like ``mybase64ish.deadbeef`` matches —
    so the base64 half must also decode to the JSON object the game puts there.
    """
    text = (secret or "").strip()
    if not TOKEN_RE.match(text):
        return False
    payload = text.rsplit(".", 1)[0]
    try:
        decoded = base64.b64decode(payload + "=" * (-len(payload) % 4))
        return isinstance(json.loads(decoded.decode("utf-8")), dict)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return False


class SatisfactoryClient:
    """One authenticated conversation with a server's HTTPS API.

    Not created directly in app code — use :data:`satisfactory_pool` so the
    bearer token and the underlying connection pool are reused across polls.
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
        self._token = ""
        # "api" (static token, retrying is pointless) | "login" (may expire)
        self._token_kind = ""
        self._auth_blocked_until = 0.0
        self._auth_error = ""
        self.created_at = time.time()
        self.last_used = 0.0
        self.call_count = 0

        if endpoint.cert_fingerprint and transport is None:
            self._verify_pinned_certificate()

        if transport is not None:
            self._http = httpx.Client(timeout=timeout, transport=transport)
        else:
            self._http = httpx.Client(timeout=timeout, verify=bool(endpoint.verify_tls))

    # --- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:  # noqa: BLE001
            pass

    def _verify_pinned_certificate(self) -> None:
        expected = normalize_fingerprint(self.endpoint.cert_fingerprint)
        observed = fetch_cert_fingerprint(self.endpoint.host, self.endpoint.port, self.timeout)
        if observed != expected:
            raise SatisfactoryTlsError(
                pin_mismatch_message(
                    self.endpoint.host, self.endpoint.port, expected, observed
                ),
                observed_fingerprint=observed,
            )

    # --- transport ---------------------------------------------------------

    def call(
        self,
        function: str,
        data: Mapping[str, Any] | None = None,
        *,
        auth: bool = True,
    ) -> Any:
        """Invoke an API function. Returns the ``data`` object, or ``None`` (204)."""
        body: dict[str, Any] = {"function": function}
        if data:
            body["data"] = dict(data)

        with self._lock:
            for attempt in (1, 2):
                token = self._ensure_token() if auth else ""
                headers = {"Content-Type": "application/json"}
                if token:
                    headers["Authorization"] = f"Bearer {token}"

                response = self._post(body, headers, function)

                expired = (
                    response.status_code in (401, 403)
                    and bool(token)
                    and self._token_kind == "login"
                    and attempt == 1
                )
                if expired:
                    logger.info(
                        "Satisfactory token rejected by %s (%s) — re-authenticating once",
                        self.endpoint.host,
                        function,
                    )
                    self._token = ""
                    self._token_kind = ""
                    continue

                result = self._parse(response, function)
                self.last_used = time.time()
                self.call_count += 1
                return result

        raise SatisfactoryApiError(f"{function} failed after re-authentication")

    def _post(
        self,
        body: Mapping[str, Any],
        headers: Mapping[str, str],
        function: str,
    ) -> httpx.Response:
        url = self.endpoint.url
        try:
            return self._http.post(url, json=dict(body), headers=dict(headers))
        except httpx.TimeoutException as exc:
            raise SatisfactoryTimeoutError(
                f"Satisfactory API timed out after {self.timeout:g}s calling {function} "
                f"on {self.endpoint.host}:{self.endpoint.port}"
            ) from exc
        except httpx.ConnectError as exc:
            message = str(exc)
            lowered = message.lower()
            if "certificate" in lowered or "ssl" in lowered or "tls" in lowered:
                raise SatisfactoryTlsError(
                    f"TLS handshake failed for {url}: {message}. Satisfactory serves a "
                    f"self-signed certificate by default — turn off 'Verify TLS' for this "
                    f"server, or pin its certificate fingerprint."
                ) from exc
            raise SatisfactoryApiError(f"Could not connect to {url}: {message}") from exc
        except httpx.HTTPError as exc:
            raise SatisfactoryApiError(f"Satisfactory API request failed: {exc}") from exc

    def _parse(self, response: httpx.Response, function: str) -> Any:
        status = response.status_code
        payload: Any = None
        if response.content:
            try:
                payload = response.json()
            except ValueError:
                payload = None

        code = str(pick(payload, "errorCode", "") or "")
        if code:
            message = str(pick(payload, "errorMessage", "") or "")
            raise _api_error(function, code=code, message=message, status=status)

        if status >= 400:
            snippet = (response.text or "").strip()[:200]
            raise _api_error(
                function,
                code="",
                message=snippet or f"HTTP {status}",
                status=status,
            )

        if status == 204 or not response.content:
            return None
        if isinstance(payload, Mapping):
            if "data" in payload or "Data" in payload:
                return pick(payload, "data")
            # Tolerate servers that return the payload unwrapped
            return payload
        return payload

    # --- auth --------------------------------------------------------------

    def _ensure_token(self) -> str:
        if self._token:
            return self._token

        now = time.time()
        if self._auth_blocked_until > now:
            wait = int(self._auth_blocked_until - now)
            raise SatisfactoryAuthError(
                f"Satisfactory API authentication for {self.endpoint.host}:"
                f"{self.endpoint.port} is in cooldown "
                f"({self._auth_error or 'rejected'}; retry in ~{wait}s)"
            )

        secret = (self.endpoint.secret or "").strip()
        if looks_like_api_token(secret):
            # Static token — use as-is; a bad one surfaces on the first real call
            self._token = secret
            self._token_kind = "api"
            return self._token

        try:
            if secret:
                data = self.call(
                    "PasswordLogin",
                    {"minimumPrivilegeLevel": PRIVILEGE_ADMINISTRATOR, "password": secret},
                    auth=False,
                )
            else:
                data = self.call(
                    "PasswordlessLogin",
                    {"minimumPrivilegeLevel": PRIVILEGE_ADMINISTRATOR},
                    auth=False,
                )
        except SatisfactoryApiError as exc:
            if isinstance(exc, SatisfactoryAuthError):
                self._auth_error = str(exc)
                self._auth_blocked_until = time.time() + self.auth_cooldown_seconds
            raise

        token = str(pick(data, "authenticationToken", "") or "")
        if not token:
            raise SatisfactoryAuthError(
                "Satisfactory login returned no authentication token"
            )
        self._token = token
        self._token_kind = "login"
        self._auth_error = ""
        self._auth_blocked_until = 0.0
        return token

    @property
    def token_kind(self) -> str:
        return self._token_kind

    # --- API functions -----------------------------------------------------

    def health_check(self, client_custom_data: str = "") -> dict[str, Any]:
        """Reachability probe — the only call that needs no credentials."""
        data = self.call(
            "HealthCheck", {"clientCustomData": client_custom_data}, auth=False
        )
        return dict(data) if isinstance(data, Mapping) else {}

    def verify_token(self) -> None:
        self.call("VerifyAuthenticationToken")

    def query_server_state(self) -> dict[str, Any]:
        data = self.call("QueryServerState")
        state = pick(data, "serverGameState")
        return dict(state) if isinstance(state, Mapping) else {}

    def get_server_options(self) -> dict[str, Any]:
        data = self.call("GetServerOptions")
        return {
            "server_options": dict(pick(data, "serverOptions", {}) or {}),
            "pending_server_options": dict(pick(data, "pendingServerOptions", {}) or {}),
        }

    def apply_server_options(self, options: Mapping[str, str]) -> None:
        self.call("ApplyServerOptions", {"updatedServerOptions": dict(options)})

    def get_advanced_game_settings(self) -> dict[str, Any]:
        data = self.call("GetAdvancedGameSettings")
        return {
            "creative_mode_enabled": bool(pick(data, "creativeModeEnabled", False)),
            "advanced_game_settings": dict(pick(data, "advancedGameSettings", {}) or {}),
        }

    def apply_advanced_game_settings(self, settings: Mapping[str, Any]) -> None:
        self.call(
            "ApplyAdvancedGameSettings",
            {"appliedAdvancedGameSettings": dict(settings)},
        )

    def run_command(self, command: str) -> dict[str, Any]:
        data = self.call("RunCommand", {"command": command})
        return {
            "result": str(pick(data, "commandResult", "") or ""),
            "return_value": bool(pick(data, "returnValue", True)),
        }

    def claim_server(self, server_name: str, admin_password: str) -> str:
        """Claim an unclaimed server. Returns the new admin token.

        Claiming needs an ``InitialAdmin`` token, which only an unclaimed server
        hands out, so this deliberately does its own login instead of going
        through the normal Administrator token path.
        """
        login = self.call(
            "PasswordlessLogin",
            {"minimumPrivilegeLevel": PRIVILEGE_INITIAL_ADMIN},
            auth=False,
        )
        initial_token = str(pick(login, "authenticationToken", "") or "")
        if not initial_token:
            raise SatisfactoryAuthError(
                "Server did not issue an InitialAdmin token — it is probably already claimed"
            )
        with self._lock:
            self._token = initial_token
            self._token_kind = "login"
            data = self.call(
                "ClaimServer",
                {"serverName": server_name, "adminPassword": admin_password},
            )
            token = str(pick(data, "authenticationToken", "") or "")
            # The InitialAdmin token dies with the claim — keep the new one
            self._token = token
            self._token_kind = "login" if token else ""
        return token

    def rename_server(self, server_name: str) -> None:
        self.call("RenameServer", {"serverName": server_name})

    def set_client_password(self, password: str) -> None:
        self.call("SetClientPassword", {"password": password})

    def set_admin_password(self, password: str) -> None:
        self.call("SetAdminPassword", {"password": password})

    def set_auto_load_session_name(self, session_name: str) -> None:
        self.call("SetAutoLoadSessionName", {"sessionName": session_name})

    def shutdown(self) -> None:
        self.call("Shutdown")

    def save_game(self, save_name: str) -> None:
        self.call("SaveGame", {"saveName": save_name})

    def delete_save_file(self, save_name: str) -> None:
        self.call("DeleteSaveFile", {"saveName": save_name})

    def delete_save_session(self, session_name: str) -> None:
        self.call("DeleteSaveSession", {"sessionName": session_name})

    def enumerate_sessions(self) -> dict[str, Any]:
        data = self.call("EnumerateSessions")
        sessions = pick(data, "sessions", []) or []
        # NB: `or -1` would turn the valid index 0 into -1
        try:
            index = int(pick(data, "currentSessionIndex", -1))
        except (TypeError, ValueError):
            index = -1
        return {
            "sessions": list(sessions) if isinstance(sessions, list) else [],
            "current_session_index": index,
        }

    def load_game(self, save_name: str, *, enable_advanced_game_settings: bool = False) -> None:
        self.call(
            "LoadGame",
            {
                "saveName": save_name,
                "enableAdvancedGameSettings": bool(enable_advanced_game_settings),
            },
        )

    def create_new_game(
        self,
        session_name: str,
        *,
        map_name: str = "",
        starting_location: str = "",
        skip_onboarding: bool = True,
        advanced_game_settings: Mapping[str, Any] | None = None,
    ) -> None:
        new_game: dict[str, Any] = {
            "sessionName": session_name,
            "skipOnboarding": bool(skip_onboarding),
        }
        if map_name:
            new_game["mapName"] = map_name
        if starting_location:
            new_game["startingLocation"] = starting_location
        if advanced_game_settings:
            new_game["advancedGameSettings"] = dict(advanced_game_settings)
        self.call("CreateNewGame", {"newGameData": new_game})


def _api_error(
    function: str,
    *,
    code: str,
    message: str,
    status: int | None,
) -> SatisfactoryApiError:
    parts = [p for p in (code, message) if p]
    detail = " — ".join(parts) if parts else (f"HTTP {status}" if status else "unknown error")
    text = f"{function} failed: {detail}"

    if code.lower() in _AUTH_ERROR_CODES or status in (401, 403):
        if code.lower() == "passwordless_login_not_possible":
            text = (
                "This server is already claimed — set its admin password or an API "
                "token as the server secret under Servers."
            )
        return SatisfactoryAuthError(text, code=code, status=status)
    return SatisfactoryApiError(text, code=code, status=status)


@dataclass
class _PoolEntry:
    client: SatisfactoryClient
    lock: threading.RLock = field(default_factory=threading.RLock)


class SatisfactorySessionPool:
    """Process-wide cache of authenticated API clients, keyed by endpoint.

    Mirrors :class:`app.services.rcon_pool.RconConnectionPool` so both
    transports are invalidated the same way when a server row changes.
    """

    def __init__(self) -> None:
        self._guard = threading.RLock()
        self._entries: dict[tuple[str, int, str, bool, str], _PoolEntry] = {}

    def client(
        self,
        endpoint: ApiEndpoint,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> SatisfactoryClient:
        key = endpoint.key
        with self._guard:
            entry = self._entries.get(key)
            if entry is not None:
                entry.client.timeout = timeout
                return entry.client
        # Construct outside the guard: pin verification does network I/O
        client = SatisfactoryClient(endpoint, timeout=timeout)
        with self._guard:
            existing = self._entries.get(key)
            if existing is not None:
                client.close()
                return existing.client
            self._entries[key] = _PoolEntry(client=client)
            logger.info(
                "Opened Satisfactory API session to %s:%s", endpoint.host, endpoint.port
            )
        return client

    def call(
        self,
        endpoint: ApiEndpoint,
        function: str,
        data: Mapping[str, Any] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> Any:
        return self.client(endpoint, timeout=timeout).call(function, data)

    def invalidate_endpoint(self, host: str, port: int) -> None:
        host = (host or "").strip()
        port = int(port)
        with self._guard:
            keys = [k for k in self._entries if k[0] == host and k[1] == port]
            entries = [self._entries.pop(k) for k in keys]
        for entry in entries:
            entry.client.close()
        if entries:
            logger.info("Invalidated Satisfactory API session %s:%s", host, port)

    def invalidate_all(self) -> None:
        with self._guard:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            entry.client.close()
        if entries:
            logger.info("Closed all Satisfactory API sessions (%s)", len(entries))

    def stats(self) -> list[dict[str, Any]]:
        with self._guard:
            items = list(self._entries.items())
        return [
            {
                "host": key[0],
                "port": key[1],
                "verify_tls": key[3],
                "pinned": bool(key[4]),
                "token": entry.client.token_kind or None,
                "call_count": entry.client.call_count,
                "created_at": entry.client.created_at or None,
                "last_used": entry.client.last_used or None,
            }
            for key, entry in items
        ]


# Process-wide pool
satisfactory_pool = SatisfactorySessionPool()

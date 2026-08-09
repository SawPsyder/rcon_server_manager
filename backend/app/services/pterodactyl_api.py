"""Pterodactyl Panel Client API transport.

The panel's *Client* API is the only one that can do what this integration
needs::

    GET  {panel}/api/client                              list visible servers
    GET  {panel}/api/client/servers/{uuid}               name, status, limits
    GET  {panel}/api/client/servers/{uuid}/resources     live utilisation
    GET  {panel}/api/client/servers/{uuid}/startup        egg startup variables
    PUT  {panel}/api/client/servers/{uuid}/startup/variable  update one variable
    POST {panel}/api/client/servers/{uuid}/power         start|stop|restart|kill
    Authorization: Bearer ptlc_...

The *Application* API (``ptla_``, ``/admin/api``) has neither a resources nor a
power endpoint, so an admin key is the wrong credential regardless of how much
access it carries. Verified against pterodactyl/panel v1.15.0.

Behaviours of the upstream API that shaped this module:

* **The panel caches ``/resources`` for 20 seconds** server-side, so polling
  faster returns byte-identical stale data and only burns rate limit. A single
  background poller (``app.services.pterodactyl_poller``) refreshes every linked
  server on exactly that cadence and everything else - every browser tab, every
  history sample - reads the cache it fills. Nothing on a request path talks to
  the panel unless the cache is cold.
* **Limits are not in the resources payload.** ``memory_limit_bytes`` exists
  only on the wings websocket stream; over REST the limits come from the server
  object and are cached separately and for far longer, since they change only
  on reconfiguration.
* **Power is fire-and-forget.** A 204 means wings accepted the signal, not that
  the state changed - the caller must re-poll. Signals are idempotent.
* **404 conflates "no such server" and "no access"** deliberately, so the two
  cannot be distinguished and the message must cover both.
* **Rate limit is 256 requests/minute per key**, with ``Retry-After`` on a 429.
* **Never validate the token prefix.** Pterodactyl issues ``ptlc_``, the
  Pelican fork issues ``pacc_``, and keys migrated between them keep their
  original prefix. Prefix validation is the single most likely way to break a
  working panel.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

import httpx

from app.services.errors import CommandError
from app.services.pterodactyl_settings import PterodactylConfig

logger = logging.getLogger(__name__)

CLIENT_PATH = "/api/client"

# How often the background poller refreshes every linked server. Matched to the
# panel's own 20s cache: faster buys nothing, slower loses readings.
POLL_INTERVAL_SECONDS = 20.0

# Comfortably longer than POLL_INTERVAL_SECONDS, because the poller - not a
# browser request - is what refreshes this cache. The TTL is therefore not a
# refresh schedule but a staleness ceiling: if the poller stalls or a server was
# linked seconds ago, a request may serve a reading this old before fetching one
# itself. Setting it below the poll interval would put every request back on the
# upstream path in the gap between ticks.
RESOURCE_TTL_SECONDS = 35.0
# Limits and names change only when a server is reconfigured.
SERVER_TTL_SECONDS = 300.0
# Startup vars change when an operator edits them; short enough that the UI
# sees its own writes, long enough not to re-hit the panel on every render.
STARTUP_TTL_SECONDS = 90.0
# Don't re-attempt a rejected key on every poll of every linked server.
AUTH_COOLDOWN_SECONDS = 30.0
# Fallback when a 429 arrives without a usable Retry-After.
DEFAULT_RETRY_AFTER_SECONDS = 20.0

POWER_SIGNALS = ("start", "stop", "restart", "kill")

# Sandstorm egg keys used by "Set as default map". Other eggs simply lack them.
MAP_DEFAULT_ENV_KEYS = ("MAP_NAME", "SCENARIO")

# The panel returns HTML when the URL points at a web vhost rather than the API.
_HTML_MARKERS = ("<!doctype", "<html")


class PterodactylApiError(CommandError):
    """A panel call failed (HTTP status, error body, or transport problem)."""

    def __init__(self, message: str, *, code: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class PterodactylAuthError(PterodactylApiError):
    """The API key was missing, rejected, or barred by its allowed-IP list."""


class PterodactylTimeoutError(PterodactylApiError):
    """The panel did not answer in time."""


class PterodactylTlsError(PterodactylApiError):
    """Certificate verification failed."""


class PterodactylNotFoundError(PterodactylApiError):
    """No such server - or the key has no access to it. The panel conflates these."""


class PterodactylConflictError(PterodactylApiError):
    """The server is suspended, installing, transferring, or restoring."""


class PterodactylRateLimitError(PterodactylApiError):
    """The panel's 256/minute budget is exhausted."""


@dataclass(frozen=True)
class PanelServer:
    """A server as the panel describes it. Limits are in MiB, ``0`` = unlimited."""

    uuid: str
    identifier: str
    name: str
    node: str = ""
    # "" is the healthy value; the panel sends null. Non-empty means installing,
    # install_failed, suspended, or restoring_backup.
    status: str = ""
    is_suspended: bool = False
    memory_limit_mb: int = 0
    disk_limit_mb: int = 0
    # Percent of one host CPU, so 200 = two full cores.
    cpu_limit: int = 0


@dataclass(frozen=True)
class ServerResources:
    current_state: str = "offline"
    is_suspended: bool = False
    memory_bytes: int = 0
    # Host-relative: 100.0 is one full core.
    cpu_absolute: float = 0.0
    disk_bytes: int = 0
    # Cumulative for the container's current lifetime - these reset to 0 on
    # restart, so differencing them needs a negative-delta clamp.
    network_rx_bytes: int = 0
    network_tx_bytes: int = 0
    uptime_ms: int = 0


@dataclass(frozen=True)
class StartupVariable:
    """One egg variable as the panel's Startup tab shows it."""

    env_variable: str
    name: str = ""
    description: str = ""
    server_value: str = ""
    default_value: str = ""
    is_editable: bool = True
    rules: str = ""


@dataclass(frozen=True)
class StartupConfig:
    """Startup variables for a container, plus the rendered command when present."""

    variables: tuple[StartupVariable, ...] = ()
    startup_command: str = ""

    def env_keys(self) -> set[str]:
        return {v.env_variable for v in self.variables if v.env_variable}

    def has_map_defaults(self) -> bool:
        """True when the egg exposes both keys used by Sandstorm default-map."""
        keys = self.env_keys()
        return all(k in keys for k in MAP_DEFAULT_ENV_KEYS)

    def get(self, env_variable: str) -> StartupVariable | None:
        for var in self.variables:
            if var.env_variable == env_variable:
                return var
        return None


def percent_of(value: float, limit: float) -> float | None:
    """Percentage of a limit, or ``None`` when the limit means "unlimited".

    Pterodactyl encodes unlimited as ``0`` on memory, disk and CPU alike, so
    every one of them is a division by zero waiting to happen.
    """
    if not limit or limit <= 0:
        return None
    return round(value / limit * 100.0, 1)


def _as_dict(payload: Any) -> dict[str, Any]:
    return dict(payload) if isinstance(payload, Mapping) else {}


def _attrs(payload: Any) -> dict[str, Any]:
    return _as_dict(_as_dict(payload).get("attributes"))


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _server_from_attrs(attrs: Mapping[str, Any]) -> PanelServer:
    limits = _as_dict(attrs.get("limits"))
    return PanelServer(
        uuid=str(attrs.get("uuid") or "").strip(),
        identifier=str(attrs.get("identifier") or "").strip(),
        name=str(attrs.get("name") or "").strip(),
        node=str(attrs.get("node") or "").strip(),
        # null is healthy; anything else is a state worth showing.
        status=str(attrs.get("status") or "").strip(),
        is_suspended=bool(attrs.get("is_suspended")),
        memory_limit_mb=_int(limits.get("memory")),
        disk_limit_mb=_int(limits.get("disk")),
        cpu_limit=_int(limits.get("cpu")),
    )


def _resources_from_attrs(attrs: Mapping[str, Any]) -> ServerResources:
    res = _as_dict(attrs.get("resources"))
    return ServerResources(
        current_state=str(attrs.get("current_state") or "offline").strip(),
        is_suspended=bool(attrs.get("is_suspended")),
        memory_bytes=_int(res.get("memory_bytes")),
        cpu_absolute=_float(res.get("cpu_absolute")),
        disk_bytes=_int(res.get("disk_bytes")),
        network_rx_bytes=_int(res.get("network_rx_bytes")),
        network_tx_bytes=_int(res.get("network_tx_bytes")),
        uptime_ms=_int(res.get("uptime")),
    )


def _variable_from_attrs(attrs: Mapping[str, Any]) -> StartupVariable:
    return StartupVariable(
        env_variable=str(attrs.get("env_variable") or "").strip(),
        name=str(attrs.get("name") or "").strip(),
        description=str(attrs.get("description") or "").strip(),
        server_value=str(attrs.get("server_value") if attrs.get("server_value") is not None else ""),
        default_value=str(
            attrs.get("default_value") if attrs.get("default_value") is not None else ""
        ),
        is_editable=bool(attrs.get("is_editable", True)),
        rules=str(attrs.get("rules") or "").strip(),
    )


def _startup_from_payload(payload: Any) -> StartupConfig:
    body = _as_dict(payload)
    data = body.get("data")
    variables: list[StartupVariable] = []
    if isinstance(data, list):
        for item in data:
            var = _variable_from_attrs(_attrs(item))
            if var.env_variable:
                variables.append(var)
    meta = _as_dict(body.get("meta"))
    # The panel may send either key depending on version / transformer.
    command = str(
        meta.get("startup_command")
        or meta.get("raw_startup_command")
        or ""
    ).strip()
    return StartupConfig(variables=tuple(variables), startup_command=command)


def _error_detail(payload: Any, fallback: str) -> str:
    """Unwrap ``{"errors":[{code,status,detail}]}`` - ``detail`` is already prose."""
    errors = _as_dict(payload).get("errors")
    if isinstance(errors, list):
        details = [
            str(_as_dict(e).get("detail") or "").strip()
            for e in errors
            if str(_as_dict(e).get("detail") or "").strip()
        ]
        if details:
            return " ".join(details)
    return fallback


@dataclass
class _CacheEntry:
    expires_at: float
    value: Any
    # When the value was fetched, so a caller can report how old a reading is
    # rather than implying it is live.
    stored_at: float = 0.0


class PanelClient:
    """One authenticated conversation with a Pterodactyl panel.

    Not created directly in app code - use :func:`client_for` so the underlying
    connection pool and the response caches are shared across polls. Tests
    construct it directly with an injected ``transport``.
    """

    def __init__(
        self,
        config: PterodactylConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        auth_cooldown_seconds: float = AUTH_COOLDOWN_SECONDS,
        resource_ttl: float = RESOURCE_TTL_SECONDS,
        server_ttl: float = SERVER_TTL_SECONDS,
        startup_ttl: float = STARTUP_TTL_SECONDS,
    ) -> None:
        self.config = config
        self.auth_cooldown_seconds = auth_cooldown_seconds
        self.resource_ttl = resource_ttl
        self.server_ttl = server_ttl
        self.startup_ttl = startup_ttl

        self._lock = threading.RLock()
        self._auth_blocked_until = 0.0
        self._auth_error = ""
        self._rate_limited_until = 0.0
        self._cache: dict[str, _CacheEntry] = {}
        self.created_at = time.time()
        self.last_used = 0.0
        self.call_count = 0

        if transport is not None:
            self._http = httpx.Client(timeout=config.timeout, transport=transport)
        else:
            self._http = httpx.Client(
                timeout=config.timeout, verify=bool(config.verify_tls)
            )

        self._headers = {
            # The docs advertise a vendor media type, but the panel never checks
            # for it - Laravel's expectsJson() accepts this and it is friendlier
            # to proxies that rewrite unknown Accept values.
            "Accept": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        }

    # --- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:  # noqa: BLE001
            pass

    def invalidate_cache(self, uuid: str = "") -> None:
        """Drop cached reads - for one server, or all of them when uuid is empty."""
        with self._lock:
            if not uuid:
                self._cache.clear()
                return
            for key in [k for k in self._cache if k.endswith(f":{uuid}")]:
                self._cache.pop(key, None)

    def resource_age(self, uuid: str) -> float | None:
        """Seconds since the cached reading for ``uuid`` was fetched.

        ``None`` when nothing is cached. Lets a response say how old a reading
        is instead of implying it is live - which matters here, because the
        number may have been fetched by the poller rather than by this request.
        """
        with self._lock:
            entry = self._cache.get(f"resources:{uuid}")
            if entry is None or not entry.stored_at:
                return None
            return max(0.0, time.time() - entry.stored_at)

    def reset_cooldowns(self) -> None:
        """Clear auth / rate-limit backoff. For the explicit "Test" button only.

        An admin pressing Test after fixing something is asking us to try now,
        and reporting a stale cooldown message instead of the real result would
        make the button look broken.
        """
        with self._lock:
            self._auth_blocked_until = 0.0
            self._auth_error = ""
            self._rate_limited_until = 0.0

    # --- transport ---------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Call the panel. Returns the decoded body, or ``None`` for empty ones.

        The lock only covers cooldown checks and cache/auth state updates.
        httpx.Client is thread-safe for concurrent requests; holding the lock
        across network I/O would serialise every panel call in the process
        (poller ticks, cold-cache reads, power actions, inventory, Test).
        """
        with self._lock:
            self._check_cooldowns()
        response = self._send(method, path, json_body, params, timeout)
        with self._lock:
            result = self._parse(response, f"{method.upper()} {path}")
            self.last_used = time.time()
            self.call_count += 1
            return result

    def _check_cooldowns(self) -> None:
        now = time.time()
        if self._rate_limited_until > now:
            wait = int(self._rate_limited_until - now) + 1
            raise PterodactylRateLimitError(
                f"The panel is rate-limiting this API key. Retrying in ~{wait}s.",
                status=429,
            )
        if self._auth_blocked_until > now:
            wait = int(self._auth_blocked_until - now) + 1
            raise PterodactylAuthError(
                f"{self._auth_error or 'The panel rejected the API key'} "
                f"(not retrying for ~{wait}s).",
                status=401,
            )

    def _send(
        self,
        method: str,
        path: str,
        json_body: Mapping[str, Any] | None,
        params: Mapping[str, Any] | None,
        timeout: float | None,
    ) -> httpx.Response:
        url = self.config.url(path)
        kwargs: dict[str, Any] = {"headers": dict(self._headers)}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if params:
            kwargs["params"] = dict(params)
        if json_body is not None:
            # IsValidJson middleware keys off Content-Type; without it Laravel
            # never parses the body and the signal reads as missing.
            kwargs["json"] = dict(json_body)
            kwargs["headers"]["Content-Type"] = "application/json"

        try:
            return self._http.request(method.upper(), url, **kwargs)
        except httpx.TimeoutException as exc:
            effective = timeout if timeout is not None else self.config.timeout
            raise PterodactylTimeoutError(
                f"The panel did not answer within {effective:g}s "
                f"({method.upper()} {path})."
            ) from exc
        except httpx.ConnectError as exc:
            message = str(exc)
            lowered = message.lower()
            if "certificate" in lowered or "ssl" in lowered or "tls" in lowered:
                raise PterodactylTlsError(
                    f"TLS verification failed for {url}: {message}. If the panel "
                    f"uses a self-signed certificate, uncheck \"Verify the "
                    f"panel's TLS certificate\" under Settings → Pterodactyl."
                ) from exc
            raise PterodactylApiError(
                f"Could not reach {url}: {message}. Check the panel URL."
            ) from exc
        except httpx.HTTPError as exc:
            raise PterodactylApiError(f"Panel request failed: {exc}") from exc

    def _parse(self, response: httpx.Response, what: str) -> Any:
        status = response.status_code
        text = response.text or ""

        if status == 429:
            self._rate_limited_until = time.time() + _retry_after(response)
            raise PterodactylRateLimitError(
                "The panel is rate-limiting this API key (256 requests/minute).",
                status=429,
            )

        payload: Any = None
        if text.strip():
            try:
                payload = response.json()
            except ValueError:
                payload = None

        if status == 401:
            # Key-level failure: every other call with this key will fail too,
            # so stop trying for a while rather than once per linked server.
            self._auth_error = (
                "The panel rejected the API key. Create a Client API key under "
                "Account Settings -> API Credentials - an admin Application key "
                "will not work."
            )
            self._auth_blocked_until = time.time() + self.auth_cooldown_seconds
            # Our own sentence wins here, unlike everywhere else: the panel's
            # detail for a 401 is the bare word "Unauthenticated.", which tells
            # an admin nothing about the Client-vs-Application key trap that
            # causes most of these.
            raise PterodactylAuthError(self._auth_error, status=401)

        if status == 403:
            # Deliberately no cooldown: a 403 is usually a missing per-server
            # subuser permission, so other servers on the same key still work.
            raise PterodactylAuthError(
                _error_detail(
                    payload,
                    "The API key is valid but not permitted here. Check the "
                    "key's allowed-IP list and the account's permissions on "
                    "this server.",
                ),
                status=403,
            )

        if status == 404:
            raise PterodactylNotFoundError(
                _error_detail(
                    payload,
                    "The panel has no such server, or this API key cannot see it. "
                    "Pterodactyl reports both the same way.",
                ),
                status=404,
            )

        if status == 409:
            raise PterodactylConflictError(
                _error_detail(
                    payload,
                    "The server is unavailable right now (suspended, installing, "
                    "or transferring).",
                ),
                status=409,
            )

        if status >= 400:
            if _looks_like_html(text):
                raise PterodactylApiError(
                    f"{self.config.base_url} answered with a web page, not the "
                    f"Pterodactyl API. Enter the panel's base URL with no path.",
                    code="not_a_panel",
                    status=status,
                )
            raise PterodactylApiError(
                _error_detail(payload, f"{what} failed: HTTP {status}"), status=status
            )

        # A successful call clears any earlier cooldown.
        self._auth_blocked_until = 0.0
        self._auth_error = ""
        self._rate_limited_until = 0.0

        if status == 204 or not response.content:
            return None
        if payload is None:
            if _looks_like_html(text):
                raise PterodactylApiError(
                    f"{self.config.base_url} answered with a web page, not the "
                    f"Pterodactyl API. Enter the panel's base URL with no path.",
                    code="not_a_panel",
                    status=status,
                )
            raise PterodactylApiError(f"{what} returned a non-JSON response.")
        return payload

    # --- cache -------------------------------------------------------------

    def _cached(self, key: str, ttl: float, produce) -> Any:
        now = time.time()
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None and entry.expires_at > now:
                return entry.value
        value = produce()
        with self._lock:
            now2 = time.time()
            self._cache[key] = _CacheEntry(
                expires_at=now2 + ttl, value=value, stored_at=now2
            )
        return value

    # --- reads -------------------------------------------------------------

    def list_servers(self) -> list[PanelServer]:
        """Every server this key can see. Paginated at the panel's 100 maximum."""
        out: list[PanelServer] = []
        page = 1
        while True:
            payload = _as_dict(
                self.request(
                    "GET", f"{CLIENT_PATH}", params={"per_page": 100, "page": page}
                )
            )
            data = payload.get("data")
            if not isinstance(data, list):
                break
            for item in data:
                server = _server_from_attrs(_attrs(item))
                if server.uuid:
                    out.append(server)

            pagination = _as_dict(_as_dict(payload.get("meta")).get("pagination"))
            total_pages = _int(pagination.get("total_pages"), 1)
            if page >= max(total_pages, 1):
                break
            page += 1
            # A panel that reports a nonsensical page count must not spin here.
            if page > 50:
                logger.warning("Stopped paginating Pterodactyl servers at page 50")
                break
        return out

    def get_server(self, uuid: str, *, use_cache: bool = True) -> PanelServer:
        key = f"server:{uuid}"

        def produce() -> PanelServer:
            return _server_from_attrs(
                _attrs(self.request("GET", f"{CLIENT_PATH}/servers/{uuid}"))
            )

        if not use_cache:
            value = produce()
            with self._lock:
                stamp = time.time()
                self._cache[key] = _CacheEntry(
                    expires_at=stamp + self.server_ttl, value=value, stored_at=stamp
                )
            return value
        return self._cached(key, self.server_ttl, produce)

    def get_resources(
        self, uuid: str, *, use_cache: bool = True, timeout: float | None = None
    ) -> ServerResources:
        key = f"resources:{uuid}"

        def produce() -> ServerResources:
            return _resources_from_attrs(
                _attrs(
                    self.request(
                        "GET",
                        f"{CLIENT_PATH}/servers/{uuid}/resources",
                        timeout=timeout,
                    )
                )
            )

        if not use_cache:
            # Still populate the cache: a forced refresh should benefit the
            # pollers that follow it, not just the caller that paid for it.
            value = produce()
            with self._lock:
                stamp = time.time()
                self._cache[key] = _CacheEntry(
                    expires_at=stamp + self.resource_ttl, value=value, stored_at=stamp
                )
            return value
        return self._cached(key, self.resource_ttl, produce)

    def list_startup(self, uuid: str, *, use_cache: bool = True) -> StartupConfig:
        """Egg startup variables for a server (panel Startup tab).

        Not polled in the background: vars change rarely and only when someone
        edits them. A short cache still avoids re-fetching on every UI paint.
        """
        key = f"startup:{uuid}"

        def produce() -> StartupConfig:
            return _startup_from_payload(
                self.request("GET", f"{CLIENT_PATH}/servers/{uuid}/startup")
            )

        if not use_cache:
            value = produce()
            with self._lock:
                stamp = time.time()
                self._cache[key] = _CacheEntry(
                    expires_at=stamp + self.startup_ttl, value=value, stored_at=stamp
                )
            return value
        return self._cached(key, self.startup_ttl, produce)

    # --- writes ------------------------------------------------------------

    def update_startup_variable(
        self, uuid: str, key: str, value: str
    ) -> StartupVariable:
        """Update one egg variable. Invalidates the cached startup list."""
        env_key = (key or "").strip()
        if not env_key:
            raise PterodactylApiError("Startup variable key is required.")
        payload = self.request(
            "PUT",
            f"{CLIENT_PATH}/servers/{uuid}/startup/variable",
            json_body={"key": env_key, "value": value if value is not None else ""},
        )
        # Drop the cached list so the next read sees the new value. The
        # returned object is the updated variable itself.
        with self._lock:
            self._cache.pop(f"startup:{uuid}", None)
        return _variable_from_attrs(_attrs(payload))

    def send_power(self, uuid: str, signal: str) -> None:
        """Queue a power signal. Returns once the panel accepts it, not once it lands."""
        if signal not in POWER_SIGNALS:
            raise PterodactylApiError(f"Unknown power signal: {signal}")
        self.request(
            "POST", f"{CLIENT_PATH}/servers/{uuid}/power", json_body={"signal": signal}
        )
        # The state is about to change, so the cached reading is already wrong.
        # (The panel's own 20s cache still applies upstream.)
        self.invalidate_cache(uuid)


def _looks_like_html(text: str) -> bool:
    head = (text or "").lstrip()[:200].lower()
    return any(marker in head for marker in _HTML_MARKERS)


def _retry_after(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS
    return max(1.0, min(value, 300.0))


class PanelClientRegistry:
    """Process-wide client cache, keyed by the full credential set.

    Keying on the key itself means rotating it opens a fresh client, which also
    clears the auth cooldown the old key may have been sitting in.
    """

    def __init__(self) -> None:
        self._guard = threading.RLock()
        self._clients: dict[tuple[str, str, bool], PanelClient] = {}

    def client(self, config: PterodactylConfig) -> PanelClient:
        if not config.enabled:
            raise PterodactylApiError(
                "Pterodactyl is not configured. Add the panel URL and an API key "
                "under Settings -> Pterodactyl.",
                code="not_configured",
            )
        key = (config.base_url, config.api_key, bool(config.verify_tls))
        with self._guard:
            existing = self._clients.get(key)
            if existing is not None:
                return existing
            # Any other entry is a stale credential set - close it.
            stale = list(self._clients.values())
            self._clients.clear()
            client = PanelClient(config)
            self._clients[key] = client
        for old in stale:
            old.close()
        logger.info("Opened Pterodactyl panel session to %s", config.base_url)
        return client

    def invalidate_all(self) -> None:
        with self._guard:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            client.close()
        if clients:
            logger.info("Closed all Pterodactyl panel sessions (%s)", len(clients))

    def stats(self) -> list[dict[str, Any]]:
        with self._guard:
            items = list(self._clients.items())
        return [
            {
                "base_url": key[0],
                "verify_tls": key[2],
                "call_count": client.call_count,
                "created_at": client.created_at or None,
                "last_used": client.last_used or None,
            }
            for key, client in items
        ]


# Process-wide registry
panel_registry = PanelClientRegistry()


def client_for(config: PterodactylConfig) -> PanelClient:
    return panel_registry.client(config)


def describe_failure(config: PterodactylConfig) -> str:
    """Probe the panel and return why it is unusable, or "" when it works.

    Mirrors ``mailer.describe_failure``: the returned string is the message the
    admin reads, so each realistic misconfiguration gets its own sentence
    rather than a generic transport error.
    """
    if not config.base_url.strip():
        return "Enter the panel URL first."
    if not config.api_key.strip():
        return "Enter a Client API key first."

    try:
        client = client_for(config)
        client.reset_cooldowns()
        payload = _as_dict(
            client.request("GET", CLIENT_PATH, params={"per_page": 1}, timeout=10.0)
        )
    except PterodactylApiError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - the button must always explain itself
        logger.exception("Unexpected Pterodactyl test failure")
        return f"Unexpected error contacting the panel: {exc}"

    if "data" not in payload:
        return (
            f"{config.base_url} responded, but not like a Pterodactyl panel. "
            f"Enter the panel's base URL with no path."
        )
    return ""

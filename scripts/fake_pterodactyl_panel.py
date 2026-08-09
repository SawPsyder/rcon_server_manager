#!/usr/bin/env python3
"""A fake Pterodactyl panel that speaks the real Client API.

Standing up a real panel just to check this integration is a poor trade, so
this stands in for one. It implements the four endpoints we use, with the
response shapes taken from pterodactyl/panel v1.15.0's transformers, and data
that actually moves - CPU jitters, memory drifts, uptime climbs, network
counters accumulate - which is what the resource cards and the history chart
need in order to show anything.

    python scripts/fake_pterodactyl_panel.py --port 8099

Then: Settings -> Pterodactyl, panel URL http://localhost:8099, any key. Link a
server under Servers and open its detail page.

Two upstream behaviours are reproduced deliberately, because getting them wrong
is how this integration would look broken:

  * /resources is cached for 20 seconds, exactly as the panel caches it, so the
    lag the UI warns about is real here too.
  * power returns 204 immediately and applies the change on a timer -
    starting takes 5s, stopping 3s, kill is instant.

Failure modes worth exercising are behind flags:

    --reject-auth        every request answers 401 (wrong or Application key)
    --forbid             every request answers 403 (IP allowlist)
    --installing UUID    that server 409s on power, and on /resources
    --suspend UUID       that server 409s on power; /resources still answers,
                         which is the documented exemption
    --rate-limit N       answer 429 with Retry-After after N requests
    --not-a-panel        answer an HTML login page instead of JSON
    --wings-down         502 on /resources and /power, as when wings is dead
    --slow SECONDS       delay every response, to exercise the 504 path

Stdlib only; nothing here is imported by the app.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

CLIENT_PREFIX = "/api/client"

# The panel caches the resource payload for 20s. Reproduced on purpose.
RESOURCE_CACHE_SECONDS = 20.0

START_SECONDS = 5.0
STOP_SECONDS = 3.0

LOGIN_PAGE = (
    "<!DOCTYPE html><html><head><title>Pterodactyl</title></head>"
    "<body><div id='app'></div></body></html>"
)


class FakeServer:
    """One container, with numbers that move."""

    def __init__(
        self,
        uuid: str,
        identifier: str,
        name: str,
        node: str,
        *,
        memory_mb: int,
        disk_mb: int,
        cpu: int,
        state: str = "running",
        rng: random.Random | None = None,
    ) -> None:
        self.uuid = uuid
        self.identifier = identifier
        self.name = name
        self.node = node
        self.memory_mb = memory_mb
        self.disk_mb = disk_mb
        self.cpu = cpu
        self.state = state
        self.is_suspended = False
        self.status: str | None = None

        self._rng = rng or random.Random(7)
        self._lock = threading.RLock()
        self._started_at = time.time() if state == "running" else 0.0
        self._rx = 0
        self._tx = 0
        self._disk_bytes = int(disk_mb * 1024 * 1024 * 0.35) if disk_mb else 2 * 1024**30
        self._cached: tuple[float, dict[str, Any]] | None = None
        self._transition: threading.Timer | None = None

    # --- state machine ---------------------------------------------------

    def signal(self, sig: str) -> None:
        with self._lock:
            if self._transition is not None:
                self._transition.cancel()
                self._transition = None

            if sig == "kill":
                self._go_offline()
                return
            if sig == "start":
                if self.state == "running":
                    return  # idempotent, like the real thing
                self.state = "starting"
                self._schedule(START_SECONDS, self._go_running)
            elif sig == "stop":
                if self.state == "offline":
                    return
                self.state = "stopping"
                self._schedule(STOP_SECONDS, self._go_offline)
            elif sig == "restart":
                self.state = "stopping"
                self._schedule(STOP_SECONDS, self._restart_second_half)

    def _schedule(self, delay: float, fn) -> None:
        timer = threading.Timer(delay, fn)
        timer.daemon = True
        self._transition = timer
        timer.start()

    def _restart_second_half(self) -> None:
        with self._lock:
            self._go_offline()
            self.state = "starting"
            self._schedule(START_SECONDS, self._go_running)

    def _go_running(self) -> None:
        with self._lock:
            self.state = "running"
            self._started_at = time.time()
            self._transition = None

    def _go_offline(self) -> None:
        with self._lock:
            self.state = "offline"
            self._started_at = 0.0
            # Container counters are per-lifetime and reset on restart. Getting
            # this wrong upstream is what makes naive rate maths spike.
            self._rx = 0
            self._tx = 0
            self._transition = None

    # --- payloads --------------------------------------------------------

    def attributes(self) -> dict[str, Any]:
        return {
            "server_owner": True,
            "identifier": self.identifier,
            "uuid": self.uuid,
            "name": self.name,
            "node": self.node,
            "description": "",
            "status": self.status,
            "is_suspended": self.is_suspended,
            "is_installing": self.status == "installing",
            "is_transferring": False,
            "limits": {
                "memory": self.memory_mb,
                "swap": 0,
                "disk": self.disk_mb,
                "io": 500,
                "cpu": self.cpu,
                "threads": None,
                "oom_disabled": True,
            },
            "feature_limits": {"databases": 2, "allocations": 1, "backups": 10},
        }

    def resources(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            if self._cached and now - self._cached[0] < RESOURCE_CACHE_SECONDS:
                return self._cached[1]

            if self.state == "running":
                uptime = int((now - self._started_at) * 1000)
                # A slow sine plus jitter, so the chart has a shape.
                base = self.cpu * 0.35 if self.cpu else 60.0
                cpu = max(
                    0.0,
                    base
                    + base * 0.4 * math.sin(now / 90.0)
                    + self._rng.uniform(-base * 0.1, base * 0.1),
                )
                limit_bytes = self.memory_mb * 1024 * 1024 if self.memory_mb else 4 * 1024**3
                memory = int(
                    limit_bytes * (0.45 + 0.15 * math.sin(now / 140.0))
                    + self._rng.uniform(-2_000_000, 2_000_000)
                )
                self._rx += self._rng.randint(20_000, 200_000)
                self._tx += self._rng.randint(40_000, 400_000)
            else:
                uptime, cpu, memory = 0, 0.0, 0

            payload = {
                "object": "stats",
                "attributes": {
                    "current_state": self.state,
                    "is_suspended": self.is_suspended,
                    "resources": {
                        "memory_bytes": max(0, memory),
                        "cpu_absolute": round(cpu, 2),
                        "disk_bytes": self._disk_bytes,
                        "network_rx_bytes": self._rx,
                        "network_tx_bytes": self._tx,
                        "uptime": uptime,
                    },
                },
            }
            self._cached = (now, payload)
            return payload


def build_servers(rng: random.Random) -> list[FakeServer]:
    return [
        FakeServer(
            "d3aac109-e5e0-4331-b03e-3454f7e136dc",
            "d3aac109",
            "Sandstorm #1",
            "node-01",
            memory_mb=4096,
            disk_mb=20480,
            cpu=200,
            rng=rng,
        ),
        FakeServer(
            "7f2b1c4e-9a83-4d21-8e55-0b6d3f9a1c22",
            "7f2b1c4e",
            "Palworld - Friends",
            "node-01",
            memory_mb=16384,
            disk_mb=51200,
            cpu=400,
            state="offline",
            rng=rng,
        ),
        FakeServer(
            "1e9d5a70-3c48-4b6f-9d21-5a7c8e0f4b13",
            "1e9d5a70",
            "Satisfactory - Unlimited",
            "node-02",
            # 0 is how the panel encodes "unlimited" - the division-by-zero case
            # every percentage has to survive.
            memory_mb=0,
            disk_mb=0,
            cpu=0,
            rng=rng,
        ),
    ]


class Handler(BaseHTTPRequestHandler):
    server_version = "nginx"
    sys_version = ""

    # --- helpers ---------------------------------------------------------

    @property
    def opts(self) -> argparse.Namespace:
        return self.server.opts  # type: ignore[attr-defined]

    @property
    def servers(self) -> dict[str, FakeServer]:
        return self.server.servers  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.opts.verbose:
            super().log_message(fmt, *args)

    def _send(self, status: int, payload: Any = None, *, headers: dict | None = None) -> None:
        if self.opts.slow:
            time.sleep(self.opts.slow)
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_html(self) -> None:
        body = LOGIN_PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, code: str, detail: str, headers: dict | None = None) -> None:
        self._send(
            status,
            {"errors": [{"code": code, "status": str(status), "detail": detail}]},
            headers=headers,
        )

    def _gate(self) -> bool:
        """Global failure modes. True when the request should stop here."""
        if self.opts.not_a_panel:
            self._send_html()
            return True

        if self.opts.rate_limit:
            self.server.request_count += 1  # type: ignore[attr-defined]
            if self.server.request_count > self.opts.rate_limit:  # type: ignore[attr-defined]
                self._error(
                    429,
                    "TooManyRequestsHttpException",
                    "Too many requests.",
                    headers={"Retry-After": "30", "X-RateLimit-Remaining": "0"},
                )
                return True

        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or self.opts.reject_auth:
            self._error(401, "InvalidCredentials", "Unauthenticated.")
            return True
        if self.opts.forbid:
            self._error(
                403,
                "AccessDeniedHttpException",
                "This IP address does not have permission to use this API key.",
            )
            return True
        return False

    def _lookup(self, uuid: str) -> FakeServer | None:
        # The real route binding accepts the full uuid or the 8-char short id.
        if uuid in self.servers:
            return self.servers[uuid]
        for server in self.servers.values():
            if server.identifier == uuid:
                return server
        return None

    # --- routes ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if self._gate():
            return
        path = self.path.split("?", 1)[0].rstrip("/") or "/"

        if path == CLIENT_PREFIX:
            data = [{"object": "server", "attributes": s.attributes()} for s in self.servers.values()]
            self._send(
                200,
                {
                    "object": "list",
                    "data": data,
                    "meta": {
                        "pagination": {
                            "total": len(data),
                            "count": len(data),
                            "per_page": 100,
                            "current_page": 1,
                            "total_pages": 1,
                            "links": {},
                        }
                    },
                },
            )
            return

        if path.startswith(f"{CLIENT_PREFIX}/servers/"):
            rest = path[len(f"{CLIENT_PREFIX}/servers/") :]
            parts = rest.split("/")
            server = self._lookup(parts[0])
            if server is None:
                self._error(404, "NotFoundHttpException", "")
                return

            if len(parts) == 1:
                self._send(
                    200,
                    {
                        "object": "server",
                        "attributes": server.attributes(),
                        "meta": {
                            "is_server_owner": True,
                            "user_permissions": ["control.console", "control.start"],
                        },
                    },
                )
                return

            if parts[1] == "resources":
                if self.opts.wings_down:
                    self._error(502, "DaemonConnectionException", "Wings is unreachable.")
                    return
                # Suspension is exempt here; installing is not.
                if server.status == "installing":
                    self._error(
                        409,
                        "ServerStateConflictException",
                        "This server is installing and the functionality requested is unavailable.",
                    )
                    return
                self._send(200, server.resources())
                return

        self._error(404, "NotFoundHttpException", "")

    def do_POST(self) -> None:  # noqa: N802
        if self._gate():
            return
        path = self.path.split("?", 1)[0].rstrip("/")
        if not path.endswith("/power"):
            self._error(404, "NotFoundHttpException", "")
            return

        rest = path[len(f"{CLIENT_PREFIX}/servers/") :].rsplit("/", 1)[0]
        server = self._lookup(rest)
        if server is None:
            self._error(404, "NotFoundHttpException", "")
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._error(400, "BadRequestHttpException", "Malformed JSON body.")
            return

        signal = body.get("signal")
        if signal not in ("start", "stop", "restart", "kill"):
            self._error(422, "ValidationException", "The selected signal is invalid.")
            return

        if self.opts.wings_down:
            self._error(502, "DaemonConnectionException", "Wings is unreachable.")
            return
        if server.is_suspended:
            self._error(
                409,
                "ServerStateConflictException",
                "This server is currently suspended and the functionality requested is unavailable.",
            )
            return
        if server.status == "installing":
            self._error(
                409,
                "ServerStateConflictException",
                "This server is installing and the functionality requested is unavailable.",
            )
            return

        server.signal(signal)
        # 204 the moment wings accepts it - the change lands later.
        self._send(204)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--reject-auth", action="store_true", help="always answer 401")
    parser.add_argument("--forbid", action="store_true", help="always answer 403")
    parser.add_argument("--installing", metavar="UUID", help="mark a server as installing")
    parser.add_argument("--suspend", metavar="UUID", help="suspend a server")
    parser.add_argument(
        "--rate-limit", type=int, metavar="N", help="429 with Retry-After after N requests"
    )
    parser.add_argument(
        "--not-a-panel", action="store_true", help="serve an HTML login page instead of JSON"
    )
    parser.add_argument("--wings-down", action="store_true", help="502 on resources and power")
    parser.add_argument("--slow", type=float, default=0.0, metavar="SECONDS")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every request")
    opts = parser.parse_args()

    rng = random.Random(opts.seed)
    servers = {s.uuid: s for s in build_servers(rng)}

    def match(needle: str) -> FakeServer | None:
        for server in servers.values():
            if needle in (server.uuid, server.identifier):
                return server
        return None

    if opts.installing:
        found = match(opts.installing)
        if found:
            found.status = "installing"
    if opts.suspend:
        found = match(opts.suspend)
        if found:
            found.is_suspended = True
            found.status = "suspended"

    httpd = ThreadingHTTPServer((opts.host, opts.port), Handler)
    httpd.opts = opts  # type: ignore[attr-defined]
    httpd.servers = servers  # type: ignore[attr-defined]
    httpd.request_count = 0  # type: ignore[attr-defined]

    print(f"Fake Pterodactyl panel on http://{opts.host}:{opts.port}")
    print("Panel URL for the Settings tab: " f"http://{opts.host}:{opts.port}")
    print("API key: anything non-empty (the prefix is deliberately not checked)")
    for server in servers.values():
        print(f"  {server.identifier}  {server.name:<28} {server.state:<9} {server.uuid}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()

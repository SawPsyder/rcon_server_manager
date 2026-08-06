"""Persistent Source RCON connections (one socket per endpoint).

Insurgency: Sandstorm leaks a server-side thread for every RCON TCP session
and never reaps it. Opening a new connection per command (status polls, kicks,
console) exhausts the process thread limit and RCON starts timing out.

This pool keeps a single authenticated socket per (host, port, password) and
serializes commands on that socket. Dead connections are dropped and rebuilt
only when a previously-live session fails mid-command.

Failed connect/auth attempts use a cooldown so we do not spam new TCP sessions
and accelerate the server-side thread leak.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from app.services.rcon import RconAuthError, RconError, RconTimeoutError, SourceRcon

logger = logging.getLogger(__name__)

# Key: host, port, password (password so a change forces a new session)
EndpointKey = tuple[str, int, str]

# After a failed open/auth, wait before trying another TCP connect
DEFAULT_CONNECT_COOLDOWN_SECONDS = 45.0


@dataclass
class _Session:
    lock: threading.RLock = field(default_factory=threading.RLock)
    client: SourceRcon | None = None
    created_at: float = 0.0
    last_used: float = 0.0
    command_count: int = 0
    # Don't open new sockets in a tight loop when the game server is broken
    connect_blocked_until: float = 0.0
    last_error: str | None = None


class RconConnectionPool:
    def __init__(self, connect_cooldown_seconds: float = DEFAULT_CONNECT_COOLDOWN_SECONDS) -> None:
        self._guard = threading.RLock()
        self._sessions: dict[EndpointKey, _Session] = {}
        self.connect_cooldown_seconds = connect_cooldown_seconds

    def _key(self, host: str, port: int, password: str) -> EndpointKey:
        return (host.strip(), int(port), password)

    def execute(
        self,
        host: str,
        port: int,
        password: str,
        command: str,
        timeout: float = 5.0,
    ) -> str:
        """
        Run a command on a persistent connection.

        - Reuses an open authenticated socket when available.
        - If the live session dies mid-command, reconnect **once** and retry.
        - If connect/auth itself fails, apply a cooldown (no immediate reconnect
          storm that would leak more Sandstorm threads).
        """
        if not password:
            raise RconError("No RCON password")

        key = self._key(host, port, password)
        session = self._get_or_create_session(key)

        with session.lock:
            # Fast path: already connected
            if session.client is not None and session.client.is_connected:
                try:
                    return self._run_on_client(session, command, timeout)
                except (RconError, OSError, TimeoutError) as exc:
                    logger.info(
                        "Persistent RCON session died on %s:%s during %r: %s — reconnecting once",
                        host,
                        port,
                        command,
                        exc,
                    )
                    self._discard_client(session)
                    # fall through to open a new session once

            # Need a (re)connect — respect cooldown after prior open failures
            now = time.time()
            if session.connect_blocked_until > now and session.client is None:
                wait = int(session.connect_blocked_until - now)
                raise RconTimeoutError(
                    f"RCON to {host}:{port} in cooldown after failed connect "
                    f"({session.last_error or 'error'}; retry in ~{wait}s). "
                    f"If Sandstorm RCON is exhausted, restart the game server."
                )

            try:
                client = self._open_client(session, host, port, password, timeout)
            except (RconError, OSError, TimeoutError) as exc:
                session.last_error = str(exc)
                session.connect_blocked_until = time.time() + self.connect_cooldown_seconds
                logger.warning(
                    "RCON connect/auth failed for %s:%s — cooldown %.0fs: %s",
                    host,
                    port,
                    self.connect_cooldown_seconds,
                    exc,
                )
                raise

            try:
                return self._run_on_client(session, command, timeout)
            except (RconError, OSError, TimeoutError) as exc:
                # Brand-new connection failed on first command — cooldown
                self._discard_client(session)
                session.last_error = str(exc)
                session.connect_blocked_until = time.time() + self.connect_cooldown_seconds
                raise

    def _run_on_client(self, session: _Session, command: str, timeout: float) -> str:
        client = session.client
        assert client is not None
        client.timeout = timeout
        client.set_socket_timeout(timeout)
        result = client.command(command)
        session.last_used = time.time()
        session.command_count += 1
        session.last_error = None
        # Successful use clears cooldown
        session.connect_blocked_until = 0.0
        return result

    def _get_or_create_session(self, key: EndpointKey) -> _Session:
        with self._guard:
            session = self._sessions.get(key)
            if session is None:
                session = _Session()
                self._sessions[key] = session
            return session

    def _open_client(
        self,
        session: _Session,
        host: str,
        port: int,
        password: str,
        timeout: float,
    ) -> SourceRcon:
        self._discard_client(session)
        client = SourceRcon(host, port, password, timeout=timeout)
        client.connect()
        session.client = client
        session.created_at = time.time()
        session.last_used = session.created_at
        session.command_count = 0
        session.connect_blocked_until = 0.0
        session.last_error = None
        logger.info("Opened persistent RCON session to %s:%s", host, port)
        return client

    def _discard_client(self, session: _Session) -> None:
        client = session.client
        session.client = None
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
            logger.info("Closed RCON session to %s:%s", client.host, client.port)

    def invalidate_endpoint(self, host: str, port: int) -> None:
        """Drop any sessions for host:port (password change, server edit/delete)."""
        host = host.strip()
        port = int(port)
        with self._guard:
            keys = [k for k in self._sessions if k[0] == host and k[1] == port]
            sessions = [(k, self._sessions.pop(k)) for k in keys]
        for key, session in sessions:
            with session.lock:
                self._discard_client(session)
                session.connect_blocked_until = 0.0
            logger.info("Invalidated RCON pool entry %s:%s", key[0], key[1])

    def invalidate_all(self) -> None:
        with self._guard:
            items = list(self._sessions.items())
            self._sessions.clear()
        for _key, session in items:
            with session.lock:
                self._discard_client(session)
        logger.info("Closed all persistent RCON sessions (%s)", len(items))

    def stats(self) -> list[dict]:
        now = time.time()
        with self._guard:
            out = []
            for (host, port, _pw), session in self._sessions.items():
                out.append(
                    {
                        "host": host,
                        "port": port,
                        "connected": bool(session.client and session.client.is_connected),
                        "command_count": session.command_count,
                        "created_at": session.created_at or None,
                        "last_used": session.last_used or None,
                        "cooldown_remaining_s": max(
                            0, int(session.connect_blocked_until - now)
                        ),
                        "last_error": session.last_error,
                    }
                )
            return out


# Process-wide pool
rcon_pool = RconConnectionPool()

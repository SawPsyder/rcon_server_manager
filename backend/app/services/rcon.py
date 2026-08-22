"""Generic Source RCON transport client + persistent pool entry points.

Insurgency: Sandstorm (and many Source forks) answer with a single
SERVERDATA_RESPONSE_VALUE and do **not** implement Valve's multi-packet
empty-packet terminator reliably. Prefer single-response reads (ISRT-style).

Prefer :func:`run_rcon` which reuses a long-lived connection - Sandstorm leaks
a thread per TCP session and never reaps it.
"""

from __future__ import annotations

import socket
import struct
from enum import IntEnum
from typing import Iterable

from app.services.errors import CommandError


class PacketType(IntEnum):
    SERVERDATA_RESPONSE_VALUE = 0
    SERVERDATA_EXECCOMMAND = 2
    SERVERDATA_AUTH_RESPONSE = 2
    SERVERDATA_AUTH = 3


class RconError(CommandError):
    pass


class RconAuthError(RconError):
    pass


class RconTimeoutError(RconError):
    pass


def is_command_allowed(command: str, allowed_prefixes: Iterable[str]) -> bool:
    raw = command or ""
    # Check the unstripped string: strip() would hide a leading "\nquit".
    if "\n" in raw or "\r" in raw:
        return False
    cmd = raw.strip().lower()
    if not cmd:
        return False
    first = cmd.split()[0]
    for prefix in allowed_prefixes:
        p = prefix.lower()
        # Trailing-dot entries are families (Satisfactory `fg.` / `server.`).
        # Everything else is an exact verb so `ban` does not match `banhammer`.
        if p.endswith("."):
            if first.startswith(p):
                return True
        elif first == p:
            return True
    return False


class SourceRcon:
    """Low-level Source RCON client. Prefer the process-wide pool for app use."""

    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._req_id = 0

    def __enter__(self) -> SourceRcon:
        self.connect()
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN001
        self.close()

    @property
    def is_connected(self) -> bool:
        return self._sock is not None

    def set_socket_timeout(self, timeout: float) -> None:
        self.timeout = timeout
        if self._sock is not None:
            try:
                self._sock.settimeout(timeout)
            except OSError:
                pass

    def connect(self) -> None:
        self.close()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        # Keepalive helps detect dead peers without extra RCON commands
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        try:
            sock.connect((self.host, self.port))
        except TimeoutError as exc:
            sock.close()
            raise RconTimeoutError(
                f"RCON TCP connect timed out to {self.host}:{self.port} "
                f"(port filtered or host unreachable?)"
            ) from exc
        except OSError as exc:
            sock.close()
            raise RconError(f"Could not connect to {self.host}:{self.port}: {exc}") from exc
        self._sock = sock
        self._req_id = 0
        self._authenticate()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _next_id(self) -> int:
        self._req_id += 1
        if self._req_id > 0x0FFFFFFF:
            self._req_id = 1
        return self._req_id

    def _send_packet(self, req_id: int, ptype: int, body: str) -> None:
        if self._sock is None:
            raise RconError("Not connected")
        body_bytes = body.encode("utf-8") + b"\x00\x00"
        payload = struct.pack("<ii", req_id, ptype) + body_bytes
        packet = struct.pack("<i", len(payload)) + payload
        try:
            self._sock.sendall(packet)
        except OSError as exc:
            self.close()
            raise RconError(f"RCON send failed: {exc}") from exc

    def _recv_exact(self, length: int) -> bytes:
        if self._sock is None:
            raise RconError("Not connected")
        buf = b""
        while len(buf) < length:
            try:
                chunk = self._sock.recv(length - len(buf))
            except TimeoutError as exc:
                raise RconTimeoutError(
                    f"RCON timed out waiting for data from {self.host}:{self.port}"
                ) from exc
            except OSError as exc:
                self.close()
                raise RconError(f"RCON recv failed: {exc}") from exc
            if not chunk:
                self.close()
                raise RconError("Connection closed by remote host")
            buf += chunk
        return buf

    def _recv_packet(self) -> tuple[int, int, str]:
        size_raw = self._recv_exact(4)
        size = struct.unpack("<i", size_raw)[0]
        if size < 10 or size > 1_000_000:
            self.close()
            raise RconError(f"Invalid RCON packet size: {size}")
        data = self._recv_exact(size)
        req_id, ptype = struct.unpack("<ii", data[:8])
        if len(data) > 10:
            body = data[8:-2].decode("utf-8", errors="replace")
        else:
            body = data[8:].split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        return req_id, ptype, body

    def _authenticate(self) -> None:
        auth_id = self._next_id()
        try:
            self._send_packet(auth_id, PacketType.SERVERDATA_AUTH, self.password)
        except RconError:
            raise
        except OSError as exc:
            self.close()
            raise RconError(f"RCON send failed during auth: {exc}") from exc

        try:
            for _ in range(4):
                req_id, ptype, _body = self._recv_packet()
                if req_id == -1:
                    self.close()
                    raise RconAuthError(
                        "RCON authentication failed (wrong password or RCON disabled)"
                    )
                if req_id == auth_id:
                    return
                if ptype == PacketType.SERVERDATA_RESPONSE_VALUE and not _body:
                    continue
                if req_id == auth_id or ptype == PacketType.SERVERDATA_AUTH_RESPONSE:
                    return
        except RconTimeoutError as exc:
            self.close()
            raise RconTimeoutError(
                f"RCON auth timed out on {self.host}:{self.port} - TCP connects but the "
                f"server never replies (wrong port, RCON not enabled, IP not allowed, "
                f"or Sandstorm RCON thread exhaustion - restart game server)"
            ) from exc

    def command(self, command: str) -> str:
        """
        Execute a command on this open connection (does not close afterward).

        ISRT-style: one EXECCOMMAND → read packets until idle.
        No Valve multi-packet terminator (Sandstorm often never answers it).
        """
        if self._sock is None:
            self.connect()
        cmd_id = self._next_id()
        self._send_packet(cmd_id, PacketType.SERVERDATA_EXECCOMMAND, command)

        parts: list[str] = []
        try:
            _req_id, _ptype, body = self._recv_packet()
            if body:
                parts.append(body)
            assert self._sock is not None
            old_timeout = self._sock.gettimeout()
            try:
                self._sock.settimeout(min(0.35, max(0.15, self.timeout * 0.1)))
                while True:
                    try:
                        req_id, _ptype, body = self._recv_packet()
                    except (TimeoutError, RconTimeoutError, OSError, RconError):
                        break
                    if body:
                        parts.append(body)
                    if req_id != cmd_id and not body:
                        break
            finally:
                if self._sock is not None:
                    self._sock.settimeout(old_timeout)
        except RconTimeoutError as exc:
            # Dead or stuck session - force reconnect next time
            self.close()
            raise RconTimeoutError(
                f"RCON command timed out ({command!r}) on {self.host}:{self.port}"
            ) from exc
        except RconError:
            self.close()
            raise

        return "".join(parts)


def run_rcon(
    host: str,
    port: int,
    password: str,
    command: str,
    timeout: float = 5.0,
    allowed_prefixes: Iterable[str] | None = None,
    *,
    persistent: bool = True,
) -> str:
    """
    Execute RCON command.

    By default uses the process-wide persistent connection pool (one TCP
    session per host:port:password) to avoid Sandstorm's per-connect thread leak.
    """
    if "\n" in command or "\r" in command:
        raise RconError("Command must be a single line")
    if allowed_prefixes is not None and not is_command_allowed(command, allowed_prefixes):
        raise RconError(
            "Command not allowed. Allowed prefixes: " + ", ".join(allowed_prefixes)
        )
    if persistent:
        # Late import avoids circular import at module load
        from app.services.rcon_pool import rcon_pool

        return rcon_pool.execute(host, port, password, command, timeout=timeout)

    with SourceRcon(host, port, password, timeout=timeout) as client:
        return client.command(command)

"""Source Query (A2S) client (Source engine protocol)."""

from __future__ import annotations

import socket
import struct
import time
from typing import Any


A2S_INFO = b"\xFF\xFF\xFF\xFFTSource Engine Query\x00"
A2S_PLAYER = b"\xFF\xFF\xFF\xFF\x55"
A2S_RULES = b"\xFF\xFF\xFF\xFF\x56"


class QueryError(Exception):
    pass


class SourceQuery:
    def __init__(self, host: str, port: int, timeout: float = 2.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._challenge: bytes | None = None

    def __enter__(self) -> SourceQuery:
        self.connect()
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN001
        self.close()

    def connect(self) -> None:
        self.close()
        try:
            addr = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_DGRAM)[0]
        except OSError as exc:
            raise QueryError(f"Could not resolve {self.host}: {exc}") from exc
        sock = socket.socket(addr[0], socket.SOCK_DGRAM)
        sock.settimeout(self.timeout)
        sock.connect(addr[4])
        self._sock = sock

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _send(self, data: bytes) -> None:
        if self._sock is None:
            self.connect()
        assert self._sock is not None
        self._sock.send(data)

    def _recv(self, size: int = 65535) -> bytes:
        assert self._sock is not None
        try:
            return self._sock.recv(size)
        except TimeoutError as exc:
            raise QueryError("Query timed out") from exc
        except OSError as exc:
            raise QueryError(f"Query socket error: {exc}") from exc

    @staticmethod
    def _get_byte(data: bytes) -> tuple[int, bytes]:
        return data[0], data[1:]

    @staticmethod
    def _get_short(data: bytes) -> tuple[int, bytes]:
        return struct.unpack("<h", data[:2])[0], data[2:]

    @staticmethod
    def _get_long(data: bytes) -> tuple[int, bytes]:
        return struct.unpack("<i", data[:4])[0], data[4:]

    @staticmethod
    def _get_float(data: bytes) -> tuple[float, bytes]:
        return struct.unpack("<f", data[:4])[0], data[4:]

    @staticmethod
    def _get_long_long(data: bytes) -> tuple[int, bytes]:
        return struct.unpack("<Q", data[:8])[0], data[8:]

    @staticmethod
    def _get_string(data: bytes) -> tuple[str, bytes]:
        end = data.find(b"\x00")
        if end < 0:
            return data.decode("utf-8", errors="replace"), b""
        return data[:end].decode("utf-8", errors="replace"), data[end + 1 :]

    def get_info(self) -> dict[str, Any]:
        self._send(A2S_INFO)
        before = time.time()
        data = self._recv()
        after = time.time()

        # Challenge response (modern A2S): 0x41
        if len(data) >= 5 and data[4] == 0x41:
            challenge = data[5:9]
            self._send(A2S_INFO + challenge)
            before = time.time()
            data = self._recv()
            after = time.time()

        if len(data) < 5:
            raise QueryError("Empty A2S_INFO response")

        data = data[4:]
        header, data = self._get_byte(data)
        result: dict[str, Any] = {"Ping": int((after - before) * 1000)}

        if header == 0x49:  # Source
            result["Protocol"], data = self._get_byte(data)
            result["Hostname"], data = self._get_string(data)
            result["Map"], data = self._get_string(data)
            result["GameDir"], data = self._get_string(data)
            result["GameDesc"], data = self._get_string(data)
            result["AppID"], data = self._get_short(data)
            result["Players"], data = self._get_byte(data)
            result["MaxPlayers"], data = self._get_byte(data)
            result["Bots"], data = self._get_byte(data)
            dedicated, data = self._get_byte(data)
            result["Dedicated"] = {ord("d"): "Dedicated", ord("l"): "Listen"}.get(dedicated, "SourceTV")
            os_b, data = self._get_byte(data)
            result["OS"] = {ord("w"): "Windows", ord("m"): "Mac", ord("o"): "Mac"}.get(os_b, "Linux")
            result["Password"], data = self._get_byte(data)
            result["Secure"], data = self._get_byte(data)
            result["Version"], data = self._get_string(data)
            if data:
                edf, data = self._get_byte(data)
                try:
                    if edf & 0x80:
                        result["GamePort"], data = self._get_short(data)
                    if edf & 0x10:
                        result["SteamID"], data = self._get_long_long(data)
                    if edf & 0x40:
                        result["SpecPort"], data = self._get_short(data)
                        result["SpecName"], data = self._get_string(data)
                    if edf & 0x20:
                        result["Tags"], data = self._get_string(data)
                    if edf & 0x01:
                        result["GameID"], data = self._get_long_long(data)
                except Exception:
                    pass
        else:
            raise QueryError(f"Unsupported A2S_INFO header: {header:#x}")

        return result

    def _get_challenge(self) -> bytes:
        self._send(A2S_PLAYER + b"\xFF\xFF\xFF\xFF")
        data = self._recv()
        if len(data) < 9:
            raise QueryError("Invalid challenge response")
        # S2C_CHALLENGE = 0x41
        self._challenge = data[5:9]
        return self._challenge

    def get_players(self) -> list[dict[str, Any]]:
        challenge = self._challenge or self._get_challenge()
        self._send(A2S_PLAYER + challenge)
        data = self._recv()
        # Sometimes server replies with a new challenge
        if len(data) >= 5 and data[4] == 0x41:
            challenge = data[5:9]
            self._challenge = challenge
            self._send(A2S_PLAYER + challenge)
            data = self._recv()

        data = data[4:]
        _header, data = self._get_byte(data)
        num, data = self._get_byte(data)
        players: list[dict[str, Any]] = []
        for i in range(num):
            try:
                _idx, data = self._get_byte(data)
                name, data = self._get_string(data)
                score, data = self._get_long(data)
                duration, data = self._get_float(data)
                pretty = time.strftime("%H:%M:%S", time.gmtime(int(duration)))
                players.append(
                    {
                        "id": i + 1,
                        "name": name,
                        "score": score,
                        "duration": duration,
                        "duration_pretty": pretty,
                    }
                )
            except Exception:
                break
        return players

    def get_rules(self) -> dict[str, str]:
        challenge = self._challenge or self._get_challenge()
        self._send(A2S_RULES + challenge)
        data = self._recv()
        if len(data) >= 5 and data[4] == 0x41:
            challenge = data[5:9]
            self._challenge = challenge
            self._send(A2S_RULES + challenge)
            data = self._recv()

        # Multi-packet split header 0xFE
        if data and data[0] == 0xFE:
            # Best-effort single buffer; Sandstorm often fits one packet
            data = data[9:] if len(data) > 9 else data
        else:
            data = data[4:]

        try:
            _header, data = self._get_byte(data)
            _num, data = self._get_short(data)
        except Exception as exc:
            raise QueryError("Could not parse rules header") from exc

        rules: dict[str, str] = {}
        while data:
            try:
                name, data = self._get_string(data)
                value, data = self._get_string(data)
                if name:
                    rules[name] = value
            except Exception:
                break
        return rules


def query_server_status(host: str, port: int, timeout: float = 2.0) -> dict[str, Any]:
    """Return normalized status dict for API layer."""
    try:
        with SourceQuery(host, port, timeout=timeout) as q:
            info = q.get_info()
            rules: dict[str, str] = {}
            players: list[dict[str, Any]] = []
            try:
                rules = q.get_rules()
            except QueryError:
                rules = {}
            try:
                players = q.get_players()
            except QueryError:
                players = []

        lighting = None
        day_b = rules.get("Day_b")
        if day_b is not None:
            lighting = "Day" if str(day_b).lower() == "true" else "Night"

        ranked = rules.get("RankedServer_b")
        coop = rules.get("Coop_b")

        return {
            "online": True,
            "host": host,
            "query_port": port,
            "hostname": info.get("Hostname"),
            "map": info.get("Map"),
            "lighting": lighting,
            "gamemode": rules.get("GameMode_s"),
            "coop_or_versus": (
                "Coop" if str(coop).lower() == "true" else "Versus" if coop is not None else None
            ),
            "players": info.get("Players"),
            "max_players": info.get("MaxPlayers"),
            "bots": info.get("Bots"),
            "ping_ms": info.get("Ping"),
            "password_protected": bool(info.get("Password")),
            "vac": bool(info.get("Secure")),
            "ranked": str(ranked).lower() == "true" if ranked is not None else None,
            "game_port": info.get("GamePort"),
            "version": info.get("Version"),
            "player_list": players,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "online": False,
            "host": host,
            "query_port": port,
            "player_list": [],
            "error": str(exc),
        }

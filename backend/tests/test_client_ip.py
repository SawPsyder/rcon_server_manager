"""CLIENT_IP_HEADER selects how client_ip() resolves the caller address."""

from starlette.requests import Request

from app.config import get_settings
from app.deps import client_ip


def _request(headers: list[tuple[bytes, bytes]], peer: str = "10.0.0.1") -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers,
        "client": (peer, 54321),
        "server": ("127.0.0.1", 80),
    }
    return Request(scope)


def test_empty_header_uses_socket_peer(monkeypatch):
    monkeypatch.delenv("CLIENT_IP_HEADER", raising=False)
    get_settings.cache_clear()
    try:
        req = _request([(b"cf-connecting-ip", b"203.0.113.9")])
        assert client_ip(req) == "10.0.0.1"
    finally:
        get_settings.cache_clear()


def test_configured_header_is_used(monkeypatch):
    monkeypatch.setenv("CLIENT_IP_HEADER", "CF-Connecting-IP")
    get_settings.cache_clear()
    try:
        req = _request([(b"cf-connecting-ip", b"203.0.113.9")])
        assert client_ip(req) == "203.0.113.9"
    finally:
        get_settings.cache_clear()


def test_xff_takes_leftmost_hop(monkeypatch):
    monkeypatch.setenv("CLIENT_IP_HEADER", "X-Forwarded-For")
    get_settings.cache_clear()
    try:
        req = _request([(b"x-forwarded-for", b"198.51.100.7, 10.0.0.2")])
        assert client_ip(req) == "198.51.100.7"
    finally:
        get_settings.cache_clear()


def test_missing_configured_header_falls_back_to_peer(monkeypatch):
    monkeypatch.setenv("CLIENT_IP_HEADER", "CF-Connecting-IP")
    get_settings.cache_clear()
    try:
        req = _request([])
        assert client_ip(req) == "10.0.0.1"
    finally:
        get_settings.cache_clear()

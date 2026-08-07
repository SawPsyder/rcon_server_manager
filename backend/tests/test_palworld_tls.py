"""Palworld client against a real socket: plain HTTP, and HTTPS with pinning.

Palworld itself serves plain HTTP, so the HTTP path is the one that has to work
out of the box. HTTPS only appears when the operator puts a reverse proxy in
front of port 8212 — which the upstream docs effectively call for, since they
warn the API must not face the internet directly. Both are exercised here
against a throwaway server rather than mocks, because the parts most likely to
break (Content-Length on bodyless POSTs, certificate pinning) live below httpx.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import http.server
import json
import ssl
import threading
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.services.palworld_api import (
    ApiEndpoint,
    PalworldAuthError,
    PalworldClient,
    PalworldTlsError,
)
from app.services.tls_pins import fetch_cert_fingerprint, format_fingerprint

ADMIN_PASSWORD = "s3cret"
EXPECTED_AUTH = "Basic " + base64.b64encode(f"admin:{ADMIN_PASSWORD}".encode()).decode()


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):  # keep pytest output clean
        return

    def _send(self, status: int, payload=None, text: str | None = None):
        body = b""
        if text is not None:
            body = text.encode()
        elif payload is not None:
            body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json" if payload else "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == EXPECTED_AUTH

    def do_GET(self):  # noqa: N802
        if not self._authorized():
            self._send(401, text="Unauthorized.")
        elif self.path == "/v1/api/info":
            self._send(200, {"version": "v1.0.2", "servername": "Pal TLS"})
        elif self.path == "/v1/api/metrics":
            self._send(200, {"serverfps": 59, "currentplayernum": 2, "maxplayernum": 32})
        else:
            self._send(404, text="Not Found")

    def do_POST(self):  # noqa: N802
        if not self._authorized():
            self._send(401, text="Unauthorized.")
            return
        if self.path == "/v1/api/save":
            # Proves the client sent an explicit Content-Length: 0
            self._send(200, text=f"len={self.headers.get('Content-Length')}")
        else:
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            self._send(200, text="ok")


def _self_signed(directory: Path) -> tuple[Path, Path, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PalProxy")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "cert.pem"
    key_path = directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    fingerprint = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    return cert_path, key_path, fingerprint


def _serve(context: ssl.SSLContext | None):
    # Threading: pooled keep-alive connections would block new accepts otherwise
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    if context is not None:
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


@pytest.fixture(scope="module")
def http_server():
    """(host, port) of a throwaway plain-HTTP server — Palworld's real shape."""
    httpd, thread = _serve(None)
    try:
        yield "127.0.0.1", httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def tls_server(tmp_path_factory):
    """(host, port, sha256_fingerprint) of a throwaway self-signed HTTPS server."""
    cert_path, key_path, fingerprint = _self_signed(tmp_path_factory.mktemp("palcerts"))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_path), str(key_path))
    httpd, thread = _serve(context)
    try:
        yield "127.0.0.1", httpd.server_address[1], fingerprint
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _client(host, port, *, secret=ADMIN_PASSWORD, **kwargs) -> PalworldClient:
    return PalworldClient(
        ApiEndpoint(host=host, port=port, secret=secret, **kwargs), timeout=10.0
    )


# --- plain HTTP (the default) ---------------------------------------------


def test_basic_auth_round_trip_over_plain_http(http_server):
    host, port = http_server
    client = _client(host, port)
    try:
        assert client.info()["servername"] == "Pal TLS"
        assert client.metrics()["serverfps"] == 59
    finally:
        client.close()


def test_wrong_password_is_an_auth_error(http_server):
    host, port = http_server
    client = _client(host, port, secret="nope")
    try:
        with pytest.raises(PalworldAuthError):
            client.info()
    finally:
        client.close()


def test_bodyless_post_sends_content_length_zero_on_the_wire(http_server):
    host, port = http_server
    client = _client(host, port)
    try:
        assert client.save() == "len=0"
    finally:
        client.close()


def test_plain_http_ignores_tls_options(http_server):
    # A stale pin from a previous HTTPS setup must not break the HTTP path
    host, port = http_server
    client = _client(host, port, verify_tls=True, cert_fingerprint="aa" * 32)
    try:
        assert client.info()["version"] == "v1.0.2"
    finally:
        client.close()


# --- HTTPS behind a reverse proxy -----------------------------------------


def test_https_works_with_verification_off(tls_server):
    host, port, _fp = tls_server
    client = _client(host, port, use_https=True)
    try:
        assert client.info()["servername"] == "Pal TLS"
    finally:
        client.close()


def test_matching_fingerprint_is_accepted(tls_server):
    host, port, fingerprint = tls_server
    # Any pasted format must compare equal
    pasted = format_fingerprint(fingerprint).upper()
    client = _client(host, port, use_https=True, cert_fingerprint=pasted)
    try:
        assert client.info()["version"] == "v1.0.2"
    finally:
        client.close()


def test_mismatched_fingerprint_names_both_values(tls_server):
    host, port, fingerprint = tls_server
    with pytest.raises(PalworldTlsError) as exc:
        _client(host, port, use_https=True, cert_fingerprint="aa" * 32)
    # The observed value is carried so the operator can copy it into the form
    assert exc.value.observed_fingerprint == fingerprint
    assert fingerprint[:8] in str(exc.value).replace(":", "")


def test_verify_tls_rejects_the_self_signed_certificate(tls_server):
    host, port, _fp = tls_server
    client = _client(host, port, use_https=True, verify_tls=True)
    try:
        with pytest.raises(PalworldTlsError) as exc:
            client.info()
        # Point at the HTTPS toggle first — Palworld itself is plain HTTP
        assert "plain HTTP" in str(exc.value)
    finally:
        client.close()


def test_fetch_cert_fingerprint_reads_the_live_certificate(tls_server):
    host, port, fingerprint = tls_server
    assert fetch_cert_fingerprint(host, port, timeout=10.0) == fingerprint

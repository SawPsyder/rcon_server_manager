"""Real-TLS paths: certificate pinning and verification against a self-signed cert.

httpx.MockTransport bypasses TLS entirely, so these are the only tests that
actually exercise the handshake, the pin check and the login round trip. The
fixture stands up a throwaway HTTPS server with a self-signed certificate —
exactly what a stock Satisfactory dedicated server presents.
"""

from __future__ import annotations

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

from app.services.satisfactory_api import (
    ApiEndpoint,
    SatisfactoryAuthError,
    SatisfactoryClient,
    SatisfactoryTlsError,
    fetch_cert_fingerprint,
    format_fingerprint,
)

ADMIN_PASSWORD = "factory-admin"
ISSUED_TOKEN = "eyJwbCI6IkFkbWluaXN0cmF0b3IifQ.deadbeefcafe"


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep pytest output clean
        pass

    def _send(self, status: int, payload=None) -> None:
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        function = body.get("function")
        data = body.get("data") or {}
        token = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()

        if function == "HealthCheck":
            self._send(200, {"data": {"health": "healthy"}})
        elif function == "PasswordLogin":
            if data.get("password") != ADMIN_PASSWORD:
                self._send(401, {"errorCode": "wrong_password"})
            else:
                self._send(200, {"data": {"authenticationToken": ISSUED_TOKEN}})
        elif token != ISSUED_TOKEN:
            self._send(401, {"errorCode": "unauthorized"})
        elif function == "QueryServerState":
            self._send(
                200,
                {"data": {"serverGameState": {"numConnectedPlayers": 1, "playerLimit": 4}}},
            )
        else:
            self._send(400, {"errorCode": "unknown_function"})


def _self_signed(directory: Path) -> tuple[Path, Path, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "FactoryServer")])
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


@pytest.fixture(scope="module")
def tls_server(tmp_path_factory):
    """(host, port, sha256_fingerprint) of a throwaway self-signed HTTPS server."""
    cert_path, key_path, fingerprint = _self_signed(tmp_path_factory.mktemp("certs"))
    # Threading: pooled keep-alive connections would block new accepts otherwise
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_path), str(key_path))
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield "127.0.0.1", httpd.server_address[1], fingerprint
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _client(host, port, **kwargs) -> SatisfactoryClient:
    return SatisfactoryClient(
        ApiEndpoint(host=host, port=port, secret=ADMIN_PASSWORD, **kwargs), timeout=10.0
    )


def test_password_login_round_trip_over_real_tls(tls_server):
    host, port, _fp = tls_server
    client = _client(host, port)
    try:
        assert client.health_check()["health"] == "healthy"
        assert client.query_server_state() == {
            "numConnectedPlayers": 1,
            "playerLimit": 4,
        }
        assert client.token_kind == "login"
    finally:
        client.close()


def test_matching_fingerprint_is_accepted(tls_server):
    host, port, fingerprint = tls_server
    # Uppercase and colon-separated, as a user would paste it
    pasted = format_fingerprint(fingerprint).upper()
    client = _client(host, port, cert_fingerprint=pasted)
    try:
        assert client.query_server_state()["playerLimit"] == 4
    finally:
        client.close()


def test_mismatched_fingerprint_names_both_values(tls_server):
    host, port, fingerprint = tls_server
    with pytest.raises(SatisfactoryTlsError) as exc:
        _client(host, port, cert_fingerprint="aa" * 32)
    message = str(exc.value)
    assert "mismatch" in message.lower()
    # The operator needs to see what the server actually presented to pin it
    assert exc.value.observed_fingerprint == fingerprint
    assert fingerprint[:8] in message.replace(":", "")


def test_verification_against_a_self_signed_cert_explains_the_fix(tls_server):
    host, port, _fp = tls_server
    client = _client(host, port, verify_tls=True)
    try:
        with pytest.raises(SatisfactoryTlsError) as exc:
            client.query_server_state()
        message = str(exc.value)
        assert "self-signed" in message
        assert "Verify TLS" in message, "the error must point at the setting to change"
    finally:
        client.close()


def test_fetch_cert_fingerprint_reads_the_live_certificate(tls_server):
    host, port, fingerprint = tls_server
    assert fetch_cert_fingerprint(host, port, timeout=10.0) == fingerprint


def test_wrong_password_is_an_auth_error_over_tls(tls_server):
    host, port, _fp = tls_server
    client = SatisfactoryClient(
        ApiEndpoint(host=host, port=port, secret="nope"), timeout=10.0
    )
    try:
        with pytest.raises(SatisfactoryAuthError) as exc:
            client.query_server_state()
        assert "wrong_password" in str(exc.value)
    finally:
        client.close()

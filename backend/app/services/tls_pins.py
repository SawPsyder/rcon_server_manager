"""Certificate fingerprint pinning, shared by every HTTP-API transport.

Game servers that speak HTTPS tend to ship a self-signed certificate (or sit
behind whatever the operator put in front of them), so "verify against the
system trust store" is usually the wrong default. The alternative anchor is a
pinned SHA-256 of the presented certificate, which the operator can copy from
the mismatch error the first time they connect.

This module is transport-agnostic on purpose: it knows nothing about
Satisfactory or Palworld, and it raises a neutral :class:`CertFetchError`
carrying a ``kind`` so each API client can re-raise its own typed error.
"""

from __future__ import annotations

import hashlib
import re
import socket
import ssl

from app.services.errors import CommandError


class CertFetchError(CommandError):
    """Reading a server's certificate failed.

    ``kind`` is ``"tls"`` (handshake failed), ``"timeout"``, or ``"connect"``.
    Callers translate it into their own error taxonomy - the distinction matters
    because a server that is simply **down** must not be reported as having a
    certificate problem, or the operator chases the wrong fix.
    """

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


def normalize_fingerprint(value: str) -> str:
    """Strip colons/spaces/0x and lowercase, so pasted formats all compare equal."""
    return re.sub(r"[^0-9a-f]", "", (value or "").lower())


def format_fingerprint(digest: str) -> str:
    """Group a hex digest in colon-separated pairs for display."""
    clean = normalize_fingerprint(digest)
    return ":".join(clean[i : i + 2] for i in range(0, len(clean), 2))


def fetch_cert_fingerprint(host: str, port: int, timeout: float = 10.0) -> str:
    """SHA-256 of the server's presented certificate (DER), as lowercase hex."""
    try:
        pem = ssl.get_server_certificate((host, int(port)), timeout=timeout)
        der = ssl.PEM_cert_to_DER_cert(pem)
    except ssl.SSLError as exc:
        raise CertFetchError(
            f"TLS handshake with {host}:{port} failed while reading its certificate: {exc}",
            kind="tls",
        ) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise CertFetchError(
            f"Timed out reading the TLS certificate of {host}:{port}",
            kind="timeout",
        ) from exc
    except OSError as exc:
        raise CertFetchError(f"Could not connect to {host}:{port}: {exc}", kind="connect") from exc
    return hashlib.sha256(der).hexdigest()


def pin_mismatch_message(host: str, port: int, expected: str, observed: str) -> str:
    return (
        f"Certificate fingerprint mismatch for {host}:{port} - expected "
        f"{format_fingerprint(expected)}, server presented {format_fingerprint(observed)}"
    )

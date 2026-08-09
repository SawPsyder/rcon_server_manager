"""Per-user TOTP (RFC 6238) enrolment and verification.

The shared secret is stored Fernet-encrypted via security.encrypt_secret, the
same at-rest protection used for RCON passwords.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from urllib.parse import quote

import pyotp

from app.models import User
from app.security import decrypt_secret, encrypt_secret, hash_password, verify_password

logger = logging.getLogger(__name__)

ISSUER = "RCON Server Manager"
# One step either side of now: tolerates ~30s of clock drift between the
# server and the user's phone without widening the guess window meaningfully.
VALID_WINDOW = 1
TIME_STEP = 30
RECOVERY_CODE_COUNT = 10


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    """otpauth:// URI for an authenticator app.

    The frontend renders this as a scannable QR code and also shows the base32
    secret and raw URI for manual entry.
    """
    return (
        f"otpauth://totp/{quote(ISSUER)}:{quote(email)}"
        f"?secret={secret}&issuer={quote(ISSUER)}&algorithm=SHA1&digits=6&period={TIME_STEP}"
    )


def current_counter(at: float | None = None) -> int:
    return int((at if at is not None else time.time()) // TIME_STEP)


def verify_code(secret: str, code: str, last_counter: int | None, at: float | None = None) -> int | None:
    """Check a 6-digit code. Returns the accepted counter, or None.

    Rejects any counter at or below ``last_counter``: a TOTP code stays valid
    for its whole window, so without this the same digits could be replayed
    within 30 seconds (or up to 90, given VALID_WINDOW).
    """
    code = (code or "").strip().replace(" ", "")
    if not code or not secret:
        return None

    now = current_counter(at)
    totp = pyotp.TOTP(secret, interval=TIME_STEP)
    for offset in range(-VALID_WINDOW, VALID_WINDOW + 1):
        counter = now + offset
        if last_counter is not None and counter <= last_counter:
            continue
        # pyotp compares with hmac.compare_digest internally.
        if totp.verify(code, for_time=counter * TIME_STEP, valid_window=0):
            return counter
    return None


def get_secret(user: User) -> str:
    return decrypt_secret(user.totp_secret_enc)


def set_secret(user: User, secret: str) -> None:
    user.totp_secret_enc = encrypt_secret(secret)


def clear_totp(user: User) -> None:
    user.totp_secret_enc = ""
    user.totp_enabled = False
    user.totp_confirmed_at = None
    user.totp_last_counter = None
    user.totp_recovery_hashes = "[]"


# --------------------------------------------------------------------------
# Recovery codes
# --------------------------------------------------------------------------


def generate_recovery_codes() -> list[str]:
    """Human-transcribable one-time codes, shown to the user exactly once."""
    return [
        f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}"
        for _ in range(RECOVERY_CODE_COUNT)
    ]


def store_recovery_codes(user: User, codes: list[str]) -> None:
    user.totp_recovery_hashes = json.dumps([hash_password(c) for c in codes])


def consume_recovery_code(user: User, code: str) -> bool:
    """Match and burn a recovery code. One use each."""
    candidate = (code or "").strip().lower()
    if not candidate:
        return False
    try:
        hashes = json.loads(user.totp_recovery_hashes or "[]")
    except ValueError:
        hashes = []
    if not isinstance(hashes, list):
        hashes = []

    for stored in hashes:
        if isinstance(stored, str) and verify_password(candidate, stored):
            hashes.remove(stored)
            user.totp_recovery_hashes = json.dumps(hashes)
            logger.info("User %s used a TOTP recovery code", user.id)
            return True
    return False


def remaining_recovery_codes(user: User) -> int:
    try:
        hashes = json.loads(user.totp_recovery_hashes or "[]")
    except ValueError:
        return 0
    return len(hashes) if isinstance(hashes, list) else 0

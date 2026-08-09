"""Session cookie payloads: versioning, revocation and salt separation."""

from itsdangerous import URLSafeTimedSerializer

from app.config import get_settings
from app.security import (
    create_mfa_token,
    create_session_token,
    hash_url_token,
    load_mfa_payload,
    load_session_payload,
)


def test_round_trip_carries_user_and_token_version():
    data = load_session_payload(create_session_token(7, 3))
    assert data["uid"] == 7
    assert data["tv"] == 3


def test_legacy_admin_cookie_is_rejected():
    """Pre-multi-user cookies carry no user id.

    Accepting one would mean unattributed admin with no token_version to
    revoke and no is_active to check - the exact backdoor the bootstrap
    design removes.
    """
    settings = get_settings()
    legacy = URLSafeTimedSerializer(settings.secret_key, salt="ssm-session").dumps(
        {"sub": "admin"}
    )
    assert load_session_payload(legacy) is None


def test_tampered_token_is_rejected():
    token = create_session_token(1, 1)
    assert load_session_payload(token[:-4] + "AAAA") is None
    assert load_session_payload("") is None
    assert load_session_payload("not-a-token") is None


def test_mfa_token_cannot_be_used_as_a_session():
    """Different salt, so a half-authenticated token is not a session cookie."""
    partial = create_mfa_token(5, 1)
    assert load_session_payload(partial) is None
    assert load_mfa_payload(partial)["uid"] == 5


def test_session_token_cannot_be_used_as_an_mfa_token():
    assert load_mfa_payload(create_session_token(5, 1)) is None


def test_url_token_hash_is_stable_and_not_the_token():
    raw = "some-random-invite-token"
    digest = hash_url_token(raw)
    assert digest == hash_url_token(raw)
    assert digest != raw
    assert len(digest) == 64

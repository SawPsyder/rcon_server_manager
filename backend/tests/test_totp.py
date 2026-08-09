"""TOTP verification: clock skew tolerated, replay refused."""

import pyotp

from app.services import totp as totp_service


def _code_at(secret: str, counter: int) -> str:
    return pyotp.TOTP(secret, interval=totp_service.TIME_STEP).at(
        counter * totp_service.TIME_STEP
    )


def test_current_code_is_accepted():
    secret = totp_service.generate_secret()
    now = 1_700_000_000.0
    counter = totp_service.current_counter(now)
    assert totp_service.verify_code(secret, _code_at(secret, counter), None, now) == counter


def test_one_step_of_drift_is_tolerated():
    secret = totp_service.generate_secret()
    now = 1_700_000_000.0
    counter = totp_service.current_counter(now)
    for offset in (-1, 1):
        assert (
            totp_service.verify_code(secret, _code_at(secret, counter + offset), None, now)
            == counter + offset
        )


def test_two_steps_of_drift_is_refused():
    secret = totp_service.generate_secret()
    now = 1_700_000_000.0
    counter = totp_service.current_counter(now)
    assert totp_service.verify_code(secret, _code_at(secret, counter + 2), None, now) is None


def test_replaying_the_same_counter_is_refused():
    """A code stays valid for its whole window; reuse must not."""
    secret = totp_service.generate_secret()
    now = 1_700_000_000.0
    counter = totp_service.current_counter(now)
    code = _code_at(secret, counter)

    assert totp_service.verify_code(secret, code, None, now) == counter
    # Same code, same window, but the counter has now been recorded.
    assert totp_service.verify_code(secret, code, counter, now) is None


def test_an_older_code_inside_the_window_is_refused_after_a_newer_one():
    secret = totp_service.generate_secret()
    now = 1_700_000_000.0
    counter = totp_service.current_counter(now)
    older = _code_at(secret, counter - 1)
    assert totp_service.verify_code(secret, older, counter, now) is None


def test_garbage_and_empty_codes_are_refused():
    secret = totp_service.generate_secret()
    for bad in ("", "   ", "000000", "abcdef"):
        assert totp_service.verify_code(secret, bad, None, 1_700_000_000.0) is None


def test_missing_secret_is_refused():
    assert totp_service.verify_code("", "123456", None) is None


def test_provisioning_uri_is_scannable():
    secret = totp_service.generate_secret()
    uri = totp_service.provisioning_uri(secret, "operator@example.org")
    assert uri.startswith("otpauth://totp/")
    assert f"secret={secret}" in uri
    assert "operator%40example.org" in uri
    assert f"period={totp_service.TIME_STEP}" in uri


class _FakeUser:
    def __init__(self):
        self.id = 1
        self.totp_recovery_hashes = "[]"


def test_recovery_codes_are_single_use():
    user = _FakeUser()
    codes = totp_service.generate_recovery_codes()
    totp_service.store_recovery_codes(user, codes)

    assert totp_service.remaining_recovery_codes(user) == totp_service.RECOVERY_CODE_COUNT
    assert totp_service.consume_recovery_code(user, codes[0]) is True
    # Second use of the same code must fail.
    assert totp_service.consume_recovery_code(user, codes[0]) is False
    assert totp_service.remaining_recovery_codes(user) == totp_service.RECOVERY_CODE_COUNT - 1


def test_recovery_codes_are_not_stored_in_the_clear():
    user = _FakeUser()
    codes = totp_service.generate_recovery_codes()
    totp_service.store_recovery_codes(user, codes)
    for code in codes:
        assert code not in user.totp_recovery_hashes


def test_unknown_recovery_code_is_refused():
    user = _FakeUser()
    totp_service.store_recovery_codes(user, totp_service.generate_recovery_codes())
    assert totp_service.consume_recovery_code(user, "dead-beef-cafe") is False
    assert totp_service.consume_recovery_code(user, "") is False

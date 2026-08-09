"""Email-keyed rate limits used by login / forgot-password."""

from app.services import rate_limit


def setup_function():
    rate_limit.reset_all()


def teardown_function():
    rate_limit.reset_all()


def test_email_rate_limit_is_independent_of_ip_bucket():
    """Stuffing one mailbox is limited even when the attacker rotates IPs."""
    assert rate_limit.check("login:email:a@b.c", limit=2, window_seconds=60)
    assert rate_limit.check("login:email:a@b.c", limit=2, window_seconds=60)
    assert rate_limit.check("login:email:a@b.c", limit=2, window_seconds=60) is False

    # A different IP bucket does not open another window for the same email key.
    assert rate_limit.check("login:ip:1.2.3.4", limit=2, window_seconds=60)
    assert rate_limit.check("login:email:a@b.c", limit=2, window_seconds=60) is False

    # A different email is unaffected.
    assert rate_limit.check("login:email:other@b.c", limit=2, window_seconds=60)


def test_rate_limit_reset_clears_email_bucket():
    key = "login:email:reset@example.org"
    assert rate_limit.check(key, 1, 60)
    assert rate_limit.check(key, 1, 60) is False
    rate_limit.reset(key)
    assert rate_limit.check(key, 1, 60)

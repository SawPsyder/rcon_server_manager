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


def test_rate_limit_capacity_does_not_wipe_existing_counters(monkeypatch):
    """A flood of unique keys must not reset login:email:victim."""
    monkeypatch.setattr(rate_limit, "_MAX_KEYS", 8)
    victim = "login:email:victim@example.org"
    assert rate_limit.check(victim, 2, 60)
    assert rate_limit.check(victim, 2, 60)
    assert rate_limit.check(victim, 2, 60) is False

    accepted_new = 0
    for i in range(20):
        if rate_limit.check(f"login:email:flood-{i}@x.test", 10, 60):
            accepted_new += 1

    # Existing counter stays locked; new keys are refused once the map is full.
    assert rate_limit.check(victim, 2, 60) is False
    # victim occupies one slot; seven new keys fit under MAX_KEYS=8.
    assert accepted_new == 7


def test_expired_windows_are_evicted_before_refusing_new_keys(monkeypatch):
    """A 60s public-share window must not occupy a slot for _STALE_AFTER."""
    monkeypatch.setattr(rate_limit, "_MAX_KEYS", 2)
    monkeypatch.setattr(rate_limit, "_STALE_AFTER", 10_000.0)
    now = [1_000.0]
    monkeypatch.setattr(rate_limit.time, "time", lambda: now[0])

    assert rate_limit.check("public-share:ip:old", 10, 10)
    now[0] = 1_011.0  # stored 10s window has expired
    assert rate_limit.check("login:email:a@x.test", 10, 60)
    assert rate_limit.check("login:email:b@x.test", 10, 60)

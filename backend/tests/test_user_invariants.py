"""User-administration invariants.

These are the rules that keep the module from locking itself out or handing
back the bootstrap backdoor, so they are asserted directly against the service
layer rather than through the routes.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    ROLE_ADMIN,
    ROLE_USER,
    TOKEN_PURPOSE_RESET,
    AuthToken,
    Base,
    Server,
    ServerGrant,
    utcnow,
)
from app.schemas import UserSelfUpdate
from app.services import users as user_service


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    for i in (1, 2, 3):
        session.add(
            Server(id=i, name=f"S{i}", host="h", query_port=27131, rcon_port=27015)
        )
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _admin(db, email="admin@example.org"):
    return user_service.create_user(
        db, email=email, role=ROLE_ADMIN, password="correct-horse-battery"
    )


def _user(db, email="op@example.org"):
    return user_service.create_user(
        db, email=email, role=ROLE_USER, password="correct-horse-battery"
    )


# --------------------------------------------------------------------------
# Last-admin protection
# --------------------------------------------------------------------------


def test_last_admin_cannot_be_demoted(db):
    admin = _admin(db)
    other = _user(db)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        user_service.update_user(db, admin, other, role=ROLE_USER)
    assert exc.value.status_code == 400
    assert "last active administrator" in exc.value.detail


def test_last_admin_cannot_be_deactivated(db):
    admin = _admin(db)
    other = _user(db)
    db.commit()

    with pytest.raises(HTTPException):
        user_service.update_user(db, admin, other, is_active=False)


def test_last_admin_cannot_be_deleted(db):
    admin = _admin(db)
    other = _user(db)
    db.commit()

    with pytest.raises(HTTPException):
        user_service.delete_user(db, admin, other)


def test_a_second_admin_makes_the_first_removable(db):
    first = _admin(db, "one@example.org")
    second = _admin(db, "two@example.org")
    db.commit()

    user_service.update_user(db, first, second, role=ROLE_USER)
    db.commit()
    assert first.role == ROLE_USER
    assert user_service.active_admin_count(db) == 1


# --------------------------------------------------------------------------
# Self-modification
# --------------------------------------------------------------------------


def test_nobody_can_change_their_own_role(db):
    admin = _admin(db, "one@example.org")
    _admin(db, "two@example.org")  # so the last-admin rule is not what fires
    db.commit()

    with pytest.raises(HTTPException) as exc:
        user_service.update_user(db, admin, admin, role=ROLE_USER)
    assert "your own role" in exc.value.detail


def test_nobody_can_deactivate_themselves(db):
    admin = _admin(db, "one@example.org")
    _admin(db, "two@example.org")
    db.commit()

    with pytest.raises(HTTPException) as exc:
        user_service.update_user(db, admin, admin, is_active=False)
    assert "your own account" in exc.value.detail


def test_nobody_can_delete_themselves(db):
    admin = _admin(db, "one@example.org")
    _admin(db, "two@example.org")
    db.commit()

    with pytest.raises(HTTPException):
        user_service.delete_user(db, admin, admin)


def test_self_update_model_cannot_carry_a_role(db):
    """Mass-assignment guard: PATCH /api/auth/me must not accept role."""
    body = UserSelfUpdate.model_validate({"display_name": "Jan", "role": "admin"})
    assert not hasattr(body, "role")
    assert "role" not in body.model_dump()


# --------------------------------------------------------------------------
# Deactivation and password changes revoke sessions
# --------------------------------------------------------------------------


def test_deactivation_bumps_token_version(db):
    _admin(db, "one@example.org")
    admin2 = _admin(db, "two@example.org")
    target = _user(db)
    db.commit()
    before = target.token_version

    user_service.update_user(db, target, admin2, is_active=False)
    db.commit()
    assert target.token_version > before


def test_setting_a_password_bumps_token_version(db):
    user = _user(db)
    db.commit()
    before = user.token_version

    user_service.set_password(db, user, "another-good-passphrase")
    db.commit()
    assert user.token_version > before


def test_short_passwords_are_rejected(db):
    user = _user(db)
    db.commit()
    with pytest.raises(HTTPException):
        user_service.set_password(db, user, "short")


def test_overlong_passwords_are_rejected_not_truncated(db):
    """bcrypt ignores everything past 72 bytes; refuse rather than pretend."""
    user = _user(db)
    db.commit()
    with pytest.raises(HTTPException):
        user_service.set_password(db, user, "a" * 73)


# --------------------------------------------------------------------------
# Email uniqueness
# --------------------------------------------------------------------------


def test_email_uniqueness_is_case_insensitive(db):
    user_service.create_user(db, email="Op@Example.org", password="correct-horse-battery")
    db.commit()
    with pytest.raises(HTTPException) as exc:
        user_service.create_user(db, email="op@EXAMPLE.ORG", password="correct-horse-battery")
    assert exc.value.status_code == 409


# --------------------------------------------------------------------------
# Grants
# --------------------------------------------------------------------------


def test_replace_grants_is_a_full_replacement(db):
    admin = _admin(db)
    user = _user(db)
    db.commit()

    assert user_service.replace_grants(db, user, [1, 2], admin) == [1, 2]
    db.commit()
    assert user_service.grant_ids(db, user) == [1, 2]

    assert user_service.replace_grants(db, user, [2, 3], admin) == [2, 3]
    db.commit()
    assert user_service.grant_ids(db, user) == [2, 3]

    user_service.replace_grants(db, user, [], admin)
    db.commit()
    assert user_service.grant_ids(db, user) == []


def test_granting_an_unknown_server_is_rejected(db):
    admin = _admin(db)
    user = _user(db)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        user_service.replace_grants(db, user, [1, 999], admin)
    assert "999" in exc.value.detail


def test_deleting_a_user_removes_their_grants(db):
    admin = _admin(db)
    user = _user(db)
    db.commit()
    user_service.replace_grants(db, user, [1, 2], admin)
    db.commit()

    user_service.delete_user(db, user, admin)
    db.commit()
    assert db.query(ServerGrant).count() == 0


# --------------------------------------------------------------------------
# Temporary lockout
# --------------------------------------------------------------------------


def test_failed_logins_lock_after_threshold(db):
    user = _user(db)
    db.commit()

    for _ in range(user_service.LOCKOUT_THRESHOLD - 1):
        assert user_service.record_failed_login(user) is False
    assert user_service.is_temporarily_locked(user) is False

    assert user_service.record_failed_login(user) is True
    assert user_service.is_temporarily_locked(user) is True
    assert user.locked_until is not None
    assert "temporarily locked" in user_service.lockout_detail(user).lower()


def test_unlock_clears_lock_and_counter(db):
    user = _user(db)
    for _ in range(user_service.LOCKOUT_THRESHOLD):
        user_service.record_failed_login(user)
    db.commit()
    assert user_service.is_temporarily_locked(user)

    user_service.unlock_user(user)
    db.commit()
    assert user.failed_logins == 0
    assert user.locked_until is None
    assert user_service.is_temporarily_locked(user) is False


def test_expired_lockout_starts_a_fresh_failure_streak(db):
    from datetime import timedelta

    user = _user(db)
    for _ in range(user_service.LOCKOUT_THRESHOLD):
        user_service.record_failed_login(user)
    user.locked_until = utcnow() - timedelta(seconds=1)
    db.commit()

    assert user_service.is_temporarily_locked(user) is False
    assert user_service.record_failed_login(user) is False
    assert user.failed_logins == 1
    assert user_service.is_temporarily_locked(user) is False


def test_set_password_clears_lock(db):
    user = _user(db)
    for _ in range(user_service.LOCKOUT_THRESHOLD):
        user_service.record_failed_login(user)
    db.commit()

    user_service.set_password(db, user, "brand-new-passphrase")
    db.commit()
    assert user_service.is_temporarily_locked(user) is False


# --------------------------------------------------------------------------
# Invite / reset tokens
# --------------------------------------------------------------------------


def test_token_is_single_use(db):
    user = _user(db)
    db.commit()
    raw, _ = user_service.issue_token(db, user, TOKEN_PURPOSE_RESET)
    db.commit()

    assert user_service.consume_token(db, raw).id == user.id
    db.commit()
    with pytest.raises(HTTPException):
        user_service.consume_token(db, raw)


def test_peek_token_does_not_consume(db):
    user = _user(db)
    db.commit()
    raw, _ = user_service.issue_token(db, user, TOKEN_PURPOSE_RESET)
    db.commit()

    assert user_service.peek_token(db, raw).id == user.id
    assert user_service.peek_token(db, raw).id == user.id
    assert db.query(AuthToken).one().used_at is None
    assert user_service.consume_token(db, raw).id == user.id


def test_issuing_a_token_invalidates_the_previous_one(db):
    user = _user(db)
    db.commit()
    first, _ = user_service.issue_token(db, user, TOKEN_PURPOSE_RESET)
    db.commit()
    second, _ = user_service.issue_token(db, user, TOKEN_PURPOSE_RESET)
    db.commit()

    with pytest.raises(HTTPException):
        user_service.consume_token(db, first)
    assert user_service.consume_token(db, second).id == user.id


def test_expired_token_is_rejected(db):
    user = _user(db)
    db.commit()
    raw, _ = user_service.issue_token(db, user, TOKEN_PURPOSE_RESET)
    row = db.query(AuthToken).one()
    row.expires_at = utcnow().replace(year=2000)
    db.commit()

    with pytest.raises(HTTPException):
        user_service.consume_token(db, raw)


def test_token_for_an_inactive_user_is_rejected(db):
    user = _user(db)
    db.commit()
    raw, _ = user_service.issue_token(db, user, TOKEN_PURPOSE_RESET)
    user.is_active = False
    db.commit()

    with pytest.raises(HTTPException):
        user_service.consume_token(db, raw)


def test_raw_token_is_not_stored(db):
    user = _user(db)
    db.commit()
    raw, _ = user_service.issue_token(db, user, TOKEN_PURPOSE_RESET)
    db.commit()
    assert db.query(AuthToken).one().token_hash != raw


def test_setting_a_password_burns_outstanding_tokens(db):
    user = _user(db)
    db.commit()
    raw, _ = user_service.issue_token(db, user, TOKEN_PURPOSE_RESET)
    db.commit()

    user_service.set_password(db, user, "another-good-passphrase")
    db.commit()
    with pytest.raises(HTTPException):
        user_service.consume_token(db, raw)

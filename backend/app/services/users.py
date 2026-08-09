"""User, grant and email-token operations, with the safety invariants.

The invariants live here rather than in the route bodies so every caller gets
them - a future endpoint cannot forget to check "am I removing the last admin?".
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    ROLE_ADMIN,
    ROLE_USER,
    TOKEN_PURPOSE_INVITE,
    TOKEN_PURPOSE_RESET,
    AuthToken,
    CommandHistory,
    PlayerActionLog,
    PlayerAdminNote,
    Server,
    ServerGrant,
    User,
    utcnow,
)
from app.security import generate_url_token, hash_password, hash_url_token

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 10
# bcrypt silently truncates beyond 72 bytes; reject rather than accept a
# password whose tail does nothing.
MAX_PASSWORD_LENGTH = 72

# Temporary lock after consecutive bad password / TOTP attempts. Cleared on
# successful sign-in, password reset, or admin unlock.
LOCKOUT_THRESHOLD = 5
LOCKOUT_MINUTES = 15


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
        )
    if len(password.encode("utf-8")) > MAX_PASSWORD_LENGTH:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Password must be at most {MAX_PASSWORD_LENGTH} bytes",
        )


def get_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email_ci == normalize_email(email)).first()


def get_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


def active_admin_count(db: Session, exclude_user_id: int | None = None) -> int:
    query = db.query(User.id).filter(User.role == ROLE_ADMIN, User.is_active.is_(True))
    if exclude_user_id is not None:
        query = query.filter(User.id != exclude_user_id)
    return query.count()


def _assert_not_last_admin(db: Session, target: User, action: str) -> None:
    """Refuse anything that would leave zero active admins.

    This underpins the promise that ADMIN_PASSWORD is inert after the first
    claim: the claim endpoint re-opens when no active admin exists, so allowing
    the count to reach zero would hand the bootstrap backdoor back.
    """
    if not (target.role == ROLE_ADMIN and target.is_active):
        return
    if active_admin_count(db, exclude_user_id=target.id) == 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot {action} the last active administrator",
        )


# --------------------------------------------------------------------------
# Creation and mutation
# --------------------------------------------------------------------------


def create_user(
    db: Session,
    *,
    email: str,
    display_name: str = "",
    role: str = ROLE_USER,
    password: str | None = None,
    is_active: bool = True,
) -> User:
    email_ci = normalize_email(email)
    if not email_ci:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Email is required")
    if get_by_email(db, email_ci) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="A user with that email already exists")
    if role not in (ROLE_ADMIN, ROLE_USER):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Unknown role")

    password_hash = ""
    if password is not None:
        validate_password(password)
        password_hash = hash_password(password)

    user = User(
        email=email.strip(),
        email_ci=email_ci,
        display_name=(display_name or "").strip(),
        role=role,
        is_active=is_active,
        password_hash=password_hash,
        password_changed_at=utcnow() if password_hash else None,
    )
    db.add(user)
    db.flush()
    return user


def update_user(
    db: Session,
    target: User,
    actor: User,
    *,
    display_name: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
) -> User:
    if display_name is not None:
        target.display_name = display_name.strip()

    if role is not None and role != target.role:
        # Nobody edits their own role. Blocks accidental self-demotion, and
        # closes any future path where a compromised admin session quietly
        # escalates or a self-service handler is wired to this function.
        if target.id == actor.id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="You cannot change your own role"
            )
        if role not in (ROLE_ADMIN, ROLE_USER):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Unknown role")
        if role != ROLE_ADMIN:
            _assert_not_last_admin(db, target, "demote")
        target.role = role

    if is_active is not None and is_active != target.is_active:
        if target.id == actor.id and not is_active:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="You cannot deactivate your own account"
            )
        if not is_active:
            _assert_not_last_admin(db, target, "deactivate")
            # Deactivation must take effect on the next request, not in 7 days
            # when the cookie happens to expire.
            target.token_version += 1
        target.is_active = is_active

    return target


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite may hand back naive datetimes from timezone=True columns."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=utcnow().tzinfo)
    return dt


def is_temporarily_locked(user: User) -> bool:
    until = _aware(user.locked_until)
    return until is not None and until > utcnow()


def lockout_detail(user: User) -> str:
    """User-facing message for a temporary lock. Safe to show on login."""
    until = _aware(user.locked_until)
    if until is None:
        return (
            "This account is temporarily locked after too many failed sign-in "
            "attempts. Try again later, or ask an administrator to unlock it."
        )
    # Ceiling of remaining minutes so a just-locked 15-minute window says 15.
    remaining_seconds = max(0, int((until - utcnow()).total_seconds()))
    remaining = max(1, (remaining_seconds + 59) // 60)
    return (
        f"This account is temporarily locked after too many failed sign-in "
        f"attempts. Try again in about {remaining} minute"
        f"{'' if remaining == 1 else 's'}, or ask an administrator to unlock it."
    )


def record_failed_login(user: User) -> bool:
    """Increment failure counter; lock when the threshold is reached.

    Returns True if the account is (now) temporarily locked.
    """
    user.failed_logins = int(user.failed_logins or 0) + 1
    if user.failed_logins >= LOCKOUT_THRESHOLD:
        user.locked_until = utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
        return True
    return is_temporarily_locked(user)


def clear_login_failures(user: User) -> None:
    user.failed_logins = 0
    user.locked_until = None


def unlock_user(user: User) -> None:
    """Admin action: clear temporary lock and failure counter."""
    clear_login_failures(user)


def set_password(db: Session, user: User, password: str) -> None:
    """Set a password and invalidate every existing session for the user."""
    validate_password(password)
    user.password_hash = hash_password(password)
    user.password_changed_at = utcnow()
    user.token_version += 1
    clear_login_failures(user)
    invalidate_tokens(db, user)


def delete_user(db: Session, target: User, actor: User) -> None:
    if target.id == actor.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="You cannot delete your own account")
    _assert_not_last_admin(db, target, "delete")

    # Null the attribution columns by hand. On databases upgraded from an older
    # schema these columns were added by ALTER TABLE without a REFERENCES clause
    # (SQLite cannot add an enforced FK), so ON DELETE SET NULL will not fire.
    db.query(CommandHistory).filter(CommandHistory.actor_user_id == target.id).update(
        {CommandHistory.actor_user_id: None}, synchronize_session=False
    )
    db.query(PlayerActionLog).filter(PlayerActionLog.actor_user_id == target.id).update(
        {PlayerActionLog.actor_user_id: None}, synchronize_session=False
    )
    db.query(PlayerAdminNote).filter(PlayerAdminNote.author_user_id == target.id).update(
        {PlayerAdminNote.author_user_id: None}, synchronize_session=False
    )
    db.query(ServerGrant).filter(ServerGrant.granted_by_user_id == target.id).update(
        {ServerGrant.granted_by_user_id: None}, synchronize_session=False
    )
    db.query(AuthToken).filter(AuthToken.user_id == target.id).delete(synchronize_session=False)
    db.query(ServerGrant).filter(ServerGrant.user_id == target.id).delete(synchronize_session=False)

    db.delete(target)


# --------------------------------------------------------------------------
# Grants
# --------------------------------------------------------------------------


def grant_ids(db: Session, user: User) -> list[int]:
    return [
        sid
        for (sid,) in db.query(ServerGrant.server_id)
        .filter(ServerGrant.user_id == user.id)
        .order_by(ServerGrant.server_id)
    ]


def replace_grants(db: Session, target: User, server_ids: list[int], actor: User) -> list[int]:
    """Set a user's server access to exactly ``server_ids``."""
    wanted = {int(s) for s in server_ids}
    if wanted:
        known = {sid for (sid,) in db.query(Server.id).filter(Server.id.in_(wanted))}
        missing = wanted - known
        if missing:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown server id(s): {', '.join(str(m) for m in sorted(missing))}",
            )

    existing = {g.server_id: g for g in db.query(ServerGrant).filter(ServerGrant.user_id == target.id)}

    for server_id in wanted - existing.keys():
        db.add(ServerGrant(user_id=target.id, server_id=server_id, granted_by_user_id=actor.id))
    for server_id in existing.keys() - wanted:
        db.delete(existing[server_id])

    db.flush()
    return sorted(wanted)


# --------------------------------------------------------------------------
# Invite / reset tokens
# --------------------------------------------------------------------------


def invalidate_tokens(db: Session, user: User) -> None:
    """Burn every outstanding invite/reset token for a user."""
    db.query(AuthToken).filter(
        AuthToken.user_id == user.id, AuthToken.used_at.is_(None)
    ).update({AuthToken.used_at: utcnow()}, synchronize_session=False)


def issue_token(db: Session, user: User, purpose: str, requested_ip: str = "") -> tuple[str, datetime]:
    """Mint a single-use token. Returns the raw value (only chance to read it)."""
    settings = get_settings()
    if purpose == TOKEN_PURPOSE_INVITE:
        expires_at = utcnow() + timedelta(hours=settings.invite_token_ttl_hours)
    else:
        expires_at = utcnow() + timedelta(minutes=settings.reset_token_ttl_minutes)

    # Only one live link at a time, so an older mail cannot still be redeemed.
    invalidate_tokens(db, user)

    raw = generate_url_token()
    db.add(
        AuthToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=hash_url_token(raw),
            expires_at=expires_at,
            requested_ip=requested_ip[:64],
        )
    )
    db.flush()
    return raw, expires_at


def _lookup_live_token(
    db: Session, raw: str, purpose: str | None = None
) -> tuple[AuthToken, User] | None:
    """Return (token row, user) when the raw token is still redeemable."""
    if not raw:
        return None

    row = db.query(AuthToken).filter(AuthToken.token_hash == hash_url_token(raw)).first()
    if row is None or row.used_at is not None:
        return None
    if purpose is not None and row.purpose != purpose:
        return None

    expires_at = row.expires_at
    # SQLite hands back naive datetimes even from a timezone=True column.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=utcnow().tzinfo)
    if expires_at < utcnow():
        return None

    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    return row, user


def peek_token(db: Session, raw: str, purpose: str | None = None) -> User | None:
    """Return the user if the token is valid, without marking it used."""
    found = _lookup_live_token(db, raw, purpose)
    return found[1] if found else None


def consume_token(db: Session, raw: str, purpose: str | None = None) -> User:
    """Redeem an invite/reset token, or raise 400."""
    invalid = HTTPException(
        status.HTTP_400_BAD_REQUEST, detail="This link is invalid or has expired"
    )
    found = _lookup_live_token(db, raw, purpose)
    if found is None:
        raise invalid
    row, user = found
    row.used_at = utcnow()
    return user


def token_purposes() -> tuple[str, str]:
    return TOKEN_PURPOSE_INVITE, TOKEN_PURPOSE_RESET

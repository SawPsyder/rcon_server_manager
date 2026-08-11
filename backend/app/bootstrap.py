import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ROLE_ADMIN, AdminAuth, Setting, User
from app.security import hash_password, verify_password
from app.server_types import list_adapters

logger = logging.getLogger(__name__)

# Type-agnostic first-boot settings; per-game defaults live in each adapter's seed()
DEFAULT_SETTINGS = {
    "query_timeout": "2.0",
    "poll_interval_seconds": "10",
    "stats_interval_seconds": "60",
    # Wall clock for all schedules (not per game server). IANA name.
    "app_timezone": "UTC",
}


def admin_exists(db: Session) -> bool:
    """True once at least one active admin user has been created."""
    return (
        db.query(User.id).filter(User.role == ROLE_ADMIN, User.is_active.is_(True)).first()
        is not None
    )


def ensure_admin(db: Session) -> None:
    """Keep the bootstrap credential in sync with ADMIN_PASSWORD, until it is claimed.

    While no admin user exists, ADMIN_PASSWORD is the only way in, so a change to
    it has to take effect - otherwise an operator who fixes a typo in their env
    is locked out with no way to notice why. Once an admin user exists the row is
    inert and must never be touched again. Locked-out admins recover via the
    normal password-reset / admin-issued reset flow, not by reopening claim.
    """
    settings = get_settings()

    row = db.query(AdminAuth).first()
    if row is None:
        db.add(AdminAuth(password_hash=hash_password(settings.admin_password)))
        db.commit()
        return

    if admin_exists(db):
        return

    if not verify_password(settings.admin_password, row.password_hash):
        row.password_hash = hash_password(settings.admin_password)
        db.commit()
        logger.info("Bootstrap password re-synced from ADMIN_PASSWORD")


def seed_if_empty(db: Session) -> None:
    existing = {s.key for s in db.query(Setting).all()}
    for key, value in DEFAULT_SETTINGS.items():
        if key not in existing:
            db.add(Setting(key=key, value=value))

    # Each server type seeds its own maps / defaults (idempotent)
    for adapter in list_adapters():
        adapter.seed(db)

    db.commit()

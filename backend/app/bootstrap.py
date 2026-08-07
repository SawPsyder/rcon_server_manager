from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AdminAuth, Setting
from app.security import hash_password
from app.server_types import list_adapters

# Type-agnostic first-boot settings; per-game defaults live in each adapter's seed()
DEFAULT_SETTINGS = {
    "query_timeout": "2.0",
    "poll_interval_seconds": "10",
    "stats_interval_seconds": "60",
}


def ensure_admin(db: Session) -> None:
    settings = get_settings()
    row = db.query(AdminAuth).first()
    if row is None:
        db.add(AdminAuth(password_hash=hash_password(settings.admin_password)))
        db.commit()


def seed_if_empty(db: Session) -> None:
    existing = {s.key for s in db.query(Setting).all()}
    for key, value in DEFAULT_SETTINGS.items():
        if key not in existing:
            db.add(Setting(key=key, value=value))

    # Each server type seeds its own maps / defaults (idempotent)
    for adapter in list_adapters():
        adapter.seed(db)

    db.commit()

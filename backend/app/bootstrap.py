from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AdminAuth, MapConfig, Setting
from app.security import hash_password
from app.seed_data import (
    CUSTOM_CHECKPOINT_MAPS,
    VANILLA_CHECKPOINT_MAPS,
    checkpoint_scenario,
)
from app.server_types.sandstorm import DEFAULT_PREFERRED_GAMEMODE


def ensure_admin(db: Session) -> None:
    settings = get_settings()
    row = db.query(AdminAuth).first()
    if row is None:
        db.add(AdminAuth(password_hash=hash_password(settings.admin_password)))
        db.commit()


def seed_if_empty(db: Session) -> None:
    if db.query(MapConfig).count() == 0:
        for alias, map_name in VANILLA_CHECKPOINT_MAPS:
            db.add(
                MapConfig(
                    server_type="sandstorm",
                    alias=alias,
                    map_name=map_name,
                    day=True,
                    night=True,
                    checkpoint=checkpoint_scenario(alias, "security"),
                    checkpoint_ins=checkpoint_scenario(alias, "insurgents"),
                    self_added=False,
                )
            )
        for alias, map_name in CUSTOM_CHECKPOINT_MAPS:
            db.add(
                MapConfig(
                    server_type="sandstorm",
                    alias=alias,
                    map_name=map_name,
                    day=True,
                    night=True,
                    checkpoint=checkpoint_scenario(alias, "security"),
                    checkpoint_ins=checkpoint_scenario(alias, "insurgents"),
                    self_added=True,
                )
            )

    defaults = {
        "query_timeout": "2.0",
        "poll_interval_seconds": "10",
        "stats_interval_seconds": "60",
        "type.sandstorm.preferred_gamemode": DEFAULT_PREFERRED_GAMEMODE,
    }
    existing = {s.key for s in db.query(Setting).all()}
    for key, value in defaults.items():
        if key not in existing:
            db.add(Setting(key=key, value=value))

    db.commit()

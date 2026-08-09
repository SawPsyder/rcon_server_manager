from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import AdminUser
from app.models import Setting
from app.schemas import SettingsOut, SettingsUpdate, TypeSettingsOut
from app.server_types import get_adapter, list_adapters, list_server_types

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _get(db: Session, key: str, default: str) -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row else default


def _set(db: Session, key: str, value: str) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def _type_preferred_key(type_id: str) -> str:
    return f"type.{type_id}.preferred_gamemode"


def _type_settings(db: Session) -> dict[str, TypeSettingsOut]:
    out: dict[str, TypeSettingsOut] = {}
    for adapter in list_adapters():
        type_id = adapter.info.id
        default = adapter.default_preferred_gamemode
        val = _get(db, _type_preferred_key(type_id), "")
        # Fall back to the pre-per-type settings key where the game had one
        legacy_key = adapter.legacy_preferred_gamemode_key
        if not val and legacy_key:
            val = _get(db, legacy_key, default)
        out[type_id] = TypeSettingsOut(preferred_gamemode=val or default)
    return out


# Readable by every signed-in user: the overview and server detail pages both
# need poll_interval_seconds, and this exposes only intervals and per-type
# gamemode defaults. Writing them is admin-only (see update_settings).
@router.get("", response_model=SettingsOut)
def get_settings_api(db: Session = Depends(get_db)) -> SettingsOut:
    return SettingsOut(
        query_timeout=float(_get(db, "query_timeout", "2.0")),
        poll_interval_seconds=int(_get(db, "poll_interval_seconds", "10")),
        stats_interval_seconds=int(_get(db, "stats_interval_seconds", "60")),
        types=_type_settings(db),
    )


@router.put("", response_model=SettingsOut)
def update_settings(
    body: SettingsUpdate,
    _admin: AdminUser,
    db: Session = Depends(get_db),
) -> SettingsOut:
    if body.query_timeout is not None:
        _set(db, "query_timeout", str(body.query_timeout))
    if body.poll_interval_seconds is not None:
        _set(db, "poll_interval_seconds", str(body.poll_interval_seconds))
    if body.stats_interval_seconds is not None:
        _set(db, "stats_interval_seconds", str(body.stats_interval_seconds))
    if body.types:
        known = {t.id for t in list_server_types()}
        for type_id, ts in body.types.items():
            if type_id not in known:
                raise HTTPException(status_code=400, detail=f"Unknown server type: {type_id}")
            if ts.preferred_gamemode is not None:
                _set(db, _type_preferred_key(type_id), ts.preferred_gamemode.strip())
    db.commit()
    return get_settings_api(db)


def resolve_preferred_gamemode(db: Session, server_type: str, server_override: str | None) -> str:
    if server_override and server_override.strip():
        return server_override.strip()
    typed = _get(db, _type_preferred_key(server_type), "")
    if typed:
        return typed
    try:
        adapter = get_adapter(server_type)
    except KeyError:
        return ""
    if adapter.legacy_preferred_gamemode_key:
        return _get(
            db,
            adapter.legacy_preferred_gamemode_key,
            adapter.default_preferred_gamemode,
        )
    return adapter.default_preferred_gamemode

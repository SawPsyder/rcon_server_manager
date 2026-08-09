from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import AdminUser, client_ip
from app.models import Setting
from app.schemas import (
    ClientIpDebugOut,
    ClientIpHeaderValue,
    SettingsOut,
    SettingsUpdate,
    TypeSettingsOut,
)
from app.server_types import get_adapter, list_adapters, list_server_types

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Headers operators commonly use for the real client IP. Always listed in the
# Helpers UI so absence is as informative as presence. Case-insensitive match
# against the request; display names keep conventional capitalisation.
_CLIENT_IP_HEADER_CANDIDATES = (
    "CF-Connecting-IP",
    "True-Client-IP",
    "X-Real-IP",
    "X-Forwarded-For",
    "Forwarded",
    "X-Client-IP",
    "X-Cluster-Client-IP",
    "X-Forwarded",
    "Forwarded-For",
)


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


@router.get("/client-ip", response_model=ClientIpDebugOut)
def client_ip_debug(request: Request, _admin: AdminUser) -> ClientIpDebugOut:
    """Show client-IP headers on this request so operators can pick CLIENT_IP_HEADER.

    Admin-only: the response is about the caller's connection, not secrets, but
    it is an ops diagnostic rather than something every operator needs.
    """
    configured = get_settings().resolved_client_ip_header
    socket_peer = request.client.host if request.client else ""

    # Preserve candidate order; append a custom configured name if unknown.
    names: list[str] = list(_CLIENT_IP_HEADER_CANDIDATES)
    if configured and not any(n.lower() == configured.lower() for n in names):
        names.append(configured)

    # Surface any other request headers that look IP-related (without dumping
    # cookies / auth). Starlette header keys are lower-case.
    seen_lower = {n.lower() for n in names}
    for key in request.headers.keys():
        lower = key.lower()
        if lower in seen_lower:
            continue
        if (
            "forwarded" in lower
            or lower.endswith("-ip")
            or lower.endswith("_ip")
            or "client-ip" in lower
            or "real-ip" in lower
            or "connecting-ip" in lower
        ):
            names.append(key)
            seen_lower.add(lower)

    headers: list[ClientIpHeaderValue] = []
    for name in names:
        raw = request.headers.get(name)
        if raw is None:
            headers.append(ClientIpHeaderValue(name=name, present=False, value=None))
        else:
            headers.append(ClientIpHeaderValue(name=name, present=True, value=raw))

    return ClientIpDebugOut(
        configured_header=configured,
        socket_peer=socket_peer,
        resolved_client_ip=client_ip(request),
        headers=headers,
    )


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

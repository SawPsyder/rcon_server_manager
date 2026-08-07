"""Admin Palworld map share CRUD + public live map read (no auth)."""

from __future__ import annotations

import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.palworld import _client, summarize_game_data
from app.api.servers import get_server_or_404
from app.database import get_db
from app.deps import require_admin
from app.models import MapShare, Server, utcnow
from app.schemas import PalworldWorldOut
from app.services.palworld_api import GAMEDATA_DISABLED_CODE, PalworldApiError

admin_router = APIRouter(prefix="/api/servers", tags=["map-share"])
public_router = APIRouter(prefix="/api/public/maps", tags=["public-maps"])

SERVER_TYPE = "palworld"


class MapShareOut(BaseModel):
    token: str
    url_path: str
    created_at: datetime


class PublicMapMeta(BaseModel):
    token: str
    server_name: str
    server_type: str = "palworld"


def _url_path(token: str) -> str:
    return f"/share/m/{token}"


def _share_out(row: MapShare) -> MapShareOut:
    return MapShareOut(
        token=row.token,
        url_path=_url_path(row.token),
        created_at=row.created_at,
    )


def _require_palworld(db: Session, server_id: int) -> Server:
    server = get_server_or_404(db, server_id)
    if (server.server_type or "").strip().lower() != SERVER_TYPE:
        raise HTTPException(
            status_code=400,
            detail="Map sharing is only available for Palworld servers",
        )
    return server


def _get_share_by_token(db: Session, token: str) -> MapShare:
    row = (
        db.query(MapShare)
        .filter(MapShare.token == (token or "").strip())
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return row


@admin_router.get("/{server_id}/map-share", response_model=MapShareOut)
def get_map_share(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> MapShareOut:
    _require_palworld(db, server_id)
    row = db.query(MapShare).filter(MapShare.server_id == server_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="No share link")
    return _share_out(row)


@admin_router.post("/{server_id}/map-share", response_model=MapShareOut)
def create_or_get_map_share(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> MapShareOut:
    _require_palworld(db, server_id)
    row = db.query(MapShare).filter(MapShare.server_id == server_id).first()
    if row:
        return _share_out(row)

    for _ in range(5):
        token = secrets.token_urlsafe(32)
        if not db.query(MapShare.id).filter(MapShare.token == token).first():
            break
    else:
        raise HTTPException(status_code=500, detail="Could not allocate share token")

    row = MapShare(token=token, server_id=server_id, created_at=utcnow())
    db.add(row)
    db.commit()
    db.refresh(row)
    return _share_out(row)


@admin_router.delete("/{server_id}/map-share", status_code=204)
def revoke_map_share(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> Response:
    _require_palworld(db, server_id)
    row = db.query(MapShare).filter(MapShare.server_id == server_id).first()
    if row:
        db.delete(row)
        db.commit()
    return Response(status_code=204)


@public_router.get("/{token}/meta", response_model=PublicMapMeta)
def public_map_meta(
    token: str,
    db: Session = Depends(get_db),
) -> PublicMapMeta:
    row = _get_share_by_token(db, token)
    server = db.get(Server, row.server_id)
    if not server or (server.server_type or "").strip().lower() != SERVER_TYPE:
        raise HTTPException(status_code=404, detail="Not found")
    return PublicMapMeta(
        token=row.token,
        server_name=server.name or "Server",
        server_type="palworld",
    )


@public_router.get("/{token}/world", response_model=PalworldWorldOut)
def public_map_world(
    token: str,
    db: Session = Depends(get_db),
) -> PalworldWorldOut:
    """Live world snapshot for a shared map (same reduction as admin /world)."""
    row = _get_share_by_token(db, token)
    server = db.get(Server, row.server_id)
    if not server or (server.server_type or "").strip().lower() != SERVER_TYPE:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        _, client = _client(db, row.server_id)
        payload = client.game_data()
    except PalworldApiError as exc:
        if exc.code == GAMEDATA_DISABLED_CODE:
            return PalworldWorldOut(enabled=False, hint=str(exc))
        if "password" in str(exc).lower() or "auth" in str(exc).lower():
            raise HTTPException(status_code=502, detail="Map source unavailable") from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="Map source unavailable") from exc
    return summarize_game_data(payload)

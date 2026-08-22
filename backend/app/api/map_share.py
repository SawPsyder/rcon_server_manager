"""Admin Palworld map share CRUD + public live map read (no auth)."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.palworld import _client, summarize_game_data
from app.api.servers import get_server_or_404
from app.config import get_settings
from app.database import get_db
from app.deps import client_ip
from app.models import MapShare, Server, utcnow
from app.schemas import PalworldWorldOut, PalworldWorldPlayer
from app.services import rate_limit
from app.services.palworld_api import GAMEDATA_DISABLED_CODE, PalworldApiError

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/api/servers", tags=["map-share"])
public_router = APIRouter(prefix="/api/public/maps", tags=["public-maps"])

SERVER_TYPE = "palworld"

# Collapse overlay tabs + scrapers onto one game-data fetch per server.
_WORLD_CACHE_TTL = 5.0
_world_cache_lock = threading.Lock()
_world_cache: dict[int, tuple[float, PalworldWorldOut]] = {}
_world_fetch_locks: dict[int, threading.Lock] = {}

PUBLIC_SHARE_LIMIT = 40
PUBLIC_SHARE_WINDOW = 60


def _limit_public(request: Request) -> None:
    ip = client_ip(request) or "unknown"
    if not rate_limit.check(f"public-share:ip:{ip}", PUBLIC_SHARE_LIMIT, PUBLIC_SHARE_WINDOW):
        raise HTTPException(status_code=429, detail="Too many requests")


def _opaque_player_id(server_id: int, user_id: str) -> str:
    """Stable marker id that is not a platform account id.

    Empty user_id stays empty. The digest is keyed by secret + server so the
    same Steam id is not a global correlator across installs or servers.
    """
    uid = (user_id or "").strip()
    if not uid:
        return ""
    digest = hmac.new(
        get_settings().secret_key.encode("utf-8"),
        f"map-share:{server_id}:{uid}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:20]
    return f"p_{digest}"


def redact_public_world(world: PalworldWorldOut, *, server_id: int) -> PalworldWorldOut:
    """Replace platform account ids with opaque per-share marker ids."""
    players: list[PalworldWorldPlayer] = []
    for player in world.players:
        players.append(
            player.model_copy(update={"user_id": _opaque_player_id(server_id, player.user_id)})
        )
    return world.model_copy(update={"players": players})


def _cached_world(server_id: int) -> PalworldWorldOut | None:
    now = time.time()
    with _world_cache_lock:
        hit = _world_cache.get(server_id)
        if hit and now - hit[0] < _WORLD_CACHE_TTL:
            return hit[1]
    return None


def _store_world(server_id: int, world: PalworldWorldOut) -> None:
    with _world_cache_lock:
        _world_cache[server_id] = (time.time(), world)
        if len(_world_cache) > 128:
            oldest = min(_world_cache, key=lambda k: _world_cache[k][0])
            del _world_cache[oldest]


def _fetch_lock(server_id: int) -> threading.Lock:
    with _world_cache_lock:
        lock = _world_fetch_locks.get(server_id)
        if lock is None:
            lock = threading.Lock()
            _world_fetch_locks[server_id] = lock
        return lock


def _unavailable(server_id: int, exc: BaseException) -> HTTPException:
    logger.warning("Public map world fetch failed for server %s: %s", server_id, exc)
    return HTTPException(status_code=502, detail="Map source unavailable")


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
    request: Request,
    db: Session = Depends(get_db),
) -> PublicMapMeta:
    _limit_public(request)
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
    request: Request,
    db: Session = Depends(get_db),
) -> PalworldWorldOut:
    """Live world snapshot for a shared map (nicknames + positions, no account ids)."""
    _limit_public(request)
    row = _get_share_by_token(db, token)
    server = db.get(Server, row.server_id)
    if not server or (server.server_type or "").strip().lower() != SERVER_TYPE:
        raise HTTPException(status_code=404, detail="Not found")

    cached = _cached_world(row.server_id)
    if cached is not None:
        return cached

    lock = _fetch_lock(row.server_id)
    with lock:
        cached = _cached_world(row.server_id)
        if cached is not None:
            return cached
        try:
            _, client = _client(db, row.server_id)
            payload = client.game_data()
        except PalworldApiError as exc:
            if exc.code == GAMEDATA_DISABLED_CODE:
                world = PalworldWorldOut(enabled=False, hint=str(exc))
                _store_world(row.server_id, world)
                return world
            raise _unavailable(row.server_id, exc) from exc
        except HTTPException as exc:
            # _client raises 400s ("no admin password", TLS, auth). Do not
            # surface those details — or those status codes — on a public URL.
            raise _unavailable(row.server_id, exc) from exc
        except Exception as exc:  # noqa: BLE001
            raise _unavailable(row.server_id, exc) from exc

        world = redact_public_world(summarize_game_data(payload), server_id=row.server_id)
        _store_world(row.server_id, world)
        return world

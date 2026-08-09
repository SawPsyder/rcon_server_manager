"""Admin chart share CRUD + public chart read (no auth)."""

from __future__ import annotations

import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.servers import get_server_or_404
from app.api.stats import PublicPlayerStatsOut, build_player_stats, to_public_stats
from app.database import get_db
from app.models import ChartShare, Server, utcnow

admin_router = APIRouter(prefix="/api/servers", tags=["chart-share"])
public_router = APIRouter(prefix="/api/public/charts", tags=["public-charts"])


class ChartShareOut(BaseModel):
    token: str
    url_path: str
    created_at: datetime


class PublicChartMeta(BaseModel):
    token: str
    server_name: str


def _url_path(token: str) -> str:
    return f"/share/c/{token}"


def _share_out(row: ChartShare) -> ChartShareOut:
    return ChartShareOut(
        token=row.token,
        url_path=_url_path(row.token),
        created_at=row.created_at,
    )


def _get_share_by_token(db: Session, token: str) -> ChartShare:
    row = (
        db.query(ChartShare)
        .filter(ChartShare.token == (token or "").strip())
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return row


@admin_router.get("/{server_id}/chart-share", response_model=ChartShareOut)
def get_chart_share(
    server_id: int,
    db: Session = Depends(get_db),
) -> ChartShareOut:
    get_server_or_404(db, server_id)
    row = db.query(ChartShare).filter(ChartShare.server_id == server_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="No share link")
    return _share_out(row)


@admin_router.post("/{server_id}/chart-share", response_model=ChartShareOut)
def create_or_get_chart_share(
    server_id: int,
    db: Session = Depends(get_db),
) -> ChartShareOut:
    get_server_or_404(db, server_id)
    row = db.query(ChartShare).filter(ChartShare.server_id == server_id).first()
    if row:
        return _share_out(row)

    # Cryptic unguessable token (url-safe, ~43 chars)
    for _ in range(5):
        token = secrets.token_urlsafe(32)
        if not db.query(ChartShare.id).filter(ChartShare.token == token).first():
            break
    else:
        raise HTTPException(status_code=500, detail="Could not allocate share token")

    row = ChartShare(token=token, server_id=server_id, created_at=utcnow())
    db.add(row)
    db.commit()
    db.refresh(row)
    return _share_out(row)


@admin_router.delete("/{server_id}/chart-share", status_code=204)
def revoke_chart_share(
    server_id: int,
    db: Session = Depends(get_db),
) -> Response:
    get_server_or_404(db, server_id)
    row = db.query(ChartShare).filter(ChartShare.server_id == server_id).first()
    if row:
        db.delete(row)
        db.commit()
    return Response(status_code=204)


@public_router.get("/{token}/meta", response_model=PublicChartMeta)
def public_chart_meta(
    token: str,
    db: Session = Depends(get_db),
) -> PublicChartMeta:
    row = _get_share_by_token(db, token)
    server = db.get(Server, row.server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Not found")
    return PublicChartMeta(token=row.token, server_name=server.name or "Server")


@public_router.get("/{token}/stats", response_model=PublicPlayerStatsOut)
def public_chart_stats(
    token: str,
    range: str = Query(default="24h", pattern="^(24h|7d|30d|180d|1y)$"),
    db: Session = Depends(get_db),
) -> PublicPlayerStatsOut:
    row = _get_share_by_token(db, token)
    if not db.get(Server, row.server_id):
        raise HTTPException(status_code=404, detail="Not found")
    # Count-only: no server_id, no player names / roster
    full = build_player_stats(db, row.server_id, range, include_names=False)
    return to_public_stats(full)

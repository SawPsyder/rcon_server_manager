"""Player count history API for charts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.servers import get_server_or_404
from app.database import get_db
from app.deps import require_admin
from app.models import PlayerCountSample

router = APIRouter(prefix="/api/servers", tags=["stats"])

RANGE_DELTAS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "180d": timedelta(days=180),
    "1y": timedelta(days=365),
}

# Soft cap on points returned to the chart (downsample if denser)
MAX_CHART_POINTS = 480


class PlayerStatPoint(BaseModel):
    t: datetime
    players: float
    max_players: float
    online: bool


class PlayerStatsOut(BaseModel):
    server_id: int
    range: str
    from_time: datetime
    to_time: datetime
    sample_count: int
    points: list[PlayerStatPoint]
    current_players: int | None = None
    peak_players: int | None = None
    avg_players: float | None = None


def _downsample(
    rows: list[PlayerCountSample],
    max_points: int = MAX_CHART_POINTS,
) -> list[PlayerStatPoint]:
    if not rows:
        return []
    if len(rows) <= max_points:
        return [
            PlayerStatPoint(
                t=r.recorded_at,
                players=float(r.players),
                max_players=float(r.max_players),
                online=bool(r.online),
            )
            for r in rows
        ]

    # Average into fixed buckets
    bucket_count = max_points
    n = len(rows)
    out: list[PlayerStatPoint] = []
    for i in range(bucket_count):
        start = int(i * n / bucket_count)
        end = int((i + 1) * n / bucket_count)
        chunk = rows[start:end]
        if not chunk:
            continue
        players_avg = sum(c.players for c in chunk) / len(chunk)
        max_avg = sum(c.max_players for c in chunk) / len(chunk)
        online = any(c.online for c in chunk)
        # Use middle timestamp of bucket
        mid = chunk[len(chunk) // 2].recorded_at
        out.append(
            PlayerStatPoint(
                t=mid,
                players=round(players_avg, 2),
                max_players=round(max_avg, 2),
                online=online,
            )
        )
    return out


@router.get("/{server_id}/player-stats", response_model=PlayerStatsOut)
def player_stats(
    server_id: int,
    range: str = Query(default="24h", pattern="^(24h|7d|30d|180d|1y)$"),
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> PlayerStatsOut:
    get_server_or_404(db, server_id)
    if range not in RANGE_DELTAS:
        raise HTTPException(status_code=400, detail="Invalid range")

    now = datetime.now(timezone.utc)
    from_time = now - RANGE_DELTAS[range]

    rows = (
        db.query(PlayerCountSample)
        .filter(
            PlayerCountSample.server_id == server_id,
            PlayerCountSample.recorded_at >= from_time,
            PlayerCountSample.recorded_at <= now,
        )
        .order_by(PlayerCountSample.recorded_at.asc())
        .all()
    )

    points = _downsample(rows)
    online_rows = [r for r in rows if r.online]
    peak = max((r.players for r in online_rows), default=None)
    avg = (
        round(sum(r.players for r in online_rows) / len(online_rows), 2)
        if online_rows
        else None
    )
    current = rows[-1].players if rows else None

    return PlayerStatsOut(
        server_id=server_id,
        range=range,
        from_time=from_time,
        to_time=now,
        sample_count=len(rows),
        points=points,
        current_players=current,
        peak_players=peak,
        avg_players=avg,
    )

"""Player count history and map popularity APIs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.servers import get_server_or_404
from app.database import get_db
from app.models import MapConfig, PlayerCountSample, Server
from app.server_types import DEFAULT_SERVER_TYPE
from app.services.map_stats import (
    DEFAULT_MIN_ACTIVE_MINUTES,
    aggregate_map_stats,
)
from app.services.roster import roster_from_json, roster_names

router = APIRouter(prefix="/api/servers", tags=["stats"])

RANGE_DELTAS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "180d": timedelta(days=180),
    "1y": timedelta(days=365),
}

VALID_RANGES = frozenset(RANGE_DELTAS.keys())

# Soft cap on points returned to the chart (downsample if denser)
MAX_CHART_POINTS = 480


class PlayerStatPoint(BaseModel):
    t: datetime
    players: float
    max_players: float
    online: bool
    # Names present at this sample (admin only; public omits)
    player_names: list[str] = Field(default_factory=list)
    # None where the server type reports no tick rate, or was offline/paused
    tick_rate: float | None = None


class PlayerStatsOut(BaseModel):
    server_id: int
    range: str
    from_time: datetime
    to_time: datetime
    points: list[PlayerStatPoint]
    current_players: int | None = None
    peak_players: int | None = None
    avg_players: float | None = None
    # Tick-rate summary; all None when nothing in range reported one
    current_tick_rate: float | None = None
    min_tick_rate: float | None = None
    avg_tick_rate: float | None = None


class PublicPlayerStatPoint(BaseModel):
    """Count-only point for public share links (no roster / names)."""

    t: datetime
    players: float
    max_players: float
    online: bool


class PublicPlayerStatsOut(BaseModel):
    """Public share payload: counts only - no server_id, names, or sample_count."""

    range: str
    from_time: datetime
    to_time: datetime
    points: list[PublicPlayerStatPoint]
    current_players: int | None = None
    peak_players: int | None = None
    avg_players: float | None = None


def _point_from_row(r: PlayerCountSample, *, include_names: bool = True) -> PlayerStatPoint:
    names = (
        roster_names(roster_from_json(getattr(r, "roster_json", None)))
        if include_names
        else []
    )
    tick = getattr(r, "tick_rate", None)
    return PlayerStatPoint(
        t=r.recorded_at,
        players=float(r.players),
        max_players=float(r.max_players),
        online=bool(r.online),
        player_names=names,
        tick_rate=float(tick) if tick is not None else None,
    )


def _downsample(
    rows: list[PlayerCountSample],
    max_points: int = MAX_CHART_POINTS,
    *,
    include_names: bool = True,
) -> list[PlayerStatPoint]:
    if not rows:
        return []
    if len(rows) <= max_points:
        return [_point_from_row(r, include_names=include_names) for r in rows]

    # Pick a representative sample per bucket (mid) so roster matches the point
    bucket_count = max_points
    n = len(rows)
    out: list[PlayerStatPoint] = []
    for i in range(bucket_count):
        start = int(i * n / bucket_count)
        end = int((i + 1) * n / bucket_count)
        chunk = rows[start:end]
        if not chunk:
            continue
        mid = chunk[len(chunk) // 2]
        out.append(_point_from_row(mid, include_names=include_names))
    return out


def build_player_stats(
    db: Session,
    server_id: int,
    range_key: str,
    *,
    include_names: bool = True,
) -> PlayerStatsOut:
    """Shared builder for admin and public chart endpoints."""
    if range_key not in RANGE_DELTAS:
        raise HTTPException(status_code=400, detail="Invalid range")

    now = datetime.now(timezone.utc)
    from_time = now - RANGE_DELTAS[range_key]

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

    points = _downsample(rows, include_names=include_names)
    online_rows = [r for r in rows if r.online]
    peak = max((r.players for r in online_rows), default=None)
    avg = (
        round(sum(r.players for r in online_rows) / len(online_rows), 2)
        if online_rows
        else None
    )
    current = rows[-1].players if rows else None

    # Summarise from every row in range, not the downsampled points, so a dip
    # that got averaged out of the line still shows up in the numbers.
    ticks = [
        float(r.tick_rate)
        for r in rows
        if getattr(r, "tick_rate", None) is not None
    ]

    return PlayerStatsOut(
        server_id=server_id,
        range=range_key,
        from_time=from_time,
        to_time=now,
        points=points,
        current_players=current,
        peak_players=peak,
        avg_players=avg,
        current_tick_rate=round(ticks[-1], 1) if ticks else None,
        min_tick_rate=round(min(ticks), 1) if ticks else None,
        avg_tick_rate=round(sum(ticks) / len(ticks), 1) if ticks else None,
    )


def to_public_stats(full: PlayerStatsOut) -> PublicPlayerStatsOut:
    """Strip internal id, roster, and sample_count for public responses."""
    return PublicPlayerStatsOut(
        range=full.range,
        from_time=full.from_time,
        to_time=full.to_time,
        points=[
            PublicPlayerStatPoint(
                t=p.t,
                players=p.players,
                max_players=p.max_players,
                online=p.online,
            )
            for p in full.points
        ],
        current_players=full.current_players,
        peak_players=full.peak_players,
        avg_players=full.avg_players,
    )


@router.get("/{server_id}/player-stats", response_model=PlayerStatsOut)
def player_stats(
    server_id: int,
    range: str = Query(default="24h", pattern="^(24h|7d|30d|180d|1y)$"),
    db: Session = Depends(get_db),
) -> PlayerStatsOut:
    get_server_or_404(db, server_id)
    return build_player_stats(db, server_id, range)


class MapStatRowOut(BaseModel):
    map_name: str
    gamemode: str = ""
    alias: str | None = None
    player_minutes: float
    active_minutes: float
    avg_players: float
    peak_players: int
    active_samples: int
    qualified: bool


class MapStatsOut(BaseModel):
    server_id: int
    range: str
    from_time: datetime
    to_time: datetime
    min_active_minutes: float
    combine_gamemodes: bool
    # Earliest sample in range that has a map tag (null if none yet)
    data_since: datetime | None = None
    rows: list[MapStatRowOut] = Field(default_factory=list)


def _alias_lookup(db: Session, server: Server) -> dict[str, str]:
    st = getattr(server, "server_type", None) or DEFAULT_SERVER_TYPE
    rows = (
        db.query(MapConfig.map_name, MapConfig.alias)
        .filter(MapConfig.server_type == st)
        .all()
    )
    out: dict[str, str] = {}
    for map_name, alias in rows:
        name = (map_name or "").strip()
        label = (alias or "").strip()
        if name and label:
            out[name] = label
    return out


@router.get("/{server_id}/map-stats", response_model=MapStatsOut)
def map_stats(
    server_id: int,
    range: str = Query(default="7d", pattern="^(24h|7d|30d|180d|1y)$"),
    combine_gamemodes: bool = Query(default=False),
    min_active_minutes: float = Query(
        default=DEFAULT_MIN_ACTIVE_MINUTES,
        ge=0,
        le=24 * 60,
    ),
    db: Session = Depends(get_db),
) -> MapStatsOut:
    """
    Player-weighted map popularity for the selected window.

    Empty samples are excluded. Default sort prefers maps with at least
    ``min_active_minutes`` of non-empty exposure, ranked by avg concurrent
    players (not cycle frequency or admin travel).
    """
    if range not in RANGE_DELTAS:
        raise HTTPException(status_code=400, detail="Invalid range")

    server = get_server_or_404(db, server_id)
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

    result = aggregate_map_stats(
        rows,
        server_id=server_id,
        range_key=range,
        from_time=from_time,
        to_time=now,
        combine_gamemodes=combine_gamemodes,
        min_active_minutes=min_active_minutes,
        alias_by_map=_alias_lookup(db, server),
    )

    return MapStatsOut(
        server_id=result.server_id,
        range=result.range,
        from_time=result.from_time,
        to_time=result.to_time,
        min_active_minutes=result.min_active_minutes,
        combine_gamemodes=result.combine_gamemodes,
        data_since=result.data_since,
        rows=[
            MapStatRowOut(
                map_name=r.map_name,
                gamemode=r.gamemode,
                alias=r.alias,
                player_minutes=r.player_minutes,
                active_minutes=r.active_minutes,
                avg_players=r.avg_players,
                peak_players=r.peak_players,
                active_samples=r.active_samples,
                qualified=r.qualified,
            )
            for r in result.rows
        ],
    )

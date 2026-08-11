"""Global player leaderboard (playtime across servers)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import CurrentUser, granted_server_ids
from app.schemas import PlayerLeaderboardOut
from app.services.player_leaderboard import SortKey, build_player_leaderboard

router = APIRouter(prefix="/api/players", tags=["players"])


@router.get("", response_model=PlayerLeaderboardOut)
def list_players(
    user: CurrentUser,
    db: Session = Depends(get_db),
    q: str = Query("", max_length=120),
    server_id: int | None = Query(None, ge=1),
    sort: SortKey = Query("total_seconds"),
    order: Literal["asc", "desc"] = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
) -> PlayerLeaderboardOut:
    """Players ever seen on servers the caller can access, ranked by playtime.

    Without ``server_id``, rank is overall (sum of ``total_seconds`` across
    granted servers). With a filter, only players who appeared on that server
    are listed and rank is that server's playtime board.
    """
    data = build_player_leaderboard(
        db,
        allowed_server_ids=granted_server_ids(db, user),
        server_id=server_id,
        q=q,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )
    return PlayerLeaderboardOut.model_validate(data)

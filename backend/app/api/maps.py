from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import MapConfig
from app.schemas import MapOut
from app.server_types import DEFAULT_SERVER_TYPE, is_known_type
from app.server_types.sandstorm import GAMEMODE_LABELS, map_gamemodes, map_lightings

router = APIRouter(prefix="/api", tags=["maps"])


def _map_out(row: MapConfig) -> MapOut:
    gms = map_gamemodes(row)
    return MapOut(
        id=row.id,
        alias=row.alias,
        map_name=row.map_name,
        mod_id=row.mod_id,
        day=row.day,
        night=row.night,
        self_added=row.self_added,
        server_type=getattr(row, "server_type", None) or DEFAULT_SERVER_TYPE,
        gamemodes=gms,
        lightings=map_lightings(row),
    )


@router.get("/maps", response_model=list[MapOut])
def list_maps(
    server_type: str | None = None,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> list[MapOut]:
    q = db.query(MapConfig)
    if server_type:
        st = server_type.strip().lower()
        if not is_known_type(st):
            raise HTTPException(status_code=400, detail=f"Unknown server type: {server_type}")
        q = q.filter(MapConfig.server_type == st)
    rows = q.order_by(MapConfig.self_added.asc(), MapConfig.alias.asc()).all()
    return [_map_out(r) for r in rows]


@router.get("/gamemode-labels")
def gamemode_labels(
    server_type: str = DEFAULT_SERVER_TYPE,
    _admin: str = Depends(require_admin),
) -> dict[str, str]:
    st = server_type.strip().lower() or DEFAULT_SERVER_TYPE
    if st == "sandstorm":
        return GAMEMODE_LABELS
    return {}

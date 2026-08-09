from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import MapConfig
from app.schemas import MapOut
from app.server_types import DEFAULT_SERVER_TYPE, get_adapter, is_known_type

router = APIRouter(prefix="/api", tags=["maps"])


def _map_out(row: MapConfig) -> MapOut:
    server_type = getattr(row, "server_type", None) or DEFAULT_SERVER_TYPE
    try:
        adapter = get_adapter(server_type)
    except KeyError:
        adapter = get_adapter(DEFAULT_SERVER_TYPE)
    return MapOut(
        id=row.id,
        alias=row.alias,
        map_name=row.map_name,
        mod_id=row.mod_id,
        day=row.day,
        night=row.night,
        self_added=row.self_added,
        server_type=server_type,
        gamemodes=adapter.map_gamemodes(row),
        lightings=adapter.map_lightings(row),
    )


@router.get("/maps", response_model=list[MapOut])
def list_maps(
    server_type: str | None = None,
    db: Session = Depends(get_db),
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
) -> dict[str, str]:
    st = server_type.strip().lower() or DEFAULT_SERVER_TYPE
    try:
        return get_adapter(st).gamemode_labels()
    except KeyError:
        return {}

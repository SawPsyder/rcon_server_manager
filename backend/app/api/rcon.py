from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.servers import get_rcon_password, get_server_or_404
from app.config import get_settings
from app.database import get_db
from app.deps import require_admin
from app.models import CommandHistory, MapConfig
from app.schemas import (
    AdminSayRequest,
    CommandHistoryOut,
    PlayerActionRequest,
    RconCommandRequest,
    RconCommandResponse,
    TravelPreview,
    TravelRequest,
    UnbanRequest,
)
from app.server_types import DEFAULT_SERVER_TYPE, get_adapter
from app.server_types.sandstorm import build_travel_command, map_gamemodes
from app.services.rcon import RconError, run_rcon

router = APIRouter(tags=["rcon"])


def _require_feature(server, feature: str) -> None:
    try:
        adapter = get_adapter(server.server_type or DEFAULT_SERVER_TYPE)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown server type: {server.server_type}") from exc
    if not getattr(adapter.info.features, feature, False):
        raise HTTPException(
            status_code=400,
            detail=f"Feature '{feature}' is not supported for server type '{adapter.info.id}'",
        )


def _exec(db: Session, server_id: int, command: str) -> RconCommandResponse:
    settings = get_settings()
    server = get_server_or_404(db, server_id)
    password = get_rcon_password(server)
    if not password:
        raise HTTPException(status_code=400, detail="Server has no RCON password configured")
    try:
        adapter = get_adapter(server.server_type or DEFAULT_SERVER_TYPE)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown server type: {server.server_type}") from exc
    try:
        response = run_rcon(
            server.host,
            server.rcon_port,
            password,
            command,
            timeout=settings.rcon_timeout,
            allowed_prefixes=adapter.allowed_rcon_prefixes,
        )
        db.add(
            CommandHistory(
                server_id=server.id,
                command=command,
                response=(response or "")[:4000],
            )
        )
        db.commit()
        return RconCommandResponse(command=command, response=response or "", ok=True)
    except RconError as exc:
        return RconCommandResponse(command=command, response="", ok=False, error=str(exc))


@router.post("/api/servers/{server_id}/rcon", response_model=RconCommandResponse)
def rcon_command(
    server_id: int,
    body: RconCommandRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> RconCommandResponse:
    return _exec(db, server_id, body.command.strip())


@router.post("/api/servers/{server_id}/say", response_model=RconCommandResponse)
def admin_say(
    server_id: int,
    body: AdminSayRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> RconCommandResponse:
    server = get_server_or_404(db, server_id)
    _require_feature(server, "admin_say")
    return _exec(db, server_id, f"say {body.message.strip()}")


@router.post("/api/servers/{server_id}/players/kick", response_model=RconCommandResponse)
def kick_player(
    server_id: int,
    body: PlayerActionRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> RconCommandResponse:
    server = get_server_or_404(db, server_id)
    _require_feature(server, "kick_ban")
    reason = body.reason.strip() or "Kicked by admin"
    cmd = f'kick "{body.player_name}" "{reason}"'
    return _exec(db, server_id, cmd)


@router.post("/api/servers/{server_id}/players/ban", response_model=RconCommandResponse)
def ban_player(
    server_id: int,
    body: PlayerActionRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> RconCommandResponse:
    server = get_server_or_404(db, server_id)
    _require_feature(server, "kick_ban")
    minutes = body.ban_minutes or 60
    reason = body.reason.strip() or "Banned by admin"
    cmd = f'ban "{body.player_name}" "{minutes}" "{reason}"'
    return _exec(db, server_id, cmd)


@router.post("/api/servers/{server_id}/players/permban", response_model=RconCommandResponse)
def permban_player(
    server_id: int,
    body: PlayerActionRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> RconCommandResponse:
    server = get_server_or_404(db, server_id)
    _require_feature(server, "kick_ban")
    reason = body.reason.strip() or "Permanently banned by admin"
    cmd = f'permban "{body.player_name}" "{reason}"'
    return _exec(db, server_id, cmd)


@router.post("/api/servers/{server_id}/players/unban", response_model=RconCommandResponse)
def unban_player(
    server_id: int,
    body: UnbanRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> RconCommandResponse:
    server = get_server_or_404(db, server_id)
    _require_feature(server, "kick_ban")
    return _exec(db, server_id, f'unban "{body.net_id.strip()}"')


@router.post("/api/servers/{server_id}/travel", response_model=RconCommandResponse)
def travel(
    server_id: int,
    body: TravelRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> RconCommandResponse:
    server = get_server_or_404(db, server_id)
    _require_feature(server, "map_travel")
    preview = _build_travel(db, body, server.server_type or DEFAULT_SERVER_TYPE)
    if not body.execute:
        return RconCommandResponse(command=preview.command, response=preview.command, ok=True)
    return _exec(db, server_id, preview.command)


@router.post("/api/travel/preview", response_model=TravelPreview)
def travel_preview(
    body: TravelRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> TravelPreview:
    return _build_travel(db, body, DEFAULT_SERVER_TYPE)


def _build_travel(db: Session, body: TravelRequest, server_type: str) -> TravelPreview:
    map_row = db.get(MapConfig, body.map_id)
    if not map_row:
        raise HTTPException(status_code=404, detail="Map not found")
    map_type = getattr(map_row, "server_type", None) or DEFAULT_SERVER_TYPE
    if map_type != server_type:
        raise HTTPException(status_code=400, detail="Map is not valid for this server type")
    gamemodes = map_gamemodes(map_row)
    if body.gamemode_key not in gamemodes:
        raise HTTPException(status_code=400, detail=f"Gamemode '{body.gamemode_key}' not available for this map")
    scenario = gamemodes[body.gamemode_key]
    command = build_travel_command(
        map_name=map_row.map_name,
        scenario=scenario,
        lighting=body.lighting,
        gamemode_key=body.gamemode_key,
    )
    return TravelPreview(
        command=command,
        map_alias=map_row.alias,
        map_name=map_row.map_name,
        scenario=scenario,
        gamemode_key=body.gamemode_key,
        lighting=body.lighting,
    )


@router.get("/api/servers/{server_id}/history", response_model=list[CommandHistoryOut])
def command_history(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
    limit: int = 50,
) -> list[CommandHistoryOut]:
    get_server_or_404(db, server_id)
    rows = (
        db.query(CommandHistory)
        .filter(CommandHistory.server_id == server_id)
        .order_by(CommandHistory.created_at.desc())
        .limit(min(limit, 200))
        .all()
    )
    return rows

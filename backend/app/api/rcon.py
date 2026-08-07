from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.servers import get_rcon_password, get_server_or_404
from app.config import get_settings
from app.database import get_db
from app.deps import require_admin
from app.models import CommandHistory, MapConfig
from app.schemas import (
    AdminSayRequest,
    BanEntryOut,
    BanListOut,
    CommandHistoryOut,
    PlayerActionRequest,
    RconCommandRequest,
    RconCommandResponse,
    TravelPreview,
    TravelRequest,
    UnbanRequest,
)
from app.server_types import DEFAULT_SERVER_TYPE, get_adapter
from app.services.ban_cache import (
    load_cached_bans,
    rebuild_local_bans,
    remove_cached_ban,
    replace_server_bans,
)
from app.services.errors import CommandError
from app.services.identity import steam_api_configured
from app.services.player_records import log_player_action
from app.services.server_options import load_options

router = APIRouter(tags=["rcon"])


def _adapter_for(server):
    try:
        return get_adapter(server.server_type or DEFAULT_SERVER_TYPE)
    except KeyError as exc:
        raise HTTPException(
            status_code=400, detail=f"Unknown server type: {server.server_type}"
        ) from exc


def _require_feature(server, feature: str):
    """Guard a feature-gated endpoint; returns the resolved adapter."""
    adapter = _adapter_for(server)
    if not getattr(adapter.info.features, feature, False):
        raise HTTPException(
            status_code=400,
            detail=f"Feature '{feature}' is not supported for server type '{adapter.info.id}'",
        )
    return adapter


def _exec(db: Session, server_id: int, command: str) -> RconCommandResponse:
    settings = get_settings()
    server = get_server_or_404(db, server_id)
    password = get_rcon_password(server)
    try:
        adapter = get_adapter(server.server_type or DEFAULT_SERVER_TYPE)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown server type: {server.server_type}") from exc
    if not password:
        raise HTTPException(
            status_code=400,
            detail=f"Server has no {adapter.info.secret_label.lower()} configured",
        )
    try:
        response = adapter.execute_command(
            server.host,
            port=server.rcon_port,
            secret=password,
            command=command,
            timeout=settings.rcon_timeout,
            options=load_options(server),
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
    except CommandError as exc:
        return RconCommandResponse(command=command, response="", ok=False, error=str(exc))


def _log_moderation(
    db: Session,
    server,
    *,
    action: str,
    result: RconCommandResponse,
    player_name: str = "",
    net_id: str = "",
    reason: str = "",
    detail: str = "",
) -> None:
    try:
        log_player_action(
            db,
            server=server,
            action=action,
            net_id=net_id,
            player_name=player_name,
            reason=reason,
            detail=detail,
            ok=result.ok,
            error=result.error or "",
        )
        db.commit()
    except Exception:
        db.rollback()


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
    adapter = _require_feature(server, "admin_say")
    return _exec(db, server_id, adapter.build_say_command(body.message.strip()))


@router.post("/api/servers/{server_id}/players/kick", response_model=RconCommandResponse)
def kick_player(
    server_id: int,
    body: PlayerActionRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> RconCommandResponse:
    server = get_server_or_404(db, server_id)
    adapter = _require_feature(server, "kick_ban")
    reason = body.reason.strip() or "Kicked by admin"
    cmd = adapter.build_kick_command(
        player_name=body.player_name, net_id=body.net_id, reason=reason
    )
    result = _exec(db, server_id, cmd)
    _log_moderation(
        db,
        server,
        action="kick",
        result=result,
        player_name=body.player_name,
        net_id=body.net_id,
        reason=reason,
    )
    return result


@router.post("/api/servers/{server_id}/players/ban", response_model=RconCommandResponse)
def ban_player(
    server_id: int,
    body: PlayerActionRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> RconCommandResponse:
    server = get_server_or_404(db, server_id)
    # Games without timed bans (e.g. Palworld) only expose permanent bans.
    adapter = _require_feature(server, "timed_ban")
    minutes = body.ban_minutes or 60
    reason = body.reason.strip() or "Banned by admin"
    cmd = adapter.build_ban_command(
        player_name=body.player_name, net_id=body.net_id, reason=reason, minutes=minutes
    )
    result = _exec(db, server_id, cmd)
    _log_moderation(
        db,
        server,
        action="ban",
        result=result,
        player_name=body.player_name,
        net_id=body.net_id,
        reason=reason,
        detail=f"{minutes} minutes",
    )
    return result


@router.post("/api/servers/{server_id}/players/permban", response_model=RconCommandResponse)
def permban_player(
    server_id: int,
    body: PlayerActionRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> RconCommandResponse:
    server = get_server_or_404(db, server_id)
    adapter = _require_feature(server, "kick_ban")
    reason = body.reason.strip() or "Permanently banned by admin"
    cmd = adapter.build_permban_command(
        player_name=body.player_name, net_id=body.net_id, reason=reason
    )
    result = _exec(db, server_id, cmd)
    _log_moderation(
        db,
        server,
        action="permban",
        result=result,
        player_name=body.player_name,
        net_id=body.net_id,
        reason=reason,
        detail="permanent",
    )
    return result


@router.post("/api/servers/{server_id}/players/unban", response_model=RconCommandResponse)
def unban_player(
    server_id: int,
    body: UnbanRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> RconCommandResponse:
    server = get_server_or_404(db, server_id)
    adapter = _require_feature(server, "kick_ban")
    net_id = body.net_id.strip()
    result = _exec(db, server_id, adapter.build_unban_command(net_id))
    _log_moderation(
        db,
        server,
        action="unban",
        result=result,
        net_id=net_id,
        reason="",
    )
    if result.ok:
        try:
            remove_cached_ban(db, server_id, net_id)
            db.commit()
        except Exception:
            db.rollback()
    return result


def _ban_list_out(server_id: int, cached: dict, *, from_cache: bool, ok: bool | None = None, error: str | None = None) -> BanListOut:
    return BanListOut(
        server_id=server_id,
        bans=[BanEntryOut(**b) for b in cached.get("bans") or []],
        raw=cached.get("raw") or "",
        ok=bool(cached.get("ok", True)) if ok is None else ok,
        error=error if error is not None else cached.get("error"),
        steam_lookup_enabled=steam_api_configured(),
        from_cache=from_cache,
        fetched_at=cached.get("fetched_at"),
        page=int(cached.get("page") or 1),
        page_size=int(cached.get("page_size") or 25),
        total=int(cached.get("total") or 0),
        total_pages=int(cached.get("total_pages") or 1),
    )


@router.get("/api/servers/{server_id}/bans", response_model=BanListOut)
def list_bans(
    server_id: int,
    refresh: bool = False,
    page: int = 1,
    page_size: int = 25,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> BanListOut:
    """
    Return structured bans for a server (paginated).

    Default: serve DB cache (instant). Use ?refresh=true to run live listbans
    and replace the cache. Names are resolved via identity_cache / Steam API
    for the current page only.
    """
    server = get_server_or_404(db, server_id)
    adapter = _require_feature(server, "ban_list")
    page = max(1, page)
    page_size = min(100, max(1, page_size))

    if adapter.info.ban_list_source == "local":
        # Nothing live to query - rebuild from our own moderation history so a
        # ban or unban issued elsewhere in the app is reflected immediately.
        try:
            rebuild_local_bans(db, server_id)
            db.commit()
        except Exception:
            db.rollback()
        cached = load_cached_bans(db, server_id, page=page, page_size=page_size)
        return _ban_list_out(server_id, cached, from_cache=True)

    if not refresh:
        cached = load_cached_bans(db, server_id, page=page, page_size=page_size)
        if cached.get("has_snapshot"):
            return _ban_list_out(server_id, cached, from_cache=True)
        # No cache yet - fall through to live fetch once

    result = _exec(db, server_id, "listbans")
    if not result.ok:
        cached = load_cached_bans(db, server_id, page=page, page_size=page_size)
        if cached.get("has_snapshot"):
            return _ban_list_out(
                server_id,
                cached,
                from_cache=True,
                ok=False,
                error=result.error or "listbans failed",
            )
        return BanListOut(
            server_id=server_id,
            bans=[],
            raw="",
            ok=False,
            error=result.error or "listbans failed",
            steam_lookup_enabled=steam_api_configured(),
            from_cache=False,
            fetched_at=None,
            page=page,
            page_size=page_size,
            total=0,
            total_pages=1,
        )

    raw = result.response or ""
    parsed = adapter.parse_bans(raw)
    try:
        replace_server_bans(db, server_id, parsed=parsed, raw=raw, ok=True, error="")
        db.commit()
    except Exception:
        db.rollback()

    cached = load_cached_bans(db, server_id, page=page, page_size=page_size)
    return _ban_list_out(server_id, cached, from_cache=False, ok=True, error=None)


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
    try:
        adapter = get_adapter(server_type)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown server type: {server_type}") from exc
    gamemodes = adapter.map_gamemodes(map_row)
    if body.gamemode_key not in gamemodes:
        raise HTTPException(status_code=400, detail=f"Gamemode '{body.gamemode_key}' not available for this map")
    scenario = gamemodes[body.gamemode_key]
    command = adapter.build_travel_command(
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

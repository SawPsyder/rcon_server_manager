from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.servers import get_rcon_password, get_server_or_404
from app.config import get_settings
from app.database import get_db
from app.deps import require_admin
from app.models import Setting
from app.schemas import PlayerInfo, ServerFeaturesOut, ServerStatus
from app.server_types import DEFAULT_SERVER_TYPE, get_adapter
from app.services.players import sample_player_count
from app.services.presence import enrich_player_list, update_presence
from app.services.server_options import load_options
from app.services.status_cache import update_server_status_cache

router = APIRouter(prefix="/api/servers", tags=["status"])


def _setting_float(db: Session, key: str, default: float) -> float:
    row = db.query(Setting).filter(Setting.key == key).first()
    if not row:
        return default
    try:
        return float(row.value)
    except ValueError:
        return default


@router.get("/{server_id}/status", response_model=ServerStatus)
def server_status(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> ServerStatus:
    server = get_server_or_404(db, server_id)
    settings = get_settings()
    timeout = _setting_float(db, "query_timeout", settings.query_timeout)
    st = server.server_type or DEFAULT_SERVER_TYPE
    try:
        adapter = get_adapter(st)
    except KeyError:
        adapter = get_adapter(DEFAULT_SERVER_TYPE)
        st = DEFAULT_SERVER_TYPE

    password = get_rcon_password(server)
    options = load_options(server)

    raw = adapter.query_status(
        server.host,
        server.query_port,
        timeout=timeout,
        rcon_port=server.rcon_port,
        secret=password,
        options=options,
    )

    snap = sample_player_count(
        host=server.host,
        query_port=server.query_port,
        rcon_port=server.rcon_port,
        rcon_password=password,
        timeout=timeout,
        server_type=st,
        options=options,
    )

    # An empty roster we actually read means everyone left, and that has to
    # reach update_presence or the last player's session never closes.
    if snap.get("roster_known") or snap.get("player_list"):
        try:
            update_presence(
                db,
                server_id=server.id,
                online_players=snap.get("player_list") or [],
                max_tick_seconds=max(timeout * 10, 180.0),
            )
            db.commit()
        except Exception:
            db.rollback()

    player_source = snap.get("player_list") or raw.get("player_list") or []
    enriched = enrich_player_list(db, server.id, list(player_source))

    players_count = snap.get("players")
    if players_count is None:
        players_count = raw.get("players")

    max_players = (
        raw.get("max_players")
        if raw.get("max_players") is not None
        else snap.get("max_players")
    )

    players = [
        PlayerInfo(
            id=p.get("id", i + 1),
            name=p.get("name", ""),
            score=int(p.get("score", 0) or 0),
            steamid=str(p.get("steamid") or ""),
            ip=str(p.get("ip") or ""),
            session_seconds=int(p.get("session_seconds") or 0),
            session_pretty=str(p.get("session_pretty") or "0s"),
            total_seconds=int(p.get("total_seconds") or 0),
            total_pretty=str(p.get("total_pretty") or "0s"),
            visit_count=int(p.get("visit_count") or 0),
            rank=p.get("rank"),
            ranked_players=int(p.get("ranked_players") or 0),
            last_seen_at=p.get("last_seen_at"),
            last_seen_pretty=str(p.get("last_seen_pretty") or "—"),
            previous_seen_at=p.get("previous_seen_at"),
            previous_seen_pretty=str(p.get("previous_seen_pretty") or "—"),
            duration=float(p.get("duration", 0) or 0),
            duration_pretty=p.get("duration_pretty", "00:00:00"),
            extra=p.get("extra") or None,
        )
        for i, p in enumerate(enriched)
    ]

    online = bool(raw.get("online") or snap.get("online"))
    error = raw.get("error")
    hint = adapter.player_count_hint(has_rcon_password=bool(password), snap=snap)
    if hint:
        error = (error + " | " if error else "") + hint

    features = ServerFeaturesOut(**adapter.info.features.to_dict())

    # Prefer live values; fall back to last cached when query missed a field
    hostname = raw.get("hostname") or server.last_hostname
    map_name = raw.get("map") or server.last_map
    lighting = raw.get("lighting") or server.last_lighting
    gamemode = raw.get("gamemode") or server.last_gamemode
    coop = raw.get("coop_or_versus") or server.last_coop_or_versus
    if players_count is None:
        players_count = server.last_players
    if max_players is None:
        max_players = server.last_max_players

    from_cache = False
    if not online and (server.last_hostname or server.last_map or server.last_gamemode):
        # Fully offline: still surface last known identity fields
        from_cache = not bool(raw.get("hostname") or raw.get("map") or raw.get("gamemode"))

    # Persist any fresh non-empty identity / count fields
    try:
        update_server_status_cache(
            server,
            hostname=raw.get("hostname"),
            map_name=raw.get("map"),
            lighting=raw.get("lighting"),
            gamemode=raw.get("gamemode"),
            coop_or_versus=raw.get("coop_or_versus"),
            players=int(players_count) if (online or snap.get("online")) and players_count is not None else None,
            max_players=int(max_players) if (online or snap.get("online")) and max_players is not None else None,
            online=online,
        )
        db.commit()
    except Exception:
        db.rollback()

    return ServerStatus(
        online=online,
        host=server.host,
        query_port=server.query_port,
        server_type=st,
        features=features,
        hostname=hostname,
        map=map_name,
        lighting=lighting,
        gamemode=gamemode,
        coop_or_versus=coop,
        players=players_count,
        max_players=max_players,
        bots=raw.get("bots"),
        ping_ms=raw.get("ping_ms"),
        password_protected=raw.get("password_protected"),
        vac=raw.get("vac"),
        ranked=raw.get("ranked"),
        game_port=raw.get("game_port"),
        version=raw.get("version"),
        player_list=players,
        error=error,
        from_cache=from_cache,
        last_status_at=server.last_status_at,
        extra=raw.get("extra") or None,
    )

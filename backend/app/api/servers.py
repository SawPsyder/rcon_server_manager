from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import CustomButton, Server
from app.schemas import (
    CustomButtonOut,
    ServerButtonsReplace,
    ServerCreate,
    ServerFeaturesOut,
    ServerOut,
    ServerTypeOut,
    ServerUpdate,
)
from app.security import decrypt_secret, encrypt_secret
from app.server_types import DEFAULT_SERVER_TYPE, get_adapter, is_known_type, list_server_types

router = APIRouter(prefix="/api/servers", tags=["servers"])


def _to_out(server: Server) -> ServerOut:
    return ServerOut(
        id=server.id,
        name=server.name,
        host=server.host,
        query_port=server.query_port,
        rcon_port=server.rcon_port,
        server_type=server.server_type or DEFAULT_SERVER_TYPE,
        preferred_gamemode=server.preferred_gamemode,
        has_rcon_password=bool(server.rcon_password_enc),
        last_hostname=getattr(server, "last_hostname", None),
        last_map=getattr(server, "last_map", None),
        last_lighting=getattr(server, "last_lighting", None),
        last_gamemode=getattr(server, "last_gamemode", None),
        last_coop_or_versus=getattr(server, "last_coop_or_versus", None),
        last_players=getattr(server, "last_players", None),
        last_max_players=getattr(server, "last_max_players", None),
        last_online=getattr(server, "last_online", None),
        last_status_at=getattr(server, "last_status_at", None),
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


def _validate_type(type_id: str) -> str:
    tid = (type_id or DEFAULT_SERVER_TYPE).strip().lower()
    if not is_known_type(tid):
        raise HTTPException(status_code=400, detail=f"Unknown server type: {type_id}")
    return tid


@router.get("/types", response_model=list[ServerTypeOut])
def server_types(_admin: str = Depends(require_admin)) -> list[ServerTypeOut]:
    out: list[ServerTypeOut] = []
    for info in list_server_types():
        out.append(
            ServerTypeOut(
                id=info.id,
                label=info.label,
                default_query_port=info.default_query_port,
                default_rcon_port=info.default_rcon_port,
                features=ServerFeaturesOut(**info.features.to_dict()),
            )
        )
    return out


@router.get("", response_model=list[ServerOut])
def list_servers(db: Session = Depends(get_db), _admin: str = Depends(require_admin)) -> list[ServerOut]:
    servers = db.query(Server).order_by(Server.name.asc()).all()
    return [_to_out(s) for s in servers]


@router.post("", response_model=ServerOut, status_code=status.HTTP_201_CREATED)
def create_server(
    body: ServerCreate,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> ServerOut:
    st = _validate_type(body.server_type)
    preferred = (body.preferred_gamemode or "").strip() or None
    server = Server(
        name=body.name.strip(),
        host=body.host.strip(),
        query_port=body.query_port,
        rcon_port=body.rcon_port,
        rcon_password_enc=encrypt_secret(body.rcon_password) if body.rcon_password else "",
        server_type=st,
        preferred_gamemode=preferred,
        options_json="{}",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return _to_out(server)


@router.get("/{server_id}", response_model=ServerOut)
def get_server(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> ServerOut:
    server = db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return _to_out(server)


@router.put("/{server_id}", response_model=ServerOut)
def update_server(
    server_id: int,
    body: ServerUpdate,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> ServerOut:
    from app.services.rcon_pool import rcon_pool

    server = db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    # Drop old persistent session if endpoint or password changes
    old_host, old_port = server.host, server.rcon_port
    endpoint_changed = False
    if body.host is not None and body.host.strip() != server.host:
        endpoint_changed = True
    if body.rcon_port is not None and body.rcon_port != server.rcon_port:
        endpoint_changed = True
    if body.rcon_password is not None:
        endpoint_changed = True

    if body.name is not None:
        server.name = body.name.strip()
    if body.host is not None:
        server.host = body.host.strip()
    if body.query_port is not None:
        server.query_port = body.query_port
    if body.rcon_port is not None:
        server.rcon_port = body.rcon_port
    if body.server_type is not None:
        server.server_type = _validate_type(body.server_type)
    if body.clear_preferred_gamemode:
        server.preferred_gamemode = None
    elif body.preferred_gamemode is not None:
        server.preferred_gamemode = body.preferred_gamemode.strip() or None
    if body.rcon_password is not None:
        server.rcon_password_enc = encrypt_secret(body.rcon_password) if body.rcon_password else ""
    db.commit()
    db.refresh(server)
    if endpoint_changed:
        rcon_pool.invalidate_endpoint(old_host, old_port)
        if server.host != old_host or server.rcon_port != old_port:
            rcon_pool.invalidate_endpoint(server.host, server.rcon_port)
    return _to_out(server)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_server(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> None:
    from app.services.rcon_pool import rcon_pool

    server = db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    host, port = server.host, server.rcon_port
    db.delete(server)
    db.commit()
    rcon_pool.invalidate_endpoint(host, port)


def resolve_buttons(db: Session, server: Server) -> list[CustomButton]:
    """Per-server overrides if any; else type defaults."""
    overrides = (
        db.query(CustomButton)
        .filter(CustomButton.server_id == server.id)
        .order_by(CustomButton.sort_order.asc())
        .all()
    )
    if overrides:
        return overrides
    st = server.server_type or DEFAULT_SERVER_TYPE
    return (
        db.query(CustomButton)
        .filter(CustomButton.server_type == st, CustomButton.server_id.is_(None))
        .order_by(CustomButton.sort_order.asc())
        .all()
    )


@router.get("/{server_id}/buttons", response_model=list[CustomButtonOut])
def server_buttons(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> list[CustomButtonOut]:
    server = get_server_or_404(db, server_id)
    return resolve_buttons(db, server)


@router.put("/{server_id}/buttons", response_model=list[CustomButtonOut])
def replace_server_buttons(
    server_id: int,
    body: ServerButtonsReplace,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> list[CustomButtonOut]:
    """
    Replace per-server button overrides.
    Empty buttons list removes overrides so type defaults are used.
    """
    server = get_server_or_404(db, server_id)
    db.query(CustomButton).filter(CustomButton.server_id == server.id).delete()
    created: list[CustomButton] = []
    for i, btn in enumerate(body.buttons):
        row = CustomButton(
            label=btn.label.strip(),
            command=btn.command.strip(),
            sort_order=btn.sort_order if btn.sort_order else i,
            server_type=server.server_type or DEFAULT_SERVER_TYPE,
            server_id=server.id,
        )
        db.add(row)
        created.append(row)
    db.commit()
    for row in created:
        db.refresh(row)
    return resolve_buttons(db, server)


def get_server_or_404(db: Session, server_id: int) -> Server:
    server = db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


def get_rcon_password(server: Server) -> str:
    return decrypt_secret(server.rcon_password_enc)

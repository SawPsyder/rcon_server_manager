from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import Server
from app.schemas import (
    QuickButtonOut,
    ServerCreate,
    ServerFeaturesOut,
    ServerOptionsOut,
    ServerOut,
    ServerTypeOut,
    ServerUpdate,
)
from app.security import decrypt_secret, encrypt_secret
from app.server_types import DEFAULT_SERVER_TYPE, get_adapter, is_known_type, list_server_types
from app.services.server_options import load_options, merge_options

router = APIRouter(prefix="/api/servers", tags=["servers"])


def _options_out(server: Server) -> ServerOptionsOut:
    options = load_options(server)
    return ServerOptionsOut(
        verify_tls=bool(options.get("verify_tls", False)),
        cert_fingerprint=str(options.get("cert_fingerprint", "") or ""),
    )


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
        options=_options_out(server),
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


def _normalize_ports(server: Server) -> None:
    """Single-port games (one HTTPS API port) keep both port columns in sync."""
    adapter = get_adapter(server.server_type or DEFAULT_SERVER_TYPE)
    if adapter.info.endpoint_style == "single_port":
        server.rcon_port = server.query_port


def _apply_options(server: Server, options) -> None:
    if options is None:
        return
    updates = options.model_dump(exclude_unset=True)
    if "cert_fingerprint" in updates:
        from app.services.satisfactory_api import normalize_fingerprint

        updates["cert_fingerprint"] = normalize_fingerprint(updates["cert_fingerprint"])
    merge_options(server, updates)


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
                quick_buttons=[
                    QuickButtonOut(label=b.label, command=b.command)
                    for b in info.quick_buttons
                ],
                secret_label=info.secret_label,
                endpoint_style=info.endpoint_style,
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
    _apply_options(server, body.options)
    _normalize_ports(server)
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
    server = db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    # Drop pooled connections if the endpoint, secret or TLS settings change
    old_host, old_port = server.host, server.rcon_port
    old_adapter = get_adapter(server.server_type or DEFAULT_SERVER_TYPE)
    endpoint_changed = False
    if body.host is not None and body.host.strip() != server.host:
        endpoint_changed = True
    if body.rcon_port is not None and body.rcon_port != server.rcon_port:
        endpoint_changed = True
    if body.query_port is not None and body.query_port != server.query_port:
        endpoint_changed = True
    if body.rcon_password is not None or body.options is not None:
        endpoint_changed = True
    if body.server_type is not None and body.server_type != server.server_type:
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
    _apply_options(server, body.options)
    _normalize_ports(server)
    db.commit()
    db.refresh(server)
    new_adapter = get_adapter(server.server_type or DEFAULT_SERVER_TYPE)
    if endpoint_changed:
        old_adapter.invalidate_connections(old_host, old_port)
        if new_adapter is not old_adapter:
            new_adapter.invalidate_connections(old_host, old_port)
        if server.host != old_host or server.rcon_port != old_port:
            new_adapter.invalidate_connections(server.host, server.rcon_port)
    return _to_out(server)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_server(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> None:
    server = db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    host, port = server.host, server.rcon_port
    adapter = get_adapter(server.server_type or DEFAULT_SERVER_TYPE)
    db.delete(server)
    db.commit()
    adapter.invalidate_connections(host, port)


def get_server_or_404(db: Session, server_id: int) -> Server:
    server = db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


def get_rcon_password(server: Server) -> str:
    return decrypt_secret(server.rcon_password_enc)

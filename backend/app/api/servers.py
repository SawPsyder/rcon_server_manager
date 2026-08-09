from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import AdminUser, CurrentUser
from app.models import Server, ServerGrant, User
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
from app.services.server_options import load_options, merge_options, option_str

router = APIRouter(prefix="/api/servers", tags=["servers"])


# Options that describe how we reach the game server. Changing one of these
# must drop pooled connections; the Pterodactyl link deliberately must not.
CONNECTION_OPTION_KEYS = ("use_https", "verify_tls", "cert_fingerprint")


def _options_out(server: Server) -> ServerOptionsOut:
    options = load_options(server)
    return ServerOptionsOut(
        use_https=bool(options.get("use_https", False)),
        verify_tls=bool(options.get("verify_tls", False)),
        cert_fingerprint=str(options.get("cert_fingerprint", "") or ""),
        pterodactyl_uuid=str(options.get("pterodactyl_uuid", "") or ""),
        pterodactyl_identifier=str(options.get("pterodactyl_identifier", "") or ""),
        pterodactyl_name=str(options.get("pterodactyl_name", "") or ""),
    )


def _pterodactyl_linked(server: Server) -> bool:
    return bool(str(load_options(server).get("pterodactyl_uuid", "") or "").strip())


def _to_out(server: Server, viewer: User | None = None) -> ServerOut:
    """Serialise a server.

    Non-admin viewers get the admin control plane redacted: the RCON port and
    the TLS options describe how we reach the server, and handing them to an
    operator widens the blast radius of a leaked password for no UI benefit.
    Host and query port stay - they are the public game endpoint every player
    already knows, and the detail page seeds its status card from them.

    ``pterodactyl_linked`` is deliberately outside the redacted block. It is
    not a credential and not a route to anything - the panel key is global and
    never leaves the backend - and a granted operator may use the resource
    panel, so the UI has to know it exists. The uuid behind it stays inside
    ``options`` and stays admin-only.
    """
    redact = viewer is not None and not viewer.is_admin
    return ServerOut(
        id=server.id,
        name=server.name,
        host=server.host,
        query_port=server.query_port,
        rcon_port=None if redact else server.rcon_port,
        server_type=server.server_type or DEFAULT_SERVER_TYPE,
        preferred_gamemode=server.preferred_gamemode,
        has_rcon_password=bool(server.rcon_password_enc),
        options=ServerOptionsOut() if redact else _options_out(server),
        pterodactyl_linked=_pterodactyl_linked(server),
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


def _assert_pterodactyl_uuid_available(
    db: Session, uuid: str, *, exclude_server_id: int | None = None
) -> None:
    """Reject linking a panel container that another app server already claims.

    Two servers pointing at the same UUID would double-poll the same container,
    write history under different server_ids, and both accept power signals.
    """
    if not uuid:
        return
    for other in db.query(Server).all():
        if exclude_server_id is not None and other.id == exclude_server_id:
            continue
        if option_str(other, "pterodactyl_uuid") == uuid:
            raise HTTPException(
                status_code=400,
                detail=(
                    f'Pterodactyl container is already linked to "{other.name}". '
                    "Unlink it there first, or pick a different container."
                ),
            )


def _apply_options(server: Server, options, db: Session | None = None) -> None:
    if options is None:
        return
    updates = options.model_dump(exclude_unset=True)
    if "cert_fingerprint" in updates:
        from app.services.tls_pins import normalize_fingerprint

        updates["cert_fingerprint"] = normalize_fingerprint(updates["cert_fingerprint"])
    if "pterodactyl_uuid" in updates:
        # Panel UUIDs are lowercase hex; normalising means a pasted uppercase
        # value still matches the inventory list. "" unlinks.
        uuid = str(updates.get("pterodactyl_uuid") or "").strip().lower()
        updates["pterodactyl_uuid"] = uuid
        if not uuid:
            # Don't leave the cached display labels behind pointing at nothing.
            updates["pterodactyl_identifier"] = ""
            updates["pterodactyl_name"] = ""
        elif db is not None:
            # Re-saving the same link on this server is fine; another server is not.
            _assert_pterodactyl_uuid_available(
                db, uuid, exclude_server_id=getattr(server, "id", None)
            )
    merge_options(server, updates)


def _connection_options_changed(server: Server, options) -> bool:
    """Whether an options update touches how we reach the game server.

    Only these keys justify tearing down pooled RCON / API sessions. The
    Pterodactyl link lives in the same JSON blob but addresses the panel, not
    the game server, so linking one must not drop a live console session.
    """
    if options is None:
        return False
    updates = options.model_dump(exclude_unset=True)
    current = load_options(server)
    return any(
        key in updates and updates[key] != current.get(key)
        for key in CONNECTION_OPTION_KEYS
    )


@router.get("/types", response_model=list[ServerTypeOut])
def server_types() -> list[ServerTypeOut]:
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
                ban_list_source=info.ban_list_source,
                tick_rate_label=info.tick_rate_label,
                tick_rate_unit=info.tick_rate_unit,
                tick_rate_target=info.tick_rate_target,
            )
        )
    return out


@router.get("", response_model=list[ServerOut])
def list_servers(user: CurrentUser, db: Session = Depends(get_db)) -> list[ServerOut]:
    """Servers the caller may operate. Admins see all; everyone else sees grants.

    This is also what filters the overview page - it calls this endpoint - and
    it does not go through get_server_or_404, so the filter has to live here.
    """
    query = db.query(Server)
    if not user.is_admin:
        query = query.join(ServerGrant, ServerGrant.server_id == Server.id).filter(
            ServerGrant.user_id == user.id
        )
    servers = query.order_by(Server.name.asc()).all()
    return [_to_out(s, user) for s in servers]


@router.post("", response_model=ServerOut, status_code=status.HTTP_201_CREATED)
def create_server(
    body: ServerCreate,
    _admin: AdminUser,
    db: Session = Depends(get_db),
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
    _apply_options(server, body.options, db=db)
    _normalize_ports(server)
    db.add(server)
    db.commit()
    db.refresh(server)
    return _to_out(server)


@router.get("/{server_id}", response_model=ServerOut)
def get_server(
    server_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> ServerOut:
    server = db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return _to_out(server, user)


# Connection settings. Admin-only: host, ports, the RCON secret, the server type
# and the TLS options all define how we reach the game server, and a granted
# operator must not be able to repoint or lock out a server they only moderate.
@router.put("/{server_id}", response_model=ServerOut)
def update_server(
    server_id: int,
    body: ServerUpdate,
    _admin: AdminUser,
    db: Session = Depends(get_db),
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
    if body.rcon_password is not None or _connection_options_changed(server, body.options):
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
    _apply_options(server, body.options, db=db)
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
    _admin: AdminUser,
    db: Session = Depends(get_db),
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

"""Pterodactyl panel credentials and inventory. Admin-only.

Per-server resource readings and power control live in
``app.api.server_pterodactyl`` - those are usable by a granted operator, these
are not. An operator presses the buttons; only an admin decides which container
the buttons point at.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import AdminUser
from app.models import Server
from app.schemas import (
    PterodactylServerOut,
    PterodactylSettingsOut,
    PterodactylSettingsUpdate,
    PterodactylTestOut,
)
from app.services import pterodactyl_api, pterodactyl_settings
from app.services.server_options import option_str

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pterodactyl", tags=["pterodactyl"])


def _to_out(db: Session) -> PterodactylSettingsOut:
    cfg = pterodactyl_settings.load_pterodactyl_config(db)
    return PterodactylSettingsOut(
        base_url=cfg.base_url,
        has_api_key=pterodactyl_settings.has_stored_api_key(db),
        verify_tls=cfg.verify_tls,
        enabled=cfg.enabled,
    )


@router.get("", response_model=PterodactylSettingsOut)
def get_pterodactyl_settings(
    _admin: AdminUser, db: Session = Depends(get_db)
) -> PterodactylSettingsOut:
    return _to_out(db)


@router.put("", response_model=PterodactylSettingsOut)
def update_pterodactyl_settings(
    body: PterodactylSettingsUpdate,
    admin: AdminUser,
    db: Session = Depends(get_db),
) -> PterodactylSettingsOut:
    pterodactyl_settings.save_pterodactyl_config(
        db,
        base_url=body.base_url,
        api_key=body.api_key,
        verify_tls=body.verify_tls,
    )
    db.commit()
    # httpx bakes `verify` in at construction, and the registry is keyed on the
    # whole credential set - but drop everything anyway so a re-saved identical
    # config also clears a stale auth cooldown.
    pterodactyl_api.panel_registry.invalidate_all()
    logger.info("Pterodactyl settings updated by %s", admin.email)
    return _to_out(db)


@router.post("/test", response_model=PterodactylTestOut)
def test_pterodactyl_connection(
    _admin: AdminUser, db: Session = Depends(get_db)
) -> PterodactylTestOut:
    """Probe the saved panel settings and report why they don't work, if they don't.

    Deliberately synchronous and against the *saved* config, matching the mail
    test button: the point is to surface the panel's own error where the admin
    can read it.
    """
    cfg = pterodactyl_settings.load_pterodactyl_config(db)
    problem = pterodactyl_api.describe_failure(cfg)
    if problem:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=problem)

    try:
        servers = pterodactyl_api.client_for(cfg).list_servers()
    except pterodactyl_api.PterodactylApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    count = len(servers)
    if count == 0:
        # The key works but the account owns nothing - a real and confusing
        # state, usually a key made on the wrong panel account.
        detail = (
            "Connected, but this key can see no servers. Check it belongs to "
            "the panel account that owns them."
        )
    else:
        detail = f"Connected. This key can see {count} server{'s' if count != 1 else ''}."
    return PterodactylTestOut(detail=detail, server_count=count)


@router.get("/servers", response_model=list[PterodactylServerOut])
def list_panel_servers(
    _admin: AdminUser,
    refresh: bool = False,
    db: Session = Depends(get_db),
) -> list[PterodactylServerOut]:
    """Panel inventory for the server-linking dropdown.

    ``linked_server_id`` marks entries one of our servers already claims, so
    the picker can warn before two of ours point at one container.
    """
    cfg = pterodactyl_settings.load_pterodactyl_config(db)
    if not cfg.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pterodactyl is not configured. Add the panel URL and an API key first.",
        )

    try:
        client = pterodactyl_api.client_for(cfg)
        if refresh:
            client.invalidate_cache()
        panel_servers = client.list_servers()
    except pterodactyl_api.PterodactylTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)
        ) from exc
    except pterodactyl_api.PterodactylApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    claimed: dict[str, int] = {}
    for server in db.query(Server).all():
        uuid = option_str(server, "pterodactyl_uuid")
        if uuid:
            claimed.setdefault(uuid, server.id)

    return [
        PterodactylServerOut(
            uuid=ps.uuid,
            identifier=ps.identifier,
            name=ps.name,
            node=ps.node,
            status=ps.status,
            is_suspended=ps.is_suspended,
            memory_limit_mb=ps.memory_limit_mb,
            disk_limit_mb=ps.disk_limit_mb,
            cpu_limit=ps.cpu_limit,
            linked_server_id=claimed.get(ps.uuid),
        )
        for ps in panel_servers
    ]

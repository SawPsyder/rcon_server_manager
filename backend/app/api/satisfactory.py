"""Satisfactory-specific admin endpoints (HTTPS API passthrough).

These operations have no equivalent in the other server types - saves, sessions,
server options, advanced game settings - so they get their own router instead of
being forced into the generic adapter contract. The frontend shows the panel
only when a type advertises ``features.admin_api``.

Destructive calls require ``confirm: true`` in the body, and every mutating call
is written to ``command_history`` so the existing audit view covers them.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.servers import get_rcon_password, get_server_or_404
from app.database import get_db
from app.deps import require_admin
from app.models import CommandHistory, Server
from app.schemas import (
    AutoLoadRequest,
    ClaimServerRequest,
    ConfirmRequest,
    LoadGameRequest,
    NewGameRequest,
    RenameServerRequest,
    SatisfactoryActionOut,
    SatisfactoryAdvancedOut,
    SatisfactoryAdvancedUpdate,
    SatisfactoryHealthOut,
    SatisfactoryOptionsOut,
    SatisfactoryOptionsUpdate,
    SatisfactorySessionsOut,
    SatisfactoryStateOut,
    SaveGameRequest,
    SetPasswordRequest,
)
from app.security import encrypt_secret
from app.server_types.satisfactory import client_for_server, normalize_state
from app.services.satisfactory_api import (
    SatisfactoryApiError,
    SatisfactoryAuthError,
    SatisfactoryClient,
    SatisfactoryTimeoutError,
    SatisfactoryTlsError,
    looks_like_api_token,
    satisfactory_pool,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/servers/{server_id}/satisfactory", tags=["satisfactory"])

SERVER_TYPE = "satisfactory"
API_TIMEOUT = 15.0


@contextmanager
def _api_errors() -> Iterator[None]:
    """Map transport failures onto HTTP statuses the UI can act on."""
    try:
        yield
    except (SatisfactoryAuthError, SatisfactoryTlsError) as exc:
        # Actionable configuration problems (credentials / certificate)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SatisfactoryTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except SatisfactoryApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _server(db: Session, server_id: int) -> Server:
    server = get_server_or_404(db, server_id)
    if (server.server_type or "").strip().lower() != SERVER_TYPE:
        raise HTTPException(
            status_code=400,
            detail=f"Server {server_id} is not a Satisfactory server",
        )
    return server


def _client(
    db: Session,
    server_id: int,
    *,
    require_secret: bool = True,
) -> tuple[Server, SatisfactoryClient]:
    server = _server(db, server_id)
    secret = get_rcon_password(server)
    if require_secret and not secret:
        raise HTTPException(
            status_code=400,
            detail="Server has no admin password or API token configured",
        )
    with _api_errors():
        return server, client_for_server(server, secret, timeout=API_TIMEOUT)


def _log(db: Session, server: Server, command: str, response: str = "") -> None:
    """Record an admin action in the shared command history."""
    try:
        db.add(
            CommandHistory(
                server_id=server.id,
                command=f"satisfactory:{command}"[:2000],
                response=(response or "ok")[:4000],
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("Could not log Satisfactory action %s", command, exc_info=True)


def _require_confirm(confirm: bool, what: str) -> None:
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail=f"{what} requires confirm=true",
        )


# --- read-only ------------------------------------------------------------


@router.get("/health", response_model=SatisfactoryHealthOut)
def health(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> SatisfactoryHealthOut:
    """Reachability probe - works even without credentials."""
    _server, client = _client(db, server_id, require_secret=False)
    with _api_errors():
        data = client.health_check()
    return SatisfactoryHealthOut(
        health=str(data.get("health") or ""),
        server_custom_data=str(data.get("serverCustomData") or ""),
    )


@router.get("/state", response_model=SatisfactoryStateOut)
def state(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> SatisfactoryStateOut:
    _server, client = _client(db, server_id)
    with _api_errors():
        raw = client.query_server_state()
    return SatisfactoryStateOut(**normalize_state(raw))


@router.get("/options", response_model=SatisfactoryOptionsOut)
def get_options(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> SatisfactoryOptionsOut:
    _server, client = _client(db, server_id)
    with _api_errors():
        data = client.get_server_options()
    return SatisfactoryOptionsOut(
        server_options={k: str(v) for k, v in data["server_options"].items()},
        pending_server_options={
            k: str(v) for k, v in data["pending_server_options"].items()
        },
    )


@router.get("/advanced-settings", response_model=SatisfactoryAdvancedOut)
def get_advanced_settings(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> SatisfactoryAdvancedOut:
    _server, client = _client(db, server_id)
    with _api_errors():
        data = client.get_advanced_game_settings()
    return SatisfactoryAdvancedOut(
        creative_mode_enabled=bool(data["creative_mode_enabled"]),
        advanced_game_settings=data["advanced_game_settings"],
    )


@router.get("/sessions", response_model=SatisfactorySessionsOut)
def sessions(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> SatisfactorySessionsOut:
    _server, client = _client(db, server_id)
    with _api_errors():
        data = client.enumerate_sessions()
    return SatisfactorySessionsOut(
        sessions=data["sessions"],
        current_session_index=data["current_session_index"],
    )


# --- configuration --------------------------------------------------------


@router.put("/options", response_model=SatisfactoryActionOut)
def apply_options(
    server_id: int,
    body: SatisfactoryOptionsUpdate,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> SatisfactoryActionOut:
    server, client = _client(db, server_id)
    with _api_errors():
        client.apply_server_options(body.options)
    keys = ", ".join(sorted(body.options))
    _log(db, server, f"ApplyServerOptions {keys}")
    return SatisfactoryActionOut(
        detail=(
            "Server options applied. Options that cannot change at runtime stay "
            "pending until the next restart."
        )
    )


@router.put("/advanced-settings", response_model=SatisfactoryActionOut)
def apply_advanced_settings(
    server_id: int,
    body: SatisfactoryAdvancedUpdate,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> SatisfactoryActionOut:
    _require_confirm(
        body.confirm,
        "Applying advanced game settings permanently marks the save as edited, so it",
    )
    server, client = _client(db, server_id)
    with _api_errors():
        client.apply_advanced_game_settings(body.settings)
    keys = ", ".join(sorted(body.settings))
    _log(db, server, f"ApplyAdvancedGameSettings {keys}")
    return SatisfactoryActionOut(
        detail="Advanced game settings applied. This save is now flagged as edited."
    )


@router.post("/rename", response_model=SatisfactoryActionOut)
def rename(
    server_id: int,
    body: RenameServerRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> SatisfactoryActionOut:
    server, client = _client(db, server_id)
    with _api_errors():
        client.rename_server(body.server_name)
    _log(db, server, f"RenameServer {body.server_name}")
    return SatisfactoryActionOut(detail=f"Server renamed to {body.server_name}")


@router.post("/auto-load", response_model=SatisfactoryActionOut)
def set_auto_load(
    server_id: int,
    body: AutoLoadRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> SatisfactoryActionOut:
    server, client = _client(db, server_id)
    with _api_errors():
        client.set_auto_load_session_name(body.session_name)
    _log(db, server, f"SetAutoLoadSessionName {body.session_name}")
    return SatisfactoryActionOut(
        detail=f"Auto-load session set to {body.session_name or '(none)'}"
    )


@router.post("/passwords/client", response_model=SatisfactoryActionOut)
def set_client_password(
    server_id: int,
    body: SetPasswordRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> SatisfactoryActionOut:
    server, client = _client(db, server_id)
    with _api_errors():
        client.set_client_password(body.password)
    _log(db, server, "SetClientPassword")
    return SatisfactoryActionOut(
        detail="Client password cleared" if not body.password else "Client password updated"
    )


@router.post("/passwords/admin", response_model=SatisfactoryActionOut)
def set_admin_password(
    server_id: int,
    body: SetPasswordRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> SatisfactoryActionOut:
    """Change the game server's admin password.

    If this app authenticates with that password (rather than a static API
    token), the stored secret is rotated too - otherwise the next poll would
    fail with the old credentials.
    """
    if not body.password:
        raise HTTPException(status_code=400, detail="Admin password cannot be empty")
    server, client = _client(db, server_id)
    current_secret = get_rcon_password(server)
    with _api_errors():
        client.set_admin_password(body.password)

    rotated = False
    if not looks_like_api_token(current_secret):
        server.rcon_password_enc = encrypt_secret(body.password)
        db.commit()
        rotated = True
    satisfactory_pool.invalidate_endpoint(server.host, server.query_port)
    _log(db, server, "SetAdminPassword")
    return SatisfactoryActionOut(
        detail=(
            "Admin password updated and the stored secret was rotated to match."
            if rotated
            else "Admin password updated. This app keeps using its API token."
        )
    )


@router.post("/claim", response_model=SatisfactoryActionOut)
def claim(
    server_id: int,
    body: ClaimServerRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> SatisfactoryActionOut:
    """Claim a fresh, unclaimed server and store the admin token it returns."""
    server, client = _client(db, server_id, require_secret=False)
    with _api_errors():
        token = client.claim_server(body.server_name, body.admin_password)

    server.rcon_password_enc = encrypt_secret(token or body.admin_password)
    db.commit()
    satisfactory_pool.invalidate_endpoint(server.host, server.query_port)
    _log(db, server, f"ClaimServer {body.server_name}")
    return SatisfactoryActionOut(
        detail=(
            f"Claimed {body.server_name}. "
            + ("Admin token stored." if token else "Admin password stored.")
        )
    )


# --- saves & sessions -----------------------------------------------------


@router.post("/save", response_model=SatisfactoryActionOut)
def save_game(
    server_id: int,
    body: SaveGameRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> SatisfactoryActionOut:
    server, client = _client(db, server_id)
    with _api_errors():
        client.save_game(body.save_name)
    _log(db, server, f"SaveGame {body.save_name}")
    return SatisfactoryActionOut(detail=f"Saved as {body.save_name}")


@router.post("/load", response_model=SatisfactoryActionOut)
def load_game(
    server_id: int,
    body: LoadGameRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> SatisfactoryActionOut:
    server, client = _client(db, server_id)
    with _api_errors():
        client.load_game(
            body.save_name,
            enable_advanced_game_settings=body.enable_advanced_game_settings,
        )
    _log(db, server, f"LoadGame {body.save_name}")
    return SatisfactoryActionOut(
        detail=f"Loading {body.save_name} - the server drops all players while it reloads."
    )


@router.delete("/saves/{save_name}", response_model=SatisfactoryActionOut)
def delete_save(
    server_id: int,
    save_name: str,
    confirm: bool = False,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> SatisfactoryActionOut:
    _require_confirm(confirm, "Deleting a save file is irreversible, so it")
    server, client = _client(db, server_id)
    with _api_errors():
        client.delete_save_file(save_name)
    _log(db, server, f"DeleteSaveFile {save_name}")
    return SatisfactoryActionOut(detail=f"Deleted save {save_name}")


@router.delete("/sessions/{session_name}", response_model=SatisfactoryActionOut)
def delete_session(
    server_id: int,
    session_name: str,
    confirm: bool = False,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> SatisfactoryActionOut:
    _require_confirm(
        confirm, "Deleting a session removes every save it contains, so it"
    )
    server, client = _client(db, server_id)
    with _api_errors():
        client.delete_save_session(session_name)
    _log(db, server, f"DeleteSaveSession {session_name}")
    return SatisfactoryActionOut(detail=f"Deleted session {session_name}")


# --- danger zone ----------------------------------------------------------


@router.post("/new-game", response_model=SatisfactoryActionOut)
def new_game(
    server_id: int,
    body: NewGameRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> SatisfactoryActionOut:
    _require_confirm(
        body.confirm, "Creating a new game abandons the running session, so it"
    )
    server, client = _client(db, server_id)
    with _api_errors():
        client.create_new_game(
            body.session_name,
            map_name=body.map_name,
            starting_location=body.starting_location,
            skip_onboarding=body.skip_onboarding,
        )
    _log(db, server, f"CreateNewGame {body.session_name}")
    return SatisfactoryActionOut(detail=f"Started new game {body.session_name}")


@router.post("/shutdown", response_model=SatisfactoryActionOut)
def shutdown(
    server_id: int,
    body: ConfirmRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> SatisfactoryActionOut:
    _require_confirm(
        body.confirm,
        "Shutting the server down disconnects everyone and needs an external "
        "restart, so it",
    )
    server, client = _client(db, server_id)
    with _api_errors():
        client.shutdown()
    satisfactory_pool.invalidate_endpoint(server.host, server.query_port)
    _log(db, server, "Shutdown")
    return SatisfactoryActionOut(
        detail="Shutdown requested. Restart it from your host or process manager."
    )

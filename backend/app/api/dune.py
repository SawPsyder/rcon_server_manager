"""Dune: Awakening admin endpoints (egg admin-HTTP passthrough).

Map markers, named teleports, the 195-key INI catalogue, and sietch /
partition scale have no generic RCON counterpart, so they get their own
router. The frontend shows the panel only when a type advertises
``features.admin_api`` and is mapped in ``ADMIN_PANELS``.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.servers import get_rcon_password, get_server_or_404
from app.database import get_db
from app.deps import CurrentUser
from app.models import CommandHistory, Server, User
from app.schemas import (
    DuneActionOut,
    DuneBroadcastRequest,
    DuneForceRequest,
    DuneLocationAction,
    DuneScaleRequest,
    DuneSettingsUpdate,
    DuneSietchCreate,
    DuneTeleportRequest,
)
from app.server_types.dune import client_for_server
from app.services.dune_api import (
    MAP_KEYS,
    DuneApiError,
    DuneAuthError,
    DuneClient,
    DuneTimeoutError,
    DuneTlsError,
    publish_detail,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/servers/{server_id}/dune", tags=["dune"])

SERVER_TYPE = "dune"
API_TIMEOUT = 20.0


def _http_error(exc: DuneApiError) -> HTTPException:
    if isinstance(exc, (DuneAuthError, DuneTlsError)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, DuneTimeoutError):
        return HTTPException(status_code=504, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


@contextmanager
def _api_errors() -> Iterator[None]:
    try:
        yield
    except DuneApiError as exc:
        raise _http_error(exc) from exc


def _server(db: Session, server_id: int) -> Server:
    server = get_server_or_404(db, server_id)
    if (server.server_type or "").strip().lower() != SERVER_TYPE:
        raise HTTPException(
            status_code=400,
            detail=f"Server {server_id} is not a Dune: Awakening server",
        )
    return server


def _client(db: Session, server_id: int) -> tuple[Server, DuneClient]:
    server = _server(db, server_id)
    secret = get_rcon_password(server)
    if not secret:
        raise HTTPException(
            status_code=400,
            detail="Server has no admin UI password configured",
        )
    with _api_errors():
        return server, client_for_server(server, secret, timeout=API_TIMEOUT)


def _log(
    db: Session,
    server: Server,
    command: str,
    response: str = "",
    actor: User | None = None,
) -> None:
    try:
        db.add(
            CommandHistory(
                server_id=server.id,
                command=f"dune:{command}"[:2000],
                response=(response or "ok")[:4000],
                actor_user_id=actor.id if actor else None,
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("Could not log Dune action %s", command, exc_info=True)


def _action(payload: dict[str, Any], *, fallback: str = "ok") -> DuneActionOut:
    errors_raw = payload.get("errors")
    errors: list[dict[str, str]] = []
    if isinstance(errors_raw, list):
        for item in errors_raw:
            if isinstance(item, dict):
                errors.append(
                    {
                        "id": str(item.get("id") or ""),
                        "error": str(item.get("error") or ""),
                    }
                )
    applied = payload.get("applied")
    return DuneActionOut(
        ok=payload.get("ok", True) is not False,
        detail=publish_detail(payload) if payload.get("stdout") or payload.get("stderr") else (
            str(payload.get("error") or payload.get("message") or fallback)
        ),
        restart_required=bool(payload.get("restartRequired") or payload.get("restart_required")),
        requires_confirmation=bool(
            payload.get("requiresConfirmation") or payload.get("requires_confirmation")
        ),
        players=payload.get("players") if isinstance(payload.get("players"), int) else None,
        applied=[str(x) for x in applied] if isinstance(applied, list) else [],
        errors=errors,
    )


# --- reads -----------------------------------------------------------------


@router.get("/status")
def status(
    server_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _, client = _client(db, server_id)
    with _api_errors():
        return client.status()


@router.get("/partitions")
def partitions(
    server_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _, client = _client(db, server_id)
    with _api_errors():
        return client.partitions()


@router.get("/settings")
def settings(
    server_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _, client = _client(db, server_id)
    with _api_errors():
        return client.settings()


@router.get("/map/markers")
def map_markers(
    server_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
    map: str = "HaggaBasin",
) -> dict[str, Any]:
    _, client = _client(db, server_id)
    with _api_errors():
        return client.map_markers(map)


@router.get("/map/locations")
def map_locations(
    server_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _, client = _client(db, server_id)
    with _api_errors():
        return client.locations()


# --- writes ----------------------------------------------------------------


@router.post("/broadcast", response_model=DuneActionOut)
def broadcast(
    server_id: int,
    user: CurrentUser,
    body: DuneBroadcastRequest,
    db: Session = Depends(get_db),
) -> DuneActionOut:
    server, client = _client(db, server_id)
    with _api_errors():
        payload = client.broadcast(body.title.strip() or "Broadcast", body.body.strip(), body.duration)
    detail = publish_detail(payload)
    _log(db, server, f"broadcast {body.title}", detail, actor=user)
    return DuneActionOut(ok=True, detail=detail or "Broadcast sent.")


@router.post("/settings", response_model=DuneActionOut)
def save_settings(
    server_id: int,
    user: CurrentUser,
    body: DuneSettingsUpdate,
    db: Session = Depends(get_db),
) -> DuneActionOut:
    server, client = _client(db, server_id)
    with _api_errors():
        payload = client.save_settings(body.settings)
    action = _action(payload, fallback="Settings saved.")
    _log(
        db,
        server,
        "settings " + ",".join(sorted(body.settings)),
        action.detail,
        actor=user,
    )
    return action


@router.post("/map/locations")
def mutate_locations(
    server_id: int,
    user: CurrentUser,
    body: DuneLocationAction,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    server, client = _client(db, server_id)
    action = (body.action or "").strip().lower()
    with _api_errors():
        if action == "add":
            if body.location is None:
                raise HTTPException(status_code=400, detail="location is required")
            if body.location.map not in MAP_KEYS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown map. Allowed: {', '.join(MAP_KEYS)}",
                )
            payload = client.add_location(body.location.model_dump())
            _log(db, server, f"location add {body.location.name}", "ok", actor=user)
            return payload
        if action == "remove":
            name = (body.name or (body.location.name if body.location else "")).strip()
            if not name:
                raise HTTPException(status_code=400, detail="name is required")
            payload = client.remove_location(name)
            _log(db, server, f"location remove {name}", "ok", actor=user)
            return payload
    raise HTTPException(status_code=400, detail="action must be add or remove")


@router.post("/map/teleport", response_model=DuneActionOut)
def teleport(
    server_id: int,
    user: CurrentUser,
    body: DuneTeleportRequest,
    db: Session = Depends(get_db),
) -> DuneActionOut:
    server, client = _client(db, server_id)
    with _api_errors():
        payload = client.teleport(body.player.strip(), body.location.strip())
    detail = publish_detail(payload)
    _log(db, server, f"teleport {body.player} {body.location}", detail, actor=user)
    return DuneActionOut(ok=True, detail=detail or "Teleported.")


@router.post("/instances/{map_name}/scale", response_model=DuneActionOut)
def scale_instance(
    server_id: int,
    map_name: str,
    user: CurrentUser,
    body: DuneScaleRequest,
    db: Session = Depends(get_db),
) -> DuneActionOut:
    server, client = _client(db, server_id)
    with _api_errors():
        payload = client.scale_instance(map_name, body.replicas, force=body.force)
    action = _action(payload, fallback=f"Scaled {map_name} to {body.replicas}.")
    if action.ok and not action.requires_confirmation:
        _log(
            db,
            server,
            f"scale {map_name} {body.replicas}",
            action.detail,
            actor=user,
        )
    return action


@router.post("/instances/dimension/{partition_id}/up", response_model=DuneActionOut)
def dimension_up(
    server_id: int,
    partition_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> DuneActionOut:
    server, client = _client(db, server_id)
    with _api_errors():
        payload = client.dimension_up(partition_id)
    action = _action(payload, fallback=f"Starting partition {partition_id}.")
    if action.ok:
        _log(db, server, f"dimension up {partition_id}", action.detail, actor=user)
    return action


@router.post("/instances/dimension/{partition_id}/down", response_model=DuneActionOut)
def dimension_down(
    server_id: int,
    partition_id: int,
    user: CurrentUser,
    body: DuneForceRequest,
    db: Session = Depends(get_db),
) -> DuneActionOut:
    server, client = _client(db, server_id)
    with _api_errors():
        payload = client.dimension_down(partition_id, force=body.force)
    action = _action(payload, fallback=f"Stopping partition {partition_id}.")
    if action.ok and not action.requires_confirmation:
        _log(db, server, f"dimension down {partition_id}", action.detail, actor=user)
    return action


@router.post("/sietches", response_model=DuneActionOut)
def add_sietch(
    server_id: int,
    user: CurrentUser,
    body: DuneSietchCreate,
    db: Session = Depends(get_db),
) -> DuneActionOut:
    server, client = _client(db, server_id)
    with _api_errors():
        payload = client.add_sietch(body.label)
    action = _action(payload, fallback="Sietch added.")
    if action.ok:
        _log(db, server, "sietch add", action.detail, actor=user)
    return action


@router.post("/sietches/{partition_id}/park", response_model=DuneActionOut)
def park_sietch(
    server_id: int,
    partition_id: int,
    user: CurrentUser,
    body: DuneForceRequest,
    db: Session = Depends(get_db),
) -> DuneActionOut:
    server, client = _client(db, server_id)
    with _api_errors():
        payload = client.park_sietch(partition_id, force=body.force)
    action = _action(payload, fallback=f"Parked sietch {partition_id}.")
    if action.ok and not action.requires_confirmation:
        _log(db, server, f"sietch park {partition_id}", action.detail, actor=user)
    return action


@router.post("/sietches/{partition_id}/unpark", response_model=DuneActionOut)
def unpark_sietch(
    server_id: int,
    partition_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> DuneActionOut:
    server, client = _client(db, server_id)
    with _api_errors():
        payload = client.unpark_sietch(partition_id)
    action = _action(payload, fallback=f"Unparking sietch {partition_id}.")
    if action.ok:
        _log(db, server, f"sietch unpark {partition_id}", action.detail, actor=user)
    return action


@router.post("/sietches/{partition_id}/remove", response_model=DuneActionOut)
def remove_sietch(
    server_id: int,
    partition_id: int,
    user: CurrentUser,
    body: DuneForceRequest,
    db: Session = Depends(get_db),
) -> DuneActionOut:
    server, client = _client(db, server_id)
    with _api_errors():
        payload = client.remove_sietch(partition_id, force=body.force)
    action = _action(payload, fallback=f"Removed sietch {partition_id}.")
    if action.ok and not action.requires_confirmation:
        _log(db, server, f"sietch remove {partition_id}", action.detail, actor=user)
    return action

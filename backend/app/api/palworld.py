"""Palworld-specific admin endpoints (REST API passthrough).

These operations have no equivalent in the other server types — world saves,
graceful shutdown with a countdown, the read-only settings block, the game-data
world snapshot — so they get their own router instead of being forced into the
generic adapter contract. The frontend shows the panel only when a type
advertises ``features.admin_api``.

Destructive calls require ``confirm: true`` in the body, and every mutating call
is written to ``command_history`` so the existing audit view covers them.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.servers import get_rcon_password, get_server_or_404
from app.database import get_db
from app.deps import require_admin
from app.models import CommandHistory, Server
from app.schemas import (
    AnnounceRequest,
    ConfirmRequest,
    PalworldActionOut,
    PalworldBaseCampOut,
    PalworldInfoOut,
    PalworldMetricsOut,
    PalworldPlayerOut,
    PalworldPlayersOut,
    PalworldSettingsOut,
    PalworldShutdownRequest,
    PalworldWorldOut,
    PalworldWorldPlayer,
)
from app.server_types.palworld import (
    client_for_server,
    normalize_info,
    normalize_metrics,
    normalize_player,
)
from app.services.palworld_api import (
    GAMEDATA_DISABLED_CODE,
    PalworldApiError,
    PalworldAuthError,
    PalworldClient,
    PalworldTimeoutError,
    PalworldTlsError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/servers/{server_id}/palworld", tags=["palworld"])

SERVER_TYPE = "palworld"
API_TIMEOUT = 15.0


def _http_error(exc: PalworldApiError) -> HTTPException:
    """Map a transport failure onto an HTTP status the UI can act on."""
    if isinstance(exc, (PalworldAuthError, PalworldTlsError)):
        # Actionable configuration problems (credentials / certificate)
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, PalworldTimeoutError):
        return HTTPException(status_code=504, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


@contextmanager
def _api_errors() -> Iterator[None]:
    try:
        yield
    except PalworldApiError as exc:
        raise _http_error(exc) from exc


def _server(db: Session, server_id: int) -> Server:
    server = get_server_or_404(db, server_id)
    if (server.server_type or "").strip().lower() != SERVER_TYPE:
        raise HTTPException(
            status_code=400,
            detail=f"Server {server_id} is not a Palworld server",
        )
    return server


def _client(db: Session, server_id: int) -> tuple[Server, PalworldClient]:
    server = _server(db, server_id)
    secret = get_rcon_password(server)
    if not secret:
        raise HTTPException(
            status_code=400,
            detail="Server has no admin password configured",
        )
    with _api_errors():
        return server, client_for_server(server, secret, timeout=API_TIMEOUT)


def _log(db: Session, server: Server, command: str, response: str = "") -> None:
    """Record an admin action in the shared command history."""
    try:
        db.add(
            CommandHistory(
                server_id=server.id,
                command=f"palworld:{command}"[:2000],
                response=(response or "ok")[:4000],
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("Could not log Palworld action %s", command, exc_info=True)


def _require_confirm(confirm: bool, what: str) -> None:
    if not confirm:
        raise HTTPException(status_code=400, detail=f"{what} requires confirm=true")


# --- read-only ------------------------------------------------------------


@router.get("/info", response_model=PalworldInfoOut)
def info(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> PalworldInfoOut:
    _, client = _client(db, server_id)
    with _api_errors():
        return PalworldInfoOut(**normalize_info(client.info()))


@router.get("/metrics", response_model=PalworldMetricsOut)
def metrics(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> PalworldMetricsOut:
    _, client = _client(db, server_id)
    with _api_errors():
        return PalworldMetricsOut(**normalize_metrics(client.metrics()))


@router.get("/settings", response_model=PalworldSettingsOut)
def settings(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> PalworldSettingsOut:
    _, client = _client(db, server_id)
    with _api_errors():
        return PalworldSettingsOut(settings=client.settings())


@router.get("/players", response_model=PalworldPlayersOut)
def players(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> PalworldPlayersOut:
    _, client = _client(db, server_id)
    with _api_errors():
        rows = [normalize_player(p) for p in client.players()]
    # normalize_player also carries "score" for the generic roster; drop it here
    return PalworldPlayersOut(players=[PalworldPlayerOut(**r) for r in rows])


# --- world snapshot --------------------------------------------------------


def _actor_str(actor: Mapping[str, Any], key: str) -> str:
    return str(actor.get(key) or "").strip()


def _actor_num(actor: Mapping[str, Any], key: str) -> float | None:
    try:
        return float(actor[key])
    except (KeyError, TypeError, ValueError):
        return None


def _actor_int(actor: Mapping[str, Any], key: str) -> int | None:
    value = _actor_num(actor, key)
    return None if value is None else int(value)


def summarize_game_data(payload: Mapping[str, Any]) -> PalworldWorldOut:
    """Reduce the actor dump to something a browser can hold.

    ``/v1/api/game-data`` returns every actor in the world — players, their
    Pals, wild Pals, NPCs and base camps — which on a busy server is megabytes.
    The panel only needs per-player detail plus counts, so the reduction happens
    here and the raw payload never leaves the backend.
    """
    actors = payload.get("ActorData")
    actors = actors if isinstance(actors, list) else []

    counts: dict[str, int] = {}
    players: list[PalworldWorldPlayer] = []
    camps: list[PalworldBaseCampOut] = []
    # Pals are linked to their owner by the owner's InstanceID
    pals_by_trainer: dict[str, int] = {}
    players_by_instance: dict[str, PalworldWorldPlayer] = {}

    for actor in actors:
        if not isinstance(actor, Mapping):
            continue
        kind = _actor_str(actor, "Type")

        if kind == "PalBox":
            counts["PalBox"] = counts.get("PalBox", 0) + 1
            camps.append(
                PalworldBaseCampOut(
                    guild_name=_actor_str(actor, "GuildName"),
                    guild_id=_actor_str(actor, "GuildID"),
                    location_x=_actor_num(actor, "LocationX"),
                    location_y=_actor_num(actor, "LocationY"),
                    location_z=_actor_num(actor, "LocationZ"),
                )
            )
            continue

        unit = _actor_str(actor, "UnitType") or kind or "Unknown"
        counts[unit] = counts.get(unit, 0) + 1

        trainer = _actor_str(actor, "TrainerInstanceID")
        if trainer:
            pals_by_trainer[trainer] = pals_by_trainer.get(trainer, 0) + 1

        if unit == "Player":
            entry = PalworldWorldPlayer(
                name=_actor_str(actor, "NickName"),
                # game-data spells it lowercase, unlike /players' userId
                user_id=_actor_str(actor, "userid"),
                level=_actor_int(actor, "level"),
                hp=_actor_int(actor, "HP"),
                max_hp=_actor_int(actor, "MaxHP"),
                guild_name=_actor_str(actor, "GuildName"),
                location_x=_actor_num(actor, "LocationX"),
                location_y=_actor_num(actor, "LocationY"),
                location_z=_actor_num(actor, "LocationZ"),
            )
            players.append(entry)
            instance = _actor_str(actor, "InstanceID")
            if instance:
                players_by_instance[instance] = entry

    for instance, count in pals_by_trainer.items():
        owner = players_by_instance.get(instance)
        if owner is not None:
            owner.pal_count = count

    players.sort(key=lambda p: p.name.lower())
    camps.sort(key=lambda c: (c.guild_name.lower(), c.guild_id))

    return PalworldWorldOut(
        enabled=True,
        snapshot_time=str(payload.get("Time") or ""),
        fps=_actor_num(payload, "FPS"),
        average_fps=_actor_num(payload, "AverageFPS"),
        actor_counts=dict(sorted(counts.items())),
        players=players,
        base_camps=camps,
    )


@router.get("/world", response_model=PalworldWorldOut)
def world(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> PalworldWorldOut:
    _, client = _client(db, server_id)
    try:
        payload = client.game_data()
    except PalworldApiError as exc:
        if exc.code == GAMEDATA_DISABLED_CODE:
            # Not a failure the operator can fix from here — explain the flag
            return PalworldWorldOut(enabled=False, hint=str(exc))
        raise _http_error(exc) from exc
    return summarize_game_data(payload)


# --- mutating --------------------------------------------------------------


@router.post("/announce", response_model=PalworldActionOut)
def announce(
    server_id: int,
    body: AnnounceRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> PalworldActionOut:
    server, client = _client(db, server_id)
    message = body.message.strip()
    with _api_errors():
        detail = client.announce(message)
    _log(db, server, f"announce {message}", detail)
    return PalworldActionOut(ok=True, detail=detail)


@router.post("/save", response_model=PalworldActionOut)
def save(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> PalworldActionOut:
    server, client = _client(db, server_id)
    with _api_errors():
        detail = client.save()
    _log(db, server, "save", detail)
    return PalworldActionOut(ok=True, detail=detail)


@router.post("/shutdown", response_model=PalworldActionOut)
def shutdown(
    server_id: int,
    body: PalworldShutdownRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> PalworldActionOut:
    _require_confirm(body.confirm, "Shutting the server down")
    server, client = _client(db, server_id)
    message = body.message.strip()
    with _api_errors():
        detail = client.shutdown(body.waittime, message)
    _log(db, server, f"shutdown {body.waittime} {message}".strip(), detail)
    return PalworldActionOut(ok=True, detail=detail)


@router.post("/stop", response_model=PalworldActionOut)
def stop(
    server_id: int,
    body: ConfirmRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> PalworldActionOut:
    _require_confirm(body.confirm, "Force-stopping the server")
    server, client = _client(db, server_id)
    with _api_errors():
        detail = client.stop()
    _log(db, server, "stop", detail)
    return PalworldActionOut(
        ok=True,
        # /stop terminates immediately without writing the world
        detail=f"{detail}\nForce stop does not save — unsaved progress is lost.",
    )

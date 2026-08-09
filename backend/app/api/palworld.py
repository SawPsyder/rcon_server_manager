"""Palworld-specific admin endpoints (REST API passthrough).

These operations have no equivalent in the other server types - world saves,
graceful shutdown with a countdown, the read-only settings block, the game-data
world snapshot - so they get their own router instead of being forced into the
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
from app.deps import CurrentUser
from app.models import CommandHistory, Server, User
from app.schemas import (
    AnnounceRequest,
    ConfirmRequest,
    PalworldActionOut,
    PalworldBaseCampOut,
    PalworldInfoOut,
    PalworldMapEntity,
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


def _log(
    db: Session,
    server: Server,
    command: str,
    response: str = "",
    actor: User | None = None,
) -> None:
    """Record an admin action in the shared command history."""
    try:
        db.add(
            CommandHistory(
                server_id=server.id,
                command=f"palworld:{command}"[:2000],
                response=(response or "ok")[:4000],
                actor_user_id=actor.id if actor else None,
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
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> PalworldInfoOut:
    _, client = _client(db, server_id)
    with _api_errors():
        return PalworldInfoOut(**normalize_info(client.info()))


@router.get("/metrics", response_model=PalworldMetricsOut)
def metrics(
    server_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> PalworldMetricsOut:
    _, client = _client(db, server_id)
    with _api_errors():
        return PalworldMetricsOut(**normalize_metrics(client.metrics()))


@router.get("/settings", response_model=PalworldSettingsOut)
def settings(
    server_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> PalworldSettingsOut:
    _, client = _client(db, server_id)
    with _api_errors():
        return PalworldSettingsOut(settings=client.settings())


@router.get("/players", response_model=PalworldPlayersOut)
def players(
    server_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
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


def _payload_pick(payload: Mapping[str, Any], *names: str) -> Any:
    """Case-insensitive lookup for top-level game-data fields."""
    lower = {str(k).lower(): v for k, v in payload.items()}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def _humanize_class(class_name: str) -> str:
    """BP_SheepBall_C → SheepBall; empty when nothing useful remains."""
    text = (class_name or "").strip()
    if not text:
        return ""
    if text.startswith("BP_"):
        text = text[3:]
    for suffix in ("_C", "_v03", "_v02", "_v01"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    # Drop long builder prefixes for structure-like classes
    for prefix in ("BuildObject_", "NPC_Female_", "NPC_Male_", "Player_"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text.replace("_", " ").strip()


def _species_label(actor: Mapping[str, Any]) -> str:
    """Prefer a readable NickName; fall back to humanized blueprint Class."""
    nick = _actor_str(actor, "NickName")
    klass = _humanize_class(_actor_str(actor, "Class"))
    if nick and nick.upper() not in {"BASE", "NONE"}:
        return nick
    return klass or nick


def _activity_label(actor: Mapping[str, Any]) -> str:
    """Shorten BP_AIAction_Worker_Working → Worker Working."""
    raw = _actor_str(actor, "AI_Action") or _actor_str(actor, "Action")
    if not raw:
        return ""
    text = raw
    for prefix in ("BP_AIAction_", "BP_Action_", "BP_"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    if text.endswith("_C"):
        text = text[:-2]
    return text.replace("_", " ").strip()


def _stable_actor_id(actor: Mapping[str, Any], prefix: str, index: int) -> str:
    instance = _actor_str(actor, "InstanceID")
    if instance:
        # InstanceIDs can contain spaces/colons; keep them but namespace the layer
        return f"{prefix}:{instance}"
    gx = _actor_num(actor, "LocationX")
    gy = _actor_num(actor, "LocationY")
    return f"{prefix}:{index}:{gx}:{gy}"


def _map_entity(actor: Mapping[str, Any], *, prefix: str, index: int) -> PalworldMapEntity:
    species = _species_label(actor)
    name = _actor_str(actor, "NickName") or species
    return PalworldMapEntity(
        id=_stable_actor_id(actor, prefix, index),
        name=name,
        species=species,
        level=_actor_int(actor, "level"),
        hp=_actor_int(actor, "HP"),
        max_hp=_actor_int(actor, "MaxHP"),
        guild_name=_actor_str(actor, "GuildName"),
        guild_id=_actor_str(actor, "GuildID"),
        location_x=_actor_num(actor, "LocationX"),
        location_y=_actor_num(actor, "LocationY"),
        location_z=_actor_num(actor, "LocationZ"),
        rotation_z=_actor_num(actor, "RotationZ"),
        activity=_activity_label(actor),
    )


# Soft caps so a 10k-actor public dump cannot flood the admin browser.
_CAP_WORKERS = 800
_CAP_WILD = 500
_CAP_NPCS = 200
_CAP_OTOMO = 200


def _cap(entities: list[PalworldMapEntity], limit: int) -> list[PalworldMapEntity]:
    if len(entities) <= limit:
        return entities
    # Prefer higher level / closer-to-origin for determinism
    entities.sort(
        key=lambda e: (
            -(e.level or 0),
            abs(e.location_x or 0) + abs(e.location_y or 0),
            e.id,
        )
    )
    return entities[:limit]


def summarize_game_data(payload: Mapping[str, Any]) -> PalworldWorldOut:
    """Reduce the actor dump to something a browser can hold.

    ``/v1/api/game-data`` returns every actor in the world - players, their
    Pals, wild Pals, NPCs and base camps - which on a busy server is megabytes.
    The map needs positioned entities plus counts; reduction happens here so the
    raw dump never leaves the backend.
    """
    actors = payload.get("ActorData")
    actors = actors if isinstance(actors, list) else []

    counts: dict[str, int] = {}
    players: list[PalworldWorldPlayer] = []
    camps: list[PalworldBaseCampOut] = []
    workers: list[PalworldMapEntity] = []
    wild_pals: list[PalworldMapEntity] = []
    npcs: list[PalworldMapEntity] = []
    otomo: list[PalworldMapEntity] = []
    # Pals are linked to their owner by the owner's InstanceID
    pals_by_trainer: dict[str, int] = {}
    players_by_instance: dict[str, PalworldWorldPlayer] = {}
    camp_i = worker_i = wild_i = npc_i = otomo_i = 0

    for actor in actors:
        if not isinstance(actor, Mapping):
            continue
        kind = _actor_str(actor, "Type")

        if kind == "PalBox":
            counts["PalBox"] = counts.get("PalBox", 0) + 1
            gid = _actor_str(actor, "GuildID")
            camps.append(
                PalworldBaseCampOut(
                    id=f"camp:{gid or camp_i}:{camp_i}",
                    guild_name=_actor_str(actor, "GuildName"),
                    guild_id=gid,
                    name=_actor_str(actor, "Name"),
                    location_x=_actor_num(actor, "LocationX"),
                    location_y=_actor_num(actor, "LocationY"),
                    location_z=_actor_num(actor, "LocationZ"),
                )
            )
            camp_i += 1
            continue

        unit = _actor_str(actor, "UnitType") or kind or "Unknown"
        counts[unit] = counts.get(unit, 0) + 1

        trainer = _actor_str(actor, "TrainerInstanceID")
        if trainer:
            pals_by_trainer[trainer] = pals_by_trainer.get(trainer, 0) + 1

        if unit == "Player":
            instance = _actor_str(actor, "InstanceID")
            entry = PalworldWorldPlayer(
                name=_actor_str(actor, "NickName"),
                # game-data spells it lowercase, unlike /players' userId
                user_id=_actor_str(actor, "userid"),
                level=_actor_int(actor, "level"),
                hp=_actor_int(actor, "HP"),
                max_hp=_actor_int(actor, "MaxHP"),
                guild_name=_actor_str(actor, "GuildName"),
                guild_id=_actor_str(actor, "GuildID"),
                location_x=_actor_num(actor, "LocationX"),
                location_y=_actor_num(actor, "LocationY"),
                location_z=_actor_num(actor, "LocationZ"),
                rotation_z=_actor_num(actor, "RotationZ"),
            )
            players.append(entry)
            if instance:
                players_by_instance[instance] = entry
            continue

        if unit == "BaseCampPal":
            workers.append(_map_entity(actor, prefix="worker", index=worker_i))
            worker_i += 1
        elif unit == "WildPal":
            wild_pals.append(_map_entity(actor, prefix="wild", index=wild_i))
            wild_i += 1
        elif unit == "NPC":
            npcs.append(_map_entity(actor, prefix="npc", index=npc_i))
            npc_i += 1
        elif unit == "OtomoPal":
            otomo.append(_map_entity(actor, prefix="otomo", index=otomo_i))
            otomo_i += 1

    for instance, count in pals_by_trainer.items():
        owner = players_by_instance.get(instance)
        if owner is not None:
            owner.pal_count = count

    players.sort(key=lambda p: p.name.lower())
    camps.sort(key=lambda c: (c.guild_name.lower(), c.guild_id, c.id))
    workers = _cap(workers, _CAP_WORKERS)
    wild_pals = _cap(wild_pals, _CAP_WILD)
    npcs = _cap(npcs, _CAP_NPCS)
    otomo = _cap(otomo, _CAP_OTOMO)
    workers.sort(key=lambda e: (e.guild_name.lower(), e.name.lower(), e.id))
    wild_pals.sort(key=lambda e: (e.species.lower(), e.name.lower(), e.id))
    npcs.sort(key=lambda e: (e.name.lower(), e.id))
    otomo.sort(key=lambda e: (e.name.lower(), e.id))

    days_raw = _payload_pick(payload, "InGameDays", "Days", "days")
    try:
        in_game_days = int(days_raw) if days_raw is not None and days_raw != "" else None
    except (TypeError, ValueError):
        in_game_days = None
    in_game_time = str(_payload_pick(payload, "InGameTime", "GameTime") or "").strip()

    def _float_pick(*names: str) -> float | None:
        raw = _payload_pick(payload, *names)
        try:
            return float(raw) if raw is not None and raw != "" else None
        except (TypeError, ValueError):
            return None

    return PalworldWorldOut(
        enabled=True,
        snapshot_time=str(_payload_pick(payload, "Time") or ""),
        fps=_float_pick("FPS", "fps"),
        average_fps=_float_pick("AverageFPS", "averagefps"),
        in_game_time=in_game_time,
        in_game_days=in_game_days,
        actor_counts=dict(sorted(counts.items())),
        players=players,
        base_camps=camps,
        workers=workers,
        wild_pals=wild_pals,
        npcs=npcs,
        otomo_pals=otomo,
    )


@router.get("/world", response_model=PalworldWorldOut)
def world(
    server_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> PalworldWorldOut:
    _, client = _client(db, server_id)
    try:
        payload = client.game_data()
    except PalworldApiError as exc:
        if exc.code == GAMEDATA_DISABLED_CODE:
            # Not a failure the operator can fix from here - explain the flag
            return PalworldWorldOut(enabled=False, hint=str(exc))
        raise _http_error(exc) from exc
    return summarize_game_data(payload)


# --- mutating --------------------------------------------------------------


@router.post("/announce", response_model=PalworldActionOut)
def announce(
    server_id: int,
    user: CurrentUser,
    body: AnnounceRequest,
    db: Session = Depends(get_db),
) -> PalworldActionOut:
    server, client = _client(db, server_id)
    message = body.message.strip()
    with _api_errors():
        detail = client.announce(message)
    _log(db, server, f"announce {message}", detail, actor=user)
    return PalworldActionOut(ok=True, detail=detail)


@router.post("/save", response_model=PalworldActionOut)
def save(
    server_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> PalworldActionOut:
    server, client = _client(db, server_id)
    with _api_errors():
        detail = client.save()
    _log(db, server, "save", detail, actor=user)
    return PalworldActionOut(ok=True, detail=detail)


@router.post("/shutdown", response_model=PalworldActionOut)
def shutdown(
    server_id: int,
    user: CurrentUser,
    body: PalworldShutdownRequest,
    db: Session = Depends(get_db),
) -> PalworldActionOut:
    _require_confirm(body.confirm, "Shutting the server down")
    server, client = _client(db, server_id)
    message = body.message.strip()
    with _api_errors():
        detail = client.shutdown(body.waittime, message)
    _log(db, server, f"shutdown {body.waittime} {message}".strip(), detail, actor=user)
    return PalworldActionOut(ok=True, detail=detail)


@router.post("/stop", response_model=PalworldActionOut)
def stop(
    server_id: int,
    user: CurrentUser,
    body: ConfirmRequest,
    db: Session = Depends(get_db),
) -> PalworldActionOut:
    _require_confirm(body.confirm, "Force-stopping the server")
    server, client = _client(db, server_id)
    with _api_errors():
        detail = client.stop()
    _log(db, server, "stop", detail, actor=user)
    return PalworldActionOut(
        ok=True,
        # /stop terminates immediately without writing the world
        detail=f"{detail}\nForce stop does not save - unsaved progress is lost.",
    )

"""Admin-only CRUD and run history for Pterodactyl-linked server schedules."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.deps import AdminUser
from app.models import (
    ScheduleAction,
    ScheduleCheck,
    ScheduleRun,
    Server,
    ServerSchedule,
    utcnow,
)
from app.schemas import (
    ScheduleCreate,
    ScheduleEnable,
    ScheduleMetaOut,
    ScheduleOut,
    ScheduleRunOut,
    ScheduleUpdate,
)
from app.services.schedule_actions import (
    server_is_linked,
    validate_action_for_server,
    validate_check,
)
from app.services.schedule_runner import is_claim_active, runner as schedule_runner
from app.services.schedule_time import (
    format_days_of_week,
    load_app_timezone,
    next_occurrence,
    parse_days_of_week,
    parse_time_local,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


def _params_json(params: dict[str, Any] | None) -> str:
    return json.dumps(params or {}, separators=(",", ":"))


def _load_params(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _days_list(value: str) -> list[int]:
    return sorted(parse_days_of_week(value))


def _get_server_or_400(db: Session, server_id: int) -> Server:
    server = db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    if not server_is_linked(server):
        raise HTTPException(
            status_code=400,
            detail="Schedules require a Pterodactyl-linked server.",
        )
    return server


def _validate_payload(
    server: Server,
    *,
    time_local: str,
    days_of_week: list[int],
    actions: list,
    checks: list,
) -> None:
    try:
        parse_time_local(time_local)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not days_of_week:
        raise HTTPException(
            status_code=400,
            detail="days_of_week must include at least one weekday (0=Mon … 6=Sun)",
        )
    try:
        parse_days_of_week(format_days_of_week(days_of_week))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for day in days_of_week:
        if day < 0 or day > 6:
            raise HTTPException(status_code=400, detail=f"Invalid weekday: {day}")

    for i, action in enumerate(actions):
        params = getattr(action, "params", None) or {}
        err = validate_action_for_server(server, action.action_type, params)
        if err:
            raise HTTPException(status_code=400, detail=f"Action[{i}]: {err}")
    for i, check in enumerate(checks):
        params = getattr(check, "params", None) or {}
        err = validate_check(check.check_type, params)
        if err:
            raise HTTPException(status_code=400, detail=f"Check[{i}]: {err}")


def _replace_children(
    db: Session,
    schedule: ServerSchedule,
    *,
    actions: list | None,
    checks: list | None,
) -> None:
    if actions is not None:
        schedule.actions.clear()
        db.flush()
        for i, action in enumerate(actions):
            schedule.actions.append(
                ScheduleAction(
                    sort_order=action.sort_order if action.sort_order is not None else i,
                    action_type=action.action_type,
                    params_json=_params_json(action.params),
                )
            )
    if checks is not None:
        schedule.checks.clear()
        db.flush()
        for i, check in enumerate(checks):
            schedule.checks.append(
                ScheduleCheck(
                    sort_order=check.sort_order if check.sort_order is not None else i,
                    check_type=check.check_type,
                    params_json=_params_json(check.params),
                )
            )


def _action_fingerprint(action_type: str, params: dict[str, Any], sort_order: int) -> str:
    return json.dumps(
        {"t": action_type, "p": params or {}, "s": sort_order},
        sort_keys=True,
        separators=(",", ":"),
    )


def _actions_payload_changed(schedule: ServerSchedule, new_actions: list) -> bool:
    """True when the incoming action list differs from the stored chain.

    Full-form PUTs always send actions; only a real change must cancel a
    parked cooperative wait (stale resume_action_index would mis-run).
    """
    old = sorted(schedule.actions or [], key=lambda a: (a.sort_order, a.id or 0))
    new_fps = [
        _action_fingerprint(
            a.action_type,
            getattr(a, "params", None) or {},
            a.sort_order if a.sort_order is not None else i,
        )
        for i, a in enumerate(new_actions)
    ]
    old_fps = [
        _action_fingerprint(a.action_type, _load_params(a.params_json), a.sort_order)
        for a in old
    ]
    return old_fps != new_fps


def _cancel_in_flight_wait(
    schedule: ServerSchedule,
    db: Session,
    *,
    reason: str,
) -> None:
    """Abort cooperative wait / clear resume so a mutated chain cannot resume."""
    had_wait = schedule.resume_action_index is not None or (
        (schedule.last_status or "") == "waiting"
    )
    schedule.resume_action_index = None
    schedule.pending_detail_json = "{}"
    if not had_wait:
        return
    # Drop the current window so we do not immediately re-fire mid-countdown
    # with a different action list.
    schedule.active_window_at = None
    tz = load_app_timezone(db)
    now = datetime.now(timezone.utc)
    schedule.next_run_at = next_occurrence(
        time_local=schedule.time_local,
        days_of_week=schedule.days_of_week,
        tz=tz,
        after=now,
        inclusive=False,
    )
    schedule.last_status = "cancelled"
    schedule.last_message = reason
    schedule.last_run_at = utcnow()


def _to_out(db: Session, schedule: ServerSchedule) -> ScheduleOut:
    server = schedule.server
    tz = load_app_timezone(db)
    return ScheduleOut(
        id=schedule.id,
        server_id=schedule.server_id,
        server_name=server.name if server else "",
        server_type=(server.server_type if server else "") or "",
        pterodactyl_linked=bool(server and server_is_linked(server)),
        name=schedule.name,
        enabled=schedule.enabled,
        time_local=schedule.time_local,
        days_of_week=_days_list(schedule.days_of_week),
        retry_after_minutes=schedule.retry_after_minutes,
        next_run_at=schedule.next_run_at,
        last_run_at=schedule.last_run_at,
        last_status=schedule.last_status or "",
        last_message=schedule.last_message or "",
        active_window_at=schedule.active_window_at,
        app_timezone=tz,
        actions=[
            {
                "id": a.id,
                "action_type": a.action_type,
                "params": _load_params(a.params_json),
                "sort_order": a.sort_order,
            }
            for a in sorted(schedule.actions or [], key=lambda x: (x.sort_order, x.id or 0))
        ],
        checks=[
            {
                "id": c.id,
                "check_type": c.check_type,
                "params": _load_params(c.params_json),
                "sort_order": c.sort_order,
            }
            for c in sorted(schedule.checks or [], key=lambda x: (x.sort_order, x.id or 0))
        ],
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


def _load_schedule(db: Session, schedule_id: int) -> ServerSchedule:
    schedule = (
        db.query(ServerSchedule)
        .options(
            joinedload(ServerSchedule.actions),
            joinedload(ServerSchedule.checks),
            joinedload(ServerSchedule.server),
        )
        .filter(ServerSchedule.id == schedule_id)
        .first()
    )
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule


def _types_with_feature(attr: str) -> list[str]:
    from app.server_types import list_adapters

    return [
        a.info.id
        for a in list_adapters()
        if getattr(a.info.features, attr, False)
    ]


def _run_out(
    db: Session,
    row: ScheduleRun,
    *,
    name_cache: dict[int, str] | None = None,
    server_cache: dict[int, str] | None = None,
) -> ScheduleRunOut:
    schedule_name = ""
    server_name = ""
    if row.schedule_id:
        if name_cache is not None and row.schedule_id in name_cache:
            schedule_name = name_cache[row.schedule_id]
        else:
            sch = db.get(ServerSchedule, row.schedule_id)
            schedule_name = (sch.name if sch else "") or f"#{row.schedule_id}"
            if name_cache is not None:
                name_cache[row.schedule_id] = schedule_name
    if row.server_id:
        if server_cache is not None and row.server_id in server_cache:
            server_name = server_cache[row.server_id]
        else:
            srv = db.get(Server, row.server_id)
            server_name = (srv.name if srv else "") or f"#{row.server_id}"
            if server_cache is not None:
                server_cache[row.server_id] = server_name
    return ScheduleRunOut(
        id=row.id,
        schedule_id=row.schedule_id,
        server_id=row.server_id,
        schedule_name=schedule_name,
        server_name=server_name,
        scheduled_for=row.scheduled_for,
        started_at=row.started_at,
        finished_at=row.finished_at,
        status=row.status,
        attempt=row.attempt,
        detail=_load_params(row.detail_json),
        message=row.message or "",
    )


@router.get("/meta", response_model=ScheduleMetaOut)
def schedule_meta(_admin: AdminUser, db: Session = Depends(get_db)) -> ScheduleMetaOut:
    """Action catalog with per-type applicability for the UI filter.

    Empty ``server_types`` means every linked server type can use the action.
    """
    say_types = _types_with_feature("admin_say")
    console_types = _types_with_feature("console")
    travel_types = _types_with_feature("map_travel")
    # Startup-map writes are Sandstorm egg vars, not a generic feature flag.
    startup_map_types = ["sandstorm"] if "sandstorm" in travel_types else []

    return ScheduleMetaOut(
        app_timezone=load_app_timezone(db),
        action_types=[
            {
                "id": "power",
                "label": "Power action",
                "params": ["signal"],
            },
            {
                "id": "wait",
                "label": "Wait",
                "params": ["seconds"],
            },
            {
                "id": "say",
                "label": "Broadcast message",
                "params": ["message"],
                "server_types": say_types,
            },
            {
                "id": "rcon",
                "label": "Custom RCON / API command",
                "params": ["command"],
                "server_types": console_types,
            },
            {
                "id": "travel",
                "label": "Change map (travel)",
                "params": ["map_id", "gamemode_key", "lighting"],
                "server_types": travel_types,
            },
            {
                "id": "travel_popular",
                "label": "Travel to most popular map",
                "params": ["range", "combine_gamemodes", "lighting"],
                "server_types": travel_types,
            },
            {
                "id": "set_startup_current",
                "label": "Set startup map to current map",
                "params": [],
                "server_types": startup_map_types,
            },
            {
                "id": "set_startup_popular",
                "label": "Set startup map to most popular map",
                "params": ["range", "combine_gamemodes"],
                "server_types": startup_map_types,
            },
        ],
        check_types=[
            {"id": "players_lte", "label": "Players ≤", "params": ["value"]},
            {"id": "players_gte", "label": "Players ≥", "params": ["value"]},
            {"id": "players_eq", "label": "Players =", "params": ["value"]},
            {"id": "server_online", "label": "Server online", "params": []},
            {"id": "server_offline", "label": "Server offline", "params": []},
            {
                "id": "container_state",
                "label": "Container state",
                "params": ["state"],
            },
        ],
    )


@router.get("/runs", response_model=list[ScheduleRunOut])
def list_all_runs(
    _admin: AdminUser,
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    schedule_id: int | None = Query(default=None),
) -> list[ScheduleRunOut]:
    """Merged run history across schedules (newest first)."""
    q = db.query(ScheduleRun)
    if schedule_id is not None:
        q = q.filter(ScheduleRun.schedule_id == schedule_id)
    rows = q.order_by(ScheduleRun.started_at.desc()).limit(limit).all()
    name_cache: dict[int, str] = {}
    server_cache: dict[int, str] = {}
    return [
        _run_out(db, row, name_cache=name_cache, server_cache=server_cache)
        for row in rows
    ]


@router.get("", response_model=list[ScheduleOut])
def list_schedules(
    _admin: AdminUser,
    server_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[ScheduleOut]:
    q = db.query(ServerSchedule).options(
        joinedload(ServerSchedule.actions),
        joinedload(ServerSchedule.checks),
        joinedload(ServerSchedule.server),
    )
    if server_id is not None:
        q = q.filter(ServerSchedule.server_id == server_id)
    rows = q.order_by(ServerSchedule.server_id.asc(), ServerSchedule.id.asc()).all()
    return [_to_out(db, row) for row in rows]


@router.get("/{schedule_id}", response_model=ScheduleOut)
def get_schedule(
    schedule_id: int,
    _admin: AdminUser,
    db: Session = Depends(get_db),
) -> ScheduleOut:
    return _to_out(db, _load_schedule(db, schedule_id))


@router.post("", response_model=ScheduleOut)
def create_schedule(
    body: ScheduleCreate,
    _admin: AdminUser,
    db: Session = Depends(get_db),
) -> ScheduleOut:
    server = _get_server_or_400(db, body.server_id)
    _validate_payload(
        server,
        time_local=body.time_local,
        days_of_week=body.days_of_week,
        actions=body.actions,
        checks=body.checks,
    )
    tz = load_app_timezone(db)
    now = datetime.now(timezone.utc)
    schedule = ServerSchedule(
        server_id=server.id,
        name=body.name,  # stripped by schema validator
        enabled=body.enabled,
        time_local=body.time_local.strip(),
        days_of_week=format_days_of_week(body.days_of_week),
        retry_after_minutes=body.retry_after_minutes,
        next_run_at=next_occurrence(
            time_local=body.time_local.strip(),
            days_of_week=format_days_of_week(body.days_of_week),
            tz=tz,
            after=now,
            inclusive=True,
        ),
        last_status="",
        last_message="",
    )
    db.add(schedule)
    db.flush()
    _replace_children(db, schedule, actions=body.actions, checks=body.checks)
    db.commit()
    return _to_out(db, _load_schedule(db, schedule.id))


@router.put("/{schedule_id}", response_model=ScheduleOut)
def update_schedule(
    schedule_id: int,
    body: ScheduleUpdate,
    _admin: AdminUser,
    db: Session = Depends(get_db),
) -> ScheduleOut:
    schedule = _load_schedule(db, schedule_id)
    server = schedule.server or _get_server_or_400(db, schedule.server_id)
    was_enabled = schedule.enabled

    time_local = body.time_local if body.time_local is not None else schedule.time_local
    days = (
        body.days_of_week
        if body.days_of_week is not None
        else _days_list(schedule.days_of_week)
    )
    actions = body.actions if body.actions is not None else [
        type("A", (), {
            "action_type": a.action_type,
            "params": _load_params(a.params_json),
            "sort_order": a.sort_order,
        })()
        for a in schedule.actions
    ]
    checks = body.checks if body.checks is not None else [
        type("C", (), {
            "check_type": c.check_type,
            "params": _load_params(c.params_json),
            "sort_order": c.sort_order,
        })()
        for c in schedule.checks
    ]

    # If server is no longer linked, still allow disabling / deleting-like edits
    # but reject re-enabling with actions.
    if body.enabled is not False and not server_is_linked(server):
        raise HTTPException(
            status_code=400,
            detail="Server is no longer linked to Pterodactyl.",
        )

    _validate_payload(
        server,
        time_local=time_local,
        days_of_week=days,
        actions=actions,
        checks=checks,
    )

    if body.name is not None:
        schedule.name = body.name  # already stripped by schema validator
    if body.enabled is not None:
        schedule.enabled = body.enabled
    if body.retry_after_minutes is not None:
        schedule.retry_after_minutes = body.retry_after_minutes

    time_changed = False
    if body.time_local is not None and body.time_local.strip() != schedule.time_local:
        schedule.time_local = body.time_local.strip()
        time_changed = True
    if body.days_of_week is not None:
        new_days = format_days_of_week(body.days_of_week)
        if new_days != schedule.days_of_week:
            schedule.days_of_week = new_days
            time_changed = True

    actions_changed = False
    if body.actions is not None:
        actions_changed = _actions_payload_changed(schedule, body.actions)

    if body.actions is not None or body.checks is not None:
        # Detect action mutation before replace so we can cancel a parked wait.
        if actions_changed and (
            schedule.resume_action_index is not None
            or (schedule.last_status or "") == "waiting"
        ):
            _cancel_in_flight_wait(
                schedule,
                db,
                reason="Wait cancelled: action list changed",
            )
        _replace_children(
            db,
            schedule,
            actions=body.actions,
            checks=body.checks,
        )

    # Only recompute next_run_at on a false→true enable or when the calendar
    # changes. A full-form PUT always sends enabled=true; treating that as a
    # reset would abort in-progress retries / cooperative waits.
    just_enabled = body.enabled is True and not was_enabled
    if body.enabled is False:
        schedule.active_window_at = None
        schedule.resume_action_index = None
        schedule.pending_detail_json = "{}"
    elif time_changed or just_enabled:
        tz = load_app_timezone(db)
        now = datetime.now(timezone.utc)
        schedule.active_window_at = None
        schedule.resume_action_index = None
        schedule.pending_detail_json = "{}"
        schedule.next_run_at = next_occurrence(
            time_local=schedule.time_local,
            days_of_week=schedule.days_of_week,
            tz=tz,
            after=now,
            inclusive=True,
        )

    schedule.updated_at = utcnow()
    db.commit()
    return _to_out(db, _load_schedule(db, schedule.id))


@router.post("/{schedule_id}/enable", response_model=ScheduleOut)
def enable_schedule(
    schedule_id: int,
    body: ScheduleEnable,
    _admin: AdminUser,
    db: Session = Depends(get_db),
) -> ScheduleOut:
    schedule = _load_schedule(db, schedule_id)
    if body.enabled and schedule.server and not server_is_linked(schedule.server):
        raise HTTPException(
            status_code=400,
            detail="Cannot enable: server is not linked to Pterodactyl.",
        )
    schedule.enabled = body.enabled
    if body.enabled:
        tz = load_app_timezone(db)
        schedule.active_window_at = None
        schedule.resume_action_index = None
        schedule.pending_detail_json = "{}"
        schedule.next_run_at = next_occurrence(
            time_local=schedule.time_local,
            days_of_week=schedule.days_of_week,
            tz=tz,
            after=datetime.now(timezone.utc),
            inclusive=True,
        )
    else:
        schedule.active_window_at = None
        schedule.resume_action_index = None
        schedule.pending_detail_json = "{}"
    schedule.updated_at = utcnow()
    db.commit()
    return _to_out(db, _load_schedule(db, schedule.id))


@router.delete("/{schedule_id}")
def delete_schedule(
    schedule_id: int,
    _admin: AdminUser,
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    schedule = _load_schedule(db, schedule_id)
    # Preserve audit history: null schedule_id (matches FK ON DELETE SET NULL).
    db.query(ScheduleRun).filter(ScheduleRun.schedule_id == schedule_id).update(
        {ScheduleRun.schedule_id: None},
        synchronize_session=False,
    )
    db.delete(schedule)
    db.commit()
    return {"ok": True}


@router.get("/{schedule_id}/runs", response_model=list[ScheduleRunOut])
def list_runs(
    schedule_id: int,
    _admin: AdminUser,
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ScheduleRunOut]:
    _load_schedule(db, schedule_id)
    rows = (
        db.query(ScheduleRun)
        .filter(ScheduleRun.schedule_id == schedule_id)
        .order_by(ScheduleRun.started_at.desc())
        .limit(limit)
        .all()
    )
    name_cache: dict[int, str] = {}
    server_cache: dict[int, str] = {}
    return [
        _run_out(db, row, name_cache=name_cache, server_cache=server_cache)
        for row in rows
    ]


@router.post("/{schedule_id}/run-now", response_model=ScheduleOut)
def run_now(
    schedule_id: int,
    _admin: AdminUser,
    db: Session = Depends(get_db),
) -> ScheduleOut:
    """Queue the schedule to run immediately (still runs checks).

    Rejects when a non-wait claim lease is active so two workers cannot
    double-execute power/RCON. Parked cooperative waits may be interrupted.
    Execution is kicked off on a daemon thread so the HTTP request does not
    block on slow panel/RCON chains; the background runner also picks it up.
    """
    schedule = _load_schedule(db, schedule_id)
    if not schedule.enabled:
        raise HTTPException(status_code=400, detail="Schedule is disabled")
    if schedule.server and not server_is_linked(schedule.server):
        raise HTTPException(status_code=400, detail="Server is not Pterodactyl-linked")
    now = datetime.now(timezone.utc)
    if is_claim_active(schedule, now=now):
        raise HTTPException(
            status_code=409,
            detail="Schedule is currently running; try again in a few minutes.",
        )
    # Ad-hoc window id so history does not invent a future calendar slot.
    # Clearing resume aborts any parked wait (intentional for run-now).
    schedule.active_window_at = now
    schedule.resume_action_index = None
    schedule.pending_detail_json = "{}"
    schedule.next_run_at = now
    schedule.last_message = "Queued for immediate run"
    db.commit()

    def _kick() -> None:
        try:
            schedule_runner.run_one(schedule_id)
        except Exception:  # noqa: BLE001
            logger.exception("Background run-now failed for schedule %s", schedule_id)

    # Do not block the request on RCON/panel; tick will also claim if the
    # thread is slow to start.
    threading.Thread(
        target=_kick, name=f"schedule-run-now-{schedule_id}", daemon=True
    ).start()
    return _to_out(db, _load_schedule(db, schedule_id))

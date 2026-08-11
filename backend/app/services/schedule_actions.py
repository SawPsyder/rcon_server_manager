"""Evaluate schedule checks and execute schedule actions.

Shared by the background runner (no actor) and optional manual run-now.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.api.stats import RANGE_DELTAS
from app.config import get_settings
from app.models import (
    CommandHistory,
    MapConfig,
    PlayerCountSample,
    PterodactylSample,
    ScheduleAction,
    ScheduleCheck,
    Server,
)
from app.security import decrypt_secret
from app.server_types import DEFAULT_SERVER_TYPE, get_adapter
from app.services import pterodactyl_api, pterodactyl_settings
from app.services.errors import CommandError
from app.services.map_stats import DEFAULT_MIN_ACTIVE_MINUTES, aggregate_map_stats
from app.services.pterodactyl_api import MAP_DEFAULT_ENV_KEYS, PterodactylApiError
from app.services.server_options import load_options, option_str

logger = logging.getLogger(__name__)

# Legacy per-signal action ids still accepted from older schedules.
_LEGACY_POWER = {
    "power_start": "start",
    "power_stop": "stop",
    "power_restart": "restart",
    "power_kill": "kill",
}
POWER_SIGNALS = frozenset({"start", "stop", "restart", "kill"})
# Cap wait so a misconfigured schedule cannot park the runner for hours.
WAIT_SECONDS_MIN = 1
WAIT_SECONDS_MAX = 3600

ACTION_TYPES = frozenset(
    {
        "power",
        *_LEGACY_POWER,
        "wait",
        "rcon",
        "say",
        "travel",
        "travel_popular",
        "set_startup_current",
        "set_startup_popular",
    }
)

CHECK_TYPES = frozenset(
    {
        "players_lte",
        "players_gte",
        "players_eq",
        "server_online",
        "server_offline",
        "container_state",
    }
)


@dataclass
class CheckResult:
    check_type: str
    ok: bool
    message: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionResult:
    action_type: str
    ok: bool
    message: str
    params: dict[str, Any] = field(default_factory=dict)


def _params(raw: str | None) -> dict[str, Any]:
    if not raw or not str(raw).strip():
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _int_param(params: dict[str, Any], *keys: str, default: int | None = None) -> int | None:
    """Parse the first present key as int.

    A present but non-numeric value fails closed (returns None) rather than
    silently falling back to ``default`` — empty-server guards must not treat
    garbage params as 0.
    """
    for key in keys:
        if key in params and params[key] is not None:
            try:
                return int(params[key])
            except (TypeError, ValueError):
                return None
    return default


def server_is_linked(server: Server) -> bool:
    return bool(option_str(server, "pterodactyl_uuid"))


def evaluate_checks(
    db: Session,
    server: Server,
    checks: list[ScheduleCheck],
) -> tuple[bool, list[CheckResult]]:
    """AND all checks. Empty list always passes."""
    ordered = sorted(checks, key=lambda c: (c.sort_order, c.id or 0))
    results: list[CheckResult] = []
    all_ok = True
    for check in ordered:
        params = _params(check.params_json)
        ok, message = _eval_one_check(db, server, check.check_type, params)
        results.append(
            CheckResult(
                check_type=check.check_type,
                ok=ok,
                message=message,
                params=params,
            )
        )
        if not ok:
            all_ok = False
    return all_ok, results


def _eval_one_check(
    db: Session,
    server: Server,
    check_type: str,
    params: dict[str, Any],
) -> tuple[bool, str]:
    if check_type in ("players_lte", "players_gte", "players_eq"):
        # Fail closed when we have never observed a player count. Treating
        # None as 0 would let "empty server" guards pass without telemetry.
        if server.last_players is None:
            return False, "player count unknown"
        players = int(server.last_players)

        if check_type == "players_lte":
            limit = _int_param(params, "value", "max")
            if limit is None:
                return False, "players_lte requires numeric value"
            ok = players <= limit
            return ok, f"players={players} lte {limit}"

        if check_type == "players_gte":
            limit = _int_param(params, "value", "min")
            if limit is None:
                return False, "players_gte requires numeric value"
            ok = players >= limit
            return ok, f"players={players} gte {limit}"

        # players_eq
        limit = _int_param(params, "value")
        if limit is None:
            return False, "players_eq requires numeric value"
        ok = players == limit
        return ok, f"players={players} eq {limit}"

    if check_type == "server_online":
        if server.last_online is None:
            return False, "online status unknown"
        online = bool(server.last_online)
        return online, f"last_online={online}"

    if check_type == "server_offline":
        if server.last_online is None:
            return False, "online status unknown"
        online = bool(server.last_online)
        return (not online), f"last_online={online}"

    if check_type == "container_state":
        want = str(params.get("state") or "running").strip().lower()
        sample = (
            db.query(PterodactylSample)
            .filter(PterodactylSample.server_id == server.id)
            .order_by(PterodactylSample.recorded_at.desc())
            .first()
        )
        if sample is None:
            return False, "container state unknown"
        have = (sample.state or "").strip().lower() or "unknown"
        ok = have == want
        return ok, f"container_state={have} want={want}"

    return False, f"Unknown check type: {check_type}"


@dataclass
class ActionRunOutcome:
    """Result of running (or parking mid-run for a cooperative wait)."""

    # success | partial | failed | wait
    status: str
    results: list[ActionResult]
    wait_seconds: int | None = None
    # Next action index to resume after a parked wait (inclusive).
    resume_index: int | None = None


def _extend_claim_lease(db: Session, schedule_id: int | None, *, seconds: int) -> None:
    """Push next_run_at forward so a slow action chain is not double-claimed.

    Uses a direct UPDATE so we do not depend on the in-memory schedule object
    (which may be expired after intermediate commits in _log_history).
    """
    if schedule_id is None:
        return
    from datetime import timedelta

    from app.models import ServerSchedule

    lock_until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    try:
        db.query(ServerSchedule).filter(ServerSchedule.id == schedule_id).update(
            {ServerSchedule.next_run_at: lock_until},
            synchronize_session=False,
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning(
            "Could not extend claim lease for schedule %s", schedule_id, exc_info=True
        )


def execute_actions(
    db: Session,
    server: Server,
    actions: list[ScheduleAction],
    *,
    schedule_id: int | None = None,
    start_index: int = 0,
    claim_seconds: int | None = None,
) -> ActionRunOutcome:
    """Run actions in order from ``start_index``.

    ``wait`` does not block the runner thread: the outcome status is ``wait``
    with ``wait_seconds`` / ``resume_index`` so the caller can park the
    schedule and free the worker for other due jobs.

    When ``claim_seconds`` is set, the schedule's claim lease is extended
    before each non-wait action so long RCON/panel chains do not expire.
    """
    ordered = sorted(actions, key=lambda a: (a.sort_order, a.id or 0))
    results: list[ActionResult] = []
    any_ok = False

    for index, action in enumerate(ordered):
        if index < start_index:
            continue
        params = _params(action.params_json)

        if action.action_type == "wait":
            seconds = _int_param(params, "seconds", "value")
            if seconds is None:
                results.append(
                    ActionResult(
                        action_type="wait",
                        ok=False,
                        message="wait requires seconds",
                        params=params,
                    )
                )
                status = "partial" if any_ok else "failed"
                return ActionRunOutcome(status=status, results=results)
            seconds = max(WAIT_SECONDS_MIN, min(WAIT_SECONDS_MAX, seconds))
            results.append(
                ActionResult(
                    action_type="wait",
                    ok=True,
                    message=f"waiting {seconds}s",
                    params=params,
                )
            )
            _log_history(
                db,
                server,
                f"wait {seconds}s",
                f"parked for {seconds}s",
                schedule_id=schedule_id,
            )
            return ActionRunOutcome(
                status="wait",
                results=results,
                wait_seconds=seconds,
                resume_index=index + 1,
            )

        if claim_seconds is not None and claim_seconds > 0:
            _extend_claim_lease(db, schedule_id, seconds=claim_seconds)

        try:
            ok, message = _exec_one(
                db, server, action.action_type, params, schedule_id=schedule_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Schedule action %s failed for server %s", action.action_type, server.id
            )
            ok, message = False, str(exc)
        results.append(
            ActionResult(
                action_type=action.action_type,
                ok=ok,
                message=message,
                params=params,
            )
        )
        if ok:
            any_ok = True
        else:
            status = "partial" if any_ok else "failed"
            return ActionRunOutcome(status=status, results=results)

    if not results and start_index == 0 and not ordered:
        return ActionRunOutcome(status="success", results=results)
    if not results and start_index >= len(ordered):
        # Resumed past the last action (e.g. wait was final step) with an
        # unchanged action list. Callers must clear resume when actions change.
        return ActionRunOutcome(status="success", results=results)
    return ActionRunOutcome(status="success", results=results)

def _log_history(
    db: Session,
    server: Server,
    command: str,
    response: str,
    *,
    schedule_id: int | None,
) -> None:
    prefix = f"schedule:{schedule_id}:" if schedule_id is not None else "schedule:"
    try:
        db.add(
            CommandHistory(
                server_id=server.id,
                command=f"{prefix}{command}"[:2000],
                response=(response or "")[:4000],
                actor_user_id=None,
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("Could not log schedule history for %s", command, exc_info=True)


def _linked_client(db: Session, server: Server):
    uuid = option_str(server, "pterodactyl_uuid")
    if not uuid:
        raise CommandError("Server is not linked to a Pterodactyl container")
    cfg = pterodactyl_settings.load_pterodactyl_config(db)
    if not cfg.enabled:
        raise CommandError("Pterodactyl integration is not enabled")
    return uuid, pterodactyl_api.client_for(cfg)


def _adapter_for(server: Server):
    try:
        return get_adapter(server.server_type or DEFAULT_SERVER_TYPE)
    except KeyError as exc:
        raise CommandError(f"Unknown server type: {server.server_type}") from exc


def _exec_rcon(db: Session, server: Server, command: str) -> str:
    settings = get_settings()
    password = decrypt_secret(server.rcon_password_enc)
    adapter = _adapter_for(server)
    if not password:
        raise CommandError(f"Server has no {adapter.info.secret_label.lower()} configured")
    return (
        adapter.execute_command(
            server.host,
            port=server.rcon_port,
            secret=password,
            command=command,
            timeout=settings.rcon_timeout,
            options=load_options(server),
        )
        or ""
    )


def _set_startup_map(
    db: Session,
    server: Server,
    *,
    map_name: str,
    scenario: str,
    schedule_id: int | None,
) -> str:
    uuid, client = _linked_client(db, server)
    startup = client.list_startup(uuid, use_cache=False)
    missing = [k for k in MAP_DEFAULT_ENV_KEYS if k not in startup.env_keys()]
    if missing:
        raise CommandError(
            "Container egg is missing MAP_NAME/SCENARIO env keys: " + ", ".join(missing)
        )
    for key, value in (("MAP_NAME", map_name), ("SCENARIO", scenario)):
        client.update_startup_variable(uuid, key, value)
    detail = f"startup MAP_NAME={map_name} SCENARIO={scenario}"
    _log_history(db, server, detail, "ok", schedule_id=schedule_id)
    return detail


def _resolve_map_row(db: Session, server: Server, map_name: str) -> MapConfig:
    st = (server.server_type or DEFAULT_SERVER_TYPE).strip() or DEFAULT_SERVER_TYPE
    row = (
        db.query(MapConfig)
        .filter(MapConfig.server_type == st, MapConfig.map_name == map_name)
        .first()
    )
    if not row:
        # Case-insensitive fallback
        rows = db.query(MapConfig).filter(MapConfig.server_type == st).all()
        for candidate in rows:
            if (candidate.map_name or "").lower() == map_name.lower():
                return candidate
        raise CommandError(f"No map config for map_name={map_name!r}")
    return row


def _pick_gamemode_key(map_row: MapConfig, hint: str | None, preferred: str | None) -> str:
    adapter = get_adapter("sandstorm")
    modes = adapter.map_gamemodes(map_row)
    if not modes:
        raise CommandError(f"Map {map_row.alias} has no gamemodes configured")

    candidates: list[str] = []
    for value in (hint, preferred):
        if value and str(value).strip():
            candidates.append(str(value).strip())

    for cand in candidates:
        # Direct key match
        if cand in modes:
            return cand
        low = cand.lower()
        for key in modes:
            if key.lower() == low:
                return key
        # Match scenario string or partial label (A2S GameMode_s is often "Checkpoint")
        for key, scenario in modes.items():
            if scenario.lower() == low or key.replace("_", "").lower() == low.replace(" ", ""):
                return key
            if low in key.lower() or low in scenario.lower():
                return key
        # Loose: "Checkpoint" → checkpoint / checkpoint_ins
        compact = low.replace(" ", "")
        for key in modes:
            if compact in key.lower().replace("_", ""):
                return key

    # Preferred server/type gamemode if key exists
    if preferred and preferred in modes:
        return preferred
    return next(iter(modes))


def _popular_map(
    db: Session,
    server: Server,
    params: dict[str, Any],
) -> tuple[MapConfig, str]:
    range_key = str(params.get("range") or "7d")
    if range_key not in RANGE_DELTAS:
        raise CommandError(f"Invalid popularity range: {range_key}")
    combine = bool(params.get("combine_gamemodes", False))
    now = datetime.now(timezone.utc)
    from_time = now - RANGE_DELTAS[range_key]
    samples = (
        db.query(PlayerCountSample)
        .filter(
            PlayerCountSample.server_id == server.id,
            PlayerCountSample.recorded_at >= from_time,
            PlayerCountSample.recorded_at <= now,
        )
        .order_by(PlayerCountSample.recorded_at.asc())
        .all()
    )
    st = server.server_type or DEFAULT_SERVER_TYPE
    alias_rows = (
        db.query(MapConfig.map_name, MapConfig.alias).filter(MapConfig.server_type == st).all()
    )
    aliases = {m: a for m, a in alias_rows if m and a}
    result = aggregate_map_stats(
        samples,
        server_id=server.id,
        range_key=range_key,
        from_time=from_time,
        to_time=now,
        combine_gamemodes=combine,
        min_active_minutes=float(params.get("min_active_minutes") or DEFAULT_MIN_ACTIVE_MINUTES),
        alias_by_map=aliases,
    )
    if not result.rows:
        raise CommandError("No map popularity data available yet")
    top = result.rows[0]
    map_row = _resolve_map_row(db, server, top.map_name)
    preferred = server.preferred_gamemode
    gkey = _pick_gamemode_key(map_row, top.gamemode or None, preferred)
    return map_row, gkey


def _resolve_power_signal(action_type: str, params: dict[str, Any]) -> str | None:
    if action_type in _LEGACY_POWER:
        return _LEGACY_POWER[action_type]
    if action_type == "power":
        signal = str(params.get("signal") or "restart").strip().lower()
        if signal in POWER_SIGNALS:
            return signal
    return None


def _exec_power(
    db: Session,
    server: Server,
    signal: str,
    *,
    schedule_id: int | None,
) -> tuple[bool, str]:
    uuid, client = _linked_client(db, server)
    try:
        client.send_power(uuid, signal)
    except PterodactylApiError as exc:
        _log_history(
            db, server, f"power {signal}", f"refused: {exc}", schedule_id=schedule_id
        )
        return False, str(exc)
    msg = f"{signal} accepted by panel"
    _log_history(db, server, f"power {signal}", msg, schedule_id=schedule_id)
    return True, msg


def _exec_one(
    db: Session,
    server: Server,
    action_type: str,
    params: dict[str, Any],
    *,
    schedule_id: int | None,
) -> tuple[bool, str]:
    power_signal = _resolve_power_signal(action_type, params)
    if power_signal is not None:
        return _exec_power(db, server, power_signal, schedule_id=schedule_id)

    if action_type == "wait":
        # Cooperative waits are handled in execute_actions (park, don't sleep).
        # Keep a defensive path if wait is ever invoked here directly.
        seconds = _int_param(params, "seconds", "value")
        if seconds is None:
            return False, "wait requires seconds"
        seconds = max(WAIT_SECONDS_MIN, min(WAIT_SECONDS_MAX, seconds))
        msg = f"waiting {seconds}s"
        _log_history(db, server, f"wait {seconds}s", msg, schedule_id=schedule_id)
        return True, msg

    if action_type == "rcon":
        command = str(params.get("command") or "").strip()
        if not command:
            return False, "rcon action requires command"
        adapter = _adapter_for(server)
        if not getattr(adapter.info.features, "console", False):
            return False, "rcon/console not supported for this server type"
        try:
            response = _exec_rcon(db, server, command)
        except CommandError as exc:
            _log_history(db, server, command, f"error: {exc}", schedule_id=schedule_id)
            return False, str(exc)
        _log_history(db, server, command, response, schedule_id=schedule_id)
        return True, response or "ok"

    if action_type == "say":
        message = str(params.get("message") or "").strip()
        if not message:
            return False, "say action requires message"
        adapter = _adapter_for(server)
        if not getattr(adapter.info.features, "admin_say", False):
            return False, "admin_say not supported for this server type"
        command = adapter.build_say_command(message)
        try:
            response = _exec_rcon(db, server, command)
        except CommandError as exc:
            _log_history(db, server, command, f"error: {exc}", schedule_id=schedule_id)
            return False, str(exc)
        _log_history(db, server, command, response, schedule_id=schedule_id)
        return True, response or "ok"

    if action_type == "travel":
        return _travel_fixed(db, server, params, schedule_id=schedule_id)

    if action_type == "travel_popular":
        if (server.server_type or "") != "sandstorm":
            return False, "travel_popular is only for Sandstorm"
        try:
            map_row, gkey = _popular_map(db, server, params)
        except CommandError as exc:
            return False, str(exc)
        lighting = str(params.get("lighting") or "Day")
        return _travel_map_row(
            db, server, map_row, gkey, lighting, schedule_id=schedule_id
        )

    if action_type == "set_startup_current":
        if (server.server_type or "") != "sandstorm":
            return False, "set_startup_current is only for Sandstorm"
        map_name = (server.last_map or "").strip()
        if not map_name:
            return False, "No current map known (last_map empty)"
        try:
            map_row = _resolve_map_row(db, server, map_name)
            gkey = _pick_gamemode_key(
                map_row, server.last_gamemode, server.preferred_gamemode
            )
            adapter = get_adapter("sandstorm")
            modes = adapter.map_gamemodes(map_row)
            scenario = modes[gkey]
            detail = _set_startup_map(
                db, server, map_name=map_row.map_name, scenario=scenario, schedule_id=schedule_id
            )
        except (CommandError, PterodactylApiError, KeyError) as exc:
            return False, str(exc)
        return True, detail

    if action_type == "set_startup_popular":
        if (server.server_type or "") != "sandstorm":
            return False, "set_startup_popular is only for Sandstorm"
        try:
            map_row, gkey = _popular_map(db, server, params)
            adapter = get_adapter("sandstorm")
            scenario = adapter.map_gamemodes(map_row)[gkey]
            detail = _set_startup_map(
                db, server, map_name=map_row.map_name, scenario=scenario, schedule_id=schedule_id
            )
        except (CommandError, PterodactylApiError, KeyError) as exc:
            return False, str(exc)
        return True, detail

    return False, f"Unknown action type: {action_type}"


def _travel_fixed(
    db: Session,
    server: Server,
    params: dict[str, Any],
    *,
    schedule_id: int | None,
) -> tuple[bool, str]:
    adapter = _adapter_for(server)
    if not getattr(adapter.info.features, "map_travel", False):
        return False, "map_travel not supported for this server type"
    map_id = _int_param(params, "map_id")
    if not map_id:
        return False, "travel requires map_id"
    gamemode_key = str(params.get("gamemode_key") or "").strip()
    lighting = str(params.get("lighting") or "Day").strip() or "Day"
    map_row = db.get(MapConfig, map_id)
    if not map_row:
        return False, f"Map id {map_id} not found"
    return _travel_map_row(db, server, map_row, gamemode_key, lighting, schedule_id=schedule_id)


def _travel_map_row(
    db: Session,
    server: Server,
    map_row: MapConfig,
    gamemode_key: str,
    lighting: str,
    *,
    schedule_id: int | None,
) -> tuple[bool, str]:
    adapter = _adapter_for(server)
    modes = adapter.map_gamemodes(map_row)
    if not gamemode_key:
        gamemode_key = _pick_gamemode_key(map_row, None, server.preferred_gamemode)
    if gamemode_key not in modes:
        return False, f"Gamemode {gamemode_key!r} not available for {map_row.alias}"
    scenario = modes[gamemode_key]
    command = adapter.build_travel_command(
        map_name=map_row.map_name,
        scenario=scenario,
        lighting=lighting,
        gamemode_key=gamemode_key,
    )
    try:
        response = _exec_rcon(db, server, command)
    except CommandError as exc:
        _log_history(db, server, command, f"error: {exc}", schedule_id=schedule_id)
        return False, str(exc)
    _log_history(db, server, command, response, schedule_id=schedule_id)
    return True, response or f"travel {map_row.alias}"


def validate_action_for_server(server: Server, action_type: str, params: dict[str, Any]) -> str | None:
    """Return error detail or None if valid for CRUD."""
    if action_type not in ACTION_TYPES:
        return f"Unknown action type: {action_type}"
    st = server.server_type or DEFAULT_SERVER_TYPE
    if action_type == "power":
        signal = str(params.get("signal") or "").strip().lower()
        if signal not in POWER_SIGNALS:
            return "power requires signal: start, stop, restart, or kill"
    if action_type == "wait":
        seconds = _int_param(params, "seconds", "value")
        if seconds is None:
            return "wait requires seconds"
        if seconds < WAIT_SECONDS_MIN or seconds > WAIT_SECONDS_MAX:
            return f"wait seconds must be between {WAIT_SECONDS_MIN} and {WAIT_SECONDS_MAX}"
    if action_type in ("travel", "travel_popular", "set_startup_current", "set_startup_popular"):
        if st != "sandstorm" and action_type != "travel":
            return f"{action_type} is only supported for Sandstorm"
        if action_type == "travel":
            try:
                adapter = get_adapter(st)
            except KeyError:
                return f"Unknown server type: {st}"
            if not getattr(adapter.info.features, "map_travel", False):
                return "map_travel not supported for this server type"
            if not _int_param(params, "map_id"):
                return "travel requires map_id"
            if not str(params.get("gamemode_key") or "").strip():
                return "travel requires gamemode_key"
    if action_type == "rcon":
        if not str(params.get("command") or "").strip():
            return "rcon requires command"
        try:
            adapter = get_adapter(st)
        except KeyError:
            return f"Unknown server type: {st}"
        if not getattr(adapter.info.features, "console", False):
            return "rcon/console not supported for this server type"
    if action_type == "say" and not str(params.get("message") or "").strip():
        return "say requires message"
    if action_type == "say":
        try:
            adapter = get_adapter(st)
        except KeyError:
            return f"Unknown server type: {st}"
        if not getattr(adapter.info.features, "admin_say", False):
            return "admin_say not supported for this server type"
    return None


def validate_check(check_type: str, params: dict[str, Any]) -> str | None:
    if check_type not in CHECK_TYPES:
        return f"Unknown check type: {check_type}"
    if check_type in ("players_lte", "players_gte", "players_eq"):
        if _int_param(params, "value", "max", "min") is None:
            return f"{check_type} requires numeric value"
    if check_type == "container_state":
        if not str(params.get("state") or "").strip():
            return "container_state requires state"
    return None

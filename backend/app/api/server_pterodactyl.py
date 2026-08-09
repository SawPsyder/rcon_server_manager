"""Container resources and power control for a server linked to Pterodactyl.

Unlike ``app.api.pterodactyl`` (admin-only credentials and inventory), these
routes are SCOPED: a user granted a server may read its resources and press
start / stop / restart. The reasoning is consistency - a grant already lets
someone stop a Palworld or Satisfactory server through its own admin API, and
admin-gating only the way *back up* would leave an operator able to take a
server down at 2am and unable to bring it back.

``kill`` is the exception. It is SIGKILL with no save, so it is admin-only on
top of the grant.

Every power action is written to ``command_history`` - successes and refusals
alike, since "the operator tried to restart a suspended container" is exactly
what an audit trail is for. Resource reads are not logged; they are a polling
loop and would swamp the table.

The read endpoints do no upstream work in the normal case.
``app.services.pterodactyl_poller`` refreshes every linked server on its own
schedule, so both the live card and the history series below are views onto the
same readings and cannot disagree.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.servers import get_server_or_404
from app.api.stats import MAX_CHART_POINTS, RANGE_DELTAS
from app.database import get_db
from app.deps import CurrentUser
from app.models import CommandHistory, PterodactylSample, Server, User
from app.schemas import (
    PterodactylHistoryOut,
    PterodactylHistoryPoint,
    PterodactylPowerOut,
    PterodactylPowerRequest,
    PterodactylResourcesOut,
)
from app.services import pterodactyl_api, pterodactyl_settings
from app.services.pterodactyl_api import (
    PterodactylApiError,
    PterodactylAuthError,
    PterodactylConflictError,
    PterodactylNotFoundError,
    PterodactylRateLimitError,
    PterodactylTimeoutError,
    PterodactylTlsError,
)
from app.services.server_options import option_str

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/servers/{server_id}/pterodactyl", tags=["pterodactyl"])

MIB = 1024 * 1024

# Signals that interrupt play without a clean shutdown.
CONFIRM_REQUIRED = {"stop", "kill", "restart"}


def _http_error(exc: PterodactylApiError) -> HTTPException:
    """Map a panel failure onto an HTTP status the UI can act on."""
    if isinstance(exc, PterodactylNotFoundError):
        # Deliberately NOT passed through as 404. require_server_scope already
        # answers 404 for "not your server", so a passthrough would be
        # ambiguous across two layers. This one is a configuration problem:
        # the link points at a container the panel key cannot see.
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, (PterodactylAuthError, PterodactylTlsError)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, PterodactylConflictError):
        # Genuinely transient - the UI should say "try again once the install
        # finishes" rather than treat it as a broken setup.
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, PterodactylRateLimitError):
        return HTTPException(status_code=429, detail=str(exc))
    if isinstance(exc, PterodactylTimeoutError):
        return HTTPException(status_code=504, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


@contextmanager
def _api_errors() -> Iterator[None]:
    try:
        yield
    except PterodactylApiError as exc:
        raise _http_error(exc) from exc


def _linked(db: Session, server_id: int) -> tuple[Server, str, pterodactyl_api.PanelClient]:
    server = get_server_or_404(db, server_id)
    uuid = option_str(server, "pterodactyl_uuid")
    if not uuid:
        raise HTTPException(
            status_code=400,
            detail="This server is not linked to a Pterodactyl server.",
        )

    cfg = pterodactyl_settings.load_pterodactyl_config(db)
    if not cfg.enabled:
        raise HTTPException(
            status_code=400,
            detail=(
                "Pterodactyl is not configured. Add the panel URL and an API key "
                "under Settings."
            ),
        )
    with _api_errors():
        return server, uuid, pterodactyl_api.client_for(cfg)


def _log(
    db: Session,
    server: Server,
    command: str,
    response: str = "",
    actor: User | None = None,
) -> None:
    """Record a power action in the shared command history."""
    try:
        db.add(
            CommandHistory(
                server_id=server.id,
                command=f"pterodactyl:{command}"[:2000],
                response=(response or "ok")[:4000],
                actor_user_id=actor.id if actor else None,
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("Could not log Pterodactyl action %s", command, exc_info=True)


def _limit_bytes(mib: int) -> int | None:
    """MiB limit to bytes. The panel encodes unlimited as 0."""
    return mib * MIB if mib and mib > 0 else None


@router.get("/resources", response_model=PterodactylResourcesOut)
def server_resources(
    server_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> PterodactylResourcesOut:
    """Live container utilisation.

    Normally a pure cache read: the background poller is what talks to the
    panel. Only a server linked seconds ago, or a stalled poller, sends this
    request upstream. ``age_seconds`` reports how old the reading actually is,
    which - with the panel's own 20s cache on top of the 20s poll interval -
    can legitimately be around 40s.
    """
    _server, uuid, client = _linked(db, server_id)

    with _api_errors():
        resources = client.get_resources(uuid)
        # Cached for 5 minutes - limits change only on reconfiguration.
        panel = client.get_server(uuid)

    return PterodactylResourcesOut(
        name=panel.name,
        identifier=panel.identifier if user.is_admin else "",
        state=resources.current_state,
        is_suspended=resources.is_suspended or panel.is_suspended,
        panel_status=panel.status,
        memory_bytes=resources.memory_bytes,
        memory_limit_bytes=_limit_bytes(panel.memory_limit_mb),
        disk_bytes=resources.disk_bytes,
        disk_limit_bytes=_limit_bytes(panel.disk_limit_mb),
        cpu_absolute=resources.cpu_absolute,
        cpu_limit=panel.cpu_limit if panel.cpu_limit > 0 else None,
        network_rx_bytes=resources.network_rx_bytes,
        network_tx_bytes=resources.network_tx_bytes,
        uptime_ms=resources.uptime_ms,
        age_seconds=round(client.resource_age(uuid) or 0.0, 1),
    )


def _history_point(row: PterodactylSample) -> PterodactylHistoryPoint:
    """One sample as a chart point - values are the real reading, not an average."""
    return PterodactylHistoryPoint(
        t=row.recorded_at,
        cpu_absolute=round(float(row.cpu_absolute), 2),
        cpu_peak=round(float(row.cpu_absolute), 2),
        memory_bytes=int(row.memory_bytes),
        memory_peak=int(row.memory_bytes),
        samples=1,
    )


def _downsample_history(
    rows: list[PterodactylSample],
    max_points: int = MAX_CHART_POINTS,
) -> list[PterodactylHistoryPoint]:
    """Same mid-of-chunk thinning the player/tick charts use.

    Only runs when there are more samples than the chart can usefully draw.
    Within a thinned chunk the plotted value is the mid sample (so the line is
    a real reading, not an average that smooths spikes away); peak fields keep
    the max in that chunk for the tooltip.
    """
    if not rows:
        return []
    if len(rows) <= max_points:
        return [_history_point(r) for r in rows]

    n = len(rows)
    out: list[PterodactylHistoryPoint] = []
    for i in range(max_points):
        start = int(i * n / max_points)
        end = int((i + 1) * n / max_points)
        chunk = rows[start:end]
        if not chunk:
            continue
        mid = chunk[len(chunk) // 2]
        out.append(
            PterodactylHistoryPoint(
                t=mid.recorded_at,
                cpu_absolute=round(float(mid.cpu_absolute), 2),
                cpu_peak=round(max(float(r.cpu_absolute) for r in chunk), 2),
                memory_bytes=int(mid.memory_bytes),
                memory_peak=max(int(r.memory_bytes) for r in chunk),
                samples=len(chunk),
            )
        )
    return out


@router.get("/history", response_model=PterodactylHistoryOut)
def server_history(
    server_id: int,
    _user: CurrentUser,
    # Aliased rather than named `range`: the builtin is used for gap logic
    # elsewhere, and shadowing it here was a bug once.
    range_key: str = Query(
        default="24h", alias="range", pattern="^(24h|7d|30d|180d|1y)$"
    ),
    db: Session = Depends(get_db),
) -> PterodactylHistoryOut:
    """Container CPU / memory history for the selected range.

    Matches the player and tick charts: return every sample in the window, and
    only thin to :data:`MAX_CHART_POINTS` when the actual series is denser than
    that. Thinning is based on sample count, not a fixed "range span / 480"
    bucket that invents empty slots and averages real readings together.

    Deliberately not gated on the server still being linked: unlinking should
    not erase the history that was already recorded.
    """
    get_server_or_404(db, server_id)

    now = datetime.now(timezone.utc)
    from_time = now - RANGE_DELTAS[range_key]

    rows = (
        db.query(PterodactylSample)
        .filter(
            PterodactylSample.server_id == server_id,
            PterodactylSample.recorded_at >= from_time,
            PterodactylSample.recorded_at <= now,
        )
        .order_by(PterodactylSample.recorded_at.asc())
        .all()
    )

    points = _downsample_history(rows)
    # Kept for API compatibility; no longer drives grouping. Approximate the
    # spacing of the returned series from the actual data window.
    if len(points) >= 2:
        first_t, last_t = points[0].t, points[-1].t
        if first_t.tzinfo is None:
            first_t = first_t.replace(tzinfo=timezone.utc)
        if last_t.tzinfo is None:
            last_t = last_t.replace(tzinfo=timezone.utc)
        span = max(0.0, (last_t - first_t).total_seconds())
        bucket_seconds = max(1, int(round(span / (len(points) - 1))))
    else:
        bucket_seconds = int(pterodactyl_api.POLL_INTERVAL_SECONDS)

    if rows:
        cpu_values = [float(r.cpu_absolute) for r in rows]
        mem_values = [int(r.memory_bytes) for r in rows]
        latest = rows[-1]
        peak_cpu = max(cpu_values)
        avg_cpu = sum(cpu_values) / len(cpu_values)
        peak_mem = max(mem_values)
        avg_mem = sum(mem_values) / len(mem_values)
    else:
        latest = None
        peak_cpu = avg_cpu = peak_mem = avg_mem = None

    return PterodactylHistoryOut(
        server_id=server_id,
        range=range_key,
        from_time=from_time,
        to_time=now,
        bucket_seconds=bucket_seconds,
        points=points,
        current_cpu_absolute=round(float(latest.cpu_absolute), 2) if latest else None,
        peak_cpu_absolute=round(peak_cpu, 2) if peak_cpu is not None else None,
        avg_cpu_absolute=round(avg_cpu, 2) if avg_cpu is not None else None,
        current_memory_bytes=int(latest.memory_bytes) if latest else None,
        peak_memory_bytes=int(peak_mem) if peak_mem is not None else None,
        avg_memory_bytes=int(avg_mem) if avg_mem is not None else None,
    )


@router.post("/power", response_model=PterodactylPowerOut)
def server_power(
    server_id: int,
    body: PterodactylPowerRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> PterodactylPowerOut:
    signal = body.signal

    if signal == "kill" and not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail=(
                "Killing a container discards unsaved progress and is restricted "
                "to administrators. Use Stop for a clean shutdown."
            ),
        )
    if signal in CONFIRM_REQUIRED and not body.confirm:
        raise HTTPException(
            status_code=400, detail=f"Sending '{signal}' requires confirm=true"
        )

    server, uuid, client = _linked(db, server_id)

    try:
        client.send_power(uuid, signal)
    except PterodactylApiError as exc:
        # A refusal belongs in the audit trail as much as a success does.
        _log(db, server, f"power {signal}", f"refused: {exc}", actor=user)
        raise _http_error(exc) from exc

    _log(
        db,
        server,
        f"power {signal}",
        "signal accepted by the panel (asynchronous - the container state is "
        "not confirmed)",
        actor=user,
    )
    return PterodactylPowerOut(
        signal=signal,
        # Never claims the state changed: the panel returns 204 the moment
        # wings accepts the signal, and does the work afterwards.
        detail=f"{signal.capitalize()} requested.",
    )

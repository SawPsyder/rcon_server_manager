"""Background worker: fire due server schedules.

Modelled on StatsCollector / PterodactylPoller: a daemon thread, own DB
sessions, optimistic claim on next_run_at so multi-worker setups do not
double-fire the same window.

Long waits do not hold the claim lock: they park the schedule with
``resume_action_index`` and a future ``next_run_at`` so other due schedules
can run on the same worker thread.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal
from app.models import ScheduleRun, ServerSchedule, utcnow
from app.services.schedule_actions import (
    evaluate_checks,
    execute_actions,
    server_is_linked,
)
from app.services.schedule_time import (
    compute_retry_next_run,
    load_app_timezone,
    next_occurrence,
    window_after,
)

logger = logging.getLogger(__name__)

TICK_SECONDS = 20.0
# Lease for one claim while executing non-wait work (RCON / panel / checks).
# Waits park instead of sleeping under this lock (see execute_actions).
CLAIM_SECONDS = 300


class ScheduleRunner:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="schedule-runner", daemon=True
        )
        self._thread.start()
        logger.info("Schedule runner started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("Schedule runner stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("Schedule runner tick failed")
            self._stop.wait(TICK_SECONDS)

    def tick(self) -> int:
        """Process every due schedule once. Returns how many were claimed."""
        db = SessionLocal()
        claimed = 0
        try:
            now = datetime.now(timezone.utc)
            due_ids = [
                row.id
                for row in (
                    db.query(ServerSchedule.id)
                    .filter(
                        ServerSchedule.enabled.is_(True),
                        ServerSchedule.next_run_at <= now,
                    )
                    .order_by(ServerSchedule.next_run_at.asc())
                    .all()
                )
            ]
            for schedule_id in due_ids:
                if self._claim_and_run(db, schedule_id):
                    claimed += 1
        finally:
            db.close()
        return claimed

    def run_one(self, schedule_id: int) -> bool:
        """Claim and run a single due schedule (used by run-now)."""
        db = SessionLocal()
        try:
            return self._claim_and_run(db, schedule_id)
        finally:
            db.close()

    def _claim_and_run(self, db: Session, schedule_id: int) -> bool:
        # Fresh wall clock per claim so a prior long tick cannot mint an
        # already-expired lock_until or skip due rows.
        now = datetime.now(timezone.utc)

        current = (
            db.query(ServerSchedule)
            .filter(
                ServerSchedule.id == schedule_id,
                ServerSchedule.enabled.is_(True),
                ServerSchedule.next_run_at <= now,
            )
            .first()
        )
        if not current:
            return False

        # Capture planned fire time before the claim overwrites next_run_at.
        planned_next = current.next_run_at
        if planned_next is not None and planned_next.tzinfo is None:
            planned_next = planned_next.replace(tzinfo=timezone.utc)

        lock_until = now + timedelta(seconds=CLAIM_SECONDS)
        updated = (
            db.query(ServerSchedule)
            .filter(
                ServerSchedule.id == schedule_id,
                ServerSchedule.enabled.is_(True),
                ServerSchedule.next_run_at <= now,
            )
            .update(
                {
                    ServerSchedule.next_run_at: lock_until,
                    # Marks non-wait execution so run-now can refuse double-fire.
                    # Cooperative waits overwrite this with "waiting" when parked.
                    ServerSchedule.last_status: "running",
                    ServerSchedule.last_message: "Running…",
                },
                synchronize_session=False,
            )
        )
        db.commit()
        if not updated:
            return False

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
            return False

        try:
            self._execute_claimed(db, schedule, now, planned_next=planned_next)
        except Exception:
            logger.exception("Schedule %s execution crashed", schedule_id)
            try:
                self._recover_after_crash(db, schedule, now)
            except Exception:
                db.rollback()
                logger.exception(
                    "Could not recover schedule %s after crash", schedule_id
                )
        return True

    def _recover_after_crash(
        self, db: Session, schedule: ServerSchedule, now: datetime
    ) -> None:
        tz_name = load_app_timezone(db)
        schedule.next_run_at = next_occurrence(
            time_local=schedule.time_local,
            days_of_week=schedule.days_of_week,
            tz=tz_name,
            after=now,
            inclusive=False,
        )
        schedule.last_status = "failed"
        schedule.last_message = "Internal error during schedule run"
        schedule.last_run_at = utcnow()
        schedule.active_window_at = None
        schedule.resume_action_index = None
        schedule.pending_detail_json = "{}"
        db.commit()

    def _execute_claimed(
        self,
        db: Session,
        schedule: ServerSchedule,
        now: datetime,
        *,
        planned_next: datetime | None,
    ) -> None:
        tz_name = load_app_timezone(db)
        server = schedule.server
        if server is None:
            schedule.enabled = False
            schedule.last_status = "failed"
            schedule.last_message = "Server missing; schedule disabled"
            schedule.active_window_at = None
            schedule.resume_action_index = None
            schedule.pending_detail_json = "{}"
            schedule.next_run_at = next_occurrence(
                time_local=schedule.time_local,
                days_of_week=schedule.days_of_week,
                tz=tz_name,
                after=now,
                inclusive=False,
            )
            db.commit()
            return

        if not server_is_linked(server):
            schedule.last_status = "failed"
            schedule.last_message = "Server is not linked to Pterodactyl; skipping"
            schedule.last_run_at = utcnow()
            schedule.active_window_at = None
            schedule.resume_action_index = None
            schedule.pending_detail_json = "{}"
            schedule.next_run_at = next_occurrence(
                time_local=schedule.time_local,
                days_of_week=schedule.days_of_week,
                tz=tz_name,
                after=now,
                inclusive=False,
            )
            db.add(
                ScheduleRun(
                    schedule_id=schedule.id,
                    server_id=server.id,
                    scheduled_for=now,
                    started_at=now,
                    finished_at=utcnow(),
                    status="failed",
                    attempt=1,
                    detail_json="{}",
                    message=schedule.last_message,
                )
            )
            db.commit()
            return

        resuming = schedule.resume_action_index is not None
        scheduled_for = self._resolve_window(
            schedule, now=now, planned_next=planned_next, tz_name=tz_name
        )
        attempt = self._next_attempt(db, schedule.id, scheduled_for)

        started = utcnow()
        prior_detail = self._load_pending_detail(schedule)

        # Always evaluate checks — including after a cooperative wait resume —
        # so "empty server" guards still hold if players join during a wait.
        checks_ok, check_results = evaluate_checks(
            db, server, list(schedule.checks or [])
        )
        check_detail = [
            {
                "type": r.check_type,
                "ok": r.ok,
                "message": r.message,
                "params": r.params,
            }
            for r in check_results
        ]

        if resuming:
            detail = prior_detail
            if "actions" not in detail:
                detail["actions"] = []
            # Keep prior check snapshots for audit; append the resume re-check.
            prior_checks = list(detail.get("checks") or [])
            detail["checks"] = prior_checks + [
                {"phase": "resume", **row} for row in check_detail
            ]
        else:
            detail = {"checks": check_detail, "actions": []}

        if not checks_ok:
            # Abort mid-wait resume state so retries start cleanly.
            schedule.resume_action_index = None
            schedule.pending_detail_json = "{}"
            self._finish_checks_failed(
                db,
                schedule,
                server=server,
                now=now,
                started=started,
                scheduled_for=scheduled_for,
                attempt=attempt,
                detail=detail,
                check_results=check_results,
                tz_name=tz_name,
            )
            return

        start_index = int(schedule.resume_action_index or 0)
        outcome = execute_actions(
            db,
            server,
            list(schedule.actions or []),
            schedule_id=schedule.id,
            start_index=start_index,
            claim_seconds=CLAIM_SECONDS,
        )

        prior_actions = list(detail.get("actions") or [])
        new_actions = [
            {
                "type": r.action_type,
                "ok": r.ok,
                "message": r.message,
                "params": r.params,
            }
            for r in outcome.results
        ]
        detail["actions"] = prior_actions + new_actions

        if outcome.status == "wait":
            assert outcome.wait_seconds is not None
            assert outcome.resume_index is not None
            resume_at = datetime.now(timezone.utc) + timedelta(
                seconds=outcome.wait_seconds
            )
            # Park: free the runner thread; next_run_at is the real resume
            # time (not a short lease). Another worker will not steal early.
            schedule.next_run_at = resume_at
            schedule.resume_action_index = outcome.resume_index
            schedule.pending_detail_json = json.dumps(detail, separators=(",", ":"))
            schedule.active_window_at = scheduled_for
            schedule.last_status = "waiting"
            schedule.last_message = (
                f"Waiting {outcome.wait_seconds}s before next action"
            )
            schedule.last_run_at = started
            db.commit()
            return

        # Terminal: success / partial / failed — clear resume state.
        schedule.resume_action_index = None
        schedule.pending_detail_json = "{}"

        if outcome.status == "success":
            status = "success"
            message = "All actions completed"
        elif outcome.status == "partial":
            status = "partial"
            failed = next((r for r in outcome.results if not r.ok), None)
            message = (
                f"Stopped after failed action {failed.action_type}: {failed.message}"
                if failed
                else "Stopped after failed action"
            )
        else:
            status = "failed"
            failed = outcome.results[0] if outcome.results else None
            message = (
                f"Action failed: {failed.action_type}: {failed.message}"
                if failed
                else "No actions configured"
            )

        schedule.next_run_at = window_after(
            scheduled_for=scheduled_for,
            time_local=schedule.time_local,
            days_of_week=schedule.days_of_week,
            tz=tz_name,
        )
        schedule.active_window_at = None
        schedule.last_status = status
        schedule.last_message = message
        schedule.last_run_at = started
        db.add(
            ScheduleRun(
                schedule_id=schedule.id,
                server_id=server.id,
                scheduled_for=scheduled_for,
                started_at=started,
                finished_at=utcnow(),
                status=status,
                attempt=attempt,
                detail_json=json.dumps(detail, separators=(",", ":")),
                message=message,
            )
        )
        db.commit()

    def _finish_checks_failed(
        self,
        db: Session,
        schedule: ServerSchedule,
        *,
        server,
        now: datetime,
        started: datetime,
        scheduled_for: datetime,
        attempt: int,
        detail: dict,
        check_results: list,
        tz_name: str,
    ) -> None:
        schedule.resume_action_index = None
        schedule.pending_detail_json = "{}"
        retry_at, next_window = compute_retry_next_run(
            now=now,
            retry_after_minutes=schedule.retry_after_minutes,
            scheduled_for=scheduled_for,
            time_local=schedule.time_local,
            days_of_week=schedule.days_of_week,
            tz=tz_name,
        )
        if retry_at is None:
            message = "Checks never passed before next window; skipped"
            status = "skipped"
            schedule.next_run_at = next_window
            schedule.active_window_at = None
        else:
            failed = [r.message for r in check_results if not r.ok]
            message = "Checks failed: " + "; ".join(failed)
            status = "checks_failed"
            schedule.next_run_at = retry_at
            # keep active_window_at for retries
        schedule.last_status = status
        schedule.last_message = message
        schedule.last_run_at = started
        detail["actions"] = []
        db.add(
            ScheduleRun(
                schedule_id=schedule.id,
                server_id=server.id,
                scheduled_for=scheduled_for,
                started_at=started,
                finished_at=utcnow(),
                status=status,
                attempt=attempt,
                detail_json=json.dumps(detail, separators=(",", ":")),
                message=message,
            )
        )
        db.commit()

    @staticmethod
    def _resolve_window(
        schedule: ServerSchedule,
        *,
        now: datetime,
        planned_next: datetime | None,
        tz_name: str,
    ) -> datetime:
        """Pick the calendar / ad-hoc window id for this attempt."""
        if schedule.active_window_at is not None:
            scheduled_for = schedule.active_window_at
            if scheduled_for.tzinfo is None:
                scheduled_for = scheduled_for.replace(tzinfo=timezone.utc)
            return scheduled_for

        # Prefer the pre-claim next_run_at (true planned fire time). Fall back
        # to walking calendar slots only if it was missing.
        if planned_next is not None:
            scheduled_for = planned_next
            # Run-now and late ticks may have next_run_at ≈ now; keep as-is.
            # Never invent a future weekday while actions already run now.
            if scheduled_for > now + timedelta(seconds=5):
                scheduled_for = now
            schedule.active_window_at = scheduled_for
            return scheduled_for

        scheduled_for = next_occurrence(
            time_local=schedule.time_local,
            days_of_week=schedule.days_of_week,
            tz=tz_name,
            after=now - timedelta(days=1),
            inclusive=True,
        )
        while True:
            nxt = next_occurrence(
                time_local=schedule.time_local,
                days_of_week=schedule.days_of_week,
                tz=tz_name,
                after=scheduled_for,
                inclusive=False,
            )
            if nxt <= now:
                scheduled_for = nxt
            else:
                break
        schedule.active_window_at = scheduled_for
        return scheduled_for

    @staticmethod
    def _load_pending_detail(schedule: ServerSchedule) -> dict:
        raw = schedule.pending_detail_json or "{}"
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return {"checks": [], "actions": []}
        return data if isinstance(data, dict) else {"checks": [], "actions": []}

    @staticmethod
    def _next_attempt(db: Session, schedule_id: int, scheduled_for: datetime) -> int:
        last = (
            db.query(ScheduleRun)
            .filter(
                ScheduleRun.schedule_id == schedule_id,
                ScheduleRun.scheduled_for == scheduled_for,
            )
            .order_by(ScheduleRun.attempt.desc())
            .first()
        )
        return (last.attempt + 1) if last else 1


def is_claim_active(schedule: ServerSchedule, *, now: datetime | None = None) -> bool:
    """True when a worker holds the short non-wait claim lease.

    Claim sets ``last_status='running'`` and ``next_run_at`` to a short lease.
    Parked waits use ``last_status='waiting'`` and may be interrupted by run-now.
    An expired lease (``next_run_at`` already past) is not active — another
    worker may reclaim or run-now may proceed.
    """
    if (schedule.last_status or "") != "running":
        return False
    if schedule.resume_action_index is not None:
        return False
    next_at = schedule.next_run_at
    if next_at is None:
        return False
    if next_at.tzinfo is None:
        next_at = next_at.replace(tzinfo=timezone.utc)
    wall = now or datetime.now(timezone.utc)
    return next_at > wall


def recompute_all_next_runs(db: Session) -> int:
    """Recompute next_run_at for every schedule after app timezone change.

    Clears active retry windows and cooperative waits so a zone change does
    not leave retries/resumes anchored to the old local day boundary.
    """
    tz_name = load_app_timezone(db)
    now = datetime.now(timezone.utc)
    count = 0
    aborted_in_progress = 0
    for schedule in db.query(ServerSchedule).all():
        if (
            schedule.active_window_at is not None
            or schedule.resume_action_index is not None
            or (schedule.last_status or "") == "waiting"
        ):
            aborted_in_progress += 1
        schedule.active_window_at = None
        schedule.resume_action_index = None
        schedule.pending_detail_json = "{}"
        schedule.next_run_at = next_occurrence(
            time_local=schedule.time_local,
            days_of_week=schedule.days_of_week,
            tz=tz_name,
            after=now,
            inclusive=False,
        )
        count += 1
    db.commit()
    if aborted_in_progress:
        logger.warning(
            "Timezone change recomputed %s schedule(s); aborted %s in-progress "
            "wait(s)/retry window(s)",
            count,
            aborted_in_progress,
        )
    else:
        logger.info("Timezone change recomputed next_run_at for %s schedule(s)", count)
    return count


runner = ScheduleRunner()

"""Runner claim / cooperative wait / run_one behaviour."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base, ScheduleAction, ScheduleRun, Server, ServerSchedule, Setting
from app.services.schedule_actions import execute_actions
from app.services.schedule_runner import CLAIM_SECONDS, ScheduleRunner
from app.services.server_options import save_options


@pytest.fixture
def db_and_sessionmaker(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr("app.services.schedule_runner.SessionLocal", Session)
    session = Session()
    session.add(Setting(key="app_timezone", value="UTC"))
    session.commit()
    try:
        yield session, Session
    finally:
        session.close()


def _linked(db) -> Server:
    server = Server(
        name="Box",
        host="10.0.0.1",
        query_port=27131,
        rcon_port=27015,
        rcon_password_enc="",
        server_type="sandstorm",
        last_players=0,
        last_online=True,
    )
    save_options(server, {"pterodactyl_uuid": "abc-uuid", "pterodactyl_name": "Box"})
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def test_cooperative_wait_parks_without_sleep(db_and_sessionmaker):
    db, _ = db_and_sessionmaker
    server = _linked(db)
    action = ScheduleAction(
        sort_order=0,
        action_type="wait",
        params_json=json.dumps({"seconds": 120}),
    )
    outcome = execute_actions(db, server, [action], schedule_id=None, start_index=0)
    assert outcome.status == "wait"
    assert outcome.wait_seconds == 120
    assert outcome.resume_index == 1
    assert outcome.results[0].ok


def test_claim_parks_wait_and_uses_planned_window(db_and_sessionmaker):
    db, _ = db_and_sessionmaker
    server = _linked(db)
    planned = datetime.now(timezone.utc) - timedelta(seconds=5)
    schedule = ServerSchedule(
        server_id=server.id,
        name="Due",
        enabled=True,
        time_local="04:00",
        days_of_week="0,1,2,3,4,5,6",
        retry_after_minutes=10,
        next_run_at=planned,
        last_status="",
        last_message="",
    )
    db.add(schedule)
    db.flush()
    db.add(
        ScheduleAction(
            schedule_id=schedule.id,
            sort_order=0,
            action_type="wait",
            params_json=json.dumps({"seconds": 30}),
        )
    )
    db.commit()
    sid = schedule.id

    runner = ScheduleRunner()
    assert runner.run_one(sid) is True

    db.expire_all()
    row = db.get(ServerSchedule, sid)
    assert row is not None
    assert row.last_status == "waiting"
    assert row.resume_action_index == 1
    assert row.active_window_at is not None
    # Planned due time becomes the window (not a future weekday invent).
    window = row.active_window_at
    if window.tzinfo is None:
        window = window.replace(tzinfo=timezone.utc)
    assert abs((window - planned).total_seconds()) < 2
    # Parked resume time is ~30s out, not a short CLAIM_SECONDS-only lease.
    nxt = row.next_run_at
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=timezone.utc)
    assert nxt > datetime.now(timezone.utc) + timedelta(seconds=20)
    assert db.query(ScheduleRun).count() == 0


def test_run_one_does_not_drain_other_due(db_and_sessionmaker):
    db, _ = db_and_sessionmaker
    server = _linked(db)
    now = datetime.now(timezone.utc)
    target = ServerSchedule(
        server_id=server.id,
        name="Target",
        enabled=True,
        time_local="04:00",
        days_of_week="0,1,2,3,4,5,6",
        retry_after_minutes=10,
        next_run_at=now,
        active_window_at=now,
        last_status="",
        last_message="",
    )
    other = ServerSchedule(
        server_id=server.id,
        name="Other",
        enabled=True,
        time_local="05:00",
        days_of_week="0,1,2,3,4,5,6",
        retry_after_minutes=10,
        next_run_at=now,
        last_status="",
        last_message="",
    )
    db.add_all([target, other])
    db.flush()
    for s in (target, other):
        db.add(
            ScheduleAction(
                schedule_id=s.id,
                sort_order=0,
                action_type="wait",
                params_json=json.dumps({"seconds": 5}),
            )
        )
    db.commit()
    tid, oid = target.id, other.id

    runner = ScheduleRunner()
    assert runner.run_one(tid) is True

    db.expire_all()
    t = db.get(ServerSchedule, tid)
    o = db.get(ServerSchedule, oid)
    assert t.last_status == "waiting"
    assert o.last_status == ""
    assert o.resume_action_index is None
    other_next = o.next_run_at
    if other_next.tzinfo is None:
        other_next = other_next.replace(tzinfo=timezone.utc)
    assert other_next <= datetime.now(timezone.utc) + timedelta(seconds=1)


def test_claim_seconds_is_finite_not_hour_lock():
    # Non-wait work uses a short lease; long waits park instead.
    assert CLAIM_SECONDS <= 600


def test_is_claim_active_only_while_running_lease(db_and_sessionmaker):
    from app.services.schedule_runner import is_claim_active

    db, _ = db_and_sessionmaker
    server = _linked(db)
    now = datetime.now(timezone.utc)
    schedule = ServerSchedule(
        server_id=server.id,
        name="Lease",
        enabled=True,
        time_local="04:00",
        days_of_week="0,1,2,3,4,5,6",
        retry_after_minutes=10,
        next_run_at=now + timedelta(seconds=100),
        last_status="running",
        last_message="Running…",
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    assert is_claim_active(schedule, now=now) is True

    schedule.last_status = "waiting"
    schedule.resume_action_index = 1
    assert is_claim_active(schedule, now=now) is False

    schedule.last_status = "running"
    schedule.resume_action_index = None
    schedule.next_run_at = now - timedelta(seconds=1)
    assert is_claim_active(schedule, now=now) is False

    # Future calendar slot without running status is not a claim.
    schedule.last_status = ""
    schedule.next_run_at = now + timedelta(seconds=60)
    assert is_claim_active(schedule, now=now) is False


def test_resume_rechecks_and_can_fail(db_and_sessionmaker, monkeypatch):
    """After a parked wait, checks run again (players may have joined)."""
    db, _ = db_and_sessionmaker
    server = _linked(db)
    server.last_players = 5
    db.commit()

    now = datetime.now(timezone.utc)
    schedule = ServerSchedule(
        server_id=server.id,
        name="Recheck",
        enabled=True,
        time_local="04:00",
        days_of_week="0,1,2,3,4,5,6",
        retry_after_minutes=10,
        next_run_at=now,
        active_window_at=now - timedelta(minutes=1),
        resume_action_index=1,
        last_status="waiting",
        last_message="Waiting",
        pending_detail_json=json.dumps(
            {
                "checks": [{"type": "players_lte", "ok": True, "message": "was empty"}],
                "actions": [{"type": "wait", "ok": True}],
            }
        ),
    )
    db.add(schedule)
    db.flush()
    db.add(
        ScheduleAction(
            schedule_id=schedule.id,
            sort_order=0,
            action_type="wait",
            params_json=json.dumps({"seconds": 5}),
        )
    )
    db.add(
        ScheduleAction(
            schedule_id=schedule.id,
            sort_order=1,
            action_type="power",
            params_json=json.dumps({"signal": "restart"}),
        )
    )
    from app.models import ScheduleCheck

    db.add(
        ScheduleCheck(
            schedule_id=schedule.id,
            sort_order=0,
            check_type="players_lte",
            params_json=json.dumps({"value": 0}),
        )
    )
    db.commit()
    sid = schedule.id

    runner = ScheduleRunner()
    assert runner.run_one(sid) is True

    db.expire_all()
    row = db.get(ServerSchedule, sid)
    assert row is not None
    assert row.resume_action_index is None
    # Checks failed on resume — not still waiting / not success via power.
    assert row.last_status in ("checks_failed", "skipped")
    runs = db.query(ScheduleRun).filter(ScheduleRun.schedule_id == sid).all()
    assert len(runs) >= 1
    assert runs[-1].status in ("checks_failed", "skipped")

"""Schedule CRUD and runner smoke tests (in-process, no TestClient)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import schedules as routes
from app.models import ROLE_ADMIN, Base, Server, ServerSchedule, User
from app.schemas import (
    ScheduleActionIn,
    ScheduleCheckIn,
    ScheduleCreate,
    ScheduleEnable,
    ScheduleUpdate,
)
from app.services.server_options import save_options
from app.services.schedule_time import next_occurrence


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    # seed app timezone
    from app.models import Setting

    session.add(Setting(key="app_timezone", value="UTC"))
    session.commit()
    try:
        yield session
    finally:
        session.close()


def admin() -> User:
    return User(
        id=1,
        email="admin@example.org",
        email_ci="admin@example.org",
        role=ROLE_ADMIN,
        is_active=True,
    )


def linked_server(db) -> Server:
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


def test_create_requires_linked(db):
    server = Server(
        name="NoLink",
        host="h",
        query_port=1,
        rcon_port=1,
        rcon_password_enc="",
        server_type="sandstorm",
    )
    db.add(server)
    db.commit()
    body = ScheduleCreate(
        server_id=server.id,
        name="Nightly",
        time_local="04:00",
        actions=[ScheduleActionIn(action_type="power", params={"signal": "restart"})],
    )
    with pytest.raises(HTTPException) as ei:
        routes.create_schedule(body, admin(), db=db)
    assert ei.value.status_code == 400


def test_create_and_list(db):
    server = linked_server(db)
    body = ScheduleCreate(
        server_id=server.id,
        name="Nightly restart",
        time_local="04:00",
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        retry_after_minutes=10,
        actions=[ScheduleActionIn(action_type="power", params={"signal": "restart"}, sort_order=0)],
        checks=[ScheduleCheckIn(check_type="players_lte", params={"value": 2})],
    )
    out = routes.create_schedule(body, admin(), db=db)
    assert out.id > 0
    assert out.name == "Nightly restart"
    assert out.actions[0].action_type == "power"
    assert out.checks[0].check_type == "players_lte"
    assert out.next_run_at is not None

    listed = routes.list_schedules(admin(), server_id=server.id, db=db)
    assert len(listed) == 1
    assert listed[0].id == out.id


def test_update_time_recomputes_next(db):
    server = linked_server(db)
    created = routes.create_schedule(
        ScheduleCreate(
            server_id=server.id,
            name="A",
            time_local="03:00",
            actions=[ScheduleActionIn(action_type="power", params={"signal": "start"})],
        ),
        admin(),
        db=db,
    )
    first = created.next_run_at
    updated = routes.update_schedule(
        created.id,
        ScheduleUpdate(time_local="05:00"),
        admin(),
        db=db,
    )
    assert updated.time_local == "05:00"
    assert updated.next_run_at is not None
    # SQLite may drop tzinfo on round-trip; value must still be a real datetime.
    assert isinstance(updated.next_run_at, datetime)
    assert first is not None


def test_put_enabled_true_does_not_clear_retry_window(db):
    """UI always sends enabled=true; that must not abort an active retry."""
    server = linked_server(db)
    created = routes.create_schedule(
        ScheduleCreate(
            server_id=server.id,
            name="A",
            time_local="04:00",
            actions=[ScheduleActionIn(action_type="power", params={"signal": "start"})],
        ),
        admin(),
        db=db,
    )
    row = db.get(ServerSchedule, created.id)
    window = datetime(2026, 8, 11, 4, 0, tzinfo=timezone.utc)
    retry_at = window + timedelta(minutes=10)
    row.active_window_at = window
    row.next_run_at = retry_at
    row.last_status = "checks_failed"
    db.commit()

    updated = routes.update_schedule(
        created.id,
        ScheduleUpdate(
            name="A renamed",
            enabled=True,
            time_local="04:00",
            days_of_week=[0, 1, 2, 3, 4, 5, 6],
            retry_after_minutes=10,
            actions=[ScheduleActionIn(action_type="power", params={"signal": "start"})],
            checks=[],
        ),
        admin(),
        db=db,
    )
    assert updated.name == "A renamed"
    assert updated.active_window_at is not None
    # next_run_at should remain the retry, not be recomputed from now.
    # SQLite may drop tzinfo on read-back.
    got = updated.next_run_at
    if got.tzinfo is None:
        got = got.replace(tzinfo=timezone.utc)
    assert got == retry_at


def test_empty_days_rejected(db):
    server = linked_server(db)
    body = ScheduleCreate(
        server_id=server.id,
        name="Bad days",
        days_of_week=[],
        actions=[ScheduleActionIn(action_type="power", params={"signal": "restart"})],
    )
    with pytest.raises(HTTPException) as ei:
        routes.create_schedule(body, admin(), db=db)
    assert ei.value.status_code == 400
    assert "days_of_week" in str(ei.value.detail)


def test_enable_toggle(db):
    server = linked_server(db)
    created = routes.create_schedule(
        ScheduleCreate(
            server_id=server.id,
            name="A",
            actions=[ScheduleActionIn(action_type="power", params={"signal": "stop"})],
        ),
        admin(),
        db=db,
    )
    disabled = routes.enable_schedule(
        created.id, ScheduleEnable(enabled=False), admin(), db=db
    )
    assert disabled.enabled is False


def test_delete_preserves_run_history(db):
    from app.models import ScheduleRun

    server = linked_server(db)
    created = routes.create_schedule(
        ScheduleCreate(
            server_id=server.id,
            name="A",
            actions=[ScheduleActionIn(action_type="power", params={"signal": "restart"})],
        ),
        admin(),
        db=db,
    )
    db.add(
        ScheduleRun(
            schedule_id=created.id,
            server_id=server.id,
            scheduled_for=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            status="success",
            attempt=1,
            detail_json="{}",
            message="ok",
        )
    )
    db.commit()
    assert routes.delete_schedule(created.id, admin(), db=db) == {"ok": True}
    with pytest.raises(HTTPException) as ei:
        routes.get_schedule(created.id, admin(), db=db)
    assert ei.value.status_code == 404
    leftover = db.query(ScheduleRun).all()
    assert len(leftover) == 1
    assert leftover[0].schedule_id is None
    assert leftover[0].status == "success"


def test_meta(db):
    meta = routes.schedule_meta(admin(), db=db)
    assert meta.app_timezone == "UTC"
    assert any(a["id"] == "power" for a in meta.action_types)
    assert any(c["id"] == "players_lte" for c in meta.check_types)
    # Type-restricted actions expose server_types for the UI filter.
    travel = next(a for a in meta.action_types if a["id"] == "travel")
    assert "sandstorm" in travel["server_types"]
    say = next(a for a in meta.action_types if a["id"] == "say")
    assert "sandstorm" in say["server_types"]
    assert "satisfactory" not in say["server_types"]


def test_invalid_action_rejected(db):
    server = linked_server(db)
    body = ScheduleCreate(
        server_id=server.id,
        name="Bad",
        actions=[ScheduleActionIn(action_type="rcon", params={})],
    )
    with pytest.raises(HTTPException) as ei:
        routes.create_schedule(body, admin(), db=db)
    assert ei.value.status_code == 400


def test_rcon_requires_console_feature(db):
    """API rejects rcon on types without console (mirrors meta/UI)."""
    server = Server(
        name="Factory",
        host="10.0.0.2",
        query_port=1,
        rcon_port=1,
        rcon_password_enc="",
        server_type="satisfactory",
        last_players=0,
        last_online=True,
    )
    save_options(server, {"pterodactyl_uuid": "sat-uuid", "pterodactyl_name": "Factory"})
    db.add(server)
    db.commit()
    db.refresh(server)
    body = ScheduleCreate(
        server_id=server.id,
        name="Rcon no",
        actions=[
            ScheduleActionIn(action_type="rcon", params={"command": "status"})
        ],
    )
    with pytest.raises(HTTPException) as ei:
        routes.create_schedule(body, admin(), db=db)
    assert ei.value.status_code == 400
    assert "console" in str(ei.value.detail).lower() or "rcon" in str(ei.value.detail).lower()


def test_blank_name_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ScheduleCreate(
            server_id=1,
            name="   ",
            actions=[ScheduleActionIn(action_type="power", params={"signal": "start"})],
        )


def test_empty_actions_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ScheduleCreate(
            server_id=1,
            name="No steps",
            actions=[],
        )


def test_update_actions_mid_wait_cancels_resume(db):
    """Changing the action list must not leave a stale resume_action_index."""
    import json

    server = linked_server(db)
    created = routes.create_schedule(
        ScheduleCreate(
            server_id=server.id,
            name="Wait chain",
            time_local="04:00",
            actions=[
                ScheduleActionIn(action_type="wait", params={"seconds": 60}, sort_order=0),
                ScheduleActionIn(
                    action_type="power", params={"signal": "restart"}, sort_order=1
                ),
            ],
        ),
        admin(),
        db=db,
    )
    row = db.get(ServerSchedule, created.id)
    assert row is not None
    resume_at = datetime.now(timezone.utc) + timedelta(seconds=45)
    row.resume_action_index = 1
    row.last_status = "waiting"
    row.next_run_at = resume_at
    row.active_window_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    row.pending_detail_json = json.dumps({"checks": [], "actions": []})
    db.commit()

    routes.update_schedule(
        created.id,
        ScheduleUpdate(
            name="Wait chain",
            enabled=True,
            time_local="04:00",
            days_of_week=[0, 1, 2, 3, 4, 5, 6],
            retry_after_minutes=10,
            # Shorter chain: resume_index=1 would have been past the end.
            actions=[
                ScheduleActionIn(action_type="power", params={"signal": "start"}, sort_order=0),
            ],
            checks=[],
        ),
        admin(),
        db=db,
    )
    db.expire_all()
    row = db.get(ServerSchedule, created.id)
    assert row is not None
    assert row.resume_action_index is None
    assert (row.pending_detail_json or "{}") == "{}"
    assert row.last_status == "cancelled"
    assert row.active_window_at is None
    nxt = row.next_run_at
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=timezone.utc)
    # Calendar recompute: must not keep the mid-wait park time.
    assert abs((nxt - resume_at).total_seconds()) > 5


def test_update_same_actions_preserves_wait(db):
    """Full-form PUT with identical actions must not abort a parked wait."""
    import json

    server = linked_server(db)
    created = routes.create_schedule(
        ScheduleCreate(
            server_id=server.id,
            name="Stable wait",
            time_local="04:00",
            actions=[
                ScheduleActionIn(action_type="wait", params={"seconds": 30}, sort_order=0),
                ScheduleActionIn(
                    action_type="power", params={"signal": "restart"}, sort_order=1
                ),
            ],
        ),
        admin(),
        db=db,
    )
    row = db.get(ServerSchedule, created.id)
    resume_at = datetime.now(timezone.utc) + timedelta(seconds=20)
    window = datetime.now(timezone.utc) - timedelta(minutes=1)
    row.resume_action_index = 1
    row.last_status = "waiting"
    row.next_run_at = resume_at
    row.active_window_at = window
    row.pending_detail_json = json.dumps({"checks": [], "actions": [{"type": "wait"}]})
    db.commit()

    routes.update_schedule(
        created.id,
        ScheduleUpdate(
            name="Stable wait",
            enabled=True,
            time_local="04:00",
            days_of_week=[0, 1, 2, 3, 4, 5, 6],
            retry_after_minutes=10,
            actions=[
                ScheduleActionIn(action_type="wait", params={"seconds": 30}, sort_order=0),
                ScheduleActionIn(
                    action_type="power", params={"signal": "restart"}, sort_order=1
                ),
            ],
            checks=[],
        ),
        admin(),
        db=db,
    )
    db.expire_all()
    row = db.get(ServerSchedule, created.id)
    assert row is not None
    assert row.resume_action_index == 1
    assert row.last_status == "waiting"
    got = row.next_run_at
    if got.tzinfo is None:
        got = got.replace(tzinfo=timezone.utc)
    assert abs((got - resume_at).total_seconds()) < 2


def test_run_now_rejects_active_claim(db):
    server = linked_server(db)
    created = routes.create_schedule(
        ScheduleCreate(
            server_id=server.id,
            name="Busy",
            actions=[ScheduleActionIn(action_type="power", params={"signal": "start"})],
        ),
        admin(),
        db=db,
    )
    row = db.get(ServerSchedule, created.id)
    row.last_status = "running"
    row.last_message = "Running…"
    row.next_run_at = datetime.now(timezone.utc) + timedelta(seconds=120)
    row.resume_action_index = None
    db.commit()

    with pytest.raises(HTTPException) as ei:
        routes.run_now(created.id, admin(), db=db)
    assert ei.value.status_code == 409


def test_run_now_allowed_when_waiting(db, monkeypatch):
    """Parked wait may be interrupted; execution is async so we stub run_one."""
    server = linked_server(db)
    created = routes.create_schedule(
        ScheduleCreate(
            server_id=server.id,
            name="Waiting",
            actions=[
                ScheduleActionIn(action_type="wait", params={"seconds": 10}),
                ScheduleActionIn(action_type="power", params={"signal": "start"}),
            ],
        ),
        admin(),
        db=db,
    )
    row = db.get(ServerSchedule, created.id)
    row.last_status = "waiting"
    row.resume_action_index = 1
    row.next_run_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    db.commit()

    called: list[int] = []

    def fake_run_one(sid: int) -> bool:
        called.append(sid)
        return True

    monkeypatch.setattr(routes.schedule_runner, "run_one", fake_run_one)
    # Thread may race; also patch Thread to run inline for determinism.
    class ImmediateThread:
        def __init__(self, target=None, name=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    monkeypatch.setattr(routes.threading, "Thread", ImmediateThread)

    out = routes.run_now(created.id, admin(), db=db)
    assert out.id == created.id
    db.expire_all()
    row = db.get(ServerSchedule, created.id)
    assert row is not None
    assert row.resume_action_index is None
    assert called == [created.id]

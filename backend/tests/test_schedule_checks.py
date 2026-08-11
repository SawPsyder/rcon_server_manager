from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, PterodactylSample, ScheduleCheck, Server
from app.services.schedule_actions import evaluate_checks


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _server(**kwargs) -> Server:
    defaults = dict(
        id=1,
        name="Test",
        host="127.0.0.1",
        query_port=27131,
        rcon_port=27015,
        rcon_password_enc="",
        server_type="sandstorm",
        last_players=0,
        last_online=True,
    )
    defaults.update(kwargs)
    return Server(**defaults)


def _check(check_type: str, params: dict, sort_order: int = 0) -> ScheduleCheck:
    import json

    return ScheduleCheck(
        check_type=check_type,
        params_json=json.dumps(params),
        sort_order=sort_order,
    )


def test_players_lte_pass(db):
    server = _server(last_players=1)
    db.add(server)
    db.commit()
    ok, results = evaluate_checks(db, server, [_check("players_lte", {"value": 2})])
    assert ok
    assert results[0].ok


def test_players_lte_fail(db):
    server = _server(last_players=5)
    db.add(server)
    db.commit()
    ok, results = evaluate_checks(db, server, [_check("players_lte", {"value": 2})])
    assert not ok
    assert not results[0].ok


def test_players_unknown_fail_closed(db):
    """None player count must not pass empty-server guards."""
    server = _server(last_players=None)
    db.add(server)
    db.commit()
    ok, results = evaluate_checks(db, server, [_check("players_lte", {"value": 0})])
    assert not ok
    assert "unknown" in results[0].message.lower()


def test_online_unknown_fail_closed(db):
    server = _server(last_online=None)
    db.add(server)
    db.commit()
    ok, results = evaluate_checks(db, server, [_check("server_offline", {})])
    assert not ok
    assert "unknown" in results[0].message.lower()


def test_checks_and(db):
    server = _server(last_players=0, last_online=True)
    db.add(server)
    db.commit()
    ok, _ = evaluate_checks(
        db,
        server,
        [
            _check("players_eq", {"value": 0}),
            _check("server_online", {}),
        ],
    )
    assert ok


def test_container_state(db):
    server = _server()
    db.add(server)
    db.flush()
    db.add(
        PterodactylSample(
            server_id=server.id,
            state="running",
            cpu_absolute=1.0,
            memory_bytes=1,
            disk_bytes=1,
            network_rx_bytes=0,
            network_tx_bytes=0,
            uptime_ms=1000,
        )
    )
    db.commit()
    ok, results = evaluate_checks(
        db, server, [_check("container_state", {"state": "running"})]
    )
    assert ok
    assert "running" in results[0].message


def test_container_state_unknown_without_sample(db):
    server = _server()
    db.add(server)
    db.commit()
    ok, results = evaluate_checks(
        db, server, [_check("container_state", {"state": "running"})]
    )
    assert not ok
    assert "unknown" in results[0].message.lower()


def test_players_lte_invalid_param_fails_closed(db):
    """Non-numeric value must not be treated as 0."""
    server = _server(last_players=0)
    db.add(server)
    db.commit()
    ok, results = evaluate_checks(
        db, server, [_check("players_lte", {"value": "nope"})]
    )
    assert not ok
    assert "requires numeric" in results[0].message.lower()


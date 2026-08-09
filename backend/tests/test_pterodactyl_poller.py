"""The background poller, and the history series it feeds.

The poller is what makes the resource card and the chart agree: both read what
it fetched. These tests pin that, plus the two things easiest to get wrong -
it must force a fresh read (or the series freezes), and request handlers must
not go upstream afterwards (or the poll was pointless).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import server_pterodactyl as routes
from app.models import ROLE_ADMIN, Base, PterodactylSample, Server, User
from app.services import pterodactyl_api, pterodactyl_settings
from app.services.pterodactyl_api import PanelClient, POLL_INTERVAL_SECONDS
from app.services.pterodactyl_poller import (
    RESOURCE_BUDGET_PER_MINUTE,
    PterodactylPoller,
)
from app.services.server_options import save_options

UUID = "d3aac109-e5e0-4331-b03e-3454f7e136dc"


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db(engine):
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def clear_registry():
    yield
    pterodactyl_api.panel_registry.invalidate_all()


def make_user() -> User:
    return User(
        id=1, email="a@example.org", email_ci="a@example.org", role=ROLE_ADMIN, is_active=True
    )


def add_server(db, *, linked: bool = True) -> Server:
    server = Server(
        name="Sandstorm #1",
        host="10.0.0.5",
        query_port=27131,
        rcon_port=27015,
        rcon_password_enc="",
        server_type="sandstorm",
        options_json="{}",
    )
    if linked:
        save_options(server, {"pterodactyl_uuid": UUID})
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def configure_panel(db):
    pterodactyl_settings.save_pterodactyl_config(
        db, base_url="https://panel.example.com", api_key="k", verify_tls=True
    )
    db.commit()


def panel_handler(cpu=50.0, memory=1024, state="running"):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/resources"):
            return httpx.Response(200, json={"attributes": {
                "current_state": state, "is_suspended": False,
                "resources": {"memory_bytes": memory, "cpu_absolute": cpu,
                              "disk_bytes": 7, "network_rx_bytes": 8,
                              "network_tx_bytes": 9, "uptime": 1000}}})
        return httpx.Response(200, json={"attributes": {
            "uuid": UUID, "identifier": "d3aac109", "name": "Box", "node": "n1",
            "status": None, "is_suspended": False,
            "limits": {"memory": 4096, "disk": 20480, "cpu": 200}}})

    return handler, seen


def install(monkeypatch, engine, handler) -> PanelClient:
    """Point the poller and the routes at one client over one in-memory DB."""
    client = PanelClient(
        pterodactyl_settings.PterodactylConfig(base_url="https://p.example", api_key="k"),
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr("app.services.pterodactyl_api.client_for", lambda _c: client)
    monkeypatch.setattr(
        "app.services.pterodactyl_poller.SessionLocal", sessionmaker(bind=engine)
    )
    return client


# --- polling ---------------------------------------------------------------


def test_poll_writes_one_sample_per_linked_server(db, engine, monkeypatch):
    configure_panel(db)
    server = add_server(db)
    handler, _ = panel_handler(cpu=61.5, memory=2048)
    install(monkeypatch, engine, handler)

    assert PterodactylPoller().poll_all() == 1

    row = db.query(PterodactylSample).one()
    assert row.server_id == server.id
    assert row.cpu_absolute == pytest.approx(61.5)
    assert row.memory_bytes == 2048
    assert row.state == "running"
    # Stored for anyone who later wants a transfer rate: the counters reset on
    # restart, and a drop in uptime is the only reliable marker of that.
    assert (row.network_rx_bytes, row.network_tx_bytes, row.uptime_ms) == (8, 9, 1000)


def test_poll_forces_a_fresh_read(db, engine, monkeypatch):
    """Reading its own cache back would freeze the series on one value."""
    configure_panel(db)
    add_server(db)
    handler, seen = panel_handler()
    install(monkeypatch, engine, handler)

    poller = PterodactylPoller()
    poller.poll_all()
    poller.poll_all()

    resource_calls = [r for r in seen if r.url.path.endswith("/resources")]
    assert len(resource_calls) == 2
    assert db.query(PterodactylSample).count() == 2


def test_the_detail_page_then_costs_nothing_upstream(db, engine, monkeypatch):
    configure_panel(db)
    server = add_server(db)
    handler, seen = panel_handler()
    install(monkeypatch, engine, handler)

    PterodactylPoller().poll_all()
    after_poll = len(seen)

    for _ in range(3):
        out = routes.server_resources(server.id, make_user(), db=db)
    assert out.cpu_absolute == pytest.approx(50.0)
    assert len(seen) == after_poll, "a request went upstream despite a warm cache"
    # And it says how old the reading is rather than implying it is live.
    assert out.age_seconds >= 0.0


def test_an_unreachable_panel_writes_no_row(db, engine, monkeypatch):
    """A gap is the honest rendering of "we could not read this"."""
    configure_panel(db)
    add_server(db)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"errors": [
            {"code": "DaemonConnectionException", "status": "502", "detail": "down"}]})

    install(monkeypatch, engine, handler)
    assert PterodactylPoller().poll_all() == 0
    assert db.query(PterodactylSample).count() == 0


def test_unlinked_and_unconfigured_do_nothing(db, engine, monkeypatch):
    handler, seen = panel_handler()
    install(monkeypatch, engine, handler)

    add_server(db, linked=False)
    configure_panel(db)
    assert PterodactylPoller().poll_all() == 0
    assert seen == []


# --- cadence ---------------------------------------------------------------


def test_interval_is_the_panels_cache_period_at_normal_scale():
    for count in (0, 1, 10, 50):
        assert PterodactylPoller.interval_for(count) == POLL_INTERVAL_SECONDS


def test_interval_stretches_rather_than_exhausting_the_rate_limit():
    """Better a coarser chart than a key the whole app is locked out of."""
    many = 600
    interval = PterodactylPoller.interval_for(many)
    assert interval > POLL_INTERVAL_SECONDS
    assert many * 60.0 / interval <= RESOURCE_BUDGET_PER_MINUTE


# --- history ---------------------------------------------------------------


def seed(db, server_id: int, *, minutes_ago: float, cpu: float, memory: int) -> None:
    db.add(PterodactylSample(
        server_id=server_id,
        recorded_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        state="running", cpu_absolute=cpu, memory_bytes=memory,
        disk_bytes=0, network_rx_bytes=0, network_tx_bytes=0, uptime_ms=0))


def test_history_returns_raw_samples_and_summarises(db):
    server = add_server(db)
    for i, cpu in enumerate([10.0, 30.0, 200.0, 40.0]):
        seed(db, server.id, minutes_ago=60 - i, cpu=cpu, memory=1000 * (i + 1))
    db.commit()

    out = routes.server_history(server.id, make_user(), range_key="24h", db=db)
    # Under the chart cap: every sample is a point (no range-based averaging).
    assert len(out.points) == 4
    assert [p.cpu_absolute for p in out.points] == [10.0, 30.0, 200.0, 40.0]
    assert out.peak_cpu_absolute == pytest.approx(200.0)
    assert out.avg_cpu_absolute == pytest.approx(70.0)
    assert out.current_cpu_absolute == pytest.approx(40.0)
    assert out.peak_memory_bytes == 4000


def test_history_keeps_each_sample_when_under_the_chart_cap(db):
    """Unlike the old SQL buckets, close samples are not averaged into one point."""
    server = add_server(db)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    base = now - timedelta(minutes=30)
    for i, cpu in enumerate([10.0, 20.0, 30.0]):
        db.add(
            PterodactylSample(
                server_id=server.id,
                recorded_at=base + timedelta(seconds=i * 5),
                state="running",
                cpu_absolute=cpu,
                memory_bytes=1000 * (i + 1),
                disk_bytes=0,
                network_rx_bytes=0,
                network_tx_bytes=0,
                uptime_ms=0,
            )
        )
    db.commit()

    out = routes.server_history(server.id, make_user(), range_key="24h", db=db)
    assert len(out.points) == 3
    assert [p.cpu_absolute for p in out.points] == [10.0, 20.0, 30.0]
    assert [p.memory_bytes for p in out.points] == [1000, 2000, 3000]


def test_history_does_not_invent_empty_points_between_samples(db):
    """Match player/tick charts: only real samples, no synthetic outage slots."""
    server = add_server(db)
    seed(db, server.id, minutes_ago=300, cpu=10.0, memory=1)
    seed(db, server.id, minutes_ago=10, cpu=20.0, memory=2)
    db.commit()

    out = routes.server_history(server.id, make_user(), range_key="24h", db=db)
    assert len(out.points) == 2
    assert all(p.cpu_absolute is not None for p in out.points)
    assert out.points[0].cpu_absolute == pytest.approx(10.0)
    assert out.points[-1].cpu_absolute == pytest.approx(20.0)


def test_history_is_bounded_when_denser_than_the_chart_cap(db):
    from app.api.stats import MAX_CHART_POINTS

    server = add_server(db)
    # More than MAX_CHART_POINTS samples in range - thin like the player chart.
    for i in range(MAX_CHART_POINTS + 120):
        seed(db, server.id, minutes_ago=i * 0.5, cpu=float(i % 90), memory=i)
    db.commit()

    for key in ("24h", "7d", "30d", "180d", "1y"):
        out = routes.server_history(server.id, make_user(), range_key=key, db=db)
        assert len(out.points) <= MAX_CHART_POINTS, (
            f"{key} returned {len(out.points)} points"
        )


def test_history_survives_unlinking(db):
    """Unlinking should not erase history that was already recorded."""
    server = add_server(db)
    seed(db, server.id, minutes_ago=5, cpu=12.0, memory=1)
    db.commit()
    save_options(server, {})
    db.commit()

    out = routes.server_history(server.id, make_user(), range_key="24h", db=db)
    assert out.current_cpu_absolute == pytest.approx(12.0)


def test_history_is_empty_not_broken_for_a_server_with_no_samples(db):
    server = add_server(db, linked=False)
    out = routes.server_history(server.id, make_user(), range_key="24h", db=db)
    assert out.points == []
    assert out.peak_cpu_absolute is None

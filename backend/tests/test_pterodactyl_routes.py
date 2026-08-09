"""Pterodactyl route behaviour: redaction, kill gating, confirmation, audit log.

Route functions are called directly against an in-memory database, matching the
rest of the suite - there is no TestClient anywhere in it.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import server_pterodactyl as routes
from app.api import servers as server_routes
from app.api.servers import _to_out
from app.models import ROLE_ADMIN, ROLE_USER, Base, CommandHistory, Server, User
from app.schemas import ServerOptionsIn, ServerUpdate
from app.services import pterodactyl_api, pterodactyl_settings
from app.services.pterodactyl_api import PanelClient, PterodactylConflictError
from app.services.server_options import save_options

UUID = "d3aac109-e5e0-4331-b03e-3454f7e136dc"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def clear_registry():
    yield
    pterodactyl_api.panel_registry.invalidate_all()


def make_user(role: str = ROLE_USER) -> User:
    return User(
        id=1,
        email="op@example.org",
        email_ci="op@example.org",
        role=role,
        is_active=True,
    )


def make_server(db, *, linked: bool = True) -> Server:
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
        save_options(server, {"pterodactyl_uuid": UUID, "pterodactyl_name": "Box"})
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def configure_panel(db):
    pterodactyl_settings.save_pterodactyl_config(
        db,
        base_url="https://panel.example.com",
        api_key="ptlc_abcdefghijklmnopqrstuvwxyz012345",
        verify_tls=True,
    )
    db.commit()


def install_client(monkeypatch, handler) -> PanelClient:
    client = PanelClient(
        pterodactyl_settings.PterodactylConfig(
            base_url="https://panel.example.com", api_key="k"
        ),
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(
        "app.services.pterodactyl_api.client_for", lambda _cfg: client
    )
    return client


def power_handler(status: int = 204, payload=None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if payload is None:
            return httpx.Response(status)
        return httpx.Response(status, json=payload)

    return handler, seen


# --- redaction -------------------------------------------------------------


def test_operator_sees_the_link_flag_but_not_the_uuid(db):
    server = make_server(db)
    out = _to_out(server, make_user(ROLE_USER))
    # The panel is usable by a grant holder, so the flag must survive redaction.
    assert out.pterodactyl_linked is True
    # The panel's inventory identifiers are not theirs to see.
    assert out.options.pterodactyl_uuid == ""
    assert out.options.pterodactyl_name == ""
    # And the existing redaction still holds.
    assert out.rcon_port is None
    assert out.options.cert_fingerprint == ""


def test_admin_sees_the_uuid(db):
    server = make_server(db)
    out = _to_out(server, make_user(ROLE_ADMIN))
    assert out.pterodactyl_linked is True
    assert out.options.pterodactyl_uuid == UUID
    assert out.options.pterodactyl_name == "Box"


def test_unlinked_server_reports_false(db):
    out = _to_out(make_server(db, linked=False), make_user(ROLE_ADMIN))
    assert out.pterodactyl_linked is False


def test_cannot_link_two_servers_to_the_same_panel_uuid(db):
    make_server(db, linked=True)
    other = Server(
        name="Sandstorm #2",
        host="10.0.0.6",
        query_port=27132,
        rcon_port=27016,
        rcon_password_enc="",
        server_type="sandstorm",
        options_json="{}",
    )
    db.add(other)
    db.commit()
    db.refresh(other)

    body = ServerUpdate(options=ServerOptionsIn(pterodactyl_uuid=UUID))
    with pytest.raises(HTTPException) as exc:
        server_routes.update_server(other.id, body, make_user(ROLE_ADMIN), db=db)
    assert exc.value.status_code == 400
    assert "already linked" in exc.value.detail.lower()


def test_re_saving_the_same_panel_link_is_allowed(db):
    server = make_server(db, linked=True)
    body = ServerUpdate(
        options=ServerOptionsIn(pterodactyl_uuid=UUID, pterodactyl_name="Box")
    )
    out = server_routes.update_server(server.id, body, make_user(ROLE_ADMIN), db=db)
    assert out.options.pterodactyl_uuid == UUID


# --- guards ----------------------------------------------------------------


def test_unlinked_server_is_a_400(db):
    configure_panel(db)
    server = make_server(db, linked=False)
    with pytest.raises(HTTPException) as exc:
        routes.server_resources(server.id, make_user(), db=db)
    assert exc.value.status_code == 400
    assert "not linked" in exc.value.detail


def test_unconfigured_panel_is_a_400(db):
    server = make_server(db)
    with pytest.raises(HTTPException) as exc:
        routes.server_resources(server.id, make_user(), db=db)
    assert exc.value.status_code == 400
    assert "not configured" in exc.value.detail


def test_kill_is_admin_only(db, monkeypatch):
    configure_panel(db)
    server = make_server(db)
    handler, seen = power_handler()
    install_client(monkeypatch, handler)

    body = routes.PterodactylPowerRequest(signal="kill", confirm=True)
    with pytest.raises(HTTPException) as exc:
        routes.server_power(server.id, body, make_user(ROLE_USER), db=db)
    assert exc.value.status_code == 403
    # And nothing was sent upstream.
    assert seen == []


def test_kill_is_allowed_for_an_admin(db, monkeypatch):
    configure_panel(db)
    server = make_server(db)
    handler, seen = power_handler()
    install_client(monkeypatch, handler)

    body = routes.PterodactylPowerRequest(signal="kill", confirm=True)
    result = routes.server_power(server.id, body, make_user(ROLE_ADMIN), db=db)
    assert result.signal == "kill"
    assert len(seen) == 1


def test_operator_may_restart(db, monkeypatch):
    configure_panel(db)
    server = make_server(db)
    handler, seen = power_handler()
    install_client(monkeypatch, handler)

    body = routes.PterodactylPowerRequest(signal="restart", confirm=True)
    result = routes.server_power(server.id, body, make_user(ROLE_USER), db=db)
    assert len(seen) == 1
    # Fire-and-forget upstream, so the wording must not claim it happened.
    assert "requested" in result.detail.lower()


@pytest.mark.parametrize("signal", ["stop", "restart", "kill"])
def test_disruptive_signals_need_confirmation(db, monkeypatch, signal):
    configure_panel(db)
    server = make_server(db)
    handler, seen = power_handler()
    install_client(monkeypatch, handler)

    body = routes.PterodactylPowerRequest(signal=signal, confirm=False)
    with pytest.raises(HTTPException) as exc:
        routes.server_power(server.id, body, make_user(ROLE_ADMIN), db=db)
    assert exc.value.status_code == 400
    assert seen == []


def test_start_needs_no_confirmation(db, monkeypatch):
    configure_panel(db)
    server = make_server(db)
    handler, seen = power_handler()
    install_client(monkeypatch, handler)

    body = routes.PterodactylPowerRequest(signal="start", confirm=False)
    routes.server_power(server.id, body, make_user(ROLE_USER), db=db)
    assert len(seen) == 1


# --- audit -----------------------------------------------------------------


def test_a_power_action_is_logged_with_its_actor(db, monkeypatch):
    configure_panel(db)
    server = make_server(db)
    handler, _ = power_handler()
    install_client(monkeypatch, handler)

    user = make_user(ROLE_ADMIN)
    db.add(user)
    db.commit()

    body = routes.PterodactylPowerRequest(signal="restart", confirm=True)
    routes.server_power(server.id, body, user, db=db)

    rows = db.query(CommandHistory).all()
    assert len(rows) == 1
    assert rows[0].command == "pterodactyl:power restart"
    assert rows[0].actor_user_id == user.id


def test_a_refused_action_is_logged_too(db, monkeypatch):
    """A 409 from a suspended container is exactly what an audit trail is for."""
    configure_panel(db)
    server = make_server(db)
    detail = "This server is currently suspended."
    handler, _ = power_handler(
        status=409,
        payload={"errors": [{"code": "x", "status": "409", "detail": detail}]},
    )
    install_client(monkeypatch, handler)

    user = make_user(ROLE_ADMIN)
    db.add(user)
    db.commit()

    body = routes.PterodactylPowerRequest(signal="start", confirm=True)
    with pytest.raises(HTTPException) as exc:
        routes.server_power(server.id, body, user, db=db)
    assert exc.value.status_code == 409

    rows = db.query(CommandHistory).all()
    assert len(rows) == 1
    assert "refused" in rows[0].response


# --- error mapping ---------------------------------------------------------


def test_panel_404_becomes_400_not_404(db):
    """require_server_scope already answers 404 for "not your server"; passing
    the panel's own 404 through would make the two indistinguishable."""
    exc = routes._http_error(
        pterodactyl_api.PterodactylNotFoundError("gone", status=404)
    )
    assert exc.status_code == 400


def test_conflict_stays_a_409():
    assert routes._http_error(PterodactylConflictError("installing")).status_code == 409


def test_timeout_is_a_504():
    exc = routes._http_error(pterodactyl_api.PterodactylTimeoutError("slow"))
    assert exc.status_code == 504


def test_anything_else_is_a_502():
    assert routes._http_error(pterodactyl_api.PterodactylApiError("?")).status_code == 502


# --- resources -------------------------------------------------------------


def test_resources_convert_limits_to_bytes_and_hide_the_identifier(db, monkeypatch):
    configure_panel(db)
    server = make_server(db)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/resources"):
            return httpx.Response(
                200,
                json={
                    "attributes": {
                        "current_state": "running",
                        "is_suspended": False,
                        "resources": {
                            "memory_bytes": 671088640,
                            "cpu_absolute": 152.2,
                            "disk_bytes": 2147483648,
                            "network_rx_bytes": 1,
                            "network_tx_bytes": 2,
                            "uptime": 3600000,
                        },
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "attributes": {
                    "uuid": UUID,
                    "identifier": "d3aac109",
                    "name": "Box",
                    "node": "node-01",
                    "status": None,
                    "is_suspended": False,
                    "limits": {"memory": 4096, "disk": 0, "cpu": 200},
                }
            },
        )

    install_client(monkeypatch, handler)

    out = routes.server_resources(server.id, make_user(ROLE_ADMIN), db=db)
    assert out.memory_limit_bytes == 4096 * 1024 * 1024
    # disk limit 0 means unlimited, and must not become a zero
    assert out.disk_limit_bytes is None
    assert out.cpu_limit == 200
    assert out.identifier == "d3aac109"

    # An operator gets no panel deep-link.
    pterodactyl_api.client_for(None).invalidate_cache()
    out = routes.server_resources(server.id, make_user(ROLE_USER), db=db)
    assert out.identifier == ""

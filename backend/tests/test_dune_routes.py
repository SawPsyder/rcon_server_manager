"""Dune admin routes: type gate, missing secret, passthrough + audit."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import dune as routes
from app.models import ROLE_ADMIN, Base, CommandHistory, Server, User
from app.schemas import DuneScaleRequest, DuneSettingsUpdate
from app.security import encrypt_secret


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def admin() -> User:
    return User(
        id=1,
        email="op@example.org",
        email_ci="op@example.org",
        role=ROLE_ADMIN,
        is_active=True,
    )


def make_server(db, *, server_type="dune", secret="pw") -> Server:
    server = Server(
        name="Arrakis",
        host="quantumrabbit",
        query_port=8090,
        rcon_port=8090,
        rcon_password_enc=encrypt_secret(secret) if secret else "",
        server_type=server_type,
        options_json="{}",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


class FakeClient:
    def __init__(self):
        self.calls: list[tuple] = []

    def status(self):
        self.calls.append(("status",))
        return {"ok": True, "totalPlayers": 0, "maps": []}

    def scale_instance(self, map_name, replicas, *, force=False):
        self.calls.append(("scale", map_name, replicas, force))
        return {"ok": True, "replicas": replicas, "previous": 1}

    def save_settings(self, settings):
        self.calls.append(("settings", dict(settings)))
        return {"ok": True, "applied": list(settings), "errors": [], "restartRequired": True}


def test_wrong_type_rejected(db):
    server = make_server(db, server_type="palworld")
    with pytest.raises(HTTPException) as ei:
        routes.status(server.id, admin(), db=db)
    assert ei.value.status_code == 400
    assert "Dune" in ei.value.detail


def test_missing_password_rejected(db):
    server = make_server(db, secret="")
    with pytest.raises(HTTPException) as ei:
        routes.status(server.id, admin(), db=db)
    assert ei.value.status_code == 400
    assert "password" in ei.value.detail.lower()


def test_status_passthrough(db, monkeypatch):
    server = make_server(db)
    client = FakeClient()
    monkeypatch.setattr(routes, "client_for_server", lambda *a, **k: client)
    assert routes.status(server.id, admin(), db=db)["ok"] is True
    assert client.calls == [("status",)]


def test_scale_and_settings_are_audited(db, monkeypatch):
    server = make_server(db)
    client = FakeClient()
    monkeypatch.setattr(routes, "client_for_server", lambda *a, **k: client)
    user = admin()
    db.add(user)
    db.commit()

    scaled = routes.scale_instance(
        server.id, "DeepDesert_1", user, DuneScaleRequest(replicas=2), db=db
    )
    assert scaled.ok is True
    saved = routes.save_settings(
        server.id, user, DuneSettingsUpdate(settings={"sandstorms_enabled": "False"}), db=db
    )
    assert saved.restart_required is True
    assert saved.applied == ["sandstorms_enabled"]

    cmds = [row.command for row in db.query(CommandHistory).order_by(CommandHistory.id)]
    assert any(c.startswith("dune:scale DeepDesert_1 2") for c in cmds)
    assert any("sandstorms_enabled" in c for c in cmds)

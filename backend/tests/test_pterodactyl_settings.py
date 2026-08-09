"""Pterodactyl panel settings live in the database, with the key encrypted."""

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Setting
from app.services import pterodactyl_settings as store

KEY = "ptlc_abcdefghijklmnopqrstuvwxyz012345"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def save(db, **overrides):
    params = {
        "base_url": "https://panel.example.com",
        "api_key": KEY,
        "verify_tls": True,
    }
    params.update(overrides)
    cfg = store.save_pterodactyl_config(db, **params)
    db.commit()
    return cfg


# --- defaults --------------------------------------------------------------


def test_unconfigured_is_disabled(db):
    cfg = store.load_pterodactyl_config(db)
    assert cfg.base_url == ""
    assert cfg.api_key == ""
    assert cfg.enabled is False
    # Verification defaults on: a bad certificate should be opted into.
    assert cfg.verify_tls is True
    assert store.has_stored_api_key(db) is False


def test_url_without_a_key_is_not_enabled(db):
    save(db, api_key="")
    cfg = store.load_pterodactyl_config(db)
    assert cfg.base_url == "https://panel.example.com"
    assert cfg.enabled is False


# --- the secret ------------------------------------------------------------


def test_api_key_is_encrypted_at_rest(db):
    save(db)
    row = db.query(Setting).filter(Setting.key == store.KEY_API_KEY).first()
    assert row is not None
    assert row.value
    assert KEY not in row.value
    # And still round-trips.
    assert store.load_pterodactyl_config(db).api_key == KEY


def test_none_keeps_the_stored_key(db):
    save(db)
    save(db, api_key=None, base_url="https://moved.example.com")
    cfg = store.load_pterodactyl_config(db)
    assert cfg.api_key == KEY
    assert cfg.base_url == "https://moved.example.com"


def test_empty_string_clears_the_stored_key(db):
    save(db)
    save(db, api_key="")
    assert store.load_pterodactyl_config(db).api_key == ""
    assert store.has_stored_api_key(db) is False


def test_verify_tls_round_trips(db):
    save(db, verify_tls=False)
    assert store.load_pterodactyl_config(db).verify_tls is False
    save(db, verify_tls=True)
    assert store.load_pterodactyl_config(db).verify_tls is True


def test_no_prefix_validation_so_pelican_keys_work(db):
    """Pelican issues pacc_, and migrated keys keep ptlc_. Never inspect it."""
    save(db, api_key="pacc_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")
    cfg = store.load_pterodactyl_config(db)
    assert cfg.api_key.startswith("pacc_")
    assert cfg.enabled is True


# --- URL normalisation -----------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", ""),
        ("   ", ""),
        ("https://panel.example.com", "https://panel.example.com"),
        ("https://panel.example.com/", "https://panel.example.com"),
        ("https://panel.example.com/pterodactyl", "https://panel.example.com/pterodactyl"),
        # http is allowed for local development only
        ("http://localhost:8080", "http://localhost:8080"),
        ("http://127.0.0.1:8080/", "http://127.0.0.1:8080"),
        # Pasting an endpoint URL is the expected mistake, so strip the suffix
        ("https://panel.example.com/api", "https://panel.example.com"),
        ("https://panel.example.com/api/client", "https://panel.example.com"),
        ("https://panel.example.com/api/application/", "https://panel.example.com"),
    ],
)
def test_normalize_panel_url_accepts(raw, expected):
    assert store.normalize_panel_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "panel.example.com",  # no scheme
        "ftp://panel.example.com",  # wrong scheme
        "https://",  # no host
        "http://panel.example.com",  # plaintext to a remote host
        "https://user:pass@panel.example.com",  # credentials belong in the key
    ],
)
def test_normalize_panel_url_rejects(raw):
    with pytest.raises(HTTPException) as exc:
        store.normalize_panel_url(raw)
    assert exc.value.status_code == 400
    assert exc.value.detail


def test_saved_url_is_normalised(db):
    save(db, base_url="https://panel.example.com/api/client/")
    assert store.load_pterodactyl_config(db).base_url == "https://panel.example.com"


def test_url_joins_without_a_double_slash(db):
    cfg = save(db, base_url="https://panel.example.com/")
    assert cfg.url("/api/client") == "https://panel.example.com/api/client"
    assert cfg.url("api/client") == "https://panel.example.com/api/client"

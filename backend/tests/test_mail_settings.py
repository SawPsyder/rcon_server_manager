"""Mail settings live in the database, with the environment as a legacy fallback."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.models import Base, Setting
from app.services import mail_settings as store


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def env_smtp(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "legacy.example.org")
    monkeypatch.setenv("SMTP_FROM", "legacy@example.org")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://legacy.example.org")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_unconfigured_falls_back_to_the_environment(db, env_smtp):
    """An install that predates the UI keeps working on upgrade."""
    cfg = store.load_mail_config(db)
    assert cfg.host == "legacy.example.org"
    assert cfg.enabled is True
    assert store.is_configured(db) is False


def test_saving_makes_the_database_authoritative(db, env_smtp):
    store.save_mail_config(
        db,
        host="smtp.example.net",
        port=465,
        user="postmaster",
        password="hunter2",
        starttls=False,
        ssl=True,
        from_address="noreply@example.net",
        from_name="SSM",
        base_url="https://ssm.example.net/",
    )
    db.commit()

    cfg = store.load_mail_config(db)
    assert cfg.host == "smtp.example.net"
    assert cfg.port == 465
    assert cfg.ssl is True and cfg.starttls is False
    assert store.is_configured(db) is True


def test_clearing_the_host_is_not_undone_by_the_environment(db, env_smtp):
    """The trap this design exists to avoid.

    Without the 'configured' marker, saving an empty host would silently fall
    back to SMTP_HOST and mail could never be switched off from the UI.
    """
    store.save_mail_config(
        db, host="", port=587, user="", password="", starttls=True, ssl=False,
        from_address="", from_name="SSM", base_url="",
    )
    db.commit()

    cfg = store.load_mail_config(db)
    assert cfg.host == ""
    assert cfg.enabled is False


def test_password_is_encrypted_at_rest(db):
    store.save_mail_config(
        db, host="h", port=587, user="u", password="super-secret", starttls=True,
        ssl=False, from_address="a@b.c", from_name="SSM", base_url="https://x.y",
    )
    db.commit()

    stored = db.query(Setting).filter(Setting.key == store.KEY_PASSWORD).one().value
    assert stored != "super-secret"
    assert "super-secret" not in stored
    assert store.load_mail_config(db).password == "super-secret"


def test_password_none_keeps_the_stored_one(db):
    store.save_mail_config(
        db, host="h", port=587, user="u", password="keep-me", starttls=True,
        ssl=False, from_address="a@b.c", from_name="SSM", base_url="https://x.y",
    )
    db.commit()

    store.save_mail_config(
        db, host="h2", port=25, user="u", password=None, starttls=True,
        ssl=False, from_address="a@b.c", from_name="SSM", base_url="https://x.y",
    )
    db.commit()

    cfg = store.load_mail_config(db)
    assert cfg.host == "h2"
    assert cfg.password == "keep-me"


def test_empty_password_clears_it(db):
    store.save_mail_config(
        db, host="h", port=587, user="u", password="drop-me", starttls=True,
        ssl=False, from_address="a@b.c", from_name="SSM", base_url="https://x.y",
    )
    db.commit()
    store.save_mail_config(
        db, host="h", port=587, user="u", password="", starttls=True,
        ssl=False, from_address="a@b.c", from_name="SSM", base_url="https://x.y",
    )
    db.commit()
    assert store.load_mail_config(db).password == ""
    assert store.has_stored_password(db) is False


def test_link_is_built_from_the_configured_base_url_only(db):
    store.save_mail_config(
        db, host="h", port=587, user="", password="", starttls=True, ssl=False,
        from_address="a@b.c", from_name="SSM", base_url="https://ssm.example.org/",
    )
    db.commit()
    cfg = store.load_mail_config(db)
    assert cfg.link("/reset/abc") == "https://ssm.example.org/reset/abc"
    assert cfg.link("invite/xyz") == "https://ssm.example.org/invite/xyz"


def test_base_url_requires_https_except_localhost(db):
    with pytest.raises(Exception) as exc:
        store.save_mail_config(
            db, host="h", port=587, user="", password="", starttls=True, ssl=False,
            from_address="a@b.c", from_name="SSM", base_url="http://phish.example/",
        )
    assert "https" in str(exc.value.detail).lower()

    with pytest.raises(Exception) as exc2:
        store.save_mail_config(
            db, host="h", port=587, user="", password="", starttls=True, ssl=False,
            from_address="a@b.c", from_name="SSM",
            base_url="https://user:pass@ssm.example.org/",
        )
    assert "password" in str(exc2.value.detail).lower() or "username" in str(
        exc2.value.detail
    ).lower()

    store.save_mail_config(
        db, host="h", port=587, user="", password="", starttls=True, ssl=False,
        from_address="a@b.c", from_name="SSM", base_url="http://localhost:5173/",
    )
    db.commit()
    assert store.load_mail_config(db).base_url == "http://localhost:5173"


def test_no_base_url_yields_no_link(db):
    store.save_mail_config(
        db, host="h", port=587, user="", password="", starttls=True, ssl=False,
        from_address="a@b.c", from_name="SSM", base_url="",
    )
    db.commit()
    assert store.load_mail_config(db).link("/reset/abc") == ""


def test_enabled_requires_both_a_host_and_a_sender(db):
    store.save_mail_config(
        db, host="h", port=587, user="", password="", starttls=True, ssl=False,
        from_address="", from_name="SSM", base_url="https://x.y",
    )
    db.commit()
    # A host with nothing to send as cannot deliver.
    assert store.load_mail_config(db).enabled is False

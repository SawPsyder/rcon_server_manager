"""Mail configuration, stored in the database and edited from the UI.

Mail is operational config an administrator changes while the app is running -
a relay moves, a password rotates - so it lives in the ``settings`` table rather
than in the container's environment, where every change costs a redeploy.

Environment variables remain a fallback for installs that predate this, and
only until an administrator saves the form once: after that ``mail.configured``
is set and the database is the sole source of truth. Without that marker,
clearing the host in the UI would silently fall back to the old env value and
mail would refuse to stay switched off.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Setting
from app.security import decrypt_secret, encrypt_secret

CONFIGURED_KEY = "mail.configured"

KEY_HOST = "mail.host"
KEY_PORT = "mail.port"
KEY_USER = "mail.user"
KEY_PASSWORD = "mail.password_enc"
KEY_STARTTLS = "mail.starttls"
KEY_SSL = "mail.ssl"
KEY_FROM = "mail.from"
KEY_FROM_NAME = "mail.from_name"
KEY_BASE_URL = "mail.base_url"


@dataclass(frozen=True)
class MailConfig:
    """A resolved snapshot of the mail settings.

    Passed explicitly into the mailer so a background send does not need a
    database session on a worker thread.
    """

    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""
    starttls: bool = True
    ssl: bool = False
    from_address: str = ""
    from_name: str = "Sandstorm Server Manager"
    base_url: str = ""
    timeout: float = 10.0

    @property
    def enabled(self) -> bool:
        """Whether a message can actually be sent."""
        return bool(self.host.strip() and self.resolved_from)

    @property
    def resolved_from(self) -> str:
        return (self.from_address or self.user or "").strip()

    def link(self, path: str) -> str:
        """Absolute app URL for an emailed link.

        Built from the configured base URL only. Deriving it from the request's
        Host header would let a spoofed Host redirect a reset link - and the
        token inside it - to an attacker-controlled domain.
        """
        base = self.base_url.strip().rstrip("/")
        if not base:
            return ""
        return f"{base}/{path.lstrip('/')}"


def _get(db: Session, key: str) -> str | None:
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row else None


def _set(db: Session, key: str, value: str) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def normalize_base_url(base_url: str) -> str:
    """Validate and normalize the public app URL used in emailed links.

    Empty is allowed (mail still works; invite/reset fall back to showing the
    link in the UI). Non-empty values must be an absolute http(s) URL without
    credentials. https is required except for localhost / 127.0.0.1 (dev).
    """
    raw = (base_url or "").strip().rstrip("/")
    if not raw:
        return ""

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Application URL must start with https:// (or http:// for local development)",
        )
    if not parsed.netloc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Application URL must include a host",
        )
    if parsed.username is not None or parsed.password is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Application URL must not contain a username or password",
        )

    host = (parsed.hostname or "").lower()
    local = host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not local:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Application URL must use https:// (http:// is only allowed for localhost)",
        )

    # Rebuild from parts so trailing junk / fragments never land in emails.
    path = parsed.path.rstrip("/") if parsed.path and parsed.path != "/" else ""
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def is_configured(db: Session) -> bool:
    """True once an administrator has saved the mail form at least once."""
    return _get(db, CONFIGURED_KEY) == "1"


def load_mail_config(db: Session) -> MailConfig:
    env = get_settings()

    if not is_configured(db):
        # Legacy install: keep honouring the environment until someone saves.
        return MailConfig(
            host=env.smtp_host,
            port=env.smtp_port,
            user=env.smtp_user,
            password=env.smtp_password,
            starttls=env.smtp_starttls,
            ssl=env.smtp_ssl,
            from_address=env.smtp_from,
            from_name=env.smtp_from_name,
            base_url=env.public_base_url,
            timeout=env.smtp_timeout,
        )

    return MailConfig(
        host=(_get(db, KEY_HOST) or "").strip(),
        port=_as_int(_get(db, KEY_PORT), 587),
        user=(_get(db, KEY_USER) or "").strip(),
        password=decrypt_secret(_get(db, KEY_PASSWORD) or ""),
        starttls=_as_bool(_get(db, KEY_STARTTLS), True),
        ssl=_as_bool(_get(db, KEY_SSL), False),
        from_address=(_get(db, KEY_FROM) or "").strip(),
        from_name=(_get(db, KEY_FROM_NAME) or "Sandstorm Server Manager").strip(),
        base_url=(_get(db, KEY_BASE_URL) or "").strip(),
        timeout=env.smtp_timeout,
    )


def save_mail_config(
    db: Session,
    *,
    host: str,
    port: int,
    user: str,
    password: str | None,
    starttls: bool,
    ssl: bool,
    from_address: str,
    from_name: str,
    base_url: str,
) -> MailConfig:
    """Persist the mail settings. ``password=None`` keeps the stored one."""
    clean_base = normalize_base_url(base_url)

    _set(db, KEY_HOST, host.strip())
    _set(db, KEY_PORT, str(port))
    _set(db, KEY_USER, user.strip())
    _set(db, KEY_STARTTLS, "1" if starttls else "0")
    _set(db, KEY_SSL, "1" if ssl else "0")
    _set(db, KEY_FROM, from_address.strip())
    _set(db, KEY_FROM_NAME, from_name.strip())
    _set(db, KEY_BASE_URL, clean_base)

    if password is not None:
        # Encrypted with the same Fernet key that protects RCON passwords.
        _set(db, KEY_PASSWORD, encrypt_secret(password) if password else "")

    _set(db, CONFIGURED_KEY, "1")
    db.flush()
    return load_mail_config(db)


def has_stored_password(db: Session) -> bool:
    if not is_configured(db):
        return bool(get_settings().smtp_password)
    return bool(_get(db, KEY_PASSWORD))

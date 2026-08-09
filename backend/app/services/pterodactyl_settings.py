"""Pterodactyl panel credentials, stored in the database and edited from the UI.

Like mail, this is operational config an administrator changes while the app is
running - a panel moves, a key rotates - so it lives in the ``settings`` table
rather than in the environment, where every change costs a redeploy. Unlike
mail there is no environment fallback and therefore no ``configured`` marker:
the feature is new, so the database is the only source of truth from day one.

Only the **Client API** is usable here. An admin "Application" key
(``/admin/api``) can list servers but has neither a resources endpoint nor a
power endpoint, so it is the wrong credential no matter how much access it
carries. The key must come from Account Settings -> API Credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import Setting
from app.security import decrypt_secret, encrypt_secret

KEY_BASE_URL = "pterodactyl.base_url"
KEY_API_KEY = "pterodactyl.api_key_enc"
KEY_VERIFY_TLS = "pterodactyl.verify_tls"

DEFAULT_TIMEOUT = 12.0


@dataclass(frozen=True)
class PterodactylConfig:
    """A resolved snapshot of the panel connection settings.

    Passed explicitly into the API client so the background stats collector can
    use it on a worker thread without holding a database session.
    """

    base_url: str = ""
    api_key: str = ""
    verify_tls: bool = True
    timeout: float = DEFAULT_TIMEOUT

    @property
    def enabled(self) -> bool:
        """Whether a call to the panel could actually be made."""
        return bool(self.base_url.strip() and self.api_key.strip())

    def url(self, path: str) -> str:
        """Absolute panel URL. No trailing slash on the result.

        Some reverse proxies answer a trailing-slash redirect by dropping the
        Authorization header, which surfaces as a confusing 401, so paths are
        joined without one.
        """
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}".rstrip("/")


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


def normalize_panel_url(base_url: str) -> str:
    """Validate and normalize the panel's base URL.

    Empty is allowed - it just means the integration is switched off. Non-empty
    values must be an absolute http(s) URL without credentials; https is
    required except for localhost, matching ``mail_settings.normalize_base_url``.

    A trailing ``/api`` (or ``/api/client``) is stripped rather than rejected:
    the setup docs an admin is most likely reading show full endpoint URLs, so
    pasting one is the expected mistake, and silently fixing it beats a 404
    later that reads like the panel is broken.
    """
    raw = (base_url or "").strip().rstrip("/")
    if not raw:
        return ""

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Panel URL must start with https:// (or http:// for local development)",
        )
    if not parsed.netloc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Panel URL must include a host",
        )
    if parsed.username is not None or parsed.password is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Panel URL must not contain a username or password",
        )

    host = (parsed.hostname or "").lower()
    local = host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not local:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Panel URL must use https:// (http:// is only allowed for localhost)",
        )

    path = parsed.path.rstrip("/") if parsed.path and parsed.path != "/" else ""
    lowered = path.lower()
    for suffix in ("/api/application", "/api/client", "/api"):
        if lowered.endswith(suffix):
            path = path[: -len(suffix)]
            break

    return f"{parsed.scheme}://{parsed.netloc}{path}"


def load_pterodactyl_config(db: Session) -> PterodactylConfig:
    return PterodactylConfig(
        base_url=(_get(db, KEY_BASE_URL) or "").strip(),
        api_key=decrypt_secret(_get(db, KEY_API_KEY) or "").strip(),
        # Defaults on: a self-hosted panel with a bad certificate should have to
        # be opted into, not silently accepted.
        verify_tls=_as_bool(_get(db, KEY_VERIFY_TLS), True),
    )


def save_pterodactyl_config(
    db: Session,
    *,
    base_url: str,
    api_key: str | None,
    verify_tls: bool,
) -> PterodactylConfig:
    """Persist the panel settings. ``api_key=None`` keeps the stored one."""
    clean_url = normalize_panel_url(base_url)

    _set(db, KEY_BASE_URL, clean_url)
    _set(db, KEY_VERIFY_TLS, "1" if verify_tls else "0")

    if api_key is not None:
        # Encrypted with the same Fernet key that protects RCON passwords.
        _set(db, KEY_API_KEY, encrypt_secret(api_key.strip()) if api_key.strip() else "")

    db.flush()
    return load_pterodactyl_config(db)


def has_stored_api_key(db: Session) -> bool:
    return bool(_get(db, KEY_API_KEY))

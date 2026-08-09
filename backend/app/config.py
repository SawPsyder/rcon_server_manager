from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    admin_password: str = "change-me"
    secret_key: str = "dev-secret-change-me"
    encryption_key: str = ""

    data_dir: Path = Path("./data")
    # Full SQLAlchemy URL. If empty, falls back to SQLite under data_dir (local dev)
    # or builds Postgres URL from POSTGRES_* when POSTGRES_HOST is set.
    database_url: str = ""

    # Optional Postgres pieces (used when DATABASE_URL is empty and POSTGRES_HOST is set)
    postgres_host: str = ""
    postgres_port: int = 5432
    postgres_user: str = "rcon"
    postgres_password: str = "rcon"
    postgres_db: str = "rcon_manager"

    # Connection pool (Postgres / production)
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800

    host: str = "0.0.0.0"
    port: int = 8080
    session_https_only: bool = False
    session_max_age: int = 60 * 60 * 24 * 7  # 7 days
    cookie_name: str = "ssm_session"

    query_timeout: float = 2.0
    rcon_timeout: float = 5.0

    # Optional Steam Web API key for persona name resolution
    # https://steamcommunity.com/dev/apikey
    # Accepts STEAM_WEB_API_KEY or STEAM_API_KEY
    steam_web_api_key: str = ""
    steam_api_key: str = ""  # alias
    # How long a cached Steam persona is considered fresh for force-refresh (seconds)
    identity_cache_ttl_seconds: int = 60 * 60 * 24 * 7  # 7 days

    # ---- Multi-user module ----
    # Absolute base URL of this deployment, e.g. https://ssm.example.org.
    # Invite / password-reset links are built from THIS, never from the request's
    # Host header - a spoofed Host would otherwise redirect the reset link
    # (and the token in it) to an attacker-controlled domain.
    public_base_url: str = ""
    reset_token_ttl_minutes: int = 60
    invite_token_ttl_hours: int = 72

    # ---- Outgoing mail (optional) ----
    # With smtp_host empty the app degrades gracefully: invite/reset links are
    # returned to the admin in the API response instead of being emailed.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    smtp_ssl: bool = False  # implicit TLS (port 465); mutually exclusive with starttls
    smtp_from: str = ""
    smtp_from_name: str = "Sandstorm Server Manager"
    smtp_timeout: float = 10.0

    # ---- Cloudflare Turnstile (optional) ----
    # Both must be set or the feature stays off. The secret is read from the
    # TURNSTILE_SECRET environment variable and never appears in source.
    turnstile_site_key: str = ""
    turnstile_secret: str = ""
    turnstile_timeout: float = 8.0

    # Comma-separated proxy addresses whose X-Forwarded-For we trust. Empty means
    # trust nothing and always use the socket peer - see deps.client_ip.
    trusted_proxy_ips: str = ""

    def resolved_steam_api_key(self) -> str:
        return (self.steam_web_api_key or self.steam_api_key or "").strip()

    @property
    def turnstile_enabled(self) -> bool:
        return bool(self.turnstile_site_key.strip() and self.turnstile_secret.strip())

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host.strip())

    @property
    def resolved_smtp_from(self) -> str:
        """Envelope/From address. Falls back to the auth user when unset."""
        return (self.smtp_from or self.smtp_user or "").strip()

    @property
    def trusted_proxies(self) -> set[str]:
        return {p.strip() for p in self.trusted_proxy_ips.split(",") if p.strip()}

    def base_url(self) -> str:
        return self.public_base_url.strip().rstrip("/")

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            url = self.database_url.strip()
            # Normalize common Postgres schemes for SQLAlchemy + psycopg3
            if url.startswith("postgres://"):
                url = "postgresql+psycopg://" + url[len("postgres://") :]
            elif url.startswith("postgresql://") and "+psycopg" not in url.split("://", 1)[0]:
                url = "postgresql+psycopg://" + url[len("postgresql://") :]
            return url

        if self.postgres_host:
            user = quote_plus(self.postgres_user)
            password = quote_plus(self.postgres_password)
            return (
                f"postgresql+psycopg://{user}:{password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )

        db_path = (self.data_dir / "app.db").resolve()
        return f"sqlite:///{db_path.as_posix()}"

    @property
    def is_sqlite(self) -> bool:
        return self.resolved_database_url.startswith("sqlite")

    @property
    def is_postgres(self) -> bool:
        return "postgresql" in self.resolved_database_url.split("://", 1)[0]


@lru_cache
def get_settings() -> Settings:
    return Settings()

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

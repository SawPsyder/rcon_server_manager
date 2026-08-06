"""Idempotent schema upgrades for existing installs (no Alembic).

Fresh installs rely on SQLAlchemy create_all. This module only patches
older SQLite (and compatible) databases that pre-date newer columns.
On Postgres, create_all creates the full current schema — column patches
are still applied safely via information_schema when tables already exist.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _dialect(engine: Engine) -> str:
    return engine.dialect.name


def _table_exists(engine: Engine, table: str) -> bool:
    return inspect(engine).has_table(table)


def _table_columns(engine: Engine, table: str) -> set[str]:
    insp = inspect(engine)
    if not insp.has_table(table):
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _add_column(engine: Engine, table: str, column_def: str) -> None:
    """Add a column if missing. column_def is dialect-neutral SQL after ADD COLUMN."""
    col_name = column_def.split()[0]
    cols = _table_columns(engine, table)
    if col_name in cols:
        return
    sql = f"ALTER TABLE {table} ADD COLUMN {column_def}"
    with engine.begin() as conn:
        conn.execute(text(sql))
    logger.info("Added column %s.%s (%s)", table, col_name, _dialect(engine))


def run_migrations(engine: Engine) -> None:
    """Apply lightweight migrations for existing DBs created with older schemas."""
    if not _table_exists(engine, "servers"):
        return

    dialect = _dialect(engine)

    # servers
    _add_column(engine, "servers", "server_type VARCHAR(32) DEFAULT 'sandstorm'")
    _add_column(engine, "servers", "preferred_gamemode VARCHAR(64)")
    _add_column(engine, "servers", "options_json TEXT DEFAULT '{}'")
    _add_column(engine, "servers", "last_hostname VARCHAR(255)")
    _add_column(engine, "servers", "last_map VARCHAR(128)")
    _add_column(engine, "servers", "last_lighting VARCHAR(64)")
    _add_column(engine, "servers", "last_gamemode VARCHAR(128)")
    _add_column(engine, "servers", "last_coop_or_versus VARCHAR(64)")
    _add_column(engine, "servers", "last_players INTEGER")
    _add_column(engine, "servers", "last_max_players INTEGER")
    if dialect == "postgresql":
        _add_column(engine, "servers", "last_online BOOLEAN")
        _add_column(engine, "servers", "last_status_at TIMESTAMPTZ")
    else:
        _add_column(engine, "servers", "last_online BOOLEAN")
        _add_column(engine, "servers", "last_status_at DATETIME")

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE servers SET server_type = 'sandstorm' "
                "WHERE server_type IS NULL OR server_type = ''"
            )
        )
        conn.execute(
            text(
                "UPDATE servers SET options_json = '{}' "
                "WHERE options_json IS NULL OR options_json = ''"
            )
        )

    # maps
    if _table_exists(engine, "maps"):
        _add_column(engine, "maps", "server_type VARCHAR(32) DEFAULT 'sandstorm'")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE maps SET server_type = 'sandstorm' "
                    "WHERE server_type IS NULL OR server_type = ''"
                )
            )

    # custom_buttons
    if _table_exists(engine, "custom_buttons"):
        _add_column(engine, "custom_buttons", "server_type VARCHAR(32) DEFAULT 'sandstorm'")
        # FK-compatible nullable int
        if dialect == "postgresql":
            _add_column(engine, "custom_buttons", "server_id INTEGER")
        else:
            _add_column(engine, "custom_buttons", "server_id INTEGER")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE custom_buttons SET server_type = 'sandstorm' "
                    "WHERE server_type IS NULL OR server_type = ''"
                )
            )

    # migrate legacy preferred_gamemode setting → type default key
    if _table_exists(engine, "settings"):
        with engine.begin() as conn:
            legacy = conn.execute(
                text("SELECT value FROM settings WHERE key = 'preferred_gamemode'")
            ).fetchone()
            typed = conn.execute(
                text(
                    "SELECT value FROM settings WHERE key = 'type.sandstorm.preferred_gamemode'"
                )
            ).fetchone()
            if legacy and not typed:
                conn.execute(
                    text(
                        "INSERT INTO settings (key, value) "
                        "VALUES ('type.sandstorm.preferred_gamemode', :v)"
                    ),
                    {"v": legacy[0]},
                )
                logger.info("Migrated preferred_gamemode → type.sandstorm.preferred_gamemode")

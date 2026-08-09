"""Idempotent schema upgrades for existing installs (no Alembic).

Fresh installs rely on SQLAlchemy create_all. This module only patches
older SQLite (and compatible) databases that pre-date newer columns.
On Postgres, create_all creates the full current schema - column patches
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


def _renormalize_unknown_identities(engine: Engine) -> None:
    """Re-file moderation rows saved before crossplay ids were understood.

    Platform-prefixed ids (``gdk_2535…``) used to fall through to
    ``platform='unknown'`` with the prefix left on the external_id. The dossier
    looks up ``(xbox, 2535…)``, so those rows were invisible - the moderation
    history simply appeared empty. Neither table has a uniqueness constraint on
    the identity pair, so this is a straight update.
    """
    from app.services.identity import parse_net_id

    for table in ("player_action_logs", "player_admin_notes"):
        if not _table_exists(engine, table):
            continue
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    f"SELECT id, external_id FROM {table} WHERE platform = 'unknown'"
                )
            ).fetchall()
            fixed = 0
            for row_id, external_id in rows:
                parsed = parse_net_id(external_id or "")
                if parsed is None or parsed[0] == "unknown":
                    continue
                platform, canonical = parsed
                if canonical == external_id and platform == "unknown":
                    continue
                conn.execute(
                    text(
                        f"UPDATE {table} SET platform = :p, external_id = :e "
                        f"WHERE id = :i"
                    ),
                    {"p": platform, "e": canonical, "i": row_id},
                )
                fixed += 1
        if fixed:
            logger.info("Re-filed %s crossplay identities in %s", fixed, table)


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


def _ensure_player_note_author_unique(engine: Engine) -> None:
    """One note per author per identity (multi-author notebook model).

    Collapses any accidental duplicates (keep the newest by updated_at/id)
    before creating the unique index. Multiple NULL-author legacy rows are left
    alone — UNIQUE treats NULLs as distinct on both SQLite and Postgres.
    """
    if not _table_exists(engine, "player_admin_notes"):
        return

    index_name = "uq_player_note_author"
    insp = inspect(engine)
    existing = {idx["name"] for idx in insp.get_indexes("player_admin_notes")}
    # SQLAlchemy may also surface unique constraints via get_unique_constraints.
    for uc in insp.get_unique_constraints("player_admin_notes"):
        if uc.get("name"):
            existing.add(uc["name"])
    if index_name in existing:
        return

    with engine.begin() as conn:
        # Keep the newest row for each (platform, external_id, author_user_id)
        # where author is known; drop the rest.
        dups = conn.execute(
            text(
                """
                SELECT platform, external_id, author_user_id, COUNT(*) AS c
                FROM player_admin_notes
                WHERE author_user_id IS NOT NULL
                GROUP BY platform, external_id, author_user_id
                HAVING COUNT(*) > 1
                """
            )
        ).fetchall()
        removed = 0
        for platform, external_id, author_user_id, _count in dups:
            rows = conn.execute(
                text(
                    """
                    SELECT id FROM player_admin_notes
                    WHERE platform = :p AND external_id = :e AND author_user_id = :a
                    ORDER BY updated_at DESC, id DESC
                    """
                ),
                {"p": platform, "e": external_id, "a": author_user_id},
            ).fetchall()
            for (row_id,) in rows[1:]:
                conn.execute(
                    text("DELETE FROM player_admin_notes WHERE id = :i"),
                    {"i": row_id},
                )
                removed += 1
        if removed:
            logger.info(
                "Collapsed %s duplicate player_admin_notes before unique index",
                removed,
            )

        conn.execute(
            text(
                f"CREATE UNIQUE INDEX {index_name} ON player_admin_notes "
                f"(platform, external_id, author_user_id)"
            )
        )
    logger.info("Created unique index %s on player_admin_notes", index_name)


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

    # player_count_samples: store roster at sample time for chart tooltips
    if _table_exists(engine, "player_count_samples"):
        if dialect == "postgresql":
            _add_column(engine, "player_count_samples", "roster_json TEXT DEFAULT '[]'")
        else:
            _add_column(engine, "player_count_samples", "roster_json TEXT DEFAULT '[]'")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE player_count_samples SET roster_json = '[]' "
                    "WHERE roster_json IS NULL OR roster_json = ''"
                )
            )
        # Tick rate at sample time. Left NULL for existing rows on purpose -
        # backfilling a zero would draw a flat line for history we never sampled.
        if dialect == "postgresql":
            _add_column(engine, "player_count_samples", "tick_rate DOUBLE PRECISION")
        else:
            _add_column(engine, "player_count_samples", "tick_rate REAL")

    # End of the previous session, so the player table can show "last visit"
    # while someone is online. Left NULL for existing rows on purpose - we never
    # recorded it, and inventing a timestamp would read as fact.
    if _table_exists(engine, "player_server_stats"):
        if dialect == "postgresql":
            _add_column(engine, "player_server_stats", "previous_seen_at TIMESTAMPTZ")
        else:
            _add_column(engine, "player_server_stats", "previous_seen_at TIMESTAMP")

    # pterodactyl_samples: container utilisation history, on its own 20s clock
    if not _table_exists(engine, "pterodactyl_samples"):
        if dialect == "postgresql":
            ddl = """
            CREATE TABLE pterodactyl_samples (
                id BIGSERIAL PRIMARY KEY,
                server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
                recorded_at TIMESTAMPTZ NOT NULL,
                state VARCHAR(16) NOT NULL DEFAULT 'offline',
                cpu_absolute DOUBLE PRECISION NOT NULL DEFAULT 0,
                memory_bytes BIGINT NOT NULL DEFAULT 0,
                disk_bytes BIGINT NOT NULL DEFAULT 0,
                network_rx_bytes BIGINT NOT NULL DEFAULT 0,
                network_tx_bytes BIGINT NOT NULL DEFAULT 0,
                uptime_ms BIGINT NOT NULL DEFAULT 0
            );
            CREATE INDEX ix_pterodactyl_samples_server_id ON pterodactyl_samples (server_id);
            CREATE INDEX ix_pterodactyl_samples_recorded_at ON pterodactyl_samples (recorded_at);
            CREATE INDEX ix_pterodactyl_samples_server_time
                ON pterodactyl_samples (server_id, recorded_at);
            """
        else:
            ddl = """
            CREATE TABLE pterodactyl_samples (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
                recorded_at DATETIME NOT NULL,
                state VARCHAR(16) NOT NULL DEFAULT 'offline',
                cpu_absolute REAL NOT NULL DEFAULT 0,
                memory_bytes BIGINT NOT NULL DEFAULT 0,
                disk_bytes BIGINT NOT NULL DEFAULT 0,
                network_rx_bytes BIGINT NOT NULL DEFAULT 0,
                network_tx_bytes BIGINT NOT NULL DEFAULT 0,
                uptime_ms BIGINT NOT NULL DEFAULT 0
            );
            CREATE INDEX ix_pterodactyl_samples_server_id ON pterodactyl_samples (server_id);
            CREATE INDEX ix_pterodactyl_samples_recorded_at ON pterodactyl_samples (recorded_at);
            CREATE INDEX ix_pterodactyl_samples_server_time
                ON pterodactyl_samples (server_id, recorded_at);
            """
        with engine.begin() as conn:
            for stmt in ddl.strip().split(";"):
                s = stmt.strip()
                if s:
                    conn.execute(text(s))
        logger.info("Created table pterodactyl_samples (%s)", dialect)

    # chart_shares: public cryptic chart share tokens
    if not _table_exists(engine, "chart_shares"):
        if dialect == "postgresql":
            ddl = """
            CREATE TABLE chart_shares (
                id SERIAL PRIMARY KEY,
                token VARCHAR(64) NOT NULL,
                server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL,
                CONSTRAINT uq_chart_share_server UNIQUE (server_id)
            );
            CREATE UNIQUE INDEX ix_chart_shares_token ON chart_shares (token);
            """
        else:
            ddl = """
            CREATE TABLE chart_shares (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                token VARCHAR(64) NOT NULL,
                server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
                created_at DATETIME NOT NULL,
                CONSTRAINT uq_chart_share_server UNIQUE (server_id)
            );
            CREATE UNIQUE INDEX ix_chart_shares_token ON chart_shares (token);
            """
        with engine.begin() as conn:
            for stmt in ddl.strip().split(";"):
                s = stmt.strip()
                if s:
                    conn.execute(text(s))
        logger.info("Created table chart_shares (%s)", dialect)

    # map_shares: public cryptic Palworld world-map share tokens
    if not _table_exists(engine, "map_shares"):
        if dialect == "postgresql":
            ddl = """
            CREATE TABLE map_shares (
                id SERIAL PRIMARY KEY,
                token VARCHAR(64) NOT NULL,
                server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL,
                CONSTRAINT uq_map_share_server UNIQUE (server_id)
            );
            CREATE UNIQUE INDEX ix_map_shares_token ON map_shares (token);
            """
        else:
            ddl = """
            CREATE TABLE map_shares (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                token VARCHAR(64) NOT NULL,
                server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
                created_at DATETIME NOT NULL,
                CONSTRAINT uq_map_share_server UNIQUE (server_id)
            );
            CREATE UNIQUE INDEX ix_map_shares_token ON map_shares (token);
            """
        with engine.begin() as conn:
            for stmt in ddl.strip().split(";"):
                s = stmt.strip()
                if s:
                    conn.execute(text(s))
        logger.info("Created table map_shares (%s)", dialect)

    # What was actually sent to the server. Needed to undo a ban on a platform
    # whose canonical identity drops the prefix (gdk_/xsx_ → xbox).
    if _table_exists(engine, "player_action_logs"):
        _add_column(engine, "player_action_logs", "net_id VARCHAR(64) DEFAULT ''")

    # Multi-user attribution. The users / server_grants / auth_tokens tables
    # themselves need nothing here - main.py runs create_all() before this, and
    # create_all is checkfirst, so brand-new tables appear on both dialects.
    # Only columns added to *existing* tables need patching.
    #
    # No REFERENCES clause on purpose: SQLite cannot add an enforced foreign key
    # via ALTER TABLE. Consequence - ON DELETE SET NULL will not fire on upgraded
    # SQLite databases, so services/users.py::delete_user nulls these explicitly.
    if _table_exists(engine, "command_history"):
        _add_column(engine, "command_history", "actor_user_id INTEGER")
    if _table_exists(engine, "player_action_logs"):
        _add_column(engine, "player_action_logs", "actor_user_id INTEGER")
        _add_column(engine, "player_action_logs", "actor_label VARCHAR(255) DEFAULT ''")
    if _table_exists(engine, "player_admin_notes"):
        _add_column(engine, "player_admin_notes", "author_user_id INTEGER")
        _ensure_player_note_author_unique(engine)

    _renormalize_unknown_identities(engine)

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

#!/usr/bin/env python3
"""Copy data from a local SQLite app.db into Postgres (SQLAlchemy models).

Usage (from repo root, with backend venv activated):

  set DATABASE_URL=postgresql+psycopg://rcon:rcon@127.0.0.1:5432/rcon_manager
  python scripts/migrate_sqlite_to_postgres.py --sqlite data/app.db

Does not drop existing Postgres tables. By default skips tables that already
have rows (use --force to wipe target tables first — destructive).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import (  # noqa: E402
    AdminAuth,
    CommandHistory,
    CustomButton,
    MapConfig,
    PlayerCountSample,
    PlayerServerStats,
    Server,
    Setting,
)

# Insert order respects FKs
TABLE_MODELS = [
    AdminAuth,
    Setting,
    Server,
    MapConfig,
    CustomButton,
    CommandHistory,
    PlayerCountSample,
    PlayerServerStats,
]


def _normalize_pg(url: str) -> str:
    url = url.strip()
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://") and "+psycopg" not in url.split("://", 1)[0]:
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate SQLite app.db → Postgres")
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=ROOT / "data" / "app.db",
        help="Source SQLite path",
    )
    parser.add_argument(
        "--postgres-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="Target DATABASE_URL (or set env DATABASE_URL)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="TRUNCATE all app tables on Postgres before copy (destructive)",
    )
    args = parser.parse_args()

    if not args.sqlite.is_file():
        print(f"SQLite file not found: {args.sqlite}", file=sys.stderr)
        return 1
    if not args.postgres_url:
        print("Provide --postgres-url or DATABASE_URL", file=sys.stderr)
        return 1

    src_url = f"sqlite:///{args.sqlite.resolve().as_posix()}"
    dst_url = _normalize_pg(args.postgres_url)

    src_engine = create_engine(src_url, connect_args={"check_same_thread": False})
    dst_engine = create_engine(dst_url, pool_pre_ping=True)

    Base.metadata.create_all(bind=dst_engine)

    SrcSession = sessionmaker(bind=src_engine)
    DstSession = sessionmaker(bind=dst_engine)

    src = SrcSession()
    dst = DstSession()
    try:
        if args.force:
            # Reverse order for FKs
            for model in reversed(TABLE_MODELS):
                table = model.__table__.name
                dst.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
            dst.commit()
            print("Truncated target tables")

        for model in TABLE_MODELS:
            table = model.__table__.name
            existing = dst.query(model).count()
            if existing and not args.force:
                print(f"skip {table}: already has {existing} row(s)")
                continue

            rows = src.query(model).all()
            if not rows:
                print(f"skip {table}: source empty")
                continue

            # Detach and re-add column dicts to avoid session identity issues
            payload = []
            for row in rows:
                data = {c.name: getattr(row, c.name) for c in model.__table__.columns}
                payload.append(model(**data))

            dst.add_all(payload)
            dst.commit()
            print(f"copied {table}: {len(payload)} row(s)")

        # Reset sequences for serial PKs after explicit ID inserts
        for model in TABLE_MODELS:
            table = model.__table__.name
            pk_cols = [c.name for c in model.__table__.primary_key.columns]
            if pk_cols != ["id"]:
                continue
            dst.execute(
                text(
                    f"""
                    SELECT setval(
                      pg_get_serial_sequence('{table}', 'id'),
                      COALESCE((SELECT MAX(id) FROM {table}), 1),
                      (SELECT MAX(id) FROM {table}) IS NOT NULL
                    )
                    """
                )
            )
        dst.commit()
        print("Done.")
        return 0
    except Exception as exc:  # noqa: BLE001
        dst.rollback()
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    raise SystemExit(main())

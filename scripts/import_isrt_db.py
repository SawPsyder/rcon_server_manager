#!/usr/bin/env python3
"""Import servers/maps from an ISRT SQLite DB into app.db (quick buttons are hardcoded)."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Allow running without installing package
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import MapConfig, Server  # noqa: E402
from app.security import encrypt_secret  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Import ISRT SQLite data into SSM")
    parser.add_argument("--isrt-db", required=True, type=Path, help="Path to isrt_data.db")
    parser.add_argument(
        "--out-db",
        type=Path,
        default=None,
        help="Target SQLite path (default: DATA_DIR/app.db)",
    )
    parser.add_argument("--skip-servers", action="store_true")
    parser.add_argument("--skip-maps", action="store_true")
    args = parser.parse_args()

    if args.out_db:
        get_settings.cache_clear()
        import os

        os.environ["DATABASE_URL"] = f"sqlite:///{args.out_db.resolve().as_posix()}"
        # recreate engine binding is already done at import — use direct connect via SessionLocal after create
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        eng = create_engine(f"sqlite:///{args.out_db.resolve().as_posix()}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=eng)
        Session = sessionmaker(bind=eng)
        db = Session()
    else:
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()

    src = sqlite3.connect(str(args.isrt_db))
    src.row_factory = sqlite3.Row
    cur = src.cursor()

    if not args.skip_servers:
        for row in cur.execute("SELECT alias, ipaddress, queryport, rconport, rconpw FROM server"):
            exists = (
                db.query(Server)
                .filter(Server.host == row["ipaddress"], Server.query_port == row["queryport"])
                .first()
            )
            if exists:
                print(f"skip server {row['alias']} (exists)")
                continue
            db.add(
                Server(
                    name=row["alias"] or row["ipaddress"],
                    host=row["ipaddress"],
                    query_port=int(row["queryport"]),
                    rcon_port=int(row["rconport"] or 0),
                    rcon_password_enc=encrypt_secret(row["rconpw"] or ""),
                )
            )
            print(f"imported server {row['alias']}")

    if not args.skip_maps:
        cols = [c[1] for c in cur.execute("PRAGMA table_info(map_config)")]
        for row in cur.execute("SELECT * FROM map_config"):
            data = dict(zip(cols, row))
            map_name = data.get("map_name")
            if not map_name:
                continue
            if db.query(MapConfig).filter(MapConfig.map_name == map_name).first():
                print(f"skip map {map_name}")
                continue
            db.add(
                MapConfig(
                    alias=data.get("map_alias") or map_name,
                    map_name=map_name,
                    mod_id=int(data.get("modid") or 0),
                    day=bool(data.get("day")),
                    night=bool(data.get("night")),
                    map_pic=data.get("map_pic") or "",
                    checkpointhardcore=data.get("checkpointhardcore") or "",
                    checkpointhardcore_ins=data.get("checkpointhardcore_ins") or "",
                    checkpoint=data.get("checkpoint") or "",
                    checkpoint_ins=data.get("checkpoint_ins") or "",
                    domination=data.get("domination") or "",
                    firefight_east=data.get("firefight_east") or "",
                    firefight_west=data.get("firefight_west") or "",
                    frontline=data.get("frontline") or "",
                    outpost=data.get("outpost") or "",
                    push=data.get("push") or "",
                    push_ins=data.get("push_ins") or "",
                    skirmish=data.get("skirmish") or "",
                    teamdeathmatch=data.get("teamdeathmatch") or "",
                    survival=data.get("survival") or "",
                    ambush=data.get("ambush") or "",
                    self_added=bool(data.get("self_added")),
                    globalday=bool(data.get("globalday")),
                    dusk=bool(data.get("dusk")),
                    dawn=bool(data.get("dawn")),
                    dark=bool(data.get("dark")),
                    fog=bool(data.get("fog")),
                    rain=bool(data.get("rain")),
                    winter=bool(data.get("winter")),
                )
            )
            print(f"imported map {map_name}")

    db.commit()
    db.close()
    src.close()
    print("done")


if __name__ == "__main__":
    main()

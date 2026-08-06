"""Background worker: periodically query all servers and store player counts."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from app.config import get_settings
from app.database import SessionLocal
from app.models import PlayerCountSample, Server, Setting
from app.security import decrypt_secret
from app.server_types import DEFAULT_SERVER_TYPE
from app.services.players import sample_player_count
from app.services.presence import update_presence
from app.services.query import query_server_status
from app.services.status_cache import update_server_status_cache

logger = logging.getLogger(__name__)

DEFAULT_STATS_INTERVAL = 60  # seconds


class StatsCollector:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="stats-collector", daemon=True)
        self._thread.start()
        logger.info("Player stats collector started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("Player stats collector stopped")

    def _interval_seconds(self) -> float:
        db = SessionLocal()
        try:
            row = db.query(Setting).filter(Setting.key == "stats_interval_seconds").first()
            if row:
                try:
                    return max(15.0, float(row.value))
                except ValueError:
                    pass
        finally:
            db.close()
        return float(DEFAULT_STATS_INTERVAL)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample_all()
            except Exception:
                logger.exception("Stats collector cycle failed")
            interval = self._interval_seconds()
            self._stop.wait(interval)

    def sample_all(self) -> int:
        """Query every configured server once. Returns number of samples written."""
        settings = get_settings()
        db = SessionLocal()
        written = 0
        try:
            servers = db.query(Server).all()
            now = datetime.now(timezone.utc)
            timeout_row = db.query(Setting).filter(Setting.key == "query_timeout").first()
            try:
                timeout = float(timeout_row.value) if timeout_row else settings.query_timeout
            except (TypeError, ValueError):
                timeout = settings.query_timeout

            interval = self._interval_seconds()
            max_tick = max(interval * 2.5, 180.0)

            for server in servers:
                password = decrypt_secret(server.rcon_password_enc)
                st = getattr(server, "server_type", None) or DEFAULT_SERVER_TYPE
                snap = sample_player_count(
                    host=server.host,
                    query_port=server.query_port,
                    rcon_port=server.rcon_port,
                    rcon_password=password,
                    timeout=timeout,
                    server_type=st,
                )
                if snap.get("rcon_error"):
                    logger.warning(
                        "Server %s (%s): RCON player count failed (%s); using %s count=%s",
                        server.id,
                        server.name,
                        snap.get("rcon_error"),
                        snap.get("source"),
                        snap.get("players"),
                    )
                elif snap.get("source") == "a2s" and password == "" and server.rcon_password_enc:
                    logger.warning(
                        "Server %s: RCON password could not be decrypted — re-save it in Servers",
                        server.id,
                    )
                elif snap.get("source") == "a2s" and not password:
                    logger.info(
                        "Server %s (%s): no RCON password; A2S count may under-report",
                        server.id,
                        st,
                    )

                # Refresh identity cache (hostname / map / gamemode) via A2S
                try:
                    raw = query_server_status(server.host, server.query_port, timeout=timeout)
                    if raw.get("online"):
                        update_server_status_cache(
                            server,
                            hostname=raw.get("hostname"),
                            map_name=raw.get("map"),
                            lighting=raw.get("lighting"),
                            gamemode=raw.get("gamemode"),
                            coop_or_versus=raw.get("coop_or_versus"),
                            players=int(snap.get("players") or 0),
                            max_players=int(
                                raw.get("max_players")
                                if raw.get("max_players") is not None
                                else (snap.get("max_players") or 0)
                            ),
                            online=True,
                            now=now,
                        )
                    else:
                        update_server_status_cache(
                            server,
                            online=False,
                            now=now,
                        )
                except Exception:
                    logger.debug(
                        "Status cache refresh failed for server %s", server.id, exc_info=True
                    )

                db.add(
                    PlayerCountSample(
                        server_id=server.id,
                        recorded_at=now,
                        players=int(snap.get("players") or 0),
                        max_players=int(snap.get("max_players") or 0),
                        online=bool(snap.get("online")),
                    )
                )

                # Presence / session tracking (humans with steam ids from RCON)
                if snap.get("source") == "rcon" or snap.get("player_list"):
                    try:
                        update_presence(
                            db,
                            server_id=server.id,
                            online_players=snap.get("player_list") or [],
                            now=now,
                            max_tick_seconds=max_tick,
                        )
                    except Exception:
                        logger.exception(
                            "Presence update failed for server %s", server.id
                        )

                written += 1
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return written


collector = StatsCollector()

"""Background worker: poll every Pterodactyl-linked server's container stats.

Runs unconditionally, whether or not anyone has a server's detail page open.
That buys two things:

* **One source of truth.** The resource card and the history chart both read
  what this poller fetched, so they cannot disagree with each other.
* **Continuous history.** A chart that only covered the minutes somebody
  happened to be watching would be missing exactly the outage you came to
  investigate.

The cadence matches the panel's own 20-second cache on ``/resources`` - faster
returns identical bytes, slower throws readings away. Because this is the only
thing refreshing the client cache, request handlers become pure cache reads and
make no upstream call at all in the normal case.

Modelled on :class:`app.services.stats_collector.StatsCollector`, including the
caveat it inherits: with more than one uvicorn worker each process runs its own
poller, so the upstream request rate multiplies by the worker count.
"""

from __future__ import annotations

import logging
import math
import threading
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models import PterodactylSample, Server
from app.services import pterodactyl_api
from app.services.pterodactyl_settings import load_pterodactyl_config
from app.services.server_options import option_str

logger = logging.getLogger(__name__)

POLL_TIMEOUT = 10.0

# Resource calls per minute we are willing to spend across all linked servers.
# The panel allows 256/minute per key; the rest of the headroom covers the
# 5-minutely limits refresh, the admin inventory list, and power actions.
RESOURCE_BUDGET_PER_MINUTE = 180.0


class PterodactylPoller:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_interval = pterodactyl_api.POLL_INTERVAL_SECONDS

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="pterodactyl-poller", daemon=True
        )
        self._thread.start()
        logger.info("Pterodactyl resource poller started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("Pterodactyl resource poller stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_all()
            except Exception:
                logger.exception("Pterodactyl poll cycle failed")
            self._stop.wait(self._last_interval)

    @staticmethod
    def interval_for(linked_count: int) -> float:
        """Poll interval for this many linked servers.

        Normally the panel's 20s cache period. Stretched only when there are
        enough linked servers that 20s would eat the rate limit - better a
        coarser chart than a key the whole app is locked out of.
        """
        base = pterodactyl_api.POLL_INTERVAL_SECONDS
        if linked_count <= 0:
            return base
        needed = linked_count * 60.0 / RESOURCE_BUDGET_PER_MINUTE
        return max(base, math.ceil(needed))

    def poll_all(self) -> int:
        """Refresh every linked server once. Returns the number of samples written."""
        db = SessionLocal()
        written = 0
        try:
            config = load_pterodactyl_config(db)
            if not config.enabled:
                self._last_interval = pterodactyl_api.POLL_INTERVAL_SECONDS
                return 0

            linked: list[tuple[int, str]] = []
            for server in db.query(Server).all():
                uuid = option_str(server, "pterodactyl_uuid")
                if uuid:
                    linked.append((server.id, uuid))

            interval = self.interval_for(len(linked))
            if interval != self._last_interval:
                if interval > pterodactyl_api.POLL_INTERVAL_SECONDS:
                    logger.info(
                        "Pterodactyl poll interval stretched to %.0fs to stay inside "
                        "the panel's rate limit (%s linked servers)",
                        interval,
                        len(linked),
                    )
                self._last_interval = interval

            if not linked:
                return 0

            try:
                client = pterodactyl_api.client_for(config)
            except pterodactyl_api.PterodactylApiError:
                logger.debug("Pterodactyl client unavailable this cycle", exc_info=True)
                return 0

            now = datetime.now(timezone.utc)
            for server_id, uuid in linked:
                if self._stop.is_set():
                    break
                try:
                    # Forced fresh: this poller is the refresh mechanism, so
                    # reading its own cache back would freeze the series.
                    resources = client.get_resources(
                        uuid, use_cache=False, timeout=POLL_TIMEOUT
                    )
                    # Cached for 5 minutes, so this is ~0.2 calls/min and keeps
                    # the detail page off the upstream path for limits too.
                    client.get_server(uuid)
                except pterodactyl_api.PterodactylApiError as exc:
                    # No row: a gap in the chart is the honest rendering of
                    # "we could not read this", and beats a fabricated zero.
                    logger.debug(
                        "Pterodactyl poll failed for server %s: %s", server_id, exc
                    )
                    continue
                except Exception:
                    logger.exception("Unexpected Pterodactyl poll error for %s", server_id)
                    continue

                db.add(
                    PterodactylSample(
                        server_id=server_id,
                        recorded_at=now,
                        state=resources.current_state[:16],
                        cpu_absolute=float(resources.cpu_absolute),
                        memory_bytes=int(resources.memory_bytes),
                        disk_bytes=int(resources.disk_bytes),
                        network_rx_bytes=int(resources.network_rx_bytes),
                        network_tx_bytes=int(resources.network_tx_bytes),
                        uptime_ms=int(resources.uptime_ms),
                    )
                )
                written += 1
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return written


poller = PterodactylPoller()

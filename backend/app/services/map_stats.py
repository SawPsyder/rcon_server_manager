"""Aggregate map popularity from tagged player-count samples.

Unbiased by design:
  - Rank by human demand (player-minutes / avg concurrent), not clock time.
  - Empty samples (players == 0) do not contribute to demand metrics.
  - Admin travel / vote counts are never used as numerators.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from app.models import PlayerCountSample

# Cap inter-sample gap so a multi-hour collector outage does not mint
# enormous player-minute blobs for the last map seen before the gap.
MAX_SAMPLE_DELTA_SECONDS = 180.0

# Default floor so a single full lobby does not top a long-range board.
DEFAULT_MIN_ACTIVE_MINUTES = 30.0


@dataclass(frozen=True)
class MapStatRow:
    map_name: str
    gamemode: str
    alias: str | None
    player_minutes: float
    active_minutes: float
    avg_players: float
    peak_players: int
    active_samples: int
    # True when active_minutes meets the min-exposure floor used for default sort.
    qualified: bool


@dataclass(frozen=True)
class MapStatsResult:
    server_id: int
    range: str
    from_time: datetime
    to_time: datetime
    min_active_minutes: float
    combine_gamemodes: bool
    data_since: datetime | None
    rows: list[MapStatRow]


def _norm_map(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _norm_mode(value: str | None, *, combine: bool) -> str:
    if combine:
        return ""
    return (value or "").strip()


def _sample_key(sample: PlayerCountSample, *, combine_gamemodes: bool) -> tuple[str, str] | None:
    map_name = _norm_map(getattr(sample, "map_name", None))
    if not map_name:
        return None
    return map_name, _norm_mode(getattr(sample, "gamemode", None), combine=combine_gamemodes)


def aggregate_map_stats(
    samples: Sequence[PlayerCountSample],
    *,
    server_id: int,
    range_key: str,
    from_time: datetime,
    to_time: datetime,
    combine_gamemodes: bool = False,
    min_active_minutes: float = DEFAULT_MIN_ACTIVE_MINUTES,
    alias_by_map: dict[str, str] | None = None,
    max_delta_seconds: float = MAX_SAMPLE_DELTA_SECONDS,
) -> MapStatsResult:
    """
    Build per-map demand metrics from ordered samples for one server.

    Each sample contributes ``players * Δt`` where Δt is the gap to the *next*
    sample, capped at ``max_delta_seconds``. Only samples with players > 0 and
    a non-empty map_name contribute (empty-server cycle time is ignored).
    """
    aliases = alias_by_map or {}

    # Accumulators keyed by (map_name, gamemode)
    player_seconds: dict[tuple[str, str], float] = {}
    active_seconds: dict[tuple[str, str], float] = {}
    players_sum: dict[tuple[str, str], float] = {}
    active_count: dict[tuple[str, str], int] = {}
    peak: dict[tuple[str, str], int] = {}
    data_since: datetime | None = None

    ordered = sorted(samples, key=lambda s: s.recorded_at)
    n = len(ordered)
    for i, sample in enumerate(ordered):
        map_key = _sample_key(sample, combine_gamemodes=combine_gamemodes)
        if map_key is None:
            continue
        if data_since is None or sample.recorded_at < data_since:
            data_since = sample.recorded_at

        players = int(sample.players or 0)
        if not sample.online or players <= 0:
            continue

        if i + 1 < n:
            delta = (ordered[i + 1].recorded_at - sample.recorded_at).total_seconds()
        else:
            # Last sample: assume one capped interval so it still counts.
            delta = max_delta_seconds
        if delta <= 0:
            continue
        dt = min(float(delta), max_delta_seconds)

        player_seconds[map_key] = player_seconds.get(map_key, 0.0) + players * dt
        active_seconds[map_key] = active_seconds.get(map_key, 0.0) + dt
        players_sum[map_key] = players_sum.get(map_key, 0.0) + players
        active_count[map_key] = active_count.get(map_key, 0) + 1
        peak[map_key] = max(peak.get(map_key, 0), players)

    min_active = max(0.0, float(min_active_minutes))
    rows: list[MapStatRow] = []
    for key, psec in player_seconds.items():
        map_name, gamemode = key
        asec = active_seconds.get(key, 0.0)
        count = active_count.get(key, 0)
        active_min = asec / 60.0
        avg = (players_sum.get(key, 0.0) / count) if count else 0.0
        rows.append(
            MapStatRow(
                map_name=map_name,
                gamemode=gamemode,
                alias=aliases.get(map_name),
                player_minutes=round(psec / 60.0, 2),
                active_minutes=round(active_min, 2),
                avg_players=round(avg, 2),
                peak_players=int(peak.get(key, 0)),
                active_samples=count,
                qualified=active_min >= min_active,
            )
        )

    # Qualified rows first by avg concurrent, then the rest; ties by player-min.
    rows.sort(
        key=lambda r: (
            0 if r.qualified else 1,
            -r.avg_players,
            -r.player_minutes,
            r.map_name.lower(),
            r.gamemode.lower(),
        )
    )

    return MapStatsResult(
        server_id=server_id,
        range=range_key,
        from_time=from_time,
        to_time=to_time,
        min_active_minutes=min_active,
        combine_gamemodes=combine_gamemodes,
        data_since=data_since,
        rows=rows,
    )


def first_tagged_at(samples: Iterable[PlayerCountSample]) -> datetime | None:
    earliest: datetime | None = None
    for s in samples:
        if _norm_map(getattr(s, "map_name", None)):
            if earliest is None or s.recorded_at < earliest:
                earliest = s.recorded_at
    return earliest

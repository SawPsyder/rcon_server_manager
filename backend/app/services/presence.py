"""Track player sessions from continuous sampling.

Definitions:
- session_seconds: time in the current continuous presence window
- total_seconds: sum of all observed presence across visits
- visit_count: number of times a player was picked up after not being
  present on the previous sample (re-join / new session)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models import PlayerServerStats


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def format_duration(seconds: float | int) -> str:
    secs = max(0, int(seconds))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def update_presence(
    db: Session,
    server_id: int,
    online_players: Iterable[dict[str, Any]],
    now: datetime | None = None,
    max_tick_seconds: float = 180.0,
) -> dict[str, PlayerServerStats]:
    """
    Apply one sample of online humans to presence tables.

    online_players items need at least: steamid, name; optional ip, score.
    Returns map steam_id -> stats row for currently online players.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    current: dict[str, dict[str, Any]] = {}
    for p in online_players:
        steam = str(p.get("steamid") or "").strip()
        if not steam or not steam.isdigit() or len(steam) != 17:
            continue
        current[steam] = p

    # All known stats for this server (need open sessions for leave detection)
    rows = (
        db.query(PlayerServerStats)
        .filter(PlayerServerStats.server_id == server_id)
        .all()
    )
    by_steam = {r.steam_id: r for r in rows}
    online_stats: dict[str, PlayerServerStats] = {}

    for steam, p in current.items():
        name = str(p.get("name") or "").strip() or steam
        ip = str(p.get("ip") or "").strip()
        try:
            score = int(p.get("score") or 0)
        except (TypeError, ValueError):
            score = 0

        stats = by_steam.get(steam)
        if stats is None:
            stats = PlayerServerStats(
                server_id=server_id,
                steam_id=steam,
                last_name=name,
                first_seen_at=now,
                last_seen_at=now,
                total_seconds=0,
                visit_count=1,
                session_started_at=now,
                last_ip=ip,
                last_score=score,
            )
            db.add(stats)
            by_steam[steam] = stats
            online_stats[steam] = stats
            try:
                from app.services.identity import _upsert_cache

                if name and name != steam:
                    _upsert_cache(
                        db,
                        platform="steam",
                        external_id=steam,
                        display_name=name,
                        profile_url=f"https://steamcommunity.com/profiles/{steam}",
                        avatar_url="",
                        source="presence",
                    )
            except Exception:
                pass
            continue

        last_seen = _aware(stats.last_seen_at) or now
        session_start = _aware(stats.session_started_at)

        if session_start is None:
            # New visit: seen after absence
            stats.visit_count = int(stats.visit_count or 0) + 1
            stats.session_started_at = now
        else:
            # Still (or again) in a session — accumulate time since last tick
            delta = (now - last_seen).total_seconds()
            if delta > 0:
                delta = min(delta, max_tick_seconds)
                stats.total_seconds = int(stats.total_seconds or 0) + int(delta)

        stats.last_seen_at = now
        stats.last_name = name
        if ip:
            stats.last_ip = ip
        stats.last_score = score
        online_stats[steam] = stats
        # Feed identity cache so ban list can show names without Steam API
        try:
            from app.services.identity import _upsert_cache

            if name and name != steam:
                _upsert_cache(
                    db,
                    platform="steam",
                    external_id=steam,
                    display_name=name,
                    profile_url=f"https://steamcommunity.com/profiles/{steam}",
                    avatar_url="",
                    source="presence",
                )
        except Exception:
            pass

    # Close sessions for players no longer present
    for steam, stats in by_steam.items():
        if steam in current:
            continue
        if stats.session_started_at is not None:
            # Credit final slice from last_seen to now (capped), then close
            last_seen = _aware(stats.last_seen_at) or now
            delta = (now - last_seen).total_seconds()
            if 0 < delta <= max_tick_seconds:
                stats.total_seconds = int(stats.total_seconds or 0) + int(delta)
            stats.session_started_at = None

    return online_stats


def build_time_ranks(db: Session, server_id: int) -> dict[str, int]:
    """
    Rank all known players on this server by stored total_seconds (desc).
    No online boost — same total as displayed. Competition ranking: 1,2,2,4.
    """
    rows = (
        db.query(PlayerServerStats)
        .filter(PlayerServerStats.server_id == server_id)
        .all()
    )
    scored = [(r.steam_id, int(r.total_seconds or 0)) for r in rows]
    scored.sort(key=lambda x: (-x[1], x[0]))

    ranks: dict[str, int] = {}
    prev_total: int | None = None
    prev_rank = 0
    for index, (steam, total) in enumerate(scored, start=1):
        if prev_total is None or total != prev_total:
            rank = index
            prev_rank = rank
            prev_total = total
        else:
            rank = prev_rank
        ranks[steam] = rank
    return ranks


def format_last_seen(dt: datetime | None, now: datetime) -> str:
    """Human-friendly last-seen label."""
    if dt is None:
        return "—"
    dt = _aware(dt)
    if dt is None:
        return "—"
    # Online right now if seen within ~2 sample intervals
    delta = (now - dt).total_seconds()
    if delta < 0:
        delta = 0
    if delta < 90:
        return "Online"
    if delta < 3600:
        mins = int(delta // 60)
        return f"{mins}m ago" if mins > 0 else "just now"
    if delta < 86400:
        hours = int(delta // 3600)
        return f"{hours}h ago"
    days = int(delta // 86400)
    if days < 14:
        return f"{days}d ago"
    return dt.strftime("%Y-%m-%d %H:%M")


def enrich_player_list(
    db: Session,
    server_id: int,
    player_list: list[dict[str, Any]],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Attach session/total/visit/rank/last_seen stats to a raw player list."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    ranks = build_time_ranks(db, server_id)
    ranked_players = len(ranks)

    steam_ids = [
        str(p.get("steamid") or "").strip()
        for p in player_list
        if str(p.get("steamid") or "").strip()
    ]
    if not steam_ids:
        return [
            {
                **p,
                "steamid": p.get("steamid") or "",
                "ip": p.get("ip") or "",
                "session_seconds": 0,
                "session_pretty": "0s",
                "total_seconds": 0,
                "total_pretty": "0s",
                "visit_count": 0,
                "rank": None,
                "ranked_players": ranked_players,
                "last_seen_at": None,
                "last_seen_pretty": "—",
            }
            for p in player_list
        ]

    rows = (
        db.query(PlayerServerStats)
        .filter(
            PlayerServerStats.server_id == server_id,
            PlayerServerStats.steam_id.in_(steam_ids),
        )
        .all()
    )
    by_steam = {r.steam_id: r for r in rows}

    out: list[dict[str, Any]] = []
    for p in player_list:
        steam = str(p.get("steamid") or "").strip()
        stats = by_steam.get(steam)
        session_seconds = 0
        total_seconds = 0
        visit_count = 0
        rank: int | None = ranks.get(steam) if steam else None
        last_seen_at: datetime | None = None
        last_seen_pretty = "—"
        if stats:
            total_seconds = int(stats.total_seconds or 0)
            visit_count = int(stats.visit_count or 0)
            start = _aware(stats.session_started_at)
            if start is not None:
                session_seconds = max(0, int((now - start).total_seconds()))
            last_seen_at = _aware(stats.last_seen_at)
            if start is not None:
                last_seen_pretty = "Online"
            else:
                last_seen_pretty = format_last_seen(last_seen_at, now)
        out.append(
            {
                **p,
                "steamid": steam,
                "ip": p.get("ip") or (stats.last_ip if stats else "") or "",
                "session_seconds": session_seconds,
                "session_pretty": format_duration(session_seconds),
                "total_seconds": total_seconds,
                "total_pretty": format_duration(total_seconds),
                "visit_count": visit_count,
                "rank": rank,
                "ranked_players": ranked_players,
                "last_seen_at": last_seen_at.isoformat() if last_seen_at else None,
                "last_seen_pretty": last_seen_pretty,
            }
        )
    return out


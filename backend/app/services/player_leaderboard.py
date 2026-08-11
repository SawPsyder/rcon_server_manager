"""Cross-server player leaderboard from ``player_server_stats``.

Aggregates presence rows by normalized platform identity so a bare SteamID64
and a ``steam_7656…`` Palworld id count as the same person. Operator-linked
accounts (Steam + Xbox, etc.) also merge into one row. Ranking is by playtime
(``total_seconds``): overall when unfiltered, per-server when a server filter
is applied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models import IdentityCache, PlayerServerStats, Server
from app.services.identity import parse_net_id
from app.services.identity_links import list_group_members, load_link_map, person_key
from app.services.presence import format_duration, format_last_seen

SortKey = Literal["total_seconds", "last_seen_at", "name", "visit_count"]


@dataclass
class _ServerSlice:
    server_id: int
    server_name: str
    server_type: str
    net_id: str
    last_name: str
    total_seconds: int
    visit_count: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    online: bool
    rank: int = 0
    ranked_players: int = 0


@dataclass
class _LinkedIdent:
    platform: str
    external_id: str
    net_id: str = ""
    last_name: str = ""


@dataclass
class _PlayerAgg:
    # Primary identity for dossier (most recently seen).
    platform: str
    external_id: str
    # Raw net id preferred for dossier links (from most recently seen row).
    net_id: str = ""
    display_name: str = ""
    total_seconds: int = 0
    visit_count: int = 0
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    online: bool = False
    online_server_ids: list[int] = field(default_factory=list)
    servers: list[_ServerSlice] = field(default_factory=list)
    # Value used for ranking when a server filter is active (that server only).
    filter_seconds: int = 0
    filter_visits: int = 0
    linked: list[_LinkedIdent] = field(default_factory=list)
    # server_id -> slice index in servers for merge
    _server_index: dict[int, int] = field(default_factory=dict)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _identity_key(net_id: str) -> tuple[str, str] | None:
    parsed = parse_net_id(net_id)
    if parsed is None:
        return None
    return parsed


def _competition_ranks(scored: list[tuple[str, int]]) -> dict[str, int]:
    """Competition ranking 1,2,2,4 on (key, score desc, key asc)."""
    ordered = sorted(scored, key=lambda x: (-x[1], x[0]))
    ranks: dict[str, int] = {}
    prev_score: int | None = None
    prev_rank = 0
    for index, (key, score) in enumerate(ordered, start=1):
        if prev_score is None or score != prev_score:
            prev_rank = index
            prev_score = score
        ranks[key] = prev_rank
    return ranks


def _cache_names(
    db: Session, keys: list[tuple[str, str]]
) -> dict[tuple[str, str], str]:
    if not keys:
        return {}
    platforms = {p for p, _ in keys}
    external_ids = {e for _, e in keys}
    rows = (
        db.query(IdentityCache)
        .filter(
            IdentityCache.platform.in_(platforms),
            IdentityCache.external_id.in_(external_ids),
        )
        .all()
    )
    out: dict[tuple[str, str], str] = {}
    wanted = set(keys)
    for row in rows:
        key = (row.platform, row.external_id)
        if key in wanted and (row.display_name or "").strip():
            out[key] = row.display_name.strip()
    return out


def build_player_leaderboard(
    db: Session,
    *,
    allowed_server_ids: set[int] | None,
    server_id: int | None = None,
    q: str = "",
    sort: SortKey = "total_seconds",
    order: Literal["asc", "desc"] = "desc",
    page: int = 1,
    page_size: int = 50,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a paginated leaderboard dict matching PlayerLeaderboardOut fields."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    q_norm = (q or "").strip().lower()

    # Empty grant set → nothing visible (non-admin with no servers).
    if allowed_server_ids is not None and not allowed_server_ids:
        return _empty_page(page, page_size, server_id)

    if server_id is not None:
        if allowed_server_ids is not None and server_id not in allowed_server_ids:
            return _empty_page(page, page_size, server_id)

    servers = db.query(Server).all()
    server_by_id = {s.id: s for s in servers}
    visible_server_ids: set[int] | None
    if allowed_server_ids is None:
        visible_server_ids = None
    else:
        visible_server_ids = set(allowed_server_ids)

    query = db.query(PlayerServerStats)
    if visible_server_ids is not None:
        query = query.filter(PlayerServerStats.server_id.in_(visible_server_ids))
    # When filtering by server we still load all visible rows for that identity
    # so the per-server breakdown can show other servers they played on. Rank
    # and primary totals use the filter server only (see below).
    rows = query.all()

    link_map = load_link_map(db)
    aggs: dict[str, _PlayerAgg] = {}

    for row in rows:
        ident = _identity_key(row.steam_id)
        if ident is None:
            continue
        platform, external_id = ident
        pkey = person_key(platform, external_id, link_map)
        agg = aggs.get(pkey)
        if agg is None:
            agg = _PlayerAgg(platform=platform, external_id=external_id)
            aggs[pkey] = agg

        last_seen = _aware(row.last_seen_at)
        first_seen = _aware(row.first_seen_at)
        online = row.session_started_at is not None
        total = int(row.total_seconds or 0)
        visits = int(row.visit_count or 0)
        name = (row.last_name or "").strip()
        srv = server_by_id.get(row.server_id)
        if srv is None:
            continue

        # Merge multiple platform accounts on the same server into one slice.
        existing_idx = agg._server_index.get(row.server_id)
        if existing_idx is not None:
            slice_ = agg.servers[existing_idx]
            slice_.total_seconds += total
            slice_.visit_count += visits
            if first_seen is not None and (
                slice_.first_seen_at is None or first_seen < slice_.first_seen_at
            ):
                slice_.first_seen_at = first_seen
            if last_seen is not None and (
                slice_.last_seen_at is None or last_seen > slice_.last_seen_at
            ):
                slice_.last_seen_at = last_seen
                slice_.net_id = row.steam_id
                if name:
                    slice_.last_name = name
            if online:
                slice_.online = True
        else:
            slice_ = _ServerSlice(
                server_id=row.server_id,
                server_name=srv.name or f"Server {row.server_id}",
                server_type=srv.server_type or "",
                net_id=row.steam_id,
                last_name=name or row.steam_id,
                total_seconds=total,
                visit_count=visits,
                first_seen_at=first_seen,
                last_seen_at=last_seen,
                online=online,
            )
            agg._server_index[row.server_id] = len(agg.servers)
            agg.servers.append(slice_)

        if not any(
            li.platform == platform and li.external_id == external_id for li in agg.linked
        ):
            agg.linked.append(
                _LinkedIdent(
                    platform=platform,
                    external_id=external_id,
                    net_id=row.steam_id,
                    last_name=name,
                )
            )
        else:
            for li in agg.linked:
                if li.platform == platform and li.external_id == external_id:
                    if name:
                        li.last_name = name
                    li.net_id = row.steam_id
                    break

        # Prefer net id / name from the most recently seen row.
        if last_seen is not None and (
            agg.last_seen_at is None or last_seen >= agg.last_seen_at
        ):
            agg.net_id = row.steam_id
            agg.platform = platform
            agg.external_id = external_id
            if name:
                agg.display_name = name

        if not agg.display_name and name:
            agg.display_name = name
        if not agg.net_id:
            agg.net_id = row.steam_id

        agg.total_seconds += total
        agg.visit_count += visits
        if first_seen is not None and (
            agg.first_seen_at is None or first_seen < agg.first_seen_at
        ):
            agg.first_seen_at = first_seen
        if last_seen is not None and (
            agg.last_seen_at is None or last_seen > agg.last_seen_at
        ):
            agg.last_seen_at = last_seen
        if online:
            agg.online = True
            if row.server_id not in agg.online_server_ids:
                agg.online_server_ids.append(row.server_id)

        if server_id is not None and row.server_id == server_id:
            agg.filter_seconds += total
            agg.filter_visits += visits

    # Include linked accounts that have not been seen on any server yet.
    for pkey, agg in aggs.items():
        if not pkey.startswith("group:"):
            continue
        try:
            gid = int(pkey.split(":", 1)[1])
        except ValueError:
            continue
        for platform, external_id in list_group_members(db, gid):
            if any(
                li.platform == platform and li.external_id == external_id
                for li in agg.linked
            ):
                continue
            agg.linked.append(
                _LinkedIdent(platform=platform, external_id=external_id)
            )

    # Person-level ranks on each server (after linking / merging slices).
    per_server_scores: dict[int, list[tuple[str, int]]] = {}
    for pkey, agg in aggs.items():
        for slice_ in agg.servers:
            per_server_scores.setdefault(slice_.server_id, []).append(
                (pkey, slice_.total_seconds)
            )
    per_server_ranks = {
        sid: _competition_ranks(scored) for sid, scored in per_server_scores.items()
    }
    per_server_counts = {sid: len(scored) for sid, scored in per_server_scores.items()}
    for pkey, agg in aggs.items():
        for slice_ in agg.servers:
            slice_.rank = per_server_ranks.get(slice_.server_id, {}).get(pkey, 0)
            slice_.ranked_players = per_server_counts.get(slice_.server_id, 0)

    missing_name = [
        (a.platform, a.external_id) for a in aggs.values() if not a.display_name
    ]
    cached = _cache_names(db, missing_name)
    for agg in aggs.values():
        if not agg.display_name:
            name = cached.get((agg.platform, agg.external_id))
            if name:
                agg.display_name = name

    players = list(aggs.values())

    # Server filter: only identities that have time on that server.
    if server_id is not None:
        players = [p for p in players if any(s.server_id == server_id for s in p.servers)]

    # Search name / net id / platform external id (including linked accounts).
    if q_norm:
        filtered: list[_PlayerAgg] = []
        for p in players:
            hay = " ".join(
                [
                    p.display_name,
                    p.net_id,
                    p.external_id,
                    p.platform,
                    *[li.external_id for li in p.linked],
                    *[li.net_id for li in p.linked],
                    *[li.last_name for li in p.linked],
                    *[li.platform for li in p.linked],
                    *[s.last_name for s in p.servers],
                    *[s.net_id for s in p.servers],
                    *[s.server_name for s in p.servers],
                ]
            ).lower()
            if q_norm in hay:
                filtered.append(p)
        players = filtered

    # Ranking scores: overall sum, or filtered server only.
    def _rk(p: _PlayerAgg) -> str:
        return f"{p.platform}:{p.external_id}:{p.net_id}"

    if server_id is not None:
        rank_scores = [(_rk(p), p.filter_seconds) for p in players]
    else:
        rank_scores = [(_rk(p), p.total_seconds) for p in players]
    ranks = _competition_ranks(rank_scores)
    ranked_players = len(players)

    def sort_value(p: _PlayerAgg) -> Any:
        if sort == "name":
            return (p.display_name or p.net_id or "").lower()
        if sort == "last_seen_at":
            # None last → sort to the end for both orders via sentinel.
            ts = p.last_seen_at
            if ts is None:
                return datetime.min.replace(tzinfo=timezone.utc)
            return ts
        if sort == "visit_count":
            return p.filter_visits if server_id is not None else p.visit_count
        # total_seconds (default)
        return p.filter_seconds if server_id is not None else p.total_seconds

    reverse = order != "asc"
    # Stable secondary key for ties.
    players.sort(
        key=lambda p: (sort_value(p), _rk(p)),
        reverse=reverse,
    )
    # datetime.min for missing last_seen sorts to the start when reverse=True
    # (desc = newest first) — we want missing at the end. Fix after sort.
    if sort == "last_seen_at":
        with_seen = [p for p in players if p.last_seen_at is not None]
        without = [p for p in players if p.last_seen_at is None]
        with_seen.sort(
            key=lambda p: (p.last_seen_at, _rk(p)),
            reverse=reverse,
        )
        players = with_seen + without

    total = len(players)
    start = (page - 1) * page_size
    page_rows = players[start : start + page_size]

    out_players: list[dict[str, Any]] = []
    for p in page_rows:
        # Primary totals for the table: filtered server or overall.
        if server_id is not None:
            primary_seconds = p.filter_seconds
            primary_visits = p.filter_visits
            # Prefer name from the filtered server row when present.
            for s in p.servers:
                if s.server_id == server_id and s.last_name:
                    p.display_name = p.display_name or s.last_name
                    break
        else:
            primary_seconds = p.total_seconds
            primary_visits = p.visit_count

        # Sort per-server slices by time desc for the breakdown.
        slices = sorted(p.servers, key=lambda s: (-s.total_seconds, s.server_name.lower()))
        online_names = [
            s.server_name for s in slices if s.online and s.server_id in p.online_server_ids
        ]
        linked_out = [
            {
                "platform": li.platform,
                "external_id": li.external_id,
                "net_id": li.net_id or "",
                "last_name": li.last_name or "",
            }
            for li in sorted(p.linked, key=lambda x: (x.platform, x.external_id))
        ]
        # Ensure at least the primary identity is listed.
        if not linked_out:
            linked_out = [
                {
                    "platform": p.platform,
                    "external_id": p.external_id,
                    "net_id": p.net_id,
                    "last_name": p.display_name,
                }
            ]

        out_players.append(
            {
                "platform": p.platform,
                "external_id": p.external_id,
                "net_id": p.net_id,
                "display_name": p.display_name or p.net_id or p.external_id,
                "total_seconds": primary_seconds,
                "total_pretty": format_duration(primary_seconds),
                "overall_seconds": p.total_seconds,
                "overall_pretty": format_duration(p.total_seconds),
                "visit_count": primary_visits,
                "first_seen_at": p.first_seen_at.isoformat() if p.first_seen_at else None,
                "last_seen_at": p.last_seen_at.isoformat() if p.last_seen_at else None,
                "last_seen_pretty": (
                    "Online" if p.online else format_last_seen(p.last_seen_at, now)
                ),
                "online": p.online,
                "online_server_ids": list(p.online_server_ids),
                "online_server_names": online_names,
                "rank": ranks.get(_rk(p)),
                "ranked_players": ranked_players,
                "linked_identities": linked_out,
                "servers": [
                    {
                        "server_id": s.server_id,
                        "server_name": s.server_name,
                        "server_type": s.server_type,
                        "net_id": s.net_id,
                        "last_name": s.last_name,
                        "total_seconds": s.total_seconds,
                        "total_pretty": format_duration(s.total_seconds),
                        "visit_count": s.visit_count,
                        "rank": s.rank or None,
                        "ranked_players": s.ranked_players,
                        "first_seen_at": (
                            s.first_seen_at.isoformat() if s.first_seen_at else None
                        ),
                        "last_seen_at": (
                            s.last_seen_at.isoformat() if s.last_seen_at else None
                        ),
                        "last_seen_pretty": (
                            "Online"
                            if s.online
                            else format_last_seen(s.last_seen_at, now)
                        ),
                        "online": s.online,
                    }
                    for s in slices
                ],
            }
        )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "ranked_players": ranked_players,
        "server_id": server_id,
        "q": q or "",
        "sort": sort,
        "order": order,
        "players": out_players,
    }


def _empty_page(page: int, page_size: int, server_id: int | None) -> dict[str, Any]:
    return {
        "total": 0,
        "page": page,
        "page_size": page_size,
        "ranked_players": 0,
        "server_id": server_id,
        "q": "",
        "sort": "total_seconds",
        "order": "desc",
        "players": [],
    }

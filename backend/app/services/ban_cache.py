"""Per-server ban list cache (listbans snapshot in DB)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import ServerBanEntry, ServerBanSnapshot
from app.services.identity import extract_steam_id, resolve_names


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def replace_server_bans(
    db: Session,
    server_id: int,
    *,
    parsed: list[dict[str, Any]],
    raw: str,
    ok: bool = True,
    error: str = "",
) -> None:
    """Replace cached ban rows for a server after a live listbans."""
    db.query(ServerBanEntry).filter(ServerBanEntry.server_id == server_id).delete()
    now = _utcnow()
    snap = db.get(ServerBanSnapshot, server_id)
    if snap is None:
        snap = ServerBanSnapshot(server_id=server_id)
        db.add(snap)
    snap.raw_text = raw or ""
    snap.fetched_at = now
    snap.ok = ok
    snap.error = error or ""

    if ok:
        for row in parsed:
            db.add(
                ServerBanEntry(
                    server_id=server_id,
                    sort_index=int(row.get("index") or 0),
                    platform=str(row.get("platform") or ""),
                    raw_id=str(row.get("raw_id") or ""),
                    net_id=str(row.get("net_id") or row.get("raw_id") or ""),
                    display_id=str(row.get("display_id") or row.get("raw_id") or ""),
                    duration=str(row.get("duration") or "—"),
                    reason=str(row.get("reason") or "—"),
                    permanent=bool(row.get("permanent")),
                )
            )


def rebuild_local_bans(db: Session, server_id: int) -> int:
    """Derive a server's ban list from this app's own moderation history.

    For games whose API cannot enumerate bans (Palworld keeps them in a
    ``banlist.txt`` the REST API never exposes), the only bans we can know about
    are the ones we issued. Folding ``player_action_logs`` — latest ban/unban
    per identity wins — gives that list, and writing it through
    :func:`replace_server_bans` means pagination and Steam name resolution reuse
    the same path as a live ``listbans``.

    Returns the number of active bans. This is *not* the server's authoritative
    ban list: bans applied in-game or by editing the file are invisible here,
    and the UI says so.
    """
    from app.models import PlayerActionLog, PlayerServerStats
    from app.services.identity import parse_net_id

    rows = (
        db.query(PlayerActionLog)
        .filter(
            PlayerActionLog.server_id == server_id,
            PlayerActionLog.action.in_(("ban", "permban", "unban")),
            PlayerActionLog.ok.is_(True),
        )
        .order_by(PlayerActionLog.created_at.asc(), PlayerActionLog.id.asc())
        .all()
    )

    # Latest action per identity decides whether they are currently banned
    latest: dict[tuple[str, str], PlayerActionLog] = {}
    for row in rows:
        latest[(row.platform, row.external_id)] = row

    # Presence keeps the raw id we last saw a player under, which recovers the
    # platform prefix for bans logged before net_id was recorded.
    known_raw: dict[tuple[str, str], str] = {}
    for stats in (
        db.query(PlayerServerStats)
        .filter(PlayerServerStats.server_id == server_id)
        .all()
    ):
        parsed = parse_net_id(stats.steam_id or "")
        if parsed is not None:
            known_raw[parsed] = stats.steam_id

    # Drop unbanned identities before numbering, so the displayed indexes are
    # 1..n with no gaps — matching how a live listbans numbers its rows.
    still_banned = [
        (key, row)
        for key, row in sorted(
            latest.items(), key=lambda kv: kv[1].created_at or _utcnow(), reverse=True
        )
        if row.action != "unban"
    ]

    parsed_rows: list[dict[str, Any]] = []
    for index, (key, row) in enumerate(still_banned, start=1):
        platform, external_id = key
        raw_id = (row.net_id or "").strip() or known_raw.get(key) or external_id
        parsed_rows.append(
            {
                "index": index,
                "platform": platform,
                "raw_id": raw_id,
                "net_id": raw_id,
                "display_id": external_id,
                # No API takes a duration, so anything we issued is permanent
                "duration": "Permanent",
                "reason": row.reason or "—",
                "permanent": True,
            }
        )

    replace_server_bans(db, server_id, parsed=parsed_rows, raw="", ok=True, error="")
    return len(parsed_rows)


def remove_cached_ban(db: Session, server_id: int, raw_or_net_id: str) -> int:
    """Remove matching ban row(s) after a successful unban. Returns count deleted."""
    rid = (raw_or_net_id or "").strip()
    if not rid:
        return 0
    rows = db.query(ServerBanEntry).filter(ServerBanEntry.server_id == server_id).all()
    deleted = 0
    for row in rows:
        if rid in {row.raw_id, row.net_id, row.display_id} or rid in row.raw_id or rid in row.net_id:
            db.delete(row)
            deleted += 1
    return deleted


def _identity_pair(row: ServerBanEntry) -> tuple[str, str] | None:
    from app.services.identity import parse_net_id

    return parse_net_id(row.raw_id or row.net_id or row.display_id or "")


def _identity_cache_by_pair(
    db: Session, rows: list[ServerBanEntry]
) -> dict[tuple[str, str], dict[str, Any]]:
    """Names already cached under (platform, external_id), for any platform."""
    from app.models import IdentityCache

    pairs = {p for p in (_identity_pair(r) for r in rows) if p is not None}
    if not pairs:
        return {}
    cached = (
        db.query(IdentityCache)
        .filter(IdentityCache.external_id.in_({ext for _, ext in pairs}))
        .all()
    )
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in cached:
        key = (row.platform, row.external_id)
        if key in pairs and row.display_name:
            out[key] = {
                "display_name": row.display_name,
                "profile_url": row.profile_url or "",
                "avatar_url": row.avatar_url or "",
                "source": "identity_cache",
            }
    return out


def _attach_names(db: Session, rows: list[ServerBanEntry]) -> list[dict[str, Any]]:
    """Resolve display names for a set of ban rows (cache + API for misses)."""
    lookup_keys: list[str] = []
    for r in rows:
        if r.raw_id:
            lookup_keys.append(r.raw_id)
        if r.display_id and r.display_id != r.raw_id:
            lookup_keys.append(r.display_id)
        if r.net_id and r.net_id not in (r.raw_id, r.display_id):
            lookup_keys.append(r.net_id)
        sid = extract_steam_id(r.raw_id or r.display_id or r.net_id or "")
        if sid:
            lookup_keys.append(sid)

    names = resolve_names(db, lookup_keys) if lookup_keys else {}
    cached_by_pair = _identity_cache_by_pair(db, rows)

    bans: list[dict[str, Any]] = []
    for r in rows:
        sid = extract_steam_id(r.raw_id or r.display_id or r.net_id or "")
        # Take the first candidate that actually carries a name. `or` chaining
        # would stop at a resolved-but-empty entry and mask a later hit — which
        # is what hid crossplay names behind resolve_names' Steam/EOS-only view.
        candidates = (
            names.get(r.raw_id),
            names.get(r.display_id),
            names.get(r.net_id),
            names.get(sid) if sid else None,
            # presence and moderation cache names under (platform, external_id)
            cached_by_pair.get(_identity_pair(r)),
        )
        info = next((c for c in candidates if c and c.get("display_name")), {})
        bans.append(
            {
                "index": r.sort_index,
                "platform": r.platform,
                "raw_id": r.raw_id,
                "net_id": r.net_id or r.raw_id,
                "display_id": r.display_id or r.raw_id,
                "duration": r.duration or "—",
                "reason": r.reason or "—",
                "permanent": bool(r.permanent),
                "display_name": str(info.get("display_name") or ""),
                "profile_url": str(info.get("profile_url") or ""),
                "avatar_url": str(info.get("avatar_url") or ""),
                "name_source": str(info.get("source") or ""),
            }
        )
    return bans


def load_cached_bans(
    db: Session,
    server_id: int,
    *,
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    """
    Load cached bans with pagination + name resolution for the current page only.
    """
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 25)))

    snap = db.get(ServerBanSnapshot, server_id)
    total = (
        db.query(func.count(ServerBanEntry.id))
        .filter(ServerBanEntry.server_id == server_id)
        .scalar()
        or 0
    )
    total = int(total)
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * page_size
    rows = (
        db.query(ServerBanEntry)
        .filter(ServerBanEntry.server_id == server_id)
        .order_by(ServerBanEntry.sort_index.asc(), ServerBanEntry.id.asc())
        .offset(offset)
        .limit(page_size)
        .all()
    )
    bans = _attach_names(db, rows)

    return {
        "bans": bans,
        "raw": (snap.raw_text if snap else "") or "",
        "ok": bool(snap.ok) if snap else True,
        "error": (snap.error if snap else "") or None,
        "fetched_at": snap.fetched_at if snap else None,
        "from_cache": True,
        "has_snapshot": snap is not None,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    }

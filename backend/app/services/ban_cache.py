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

    bans: list[dict[str, Any]] = []
    for r in rows:
        sid = extract_steam_id(r.raw_id or r.display_id or r.net_id or "")
        info = (
            names.get(r.raw_id)
            or names.get(r.display_id)
            or names.get(r.net_id)
            or (names.get(sid) if sid else None)
            or {}
        )
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

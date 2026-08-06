"""Per-server ban list cache (listbans snapshot in DB)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import ServerBanEntry, ServerBanSnapshot
from app.services.identity import resolve_names


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
    q = db.query(ServerBanEntry).filter(ServerBanEntry.server_id == server_id)
    # Match full raw_id or net_id or display suffix
    rows = q.all()
    deleted = 0
    for row in rows:
        if rid in {row.raw_id, row.net_id, row.display_id} or rid in row.raw_id or rid in row.net_id:
            db.delete(row)
            deleted += 1
    return deleted


def load_cached_bans(db: Session, server_id: int) -> dict[str, Any]:
    """
    Load cache + resolve names from identity_cache.
    Returns dict suitable for BanListOut fields.
    """
    snap = db.get(ServerBanSnapshot, server_id)
    rows = (
        db.query(ServerBanEntry)
        .filter(ServerBanEntry.server_id == server_id)
        .order_by(ServerBanEntry.sort_index.asc(), ServerBanEntry.id.asc())
        .all()
    )
    raw_ids = [r.raw_id for r in rows if r.raw_id]
    names = resolve_names(db, raw_ids) if raw_ids else {}

    bans: list[dict[str, Any]] = []
    for r in rows:
        info = names.get(r.raw_id) or {}
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

    return {
        "bans": bans,
        "raw": (snap.raw_text if snap else "") or "",
        "ok": bool(snap.ok) if snap else True,
        "error": (snap.error if snap else "") or None,
        "fetched_at": snap.fetched_at if snap else None,
        "from_cache": True,
        "has_snapshot": snap is not None,
    }

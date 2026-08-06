"""Per-identity admin history: kick/ban/unban logs + free-text notes."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import PlayerActionLog, PlayerAdminNote, Server
from app.services.identity import extract_eos_id, extract_steam_id, remember_identity

STEAM_ID_RE = re.compile(r"^\d{17}$")


def normalize_identity(
    *,
    net_id: str = "",
    platform_hint: str = "",
) -> tuple[str, str] | None:
    """
    Return (platform, external_id) or None if we cannot identify the user.
    platform: steam | eos | unknown
    """
    raw = (net_id or "").strip()
    if not raw:
        return None

    steam = extract_steam_id(raw)
    if steam:
        return "steam", steam

    eos = extract_eos_id(raw)
    if eos:
        return "eos", eos

    # Already-canonical forms
    if STEAM_ID_RE.fullmatch(raw):
        return "steam", raw

    hint = (platform_hint or "").strip().lower()
    if hint in {"steam", "eos", "unknown"}:
        return hint, raw

    return "unknown", raw


def log_player_action(
    db: Session,
    *,
    server: Server | None,
    action: str,
    net_id: str = "",
    player_name: str = "",
    reason: str = "",
    detail: str = "",
    ok: bool = True,
    error: str = "",
    platform_hint: str = "",
) -> PlayerActionLog | None:
    """Persist a moderation action. Skips if no usable platform id."""
    ident = normalize_identity(net_id=net_id, platform_hint=platform_hint)
    if ident is None:
        # Try name-only is not allowed for identity key — skip log
        return None
    platform, external_id = ident

    name = (player_name or "").strip()
    if name and platform == "steam":
        remember_identity(
            db,
            platform="steam",
            external_id=external_id,
            display_name=name,
            profile_url=f"https://steamcommunity.com/profiles/{external_id}",
            source="moderation",
        )

    row = PlayerActionLog(
        platform=platform,
        external_id=external_id,
        action=action.strip().lower(),
        server_id=server.id if server else None,
        server_name=(server.name if server else "") or "",
        player_name=name,
        reason=(reason or "").strip(),
        detail=(detail or "").strip(),
        ok=bool(ok),
        error=(error or "").strip(),
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    return row


def identity_has_records(db: Session, platform: str, external_id: str) -> bool:
    actions = (
        db.query(PlayerActionLog.id)
        .filter(
            PlayerActionLog.platform == platform,
            PlayerActionLog.external_id == external_id,
        )
        .limit(1)
        .first()
    )
    if actions:
        return True
    notes = (
        db.query(PlayerAdminNote.id)
        .filter(
            PlayerAdminNote.platform == platform,
            PlayerAdminNote.external_id == external_id,
        )
        .limit(1)
        .first()
    )
    return notes is not None


def batch_has_records(db: Session, identities: list[tuple[str, str]]) -> dict[str, bool]:
    """
    identities: list of (platform, external_id)
    Returns map external_id -> has_info (external_id alone is unique enough with platform
    encoded in key as platform:external_id)
    """
    out: dict[str, bool] = {}
    if not identities:
        return out

    keys = {(p, e) for p, e in identities if p and e}
    for p, e in keys:
        out[f"{p}:{e}"] = False

    if not keys:
        return out

    # Query actions
    platforms = {p for p, _ in keys}
    ext_ids = {e for _, e in keys}
    for row in (
        db.query(PlayerActionLog.platform, PlayerActionLog.external_id)
        .filter(
            PlayerActionLog.platform.in_(platforms),
            PlayerActionLog.external_id.in_(ext_ids),
        )
        .distinct()
        .all()
    ):
        k = f"{row[0]}:{row[1]}"
        if k in out:
            out[k] = True

    for row in (
        db.query(PlayerAdminNote.platform, PlayerAdminNote.external_id)
        .filter(
            PlayerAdminNote.platform.in_(platforms),
            PlayerAdminNote.external_id.in_(ext_ids),
        )
        .distinct()
        .all()
    ):
        k = f"{row[0]}:{row[1]}"
        if k in out:
            out[k] = True

    return out


def get_dossier(db: Session, platform: str, external_id: str) -> dict[str, Any]:
    from app.models import IdentityCache
    from app.services.identity import resolve_names

    platform = platform.strip().lower()
    external_id = external_id.strip()

    # Resolve display name via cache/API
    raw_for_resolve = external_id
    if platform == "steam" and STEAM_ID_RE.fullmatch(external_id):
        raw_for_resolve = external_id
    elif platform == "eos":
        raw_for_resolve = f"EOS:{external_id}"

    names = resolve_names(db, [raw_for_resolve])
    info = names.get(raw_for_resolve) or {}

    cache_row = (
        db.query(IdentityCache)
        .filter(
            IdentityCache.platform == platform,
            IdentityCache.external_id == external_id,
        )
        .first()
    )

    actions = (
        db.query(PlayerActionLog)
        .filter(
            PlayerActionLog.platform == platform,
            PlayerActionLog.external_id == external_id,
        )
        .order_by(PlayerActionLog.created_at.desc())
        .limit(200)
        .all()
    )
    notes = (
        db.query(PlayerAdminNote)
        .filter(
            PlayerAdminNote.platform == platform,
            PlayerAdminNote.external_id == external_id,
        )
        .order_by(PlayerAdminNote.created_at.desc())
        .limit(100)
        .all()
    )

    return {
        "platform": platform,
        "external_id": external_id,
        "display_name": info.get("display_name")
        or (cache_row.display_name if cache_row else "")
        or "",
        "profile_url": info.get("profile_url")
        or (cache_row.profile_url if cache_row else "")
        or "",
        "avatar_url": info.get("avatar_url")
        or (cache_row.avatar_url if cache_row else "")
        or "",
        "has_info": bool(actions or notes),
        "actions": actions,
        "notes": notes,
    }


def set_note(
    db: Session,
    *,
    platform: str,
    external_id: str,
    body: str,
) -> PlayerAdminNote | None:
    """
    Upsert a single admin note document for this identity.
    Empty body clears all notes. Multiple legacy rows are collapsed into one.
    """
    platform = platform.strip().lower()
    external_id = external_id.strip()
    if not external_id:
        raise ValueError("external_id is required")
    text = body if body is not None else ""
    now = datetime.now(timezone.utc)

    existing = (
        db.query(PlayerAdminNote)
        .filter(
            PlayerAdminNote.platform == platform,
            PlayerAdminNote.external_id == external_id,
        )
        .order_by(PlayerAdminNote.created_at.asc())
        .all()
    )

    if not text.strip():
        for row in existing:
            db.delete(row)
        return None

    if existing:
        note = existing[0]
        note.body = text
        note.updated_at = now
        for row in existing[1:]:
            db.delete(row)
        return note

    note = PlayerAdminNote(
        platform=platform,
        external_id=external_id,
        body=text,
        created_at=now,
        updated_at=now,
    )
    db.add(note)
    return note


def add_note(
    db: Session,
    *,
    platform: str,
    external_id: str,
    body: str,
) -> PlayerAdminNote:
    """Backward-compatible alias: set non-empty note body."""
    note = set_note(db, platform=platform, external_id=external_id, body=body)
    if note is None:
        raise ValueError("Note body is required")
    return note


def delete_note(db: Session, note_id: int) -> bool:
    row = db.get(PlayerAdminNote, note_id)
    if not row:
        return False
    db.delete(row)
    return True

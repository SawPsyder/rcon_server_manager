"""Per-identity admin history: kick/ban/unban logs + free-text notes."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import PlayerActionLog, PlayerAdminNote, Server, User
from app.services.identity import parse_net_id, remember_identity

STEAM_ID_RE = re.compile(r"^\d{17}$")


def normalize_identity(
    *,
    net_id: str = "",
    platform_hint: str = "",
) -> tuple[str, str] | None:
    """
    Return (platform, external_id) or None if we cannot identify the user.
    platform: steam | xbox | psn | eos | mac | unknown
    """
    raw = (net_id or "").strip()
    if not raw:
        return None

    # Shared with presence tracking, so a moderation entry and a playtime row
    # resolve to the same person for crossplay ids like ``gdk_2535…``
    parsed = parse_net_id(raw)
    if parsed is not None:
        return parsed

    hint = (platform_hint or "").strip().lower()
    if hint in {"steam", "xbox", "psn", "eos", "mac", "unknown"}:
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
    actor: "User | None" = None,
) -> PlayerActionLog | None:
    """Persist a moderation action. Skips if no usable platform id."""
    ident = normalize_identity(net_id=net_id, platform_hint=platform_hint)
    if ident is None:
        # Try name-only is not allowed for identity key - skip log
        return None
    platform, external_id = ident

    name = (player_name or "").strip()
    if name:
        # Cache the name for every platform, not just Steam - otherwise a
        # kicked Game Pass player has no name in the dossier unless presence
        # happened to see them first. Only Steam has a public profile URL.
        remember_identity(
            db,
            platform=platform,
            external_id=external_id,
            display_name=name,
            profile_url=(
                f"https://steamcommunity.com/profiles/{external_id}"
                if platform == "steam"
                else ""
            ),
            source="moderation",
        )

    row = PlayerActionLog(
        platform=platform,
        external_id=external_id,
        action=action.strip().lower(),
        server_id=server.id if server else None,
        server_name=(server.name if server else "") or "",
        player_name=name,
        net_id=(net_id or "").strip()[:64],
        reason=(reason or "").strip(),
        detail=(detail or "").strip(),
        ok=bool(ok),
        error=(error or "").strip(),
        # actor_label freezes the operator's email so the log stays readable
        # after the account is deleted and actor_user_id goes null.
        actor_user_id=actor.id if actor else None,
        actor_label=(actor.display_name or actor.email) if actor else "",
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


def _scope_actions(query, allowed_server_ids: set[int] | None):
    """Restrict a PlayerActionLog query to servers the caller may see.

    None means unrestricted (admin). For everyone else this also hides rows with
    a null server_id - those belong to a since-deleted server and there is no
    way to tell whether the caller was ever granted it.
    """
    if allowed_server_ids is None:
        return query
    if not allowed_server_ids:
        return query.filter(False)
    return query.filter(PlayerActionLog.server_id.in_(allowed_server_ids))


def batch_has_records(
    db: Session,
    identities: list[tuple[str, str]],
    allowed_server_ids: set[int] | None = None,
) -> dict[str, bool]:
    """
    identities: list of (platform, external_id)
    Returns map external_id -> has_info (external_id alone is unique enough with platform
    encoded in key as platform:external_id)

    Linked accounts share the flag: if any member of a person group has notes or
    moderation history, every requested key in that group is marked true.
    """
    out: dict[str, bool] = {}
    if not identities:
        return out

    keys = {(p.strip().lower(), e.strip()) for p, e in identities if p and e}
    for p, e in keys:
        out[f"{p}:{e}"] = False

    if not keys:
        return out

    from app.services.identity_links import expand_identity_set, load_link_map

    link_map = load_link_map(db)
    expanded = expand_identity_set(keys, link_map)

    platforms = {p for p, _ in expanded}
    ext_ids = {e for _, e in expanded}
    hits: set[tuple[str, str]] = set()
    for row in (
        _scope_actions(
            db.query(PlayerActionLog.platform, PlayerActionLog.external_id).filter(
                PlayerActionLog.platform.in_(platforms),
                PlayerActionLog.external_id.in_(ext_ids),
            ),
            allowed_server_ids,
        )
        .distinct()
        .all()
    ):
        hits.add((row[0], row[1]))

    for row in (
        db.query(PlayerAdminNote.platform, PlayerAdminNote.external_id)
        .filter(
            PlayerAdminNote.platform.in_(platforms),
            PlayerAdminNote.external_id.in_(ext_ids),
        )
        .distinct()
        .all()
    ):
        hits.add((row[0], row[1]))

    if not hits:
        return out

    # Any hit on a linked member lights up every requested key in that person.
    hit_groups = {link_map[h] for h in hits if h in link_map}
    for p, e in keys:
        if (p, e) in hits:
            out[f"{p}:{e}"] = True
            continue
        gid = link_map.get((p, e))
        if gid is not None and gid in hit_groups:
            out[f"{p}:{e}"] = True

    return out


def _profile_shell(
    db: Session,
    platform: str,
    external_id: str,
) -> dict[str, Any]:
    """Display metadata for one platform identity (no actions/notes)."""
    from app.models import IdentityCache
    from app.services.identity import resolve_names

    platform = platform.strip().lower()
    external_id = external_id.strip()

    raw_for_resolve = external_id
    if platform == "steam" and STEAM_ID_RE.fullmatch(external_id):
        raw_for_resolve = external_id
    elif platform == "eos":
        raw_for_resolve = f"EOS:{external_id}"
    elif platform == "xbox":
        # Prefer a presence-style key so resolve can hit PlayerServerStats.
        raw_for_resolve = f"gdk_{external_id}"

    names = resolve_names(db, [raw_for_resolve, external_id])
    info = names.get(raw_for_resolve) or names.get(external_id) or {}

    cache_row = (
        db.query(IdentityCache)
        .filter(
            IdentityCache.platform == platform,
            IdentityCache.external_id == external_id,
        )
        .first()
    )

    display_name = (
        info.get("display_name")
        or (cache_row.display_name if cache_row else "")
        or ""
    )
    profile_url = (
        info.get("profile_url")
        or (cache_row.profile_url if cache_row else "")
        or ""
    )
    avatar_url = (
        info.get("avatar_url")
        or (cache_row.avatar_url if cache_row else "")
        or ""
    )

    # Reconstruct a net_id operators can paste into kick/ban when known.
    if platform == "steam" and STEAM_ID_RE.fullmatch(external_id):
        net_id = external_id
    elif platform == "eos":
        net_id = f"EOS:{external_id}"
    elif platform in {"xbox", "psn", "mac"} and external_id:
        prefix = {"xbox": "gdk", "psn": "psn", "mac": "mac"}[platform]
        net_id = f"{prefix}_{external_id}"
    else:
        net_id = external_id

    return {
        "platform": platform,
        "external_id": external_id,
        "net_id": net_id,
        "display_name": display_name,
        "profile_url": profile_url,
        "avatar_url": avatar_url,
    }


def _profile_records(
    db: Session,
    platform: str,
    external_id: str,
    allowed_server_ids: set[int] | None,
) -> tuple[list[PlayerActionLog], list[PlayerAdminNote]]:
    actions = (
        _scope_actions(
            db.query(PlayerActionLog).filter(
                PlayerActionLog.platform == platform,
                PlayerActionLog.external_id == external_id,
            ),
            allowed_server_ids,
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
        .order_by(PlayerAdminNote.updated_at.desc(), PlayerAdminNote.id.desc())
        .limit(100)
        .all()
    )
    return actions, notes


def get_dossier(
    db: Session,
    platform: str,
    external_id: str,
    allowed_server_ids: set[int] | None = None,
) -> dict[str, Any]:
    from app.services.identity_links import get_group_id, linked_identities

    platform = platform.strip().lower()
    external_id = external_id.strip()

    members = linked_identities(db, platform, external_id)
    # Requested identity first, then the rest alphabetically by platform.
    ordered = [(platform, external_id)]
    for p, e in members:
        if (p, e) != (platform, external_id):
            ordered.append((p, e))

    profiles: list[dict[str, Any]] = []
    any_info = False
    for p, e in ordered:
        shell = _profile_shell(db, p, e)
        actions, notes = _profile_records(db, p, e, allowed_server_ids)
        has = bool(actions or notes)
        any_info = any_info or has
        profiles.append(
            {
                **shell,
                "has_info": has,
                "actions": actions,
                "notes": notes,
            }
        )

    primary = profiles[0] if profiles else _profile_shell(db, platform, external_id)
    group_id = get_group_id(db, platform, external_id)

    return {
        "platform": platform,
        "external_id": external_id,
        "display_name": primary.get("display_name") or "",
        "profile_url": primary.get("profile_url") or "",
        "avatar_url": primary.get("avatar_url") or "",
        "has_info": any_info,
        # Backward-compatible fields for the requested identity only.
        "actions": primary.get("actions") or [],
        "notes": primary.get("notes") or [],
        "link_group_id": group_id,
        "profiles": profiles,
    }


def note_author_label(db: Session, note: PlayerAdminNote) -> str:
    """Display label for a note author; survives user deletion reasonably."""
    if note.author_user_id is None:
        return "Unknown"
    author = db.get(User, note.author_user_id)
    if author is None:
        return "Former user"
    return (author.display_name or author.email or f"User #{author.id}").strip()


def upsert_own_note(
    db: Session,
    *,
    platform: str,
    external_id: str,
    body: str,
    author: User,
) -> PlayerAdminNote | None:
    """Upsert the caller's own note for this identity.

    Empty body deletes only the caller's note. Other authors' notes are untouched.
    """
    platform = platform.strip().lower()
    external_id = external_id.strip()
    if not external_id:
        raise ValueError("external_id is required")
    text = (body if body is not None else "").strip()
    now = datetime.now(timezone.utc)

    existing = (
        db.query(PlayerAdminNote)
        .filter(
            PlayerAdminNote.platform == platform,
            PlayerAdminNote.external_id == external_id,
            PlayerAdminNote.author_user_id == author.id,
        )
        .first()
    )

    if not text:
        if existing is not None:
            db.delete(existing)
        return None

    if existing is not None:
        existing.body = text
        existing.updated_at = now
        return existing

    note = PlayerAdminNote(
        platform=platform,
        external_id=external_id,
        body=text,
        author_user_id=author.id,
        created_at=now,
        updated_at=now,
    )
    db.add(note)
    return note


def delete_note(db: Session, note_id: int) -> bool:
    row = db.get(PlayerAdminNote, note_id)
    if not row:
        return False
    db.delete(row)
    return True

"""Link multiple platform identities to one natural person.

Operators mark Steam / Xbox / PSN / etc. accounts as the same player. Storage
stays per-platform (notes, bans, presence rows); aggregation (leaderboard,
dossier shell) follows the link graph.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import IdentityLinkGroup, IdentityLinkMember, User


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(platform: str, external_id: str) -> tuple[str, str]:
    return (platform or "").strip().lower(), (external_id or "").strip()


def get_member(
    db: Session, platform: str, external_id: str
) -> IdentityLinkMember | None:
    platform, external_id = _norm(platform, external_id)
    if not platform or not external_id:
        return None
    return (
        db.query(IdentityLinkMember)
        .filter(
            IdentityLinkMember.platform == platform,
            IdentityLinkMember.external_id == external_id,
        )
        .first()
    )


def get_group_id(db: Session, platform: str, external_id: str) -> int | None:
    member = get_member(db, platform, external_id)
    return int(member.group_id) if member else None


def list_group_members(
    db: Session, group_id: int
) -> list[tuple[str, str]]:
    rows = (
        db.query(IdentityLinkMember)
        .filter(IdentityLinkMember.group_id == group_id)
        .order_by(IdentityLinkMember.platform.asc(), IdentityLinkMember.external_id.asc())
        .all()
    )
    return [(r.platform, r.external_id) for r in rows]


def linked_identities(
    db: Session, platform: str, external_id: str
) -> list[tuple[str, str]]:
    """All identities in this person's group, or just ``(platform, external_id)``."""
    platform, external_id = _norm(platform, external_id)
    if not platform or not external_id:
        return []
    gid = get_group_id(db, platform, external_id)
    if gid is None:
        return [(platform, external_id)]
    members = list_group_members(db, gid)
    # Ensure the requested identity is present even if data is inconsistent.
    if (platform, external_id) not in members:
        members = [(platform, external_id), *members]
    return members


def load_link_map(db: Session) -> dict[tuple[str, str], int]:
    """Map every linked (platform, external_id) → group_id."""
    rows = db.query(IdentityLinkMember).all()
    return {(r.platform, r.external_id): int(r.group_id) for r in rows}


def person_key(
    platform: str,
    external_id: str,
    link_map: dict[tuple[str, str], int],
) -> str:
    """Stable aggregation key for leaderboard / flags."""
    platform, external_id = _norm(platform, external_id)
    gid = link_map.get((platform, external_id))
    if gid is not None:
        return f"group:{gid}"
    return f"solo:{platform}:{external_id}"


def expand_identity_set(
    identities: Iterable[tuple[str, str]],
    link_map: dict[tuple[str, str], int],
) -> set[tuple[str, str]]:
    """Expand a set of identities to include every member of their link groups."""
    group_ids: set[int] = set()
    out: set[tuple[str, str]] = set()
    for platform, external_id in identities:
        p, e = _norm(platform, external_id)
        if not p or not e:
            continue
        out.add((p, e))
        gid = link_map.get((p, e))
        if gid is not None:
            group_ids.add(gid)
    if not group_ids:
        return out
    for (p, e), gid in link_map.items():
        if gid in group_ids:
            out.add((p, e))
    return out


def _ensure_in_group(
    db: Session,
    *,
    group: IdentityLinkGroup,
    platform: str,
    external_id: str,
    actor: User | None,
) -> IdentityLinkMember:
    existing = get_member(db, platform, external_id)
    if existing is not None:
        if existing.group_id == group.id:
            return existing
        # Should not happen if caller merges first; move to target group.
        existing.group_id = group.id
        return existing
    member = IdentityLinkMember(
        group_id=group.id,
        platform=platform,
        external_id=external_id,
        linked_at=_now(),
        linked_by_user_id=actor.id if actor else None,
    )
    db.add(member)
    return member


def _dissolve_if_tiny(db: Session, group_id: int) -> None:
    """A group with fewer than 2 members is not a link - drop it."""
    members = (
        db.query(IdentityLinkMember)
        .filter(IdentityLinkMember.group_id == group_id)
        .all()
    )
    if len(members) >= 2:
        return
    for m in members:
        db.delete(m)
    group = db.get(IdentityLinkGroup, group_id)
    if group is not None:
        db.delete(group)


def link_identities(
    db: Session,
    *,
    a: tuple[str, str],
    b: tuple[str, str],
    actor: User | None = None,
) -> IdentityLinkGroup:
    """Link two platform identities into one person group (merge if needed)."""
    a_p, a_e = _norm(*a)
    b_p, b_e = _norm(*b)
    if not a_p or not a_e or not b_p or not b_e:
        raise ValueError("Both identities need a platform and external id")
    if (a_p, a_e) == (b_p, b_e):
        raise ValueError("Cannot link an account to itself")

    a_member = get_member(db, a_p, a_e)
    b_member = get_member(db, b_p, b_e)

    if a_member and b_member and a_member.group_id == b_member.group_id:
        group = db.get(IdentityLinkGroup, a_member.group_id)
        if group is None:
            raise ValueError("Link group missing")
        return group

    if a_member and b_member:
        # Merge B's group into A's.
        target_id = int(a_member.group_id)
        source_id = int(b_member.group_id)
        if target_id == source_id:
            return db.get(IdentityLinkGroup, target_id)  # type: ignore[return-value]
        for m in (
            db.query(IdentityLinkMember)
            .filter(IdentityLinkMember.group_id == source_id)
            .all()
        ):
            m.group_id = target_id
        old = db.get(IdentityLinkGroup, source_id)
        if old is not None:
            db.delete(old)
        group = db.get(IdentityLinkGroup, target_id)
        if group is None:
            raise ValueError("Link group missing after merge")
        db.flush()
        return group

    if a_member:
        group = db.get(IdentityLinkGroup, a_member.group_id)
        if group is None:
            raise ValueError("Link group missing")
        _ensure_in_group(db, group=group, platform=b_p, external_id=b_e, actor=actor)
        db.flush()
        return group

    if b_member:
        group = db.get(IdentityLinkGroup, b_member.group_id)
        if group is None:
            raise ValueError("Link group missing")
        _ensure_in_group(db, group=group, platform=a_p, external_id=a_e, actor=actor)
        db.flush()
        return group

    group = IdentityLinkGroup(
        created_at=_now(),
        created_by_user_id=actor.id if actor else None,
    )
    db.add(group)
    db.flush()
    _ensure_in_group(db, group=group, platform=a_p, external_id=a_e, actor=actor)
    _ensure_in_group(db, group=group, platform=b_p, external_id=b_e, actor=actor)
    db.flush()
    return group


def unlink_identity(
    db: Session,
    *,
    platform: str,
    external_id: str,
) -> bool:
    """Remove one identity from its group. Returns False if it was not linked."""
    platform, external_id = _norm(platform, external_id)
    member = get_member(db, platform, external_id)
    if member is None:
        return False
    group_id = int(member.group_id)
    db.delete(member)
    db.flush()
    _dissolve_if_tiny(db, group_id)
    db.flush()
    return True

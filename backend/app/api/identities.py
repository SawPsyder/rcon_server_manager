"""Player identity dossier: history + per-user notes + account linking."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import CurrentUser, granted_server_ids
from app.models import PlayerAdminNote
from app.schemas import (
    IdentityDossierOut,
    IdentityFlagsOut,
    IdentityFlagsRequest,
    IdentityLinkRequest,
    IdentityProfileOut,
    PlayerActionLogOut,
    PlayerNoteCreate,
    PlayerNoteOut,
)
from app.services.identity_links import link_identities, unlink_identity
from app.services.player_records import (
    batch_has_records,
    delete_note,
    get_dossier,
    note_author_label,
    normalize_identity,
    upsert_own_note,
)

router = APIRouter(prefix="/api/identities", tags=["identities"])


def _note_out(db: Session, note: PlayerAdminNote) -> PlayerNoteOut:
    return PlayerNoteOut(
        id=note.id,
        platform=note.platform,
        external_id=note.external_id,
        body=note.body,
        author_user_id=note.author_user_id,
        author_label=note_author_label(db, note),
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


def _dossier_out(db: Session, data: dict) -> IdentityDossierOut:
    profiles: list[IdentityProfileOut] = []
    for prof in data.get("profiles") or []:
        profiles.append(
            IdentityProfileOut(
                platform=prof["platform"],
                external_id=prof["external_id"],
                net_id=prof.get("net_id") or "",
                display_name=prof.get("display_name") or "",
                profile_url=prof.get("profile_url") or "",
                avatar_url=prof.get("avatar_url") or "",
                has_info=bool(prof.get("has_info")),
                actions=[
                    PlayerActionLogOut.model_validate(a) for a in (prof.get("actions") or [])
                ],
                notes=[_note_out(db, n) for n in (prof.get("notes") or [])],
            )
        )
    return IdentityDossierOut(
        platform=data["platform"],
        external_id=data["external_id"],
        display_name=data["display_name"],
        profile_url=data["profile_url"],
        avatar_url=data["avatar_url"],
        has_info=data["has_info"],
        actions=[PlayerActionLogOut.model_validate(a) for a in data["actions"]],
        notes=[_note_out(db, n) for n in data["notes"]],
        link_group_id=data.get("link_group_id"),
        profiles=profiles,
    )


@router.post("/flags", response_model=IdentityFlagsOut)
def identity_flags(
    body: IdentityFlagsRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> IdentityFlagsOut:
    pairs: list[tuple[str, str]] = []
    for item in body.identities:
        if not isinstance(item, dict):
            continue
        if item.get("platform") and item.get("external_id"):
            pairs.append((item["platform"].strip().lower(), item["external_id"].strip()))
            continue
        net = (item.get("net_id") or item.get("steamid") or "").strip()
        if not net:
            continue
        ident = normalize_identity(net_id=net)
        if ident:
            pairs.append(ident)
    return IdentityFlagsOut(flags=batch_has_records(db, pairs, granted_server_ids(db, user)))


@router.get("/{platform}/{external_id}", response_model=IdentityDossierOut)
def identity_dossier(
    platform: str,
    external_id: str,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> IdentityDossierOut:
    # external_id may be URL-encoded EOS id with |
    data = get_dossier(db, platform, external_id, granted_server_ids(db, user))
    return _dossier_out(db, data)


@router.post("/{platform}/{external_id}/link", response_model=IdentityDossierOut)
def link_account(
    platform: str,
    external_id: str,
    body: IdentityLinkRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> IdentityDossierOut:
    """Link another platform account to this identity (same natural person)."""
    other: tuple[str, str] | None = None
    if (body.platform or "").strip() and (body.external_id or "").strip():
        other = (body.platform.strip().lower(), body.external_id.strip())
    else:
        net = (body.net_id or "").strip()
        if net:
            other = normalize_identity(net_id=net)
    if other is None:
        raise HTTPException(
            status_code=400,
            detail="Provide net_id (e.g. SteamID64 or gdk_…) or platform + external_id",
        )

    try:
        link_identities(
            db,
            a=(platform, external_id),
            b=other,
            actor=user,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    data = get_dossier(db, platform, external_id, granted_server_ids(db, user))
    return _dossier_out(db, data)


@router.delete("/{platform}/{external_id}/link", response_model=IdentityDossierOut)
def unlink_account(
    platform: str,
    external_id: str,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> IdentityDossierOut:
    """Remove this identity from its link group (other members stay linked)."""
    if not unlink_identity(db, platform=platform, external_id=external_id):
        raise HTTPException(status_code=404, detail="This account is not linked to others")
    db.commit()
    data = get_dossier(db, platform, external_id, granted_server_ids(db, user))
    return _dossier_out(db, data)


@router.put("/{platform}/{external_id}/notes", response_model=None)
@router.post("/{platform}/{external_id}/notes", response_model=None)
def upsert_note(
    platform: str,
    external_id: str,
    body: PlayerNoteCreate,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> PlayerNoteOut | Response:
    """Upsert the caller's own note (empty body clears only their note → 204).

    Notes are identity-scoped and multi-author: every operator sees every note;
    each person may only create/edit/delete the one that belongs to them.
    """
    try:
        note = upsert_own_note(
            db,
            platform=platform,
            external_id=external_id,
            body=body.body,
            author=user,
        )
        db.commit()
        if note is None:
            return Response(status_code=204)
        db.refresh(note)
        return _note_out(db, note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/notes/{note_id}", status_code=204)
def remove_note(
    note_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> Response:
    existing = db.get(PlayerAdminNote, note_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Note not found")
    # Own note only. Orphaned pre-multi-user rows (no author) may be cleaned up
    # by an admin so they do not sit uneditable forever.
    is_author = existing.author_user_id is not None and existing.author_user_id == user.id
    is_orphan_cleanup = existing.author_user_id is None and user.is_admin
    if not is_author and not is_orphan_cleanup:
        raise HTTPException(
            status_code=403, detail="You can only delete your own note"
        )
    if not delete_note(db, note_id):
        raise HTTPException(status_code=404, detail="Note not found")
    db.commit()
    return Response(status_code=204)

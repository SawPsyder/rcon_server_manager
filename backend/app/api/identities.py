"""Player identity dossier: history + admin notes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.schemas import (
    IdentityDossierOut,
    IdentityFlagsOut,
    IdentityFlagsRequest,
    PlayerActionLogOut,
    PlayerNoteCreate,
    PlayerNoteOut,
)
from app.services.player_records import (
    add_note,
    batch_has_records,
    delete_note,
    get_dossier,
    normalize_identity,
)

router = APIRouter(prefix="/api/identities", tags=["identities"])


@router.post("/flags", response_model=IdentityFlagsOut)
def identity_flags(
    body: IdentityFlagsRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
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
    return IdentityFlagsOut(flags=batch_has_records(db, pairs))


@router.get("/{platform}/{external_id}", response_model=IdentityDossierOut)
def identity_dossier(
    platform: str,
    external_id: str,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> IdentityDossierOut:
    # external_id may be URL-encoded EOS id with |
    data = get_dossier(db, platform, external_id)
    return IdentityDossierOut(
        platform=data["platform"],
        external_id=data["external_id"],
        display_name=data["display_name"],
        profile_url=data["profile_url"],
        avatar_url=data["avatar_url"],
        has_info=data["has_info"],
        actions=[PlayerActionLogOut.model_validate(a) for a in data["actions"]],
        notes=[PlayerNoteOut.model_validate(n) for n in data["notes"]],
    )


@router.post("/{platform}/{external_id}/notes", response_model=PlayerNoteOut)
def create_note(
    platform: str,
    external_id: str,
    body: PlayerNoteCreate,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> PlayerNoteOut:
    try:
        note = add_note(
            db,
            platform=platform,
            external_id=external_id,
            body=body.body,
        )
        db.commit()
        db.refresh(note)
        return note
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/notes/{note_id}", status_code=204)
def remove_note(
    note_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> Response:
    if not delete_note(db, note_id):
        raise HTTPException(status_code=404, detail="Note not found")
    db.commit()
    return Response(status_code=204)

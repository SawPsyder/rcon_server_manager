"""User administration. Every route here is admin-only."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import AdminUser, client_ip
from app.models import TOKEN_PURPOSE_INVITE, TOKEN_PURPOSE_RESET, User
from app.schemas import (
    GrantsUpdate,
    InviteLinkOut,
    UserAdminUpdate,
    UserCreateRequest,
    UserOut,
)
from app.services import mail_settings as mail_config_store
from app.services import mailer, rate_limit
from app.services import totp as totp_service
from app.services import users as user_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["users"])


def _to_out(db: Session, user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        totp_enabled=user.totp_enabled,
        has_password=bool(user.password_hash),
        is_locked=user_service.is_temporarily_locked(user),
        locked_until=user.locked_until,
        failed_logins=int(user.failed_logins or 0),
        server_ids=user_service.grant_ids(db, user),
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


@router.get("", response_model=list[UserOut])
def list_users(_admin: AdminUser, db: Session = Depends(get_db)) -> list[UserOut]:
    rows = db.query(User).order_by(User.email_ci.asc()).all()
    return [_to_out(db, u) for u in rows]


@router.post("", response_model=InviteLinkOut, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreateRequest,
    admin: AdminUser,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> InviteLinkOut:
    """Create a user with no password and issue an invite link.

    When SMTP is unconfigured (or PUBLIC_BASE_URL is unset) the link comes back
    in the response so the admin can pass it on by hand - a self-hosted install
    with no relay still works.
    """
    user = user_service.create_user(
        db,
        email=body.email,
        display_name=body.display_name,
        role=body.role,
    )
    if body.server_ids:
        user_service.replace_grants(db, user, body.server_ids, admin)

    raw, _ = user_service.issue_token(
        db, user, TOKEN_PURPOSE_INVITE, requested_ip=client_ip(request)
    )
    db.commit()
    db.refresh(user)

    # Resolve while a session is still open - the background task runs after
    # the response, with no request-scoped database session of its own.
    cfg = mail_config_store.load_mail_config(db)
    link = cfg.link(f"/invite/{raw}")
    emailed = False
    if link and cfg.enabled:
        background.add_task(
            mailer.send_invite,
            cfg,
            user.email,
            link,
            admin.display_name or admin.email,
            get_settings().invite_token_ttl_hours,
        )
        emailed = True
    elif not link:
        logger.warning(
            "Invited %s but no application URL is configured; no link could be built",
            user.email,
        )

    # Only hand the link back when it will not arrive by mail.
    return InviteLinkOut(
        user=_to_out(db, user),
        invite_url="" if emailed else link,
        emailed=emailed,
    )


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int, _admin: AdminUser, db: Session = Depends(get_db)) -> UserOut:
    return _to_out(db, user_service.get_or_404(db, user_id))


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserAdminUpdate,
    admin: AdminUser,
    db: Session = Depends(get_db),
) -> UserOut:
    target = user_service.get_or_404(db, user_id)
    user_service.update_user(
        db,
        target,
        admin,
        display_name=body.display_name,
        role=body.role,
        is_active=body.is_active,
    )
    db.commit()
    db.refresh(target)
    return _to_out(db, target)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, admin: AdminUser, db: Session = Depends(get_db)) -> Response:
    target = user_service.get_or_404(db, user_id)
    user_service.delete_user(db, target, admin)
    db.commit()
    logger.info("Admin %s deleted user %s", admin.email, target.email)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{user_id}/grants", response_model=UserOut)
def set_grants(
    user_id: int,
    body: GrantsUpdate,
    admin: AdminUser,
    db: Session = Depends(get_db),
) -> UserOut:
    """Replace a user's server access with exactly the ids given."""
    target = user_service.get_or_404(db, user_id)
    user_service.replace_grants(db, target, body.server_ids, admin)
    db.commit()
    db.refresh(target)
    return _to_out(db, target)


@router.post("/{user_id}/reset-password", response_model=InviteLinkOut)
def admin_reset_password(
    user_id: int,
    admin: AdminUser,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> InviteLinkOut:
    target = user_service.get_or_404(db, user_id)
    raw, _ = user_service.issue_token(
        db, target, TOKEN_PURPOSE_RESET, requested_ip=client_ip(request)
    )
    db.commit()

    cfg = mail_config_store.load_mail_config(db)
    link = cfg.link(f"/reset/{raw}")
    emailed = False
    if link and cfg.enabled:
        background.add_task(
            mailer.send_password_reset,
            cfg,
            target.email,
            link,
            get_settings().reset_token_ttl_minutes,
        )
        emailed = True

    logger.info("Admin %s issued a password reset for %s", admin.email, target.email)
    return InviteLinkOut(
        user=_to_out(db, target),
        invite_url="" if emailed else link,
        emailed=emailed,
    )


@router.delete("/{user_id}/totp", response_model=UserOut)
def clear_totp(user_id: int, admin: AdminUser, db: Session = Depends(get_db)) -> UserOut:
    """Force-disable a user's 2FA, e.g. after they lose their phone."""
    target = user_service.get_or_404(db, user_id)
    totp_service.clear_totp(target)
    db.commit()
    db.refresh(target)
    logger.info("Admin %s cleared 2FA for %s", admin.email, target.email)
    return _to_out(db, target)


@router.post("/{user_id}/logout-everywhere", response_model=UserOut)
def force_logout(user_id: int, admin: AdminUser, db: Session = Depends(get_db)) -> UserOut:
    target = user_service.get_or_404(db, user_id)
    target.token_version += 1
    db.commit()
    db.refresh(target)
    logger.info("Admin %s revoked all sessions for %s", admin.email, target.email)
    return _to_out(db, target)


@router.post("/{user_id}/unlock", response_model=UserOut)
def unlock_user(user_id: int, admin: AdminUser, db: Session = Depends(get_db)) -> UserOut:
    """Clear a temporary lockout from failed sign-in attempts."""
    target = user_service.get_or_404(db, user_id)
    user_service.unlock_user(target)
    # Drop in-process email counters so a fresh attempt is not immediately 429'd.
    email = user_service.normalize_email(target.email)
    rate_limit.reset(f"login:email:{email}")
    rate_limit.reset(f"forgot:email:{email}")
    rate_limit.reset(f"totp:email:{email}")
    db.commit()
    db.refresh(target)
    logger.info("Admin %s unlocked user %s", admin.email, target.email)
    return _to_out(db, target)


# Mail configuration and the test-send button live in api/mail.py.

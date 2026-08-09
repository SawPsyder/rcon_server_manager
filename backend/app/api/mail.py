"""Mail configuration and delivery testing. Admin-only."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import AdminUser
from app.schemas import MailSettingsOut, MailSettingsUpdate, TestEmailRequest
from app.services import mail_settings as mail_config_store
from app.services import mailer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mail", tags=["mail"])


def _to_out(db: Session) -> MailSettingsOut:
    cfg = mail_config_store.load_mail_config(db)
    return MailSettingsOut(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        has_password=mail_config_store.has_stored_password(db),
        starttls=cfg.starttls,
        ssl=cfg.ssl,
        from_address=cfg.from_address,
        from_name=cfg.from_name,
        base_url=cfg.base_url,
        enabled=cfg.enabled,
        configured=mail_config_store.is_configured(db),
    )


@router.get("", response_model=MailSettingsOut)
def get_mail_settings(_admin: AdminUser, db: Session = Depends(get_db)) -> MailSettingsOut:
    return _to_out(db)


@router.put("", response_model=MailSettingsOut)
def update_mail_settings(
    body: MailSettingsUpdate,
    admin: AdminUser,
    db: Session = Depends(get_db),
) -> MailSettingsOut:
    if body.starttls and body.ssl:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "STARTTLS and implicit TLS are mutually exclusive. Use implicit TLS "
                "on port 465, or STARTTLS on 587."
            ),
        )

    mail_config_store.save_mail_config(
        db,
        host=body.host,
        port=body.port,
        user=body.user,
        password=body.password,
        starttls=body.starttls,
        ssl=body.ssl,
        from_address=body.from_address,
        from_name=body.from_name,
        base_url=body.base_url,
    )
    db.commit()
    logger.info("Mail settings updated by %s", admin.email)
    return _to_out(db)


@router.post("/test", status_code=status.HTTP_204_NO_CONTENT)
def send_test_email(
    body: TestEmailRequest,
    _admin: AdminUser,
    db: Session = Depends(get_db),
) -> Response:
    """Send a message now and report why it failed, if it did.

    Deliberately synchronous: the point of the button is to surface the SMTP
    error, which a background task would only write to the log.
    """
    cfg = mail_config_store.load_mail_config(db)
    problem = mailer.describe_failure(cfg, body.to_address)
    if problem:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=problem)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

"""Authentication: login, 2FA, bootstrap claim, password reset, self-service."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import CurrentUser, client_ip, get_current_user, require_turnstile
from app.models import ROLE_ADMIN, TOKEN_PURPOSE_RESET, AdminAuth, Setting, User, utcnow
from app.schemas import (
    AuthStatus,
    BootstrapClaimRequest,
    BootstrapStatus,
    ChangePasswordRequest,
    CurrentUserOut,
    ForgotPasswordRequest,
    LoginRequest,
    PublicConfig,
    ResetPasswordRequest,
    ResetPasswordResult,
    ResetTokenCheck,
    ResetTokenStatus,
    TotpConfirmOut,
    TotpConfirmRequest,
    TotpDisableRequest,
    TotpLoginRequest,
    TotpSetupOut,
    TotpSetupRequest,
    UserSelfUpdate,
)
from app.security import (
    MFA_TOKEN_MAX_AGE,
    create_mfa_token,
    create_session_token,
    hash_password,
    load_mfa_payload,
    verify_password,
)
from app.services import mail_settings as mail_config_store
from app.services import mailer, rate_limit
from app.services import totp as totp_service
from app.services import users as user_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

MFA_COOKIE = "ssm_mfa"

# Constant-cost comparison target for unknown emails. Without it, "no such user"
# returns before bcrypt runs and the timing difference enumerates accounts.
_DUMMY_HASH = hash_password("not-a-real-password-placeholder")

# Per-IP ceilings (shared NAT friendly-ish). Email-keyed limits below stop
# distributed stuffing against one account.
LOGIN_IP_LIMIT = 20
LOGIN_EMAIL_LIMIT = 10
LOGIN_WINDOW = 300
CLAIM_LIMIT = 5
CLAIM_WINDOW = 900
TOTP_LIMIT = 8
TOTP_WINDOW = 300
FORGOT_IP_LIMIT = 10
FORGOT_EMAIL_LIMIT = 5
FORGOT_WINDOW = 900
RESET_CHECK_IP_LIMIT = 30
RESET_CHECK_WINDOW = 300

INVALID_CREDENTIALS = "Invalid email or password"
RATE_LIMITED = "Too many attempts. Please wait a few minutes and try again."


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _limit_key(bucket: str, *parts: str) -> str:
    return f"{bucket}:" + ":".join(parts)


def _limit(key: str, limit: int, window: int) -> str:
    if not rate_limit.check(key, limit, window):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=RATE_LIMITED,
        )
    return key


def _ip_limit(request: Request, bucket: str, limit: int, window: int) -> str:
    return _limit(_limit_key(bucket, "ip", client_ip(request) or "unknown"), limit, window)


def _email_limit(bucket: str, email: str, limit: int, window: int) -> str:
    # Normalized address so casing variants share one counter.
    return _limit(
        _limit_key(bucket, "email", user_service.normalize_email(email) or "empty"),
        limit,
        window,
    )


def _cookie_flags() -> dict:
    """Attributes that must match on set and delete or browsers keep the cookie."""
    settings = get_settings()
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": settings.session_https_only,
        "path": "/",
    }


def _set_session_cookie(response: Response, user: User) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.cookie_name,
        value=create_session_token(user.id, user.token_version),
        max_age=settings.session_max_age,
        **_cookie_flags(),
    )


def _set_mfa_cookie(response: Response, user: User) -> None:
    response.set_cookie(
        key=MFA_COOKIE,
        value=create_mfa_token(user.id, user.token_version),
        max_age=MFA_TOKEN_MAX_AGE,
        **_cookie_flags(),
    )


def _clear_mfa_cookie(response: Response) -> None:
    response.delete_cookie(MFA_COOKIE, **_cookie_flags())


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(get_settings().cookie_name, **_cookie_flags())


def user_out(db: Session, user: User) -> CurrentUserOut:
    return CurrentUserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_admin=user.is_admin,
        totp_enabled=user.totp_enabled,
        server_ids=[] if user.is_admin else user_service.grant_ids(db, user),
    )


def bootstrap_available(db: Session) -> bool:
    """True while ADMIN_PASSWORD can still be exchanged for the first admin account."""
    return (
        db.query(User.id).filter(User.role == ROLE_ADMIN, User.is_active.is_(True)).first()
        is None
    )


# --------------------------------------------------------------------------
# Public status / config
# --------------------------------------------------------------------------


@router.get("/config", response_model=PublicConfig)
def public_config(db: Session = Depends(get_db)) -> PublicConfig:
    """Runtime config for the login screen.

    The Turnstile site key is served here rather than baked in at build time:
    the SPA ships inside the backend image, so a VITE_ variable would hard-code
    one deployment's widget into the published image and every operator would
    have to rebuild the frontend to use their own.
    """
    settings = get_settings()
    return PublicConfig(
        turnstile_enabled=settings.turnstile_enabled,
        turnstile_site_key=settings.turnstile_site_key if settings.turnstile_enabled else "",
        smtp_enabled=mail_config_store.load_mail_config(db).enabled,
        bootstrap_available=bootstrap_available(db),
    )


@router.get("/bootstrap", response_model=BootstrapStatus)
def bootstrap_status(db: Session = Depends(get_db)) -> BootstrapStatus:
    return BootstrapStatus(available=bootstrap_available(db))


@router.get("/status", response_model=AuthStatus)
def auth_status(request: Request, db: Session = Depends(get_db)) -> AuthStatus:
    try:
        user = get_current_user(request, db)
    except HTTPException:
        return AuthStatus(authenticated=False)
    return AuthStatus(authenticated=True, user=user_out(db, user))


@router.get("/me", response_model=AuthStatus)
def me(user: CurrentUser, db: Session = Depends(get_db)) -> AuthStatus:
    return AuthStatus(authenticated=True, user=user_out(db, user))


@router.patch("/me", response_model=AuthStatus)
def update_me(
    body: UserSelfUpdate,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> AuthStatus:
    # UserSelfUpdate carries display_name only - role and is_active are not
    # fields on it, so a crafted body cannot escalate.
    user.display_name = body.display_name.strip()
    db.commit()
    db.refresh(user)
    return AuthStatus(authenticated=True, user=user_out(db, user))


# --------------------------------------------------------------------------
# Bootstrap claim
# --------------------------------------------------------------------------


@router.post("/bootstrap-claim", response_model=AuthStatus)
def bootstrap_claim(
    body: BootstrapClaimRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthStatus:
    """Exchange ADMIN_PASSWORD for the first admin account.

    404 rather than 403 once an admin exists: a closed endpoint should not
    advertise that it was ever open.

    Concurrent claims are serialized by locking the AdminAuth row (FOR UPDATE)
    before re-checking availability, so two workers cannot both mint an admin
    under READ COMMITTED.
    """
    _ip_limit(request, "claim", CLAIM_LIMIT, CLAIM_WINDOW)
    require_turnstile(request, body.turnstile_token)

    # Lock the bootstrap credential so only one claim transaction proceeds.
    # On SQLite this upgrades to a write lock; on Postgres it blocks the peer.
    row = db.query(AdminAuth).with_for_update().first()
    if row is None or not bootstrap_available(db):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not verify_password(body.admin_password, row.password_hash):
        logger.warning("Failed admin claim attempt from %s", client_ip(request))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid administrator password"
        )

    user = user_service.create_user(
        db,
        email=body.email,
        display_name=body.display_name,
        role=ROLE_ADMIN,
        password=body.password,
    )

    # Belt-and-suspenders under the lock: never leave more than one bootstrap admin.
    if user_service.active_admin_count(db) > 1:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An administrator was created by someone else. Please sign in instead.",
        )

    if db.query(Setting).filter(Setting.key == "auth.bootstrap_claimed_at").first() is None:
        db.add(Setting(key="auth.bootstrap_claimed_at", value=utcnow().isoformat()))

    db.commit()
    db.refresh(user)
    logger.info("Admin account claimed by %s", user.email)

    _set_session_cookie(response, user)
    return AuthStatus(authenticated=True, user=user_out(db, user))


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------


@router.post("/login", response_model=AuthStatus)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthStatus:
    ip_key = _ip_limit(request, "login", LOGIN_IP_LIMIT, LOGIN_WINDOW)
    email_key = _email_limit("login", body.email, LOGIN_EMAIL_LIMIT, LOGIN_WINDOW)
    require_turnstile(request, body.turnstile_token)

    user = user_service.get_by_email(db, body.email)

    # Every failure below returns the same message, and the unknown-email path
    # still pays the bcrypt cost, so neither the response nor its timing
    # distinguishes "no such account" from "wrong password" - except temporary
    # lockout, which is intentional so the real owner knows why they cannot in.
    if user is None:
        verify_password(body.password, _DUMMY_HASH)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS)

    if not user.password_hash or not user.is_active:
        verify_password(body.password, _DUMMY_HASH)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS)

    if user_service.is_temporarily_locked(user):
        # Still burn a bcrypt compare so lockout timing matches a wrong password.
        verify_password(body.password, _DUMMY_HASH)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=user_service.lockout_detail(user),
        )

    if not verify_password(body.password, user.password_hash):
        locked = user_service.record_failed_login(user)
        db.commit()
        if locked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=user_service.lockout_detail(user),
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS)

    rate_limit.reset(ip_key)
    rate_limit.reset(email_key)

    if user.totp_enabled:
        # No session cookie yet. The partial token is signed with a different
        # salt, so it cannot be replayed as a session.
        _set_mfa_cookie(response, user)
        return AuthStatus(authenticated=False, mfa_required=True)

    user_service.clear_login_failures(user)
    user.last_login_at = utcnow()
    db.commit()
    db.refresh(user)

    _set_session_cookie(response, user)
    return AuthStatus(authenticated=True, user=user_out(db, user))


@router.post("/login/totp", response_model=AuthStatus)
def login_totp(
    body: TotpLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthStatus:
    """Second login step.

    This endpoint carries no Turnstile token by design - the one from step 1 was
    redeemed there and Turnstile tokens are single-use. The short-lived MFA
    cookie plus per-IP attempt limit and the account lockout counter bound the
    exposure instead.
    """
    _ip_limit(request, "totp", TOTP_LIMIT, TOTP_WINDOW)

    expired = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Your sign-in attempt expired. Please start again.",
    )

    data = load_mfa_payload(request.cookies.get(MFA_COOKIE, ""))
    if not data:
        raise expired

    user = db.get(User, int(data.get("uid", 0)))
    if user is None or not user.is_active or not user.totp_enabled:
        _clear_mfa_cookie(response)
        raise expired
    if user.token_version != int(data.get("tv", -1)):
        _clear_mfa_cookie(response)
        raise expired

    if user_service.is_temporarily_locked(user):
        _clear_mfa_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=user_service.lockout_detail(user),
        )

    secret = totp_service.get_secret(user)
    counter = totp_service.verify_code(secret, body.code, user.totp_last_counter)

    if counter is None:
        if not totp_service.consume_recovery_code(user, body.code):
            locked = user_service.record_failed_login(user)
            db.commit()
            if locked:
                _clear_mfa_cookie(response)
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=user_service.lockout_detail(user),
                )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication code"
            )
    else:
        user.totp_last_counter = counter

    user_service.clear_login_failures(user)
    user.last_login_at = utcnow()
    db.commit()
    db.refresh(user)

    _clear_mfa_cookie(response)
    _set_session_cookie(response, user)
    return AuthStatus(authenticated=True, user=user_out(db, user))


@router.post("/logout", response_model=AuthStatus)
def logout(response: Response) -> AuthStatus:
    _clear_session_cookie(response)
    _clear_mfa_cookie(response)
    return AuthStatus(authenticated=False)


@router.post("/logout-everywhere", response_model=AuthStatus)
def logout_everywhere(
    user: CurrentUser,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthStatus:
    """Invalidate every session for this user, including the current one."""
    user.token_version += 1
    db.commit()
    _clear_session_cookie(response)
    _clear_mfa_cookie(response)
    return AuthStatus(authenticated=False)


# --------------------------------------------------------------------------
# Password management
# --------------------------------------------------------------------------


@router.post("/change-password", response_model=AuthStatus)
def change_password(
    body: ChangePasswordRequest,
    user: CurrentUser,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthStatus:
    if not user.password_hash or not verify_password(body.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is wrong"
        )

    user_service.set_password(db, user, body.new_password)
    db.commit()
    db.refresh(user)

    # set_password bumped token_version, so the caller's own cookie is now
    # stale. Re-issue it rather than logging them out of the tab they are in.
    _set_session_cookie(response, user)
    return AuthStatus(authenticated=True, user=user_out(db, user))


@router.post("/forgot-password", status_code=status.HTTP_204_NO_CONTENT)
def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> Response:
    """Always returns 204, whether or not the address exists."""
    _ip_limit(request, "forgot", FORGOT_IP_LIMIT, FORGOT_WINDOW)
    _email_limit("forgot", body.email, FORGOT_EMAIL_LIMIT, FORGOT_WINDOW)
    require_turnstile(request, body.turnstile_token)

    user = user_service.get_by_email(db, body.email)
    if user is not None and user.is_active:
        raw, _ = user_service.issue_token(
            db, user, TOKEN_PURPOSE_RESET, requested_ip=client_ip(request)
        )
        db.commit()

        cfg = mail_config_store.load_mail_config(db)
        link = cfg.link(f"/reset/{raw}")
        if link and cfg.enabled:
            # Off the request thread: a slow relay would otherwise hold the
            # response open for the SMTP handshake.
            background.add_task(
                mailer.send_password_reset,
                cfg,
                user.email,
                link,
                get_settings().reset_token_ttl_minutes,
            )
        else:
            logger.warning(
                "Password reset requested for %s but mail is not configured; "
                "an administrator must issue the link from the Users page",
                user.email,
            )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/reset-password", response_model=ResetPasswordResult)
def reset_password(
    body: ResetPasswordRequest,
    db: Session = Depends(get_db),
) -> ResetPasswordResult:
    """Redeem a reset or invite link and set a password.

    Does not issue a session: the user must complete the normal login path
    (including TOTP when enrolled). Auto-login here would bypass MFA.
    """
    user = user_service.consume_token(db, body.token)
    user_service.set_password(db, user, body.password)
    db.commit()
    return ResetPasswordResult(ok=True)


@router.post("/reset-token/check", response_model=ResetTokenStatus)
def check_reset_token(
    body: ResetTokenCheck, request: Request, db: Session = Depends(get_db)
) -> ResetTokenStatus:
    """Lets the reset page show 'this link expired' before asking for a password.

    POST + body rather than a path parameter: a GET /reset-token/<secret> would
    put the live token in access logs, browser history, and Referer headers.
    """
    _ip_limit(request, "reset-check", RESET_CHECK_IP_LIMIT, RESET_CHECK_WINDOW)
    return ResetTokenStatus(valid=user_service.peek_token(db, body.token) is not None)


# --------------------------------------------------------------------------
# TOTP self-service
# --------------------------------------------------------------------------


@router.post("/totp/setup", response_model=TotpSetupOut)
def totp_setup(
    body: TotpSetupRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> TotpSetupOut:
    """Generate a secret. 2FA is not active until /totp/confirm succeeds."""
    if not user.password_hash or not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password is wrong")
    if user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Two-factor authentication is already enabled",
        )

    secret = totp_service.generate_secret()
    totp_service.set_secret(user, secret)
    user.totp_last_counter = None
    db.commit()

    return TotpSetupOut(secret=secret, otpauth_uri=totp_service.provisioning_uri(secret, user.email))


@router.post("/totp/confirm", response_model=TotpConfirmOut)
def totp_confirm(
    body: TotpConfirmRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> TotpConfirmOut:
    secret = totp_service.get_secret(user)
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Start the setup step first"
        )

    counter = totp_service.verify_code(secret, body.code, user.totp_last_counter)
    if counter is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="That code did not match"
        )

    codes = totp_service.generate_recovery_codes()
    totp_service.store_recovery_codes(user, codes)
    user.totp_enabled = True
    user.totp_confirmed_at = utcnow()
    user.totp_last_counter = counter
    db.commit()

    # Shown once. Only hashes are stored.
    return TotpConfirmOut(recovery_codes=codes)


@router.post("/totp/disable", response_model=AuthStatus)
def totp_disable(
    body: TotpDisableRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> AuthStatus:
    if not user.password_hash or not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password is wrong")

    totp_service.clear_totp(user)
    db.commit()
    db.refresh(user)
    return AuthStatus(authenticated=True, user=user_out(db, user))

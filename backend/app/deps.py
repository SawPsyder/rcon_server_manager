"""FastAPI dependencies: authentication, per-server authorization, Turnstile.

Authorization is applied at the router level in main.py rather than per route,
so the whole policy is visible in one place and new routes are covered by
default. See tests/test_authz_coverage.py, which asserts that mechanically.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import ROLE_ADMIN, Server, ServerGrant, User
from app.security import load_session_payload
from app.services.turnstile import verify_turnstile


def _unauthenticated() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


def client_ip(request: Request) -> str:
    """Best-effort caller address for rate limiting and Turnstile remoteip.

    When CLIENT_IP_HEADER is set, that header's value is used (first hop of a
    comma-separated list, for headers such as X-Forwarded-For). Empty config
    means the app is not behind a proxy: the TCP peer is the client.

    Only set the header when every request reaches the app through a proxy that
    overwrites it. If clients can open a TCP connection directly, they can spoof
    the header and walk through every per-IP limit.
    """
    header_name = get_settings().resolved_client_ip_header
    if header_name:
        raw = (request.headers.get(header_name) or "").strip()
        if raw:
            return raw.split(",")[0].strip()
    return request.client.host if request.client else ""


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(get_settings().cookie_name)
    if not token:
        raise _unauthenticated()

    # Legacy {"sub": "admin"} cookies return None here: they carry no user id,
    # so honouring one would mean unattributed admin with nothing to revoke.
    data = load_session_payload(token)
    if not data:
        raise _unauthenticated()

    user = db.get(User, int(data.get("uid", 0)))
    if user is None or not user.is_active:
        raise _unauthenticated()
    if user.token_version != int(data.get("tv", -1)):
        raise _unauthenticated()

    request.state.user = user
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required"
        )
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def user_can_access_server(db: Session, user: User, server_id: int) -> bool:
    if user.role == ROLE_ADMIN:
        return True
    return (
        db.query(ServerGrant.id)
        .filter(ServerGrant.user_id == user.id, ServerGrant.server_id == server_id)
        .first()
        is not None
    )


def granted_server_ids(db: Session, user: User) -> set[int] | None:
    """Servers this user may see. None means unrestricted (admin)."""
    if user.role == ROLE_ADMIN:
        return None
    return {
        sid
        for (sid,) in db.query(ServerGrant.server_id).filter(ServerGrant.user_id == user.id)
    }


def require_server_scope(
    request: Request,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> User:
    """Router-level guard.

    Authenticates every request, and additionally requires a grant when the
    matched route declares a ``server_id`` path parameter. Routes in the same
    router without one (GET /api/servers, /api/servers/types,
    POST /api/travel/preview) are authenticated only.
    """
    raw = request.path_params.get("server_id")
    if raw is None:
        return user
    try:
        server_id = int(raw)
    except (TypeError, ValueError):
        # Let the route's own int coercion produce the 422.
        return user

    if not user_can_access_server(db, user, server_id):
        # 404 rather than 403: a 403 would confirm which server ids exist. This
        # matches get_server_or_404, so "no such server" and "not yours" are
        # indistinguishable from outside.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    return user


def get_scoped_server(
    server_id: int,
    user: CurrentUser,
    db: Session = Depends(get_db),
) -> Server:
    """Grant-checked server lookup, for routes that want the object directly."""
    if not user_can_access_server(db, user, server_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    server = db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    return server


ScopedServer = Annotated[Server, Depends(get_scoped_server)]


def require_turnstile(request: Request, token: str) -> None:
    """Redeem a Turnstile token or reject the request.

    No-op when Turnstile is not configured. The failure message is deliberately
    generic - Cloudflare's error codes go to the log, not to the caller.
    """
    if not verify_turnstile(token, client_ip(request)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verification failed. Please try again.",
        )


AdminDep = Depends(require_admin)
DbDep = Depends(get_db)

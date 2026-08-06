from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import require_admin
from app.models import AdminAuth
from app.schemas import AuthStatus, ChangePasswordRequest, LoginRequest
from app.security import create_session_token, hash_password, load_session_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/status", response_model=AuthStatus)
def auth_status(request: Request) -> AuthStatus:
    settings = get_settings()
    token = request.cookies.get(settings.cookie_name)
    if not token:
        return AuthStatus(authenticated=False)
    return AuthStatus(authenticated=load_session_token(token) == "admin")


@router.get("/me", response_model=AuthStatus)
def me(_admin: str = Depends(require_admin)) -> AuthStatus:
    return AuthStatus(authenticated=True)


@router.post("/login", response_model=AuthStatus)
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)) -> AuthStatus:
    settings = get_settings()
    row = db.query(AdminAuth).first()
    if row is None or not verify_password(body.password, row.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")

    token = create_session_token("admin")
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.session_https_only,
        max_age=settings.session_max_age,
        path="/",
    )
    return AuthStatus(authenticated=True)


@router.post("/logout", response_model=AuthStatus)
def logout(response: Response) -> AuthStatus:
    settings = get_settings()
    response.delete_cookie(settings.cookie_name, path="/")
    return AuthStatus(authenticated=False)


@router.post("/change-password", response_model=AuthStatus)
def change_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
) -> AuthStatus:
    row = db.query(AdminAuth).first()
    if row is None or not verify_password(body.current_password, row.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is wrong")
    row.password_hash = hash_password(body.new_password)
    db.commit()
    return AuthStatus(authenticated=True)

import base64
import hashlib
import logging
from pathlib import Path

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def _serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(settings.secret_key, salt="ssm-session")


def create_session_token(subject: str = "admin") -> str:
    return _serializer().dumps({"sub": subject})


def load_session_token(token: str) -> str | None:
    settings = get_settings()
    try:
        data = _serializer().loads(token, max_age=settings.session_max_age)
        return data.get("sub")
    except (BadSignature, SignatureExpired, TypeError, AttributeError):
        return None


def _fernet_candidates() -> list[bytes]:
    """
    Build possible Fernet keys (current + legacy) so restarts / env changes
    still decrypt when possible. Prefer a stable key file under DATA_DIR.
    """
    settings = get_settings()
    keys: list[bytes] = []

    def _add(key_str: str) -> None:
        key_str = key_str.strip()
        if not key_str:
            return
        raw = key_str.encode("utf-8")
        if raw not in keys:
            keys.append(raw)

    # 1) Explicit env key
    if settings.encryption_key.strip():
        _add(settings.encryption_key)

    # 2) Persistent key file in data dir (stable across SECRET_KEY changes)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    key_path = Path(settings.data_dir) / ".encryption_key"
    if key_path.is_file():
        _add(key_path.read_text(encoding="utf-8"))
    else:
        # Create once if no env key configured
        if not settings.encryption_key.strip():
            generated = Fernet.generate_key().decode("ascii")
            key_path.write_text(generated, encoding="utf-8")
            try:
                key_path.chmod(0o600)
            except OSError:
                pass
            _add(generated)

    # 3) Legacy derive-from-SECRET_KEY (older builds)
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    _add(base64.urlsafe_b64encode(digest).decode("ascii"))

    return keys


def _fernet() -> Fernet:
    candidates = _fernet_candidates()
    if not candidates:
        # Absolute fallback
        return Fernet(Fernet.generate_key())
    return Fernet(candidates[0])


def encrypt_secret(plaintext: str) -> str:
    if plaintext == "":
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    last_err: Exception | None = None
    for key in _fernet_candidates():
        try:
            return Fernet(key).decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            last_err = exc
            continue
    logger.warning("Failed to decrypt secret (re-save RCON password in Servers): %s", last_err)
    return ""

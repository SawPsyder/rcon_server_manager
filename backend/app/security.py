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


def _is_valid_fernet_key(key: bytes) -> bool:
    """Fernet keys must be 32 url-safe base64-encoded bytes."""
    try:
        Fernet(key)
        return True
    except (ValueError, TypeError, Exception):  # noqa: BLE001
        return False


def _coerce_fernet_key(key_str: str) -> bytes | None:
    """
    Accept a Fernet key string. Invalid values are rejected (with a log),
    so a bad ENCRYPTION_KEY env does not crash encrypt/decrypt.
    """
    key_str = (key_str or "").strip().strip('"').strip("'")
    if not key_str:
        return None
    raw = key_str.encode("utf-8")
    if _is_valid_fernet_key(raw):
        return raw
    # Common mistake: user put a random password. Derive a stable Fernet key
    # from it so deployments still work (document proper generate_key in README).
    derived = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    if _is_valid_fernet_key(derived):
        logger.warning(
            "ENCRYPTION_KEY is not a Fernet key (expected output of "
            "Fernet.generate_key()). Deriving a key via SHA-256; re-save "
            "RCON passwords if you later switch to a proper Fernet key."
        )
        return derived
    return None


def _fernet_candidates() -> list[bytes]:
    """
    Build possible Fernet keys (current + legacy) so restarts / env changes
    still decrypt when possible. Prefer a stable key file under DATA_DIR.
    """
    settings = get_settings()
    keys: list[bytes] = []

    def _add(key_str: str) -> None:
        key = _coerce_fernet_key(key_str)
        if key is not None and key not in keys:
            keys.append(key)

    # 1) Explicit env key
    if settings.encryption_key.strip():
        _add(settings.encryption_key)

    # 2) Persistent key file in data dir (stable across SECRET_KEY changes)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    key_path = Path(settings.data_dir) / ".encryption_key"
    if key_path.is_file():
        try:
            _add(key_path.read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("Could not read encryption key file: %s", exc)
    else:
        # Create once if we still have no usable key from env
        if not keys:
            generated = Fernet.generate_key().decode("ascii")
            try:
                key_path.write_text(generated, encoding="utf-8")
                try:
                    key_path.chmod(0o600)
                except OSError:
                    pass
                _add(generated)
                logger.info("Generated new Fernet key at %s", key_path)
            except OSError as exc:
                logger.warning("Could not write encryption key file: %s", exc)

    # 3) Legacy derive-from-SECRET_KEY (older builds) — always valid
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    legacy = base64.urlsafe_b64encode(digest)
    if legacy not in keys:
        keys.append(legacy)

    return keys


def _fernet() -> Fernet:
    candidates = _fernet_candidates()
    if not candidates:
        # Absolute fallback (ephemeral — avoid if possible)
        logger.error("No Fernet key material available; using ephemeral key")
        return Fernet(Fernet.generate_key())
    return Fernet(candidates[0])


def encrypt_secret(plaintext: str) -> str:
    if plaintext == "":
        return ""
    try:
        return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.exception("encrypt_secret failed")
        raise RuntimeError(
            "Failed to encrypt RCON password. Set ENCRYPTION_KEY to a valid Fernet key "
            '(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") '
            "or leave it empty to auto-generate under DATA_DIR."
        ) from exc


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

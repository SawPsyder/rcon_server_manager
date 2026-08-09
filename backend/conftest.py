"""Make ``app`` importable and keep tests off the developer's real data dir."""

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Never touch a real database or data dir from a test run
_TMP = Path(tempfile.gettempdir()) / "ssm-tests"
_TMP.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("DATA_DIR", str(_TMP))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(_TMP / 'test.db').as_posix()}")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

# Optional integrations must be OFF by default in tests. setdefault is no use
# here - it cannot override an already-exported value, and an exported SMTP or
# Turnstile var would make the suite talk to a live relay or to Cloudflare.
# Clear them outright. (A repo-local .env is still read by pydantic-settings;
# tests asserting on these should monkeypatch and call get_settings.cache_clear.)
for _optional in (
    "SMTP_HOST",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "TURNSTILE_SITE_KEY",
    "TURNSTILE_SECRET",
    "PUBLIC_BASE_URL",
    "CLIENT_IP_HEADER",
    "TRUSTED_PROXY_IPS",  # legacy name; keep cleared so old shells cannot re-enable it
):
    os.environ.pop(_optional, None)

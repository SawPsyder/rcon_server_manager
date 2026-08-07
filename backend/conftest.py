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

"""Per-server JSON options stored in ``servers.options_json``.

Connection extras that only some server types need (TLS verification, pinned
certificate fingerprints, ...) live here instead of adding a column per game.
"""

from __future__ import annotations

import json
from typing import Any, Mapping


def load_options(server: Any) -> dict[str, Any]:
    """Parse ``options_json``; always returns a dict (never raises)."""
    raw = getattr(server, "options_json", "") or ""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_options(server: Any, options: Mapping[str, Any]) -> None:
    server.options_json = json.dumps(dict(options), separators=(",", ":"))


def merge_options(server: Any, updates: Mapping[str, Any]) -> dict[str, Any]:
    """Apply a partial update and persist it on the row. Returns the merged dict."""
    options = load_options(server)
    options.update(updates)
    save_options(server, options)
    return options


def coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def option_bool(server: Any, key: str, default: bool = False) -> bool:
    return coerce_bool(load_options(server).get(key), default)


def option_str(server: Any, key: str, default: str = "") -> str:
    return coerce_str(load_options(server).get(key), default)

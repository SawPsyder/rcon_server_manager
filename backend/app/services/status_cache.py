"""Persist last-known server status fields for instant UI display."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models import Server


def update_server_status_cache(
    server: Server,
    *,
    hostname: str | None = None,
    map_name: str | None = None,
    lighting: str | None = None,
    gamemode: str | None = None,
    coop_or_versus: str | None = None,
    players: int | None = None,
    max_players: int | None = None,
    online: bool | None = None,
    now: datetime | None = None,
) -> bool:
    """
    Update cached snapshot fields when new non-empty values arrive.
    Returns True if any field changed.
    """
    changed = False
    ts = now or datetime.now(timezone.utc)

    def _set_str(attr: str, value: str | None) -> None:
        nonlocal changed
        if value is None:
            return
        text = str(value).strip()
        if not text:
            return
        if getattr(server, attr) != text:
            setattr(server, attr, text)
            changed = True

    def _set_int(attr: str, value: int | None) -> None:
        nonlocal changed
        if value is None:
            return
        try:
            iv = int(value)
        except (TypeError, ValueError):
            return
        if getattr(server, attr) != iv:
            setattr(server, attr, iv)
            changed = True

    _set_str("last_hostname", hostname)
    _set_str("last_map", map_name)
    _set_str("last_lighting", lighting)
    _set_str("last_gamemode", gamemode)
    _set_str("last_coop_or_versus", coop_or_versus)
    _set_int("last_players", players)
    _set_int("last_max_players", max_players)

    if online is not None and server.last_online != online:
        server.last_online = bool(online)
        changed = True

    if changed or server.last_status_at is None:
        server.last_status_at = ts
        changed = True

    return changed


def cache_dict_from_server(server: Server) -> dict[str, Any]:
    return {
        "hostname": server.last_hostname,
        "map": server.last_map,
        "lighting": server.last_lighting,
        "gamemode": server.last_gamemode,
        "coop_or_versus": server.last_coop_or_versus,
        "players": server.last_players,
        "max_players": server.last_max_players,
        "online": server.last_online,
        "last_status_at": server.last_status_at,
    }

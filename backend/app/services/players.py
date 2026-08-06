"""Player sampling dispatcher — routes to the server type adapter."""

from __future__ import annotations

from typing import Any

from app.server_types import DEFAULT_SERVER_TYPE, get_adapter


def sample_player_count(
    host: str,
    query_port: int,
    rcon_port: int | None = None,
    rcon_password: str = "",
    timeout: float = 3.0,
    server_type: str | None = None,
) -> dict[str, Any]:
    adapter = get_adapter(server_type or DEFAULT_SERVER_TYPE)
    return adapter.sample_players(
        host=host,
        query_port=query_port,
        rcon_port=rcon_port,
        rcon_password=rcon_password,
        timeout=timeout,
    )

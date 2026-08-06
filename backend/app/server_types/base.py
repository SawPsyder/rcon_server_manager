"""Server type adapter contract (game-agnostic core + pluggable games)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ServerFeatures:
    map_travel: bool = False
    structured_player_list: bool = False
    kick_ban: bool = False
    admin_say: bool = False
    a2s_query: bool = True

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass(frozen=True)
class ServerTypeInfo:
    id: str
    label: str
    default_query_port: int
    default_rcon_port: int
    features: ServerFeatures

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "default_query_port": self.default_query_port,
            "default_rcon_port": self.default_rcon_port,
            "features": self.features.to_dict(),
        }


@runtime_checkable
class ServerTypeAdapter(Protocol):
    info: ServerTypeInfo
    allowed_rcon_prefixes: tuple[str, ...]

    def is_command_allowed(self, command: str) -> bool:
        ...

    def sample_players(
        self,
        host: str,
        query_port: int,
        rcon_port: int | None = None,
        rcon_password: str = "",
        timeout: float = 3.0,
    ) -> dict[str, Any]:
        ...

    def player_count_hint(self, *, has_rcon_password: bool, snap: dict[str, Any]) -> str | None:
        """Optional status warning when player count may be wrong."""
        ...

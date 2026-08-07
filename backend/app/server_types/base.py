"""Server type adapter contract (game-agnostic core + pluggable games).

A game is added by writing one module in this package that subclasses
:class:`DefaultAdapter` (or satisfies :class:`ServerTypeAdapter` structurally)
and registering it in ``app.server_types.__init__``.

:class:`DefaultAdapter` carries the Source-engine behaviour the app started
with: A2S for status and Source RCON for commands. Games that speak something
else — e.g. Satisfactory's HTTPS API — override the two transport hooks
(:meth:`~DefaultAdapter.query_status`, :meth:`~DefaultAdapter.execute_command`)
and nothing in the generic layer needs to know the difference.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Mapping, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass(frozen=True)
class ServerFeatures:
    map_travel: bool = False
    structured_player_list: bool = False
    kick_ban: bool = False
    admin_say: bool = False
    a2s_query: bool = True
    # Game-specific admin panel available (see api/<game>.py)
    admin_api: bool = False
    # Free-text command console makes sense for this game
    console: bool = True
    # sample_players() reports a tick rate, so the history chart has data
    tick_rate_history: bool = False

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass(frozen=True)
class QuickButton:
    """Hardcoded dashboard command shortcut for a server type."""

    label: str
    command: str

    def to_dict(self) -> dict[str, str]:
        return {"label": self.label, "command": self.command}


@dataclass(frozen=True)
class ServerTypeInfo:
    id: str
    label: str
    default_query_port: int
    default_rcon_port: int
    features: ServerFeatures
    quick_buttons: tuple[QuickButton, ...] = ()
    # UI label for the stored secret (RCON password / API token / ...)
    secret_label: str = "RCON password"
    # "query_rcon" → separate query + RCON ports; "single_port" → one API port
    endpoint_style: str = "query_rcon"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "default_query_port": self.default_query_port,
            "default_rcon_port": self.default_rcon_port,
            "features": self.features.to_dict(),
            "quick_buttons": [b.to_dict() for b in self.quick_buttons],
            "secret_label": self.secret_label,
            "endpoint_style": self.endpoint_style,
        }


@runtime_checkable
class ServerTypeAdapter(Protocol):
    """Structural contract the generic layer relies on."""

    info: ServerTypeInfo
    allowed_rcon_prefixes: tuple[str, ...]
    # Type-level default for the preferred gamemode setting ("" when N/A)
    default_preferred_gamemode: str
    # Pre-per-type settings key still honoured for this game (or None)
    legacy_preferred_gamemode_key: str | None

    def is_command_allowed(self, command: str) -> bool:
        ...

    def sample_players(
        self,
        host: str,
        query_port: int,
        rcon_port: int | None = None,
        rcon_password: str = "",
        timeout: float = 3.0,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def player_count_hint(self, *, has_rcon_password: bool, snap: dict[str, Any]) -> str | None:
        """Optional status warning when player count may be wrong."""
        ...

    def query_status(
        self,
        host: str,
        query_port: int,
        *,
        timeout: float = 2.0,
        rcon_port: int | None = None,
        secret: str = "",
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Identity / status snapshot in the shape ``services.query`` returns."""
        ...

    def execute_command(
        self,
        host: str,
        *,
        port: int,
        secret: str,
        command: str,
        timeout: float = 5.0,
        options: Mapping[str, Any] | None = None,
    ) -> str:
        """Run an admin command; raise a ``CommandError`` subclass on failure."""
        ...

    def invalidate_connections(self, host: str, port: int) -> None:
        """Drop pooled connections for an endpoint (server edited / deleted)."""
        ...

    def gamemode_labels(self) -> dict[str, str]:
        ...

    def map_gamemodes(self, map_row: Any) -> dict[str, str]:
        ...

    def map_lightings(self, map_row: Any) -> list[str]:
        ...

    def build_travel_command(
        self,
        *,
        map_name: str,
        scenario: str,
        lighting: str,
        gamemode_key: str,
    ) -> str:
        ...

    def parse_bans(self, text: str) -> list[dict[str, Any]]:
        ...

    def seed(self, db: Session) -> None:
        """Insert first-boot defaults for this type (maps, settings, ...)."""
        ...


class DefaultAdapter:
    """Source-engine defaults: A2S status queries and Source RCON commands.

    Transport imports are function-local so this module stays importable from
    ``app.services`` without a circular import at load time.
    """

    info: ServerTypeInfo
    allowed_rcon_prefixes: tuple[str, ...] = ()
    default_preferred_gamemode: str = ""
    legacy_preferred_gamemode_key: str | None = None

    # --- commands ----------------------------------------------------------

    def is_command_allowed(self, command: str) -> bool:
        from app.services.rcon import is_command_allowed

        return is_command_allowed(command, self.allowed_rcon_prefixes)

    def execute_command(
        self,
        host: str,
        *,
        port: int,
        secret: str,
        command: str,
        timeout: float = 5.0,
        options: Mapping[str, Any] | None = None,
    ) -> str:
        from app.services.rcon import run_rcon

        return run_rcon(
            host,
            port,
            secret,
            command,
            timeout=timeout,
            allowed_prefixes=self.allowed_rcon_prefixes or None,
        )

    def invalidate_connections(self, host: str, port: int) -> None:
        from app.services.rcon_pool import rcon_pool

        rcon_pool.invalidate_endpoint(host, port)

    # --- status ------------------------------------------------------------

    def query_status(
        self,
        host: str,
        query_port: int,
        *,
        timeout: float = 2.0,
        rcon_port: int | None = None,
        secret: str = "",
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from app.services.query import query_server_status

        return query_server_status(host, query_port, timeout=timeout)

    def sample_players(
        self,
        host: str,
        query_port: int,
        rcon_port: int | None = None,
        rcon_password: str = "",
        timeout: float = 3.0,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("Server type must implement sample_players()")

    def player_count_hint(self, *, has_rcon_password: bool, snap: dict[str, Any]) -> str | None:
        return None

    # --- maps / gamemodes (opt-in per game) --------------------------------

    def gamemode_labels(self) -> dict[str, str]:
        return {}

    def map_gamemodes(self, map_row: Any) -> dict[str, str]:
        return {}

    def map_lightings(self, map_row: Any) -> list[str]:
        return []

    def build_travel_command(
        self,
        *,
        map_name: str,
        scenario: str,
        lighting: str,
        gamemode_key: str,
    ) -> str:
        raise NotImplementedError(
            f"Server type '{getattr(self.info, 'id', '?')}' does not support map travel"
        )

    # --- moderation -------------------------------------------------------

    def parse_bans(self, text: str) -> list[dict[str, Any]]:
        return []

    # --- first-boot seeding ------------------------------------------------

    def seed(self, db: Session) -> None:
        return None

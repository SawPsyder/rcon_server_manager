"""Server type adapter contract (game-agnostic core + pluggable games).

A game is added by writing one module in this package that subclasses
:class:`DefaultAdapter` (or satisfies :class:`ServerTypeAdapter` structurally)
and registering it in ``app.server_types.__init__``.

:class:`DefaultAdapter` carries the Source-engine behaviour the app started
with: A2S for status and Source RCON for commands. Games that speak something
else - e.g. Satisfactory's HTTPS API - override the two transport hooks
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
    # The game reports a per-player score. Off for games where the column would
    # be filled with something that isn't one (Palworld reports a level).
    player_score: bool = True
    kick_ban: bool = False
    # Timed (duration-based) bans. Off for games like Palworld where every ban
    # is permanent and the transport has no duration parameter.
    timed_ban: bool = False
    # Permanent ban + unban exist. Off for games like Dune that can kick but
    # have no ban command at all. Defaults on so Sandstorm/Palworld stay as-is.
    perm_ban: bool = True
    # The transport can enumerate existing bans. Separate from kick_ban: Palworld
    # can ban but keeps its ban list in a file the REST API never exposes.
    ban_list: bool = False
    admin_say: bool = False
    a2s_query: bool = True
    # Game-specific admin panel available (see api/<game>.py)
    admin_api: bool = False
    # Free-text command console makes sense for this game
    console: bool = True
    # sample_players() reports a tick rate, so the history chart has data
    tick_rate_history: bool = False
    # HTTP or HTTPS both possible (reverse proxy), so offer the scheme toggle
    tls_optional: bool = False

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
    # Where the ban list comes from. "live" = ask the server (listbans);
    # "local" = derive from our own moderation history, because the game exposes
    # no way to enumerate bans (Palworld keeps them in a file on disk).
    ban_list_source: str = "live"
    # How the tick_rate series reads for this game (Source ticks vs server FPS)
    tick_rate_label: str = "Tick rate"
    tick_rate_unit: str = "tps"
    tick_rate_target: int = 30

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
            "ban_list_source": self.ban_list_source,
            "tick_rate_label": self.tick_rate_label,
            "tick_rate_unit": self.tick_rate_unit,
            "tick_rate_target": self.tick_rate_target,
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

    def build_say_command(self, message: str) -> str:
        ...

    def build_kick_command(self, *, player_name: str, net_id: str, reason: str) -> str:
        ...

    def build_ban_command(
        self, *, player_name: str, net_id: str, reason: str, minutes: int
    ) -> str:
        ...

    def build_permban_command(self, *, player_name: str, net_id: str, reason: str) -> str:
        ...

    def build_unban_command(self, net_id: str) -> str:
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
        """Snapshot of who is online.

        Besides ``player_list``, a snapshot should set ``roster_known`` to True
        whenever the roster was read successfully - **including when it came
        back empty**. Presence uses it to tell "everyone left" apart from "we
        could not ask", and only the former may close sessions.
        """
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

    # The generic moderation endpoints build a command string and hand it to
    # execute_command, exactly as map travel does. Games whose transport wants
    # different arguments (Palworld's REST API addresses players by user ID, not
    # display name) override these; the defaults are the Source RCON forms.

    def build_say_command(self, message: str) -> str:
        return f"say {message}"

    def build_kick_command(self, *, player_name: str, net_id: str, reason: str) -> str:
        return f'kick "{player_name}" "{reason}"'

    def build_ban_command(
        self, *, player_name: str, net_id: str, reason: str, minutes: int
    ) -> str:
        return f'ban "{player_name}" "{minutes}" "{reason}"'

    def build_permban_command(self, *, player_name: str, net_id: str, reason: str) -> str:
        return f'permban "{player_name}" "{reason}"'

    def build_unban_command(self, net_id: str) -> str:
        return f'unban "{net_id}"'

    def parse_bans(self, text: str) -> list[dict[str, Any]]:
        return []

    # --- first-boot seeding ------------------------------------------------

    def seed(self, db: Session) -> None:
        return None

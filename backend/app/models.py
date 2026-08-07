from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AdminAuth(Base):
    __tablename__ = "admin_auth"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    query_port: Mapped[int] = mapped_column(Integer, nullable=False)
    rcon_port: Mapped[int] = mapped_column(Integer, nullable=False)
    rcon_password_enc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Game type id from server_types registry (e.g. "sandstorm")
    server_type: Mapped[str] = mapped_column(String(32), nullable=False, default="sandstorm")
    # Optional per-server override; null → type default setting
    preferred_gamemode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    options_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # Last successful live snapshot (for instant UI before re-query)
    last_hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_map: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_lighting: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_gamemode: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_coop_or_versus: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_players: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_max_players: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_online: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    history: Mapped[list["CommandHistory"]] = relationship(back_populates="server", cascade="all, delete-orphan")
    player_samples: Mapped[list["PlayerCountSample"]] = relationship(
        back_populates="server", cascade="all, delete-orphan"
    )
    custom_buttons: Mapped[list["CustomButton"]] = relationship(
        back_populates="server", cascade="all, delete-orphan"
    )


class PlayerCountSample(Base):
    """Endless player-count history from background Source Query sampling."""

    __tablename__ = "player_count_samples"
    __table_args__ = (
        Index("ix_player_samples_server_time", "server_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    players: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_players: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # JSON list of {name, steamid?} present at sample time (empty string if unknown)
    roster_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # Server-reported simulation rate at sample time. NULL for server types that
    # do not expose one (A2S has no equivalent), so the chart shows a gap rather
    # than a misleading zero.
    tick_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    server: Mapped[Server] = relationship(back_populates="player_samples")


class ChartShare(Base):
    """Public unguessable share token for a server player-count chart."""

    __tablename__ = "chart_shares"
    __table_args__ = (
        UniqueConstraint("server_id", name="uq_chart_share_server"),
        Index("ix_chart_shares_token", "token", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    server: Mapped[Server] = relationship()


class PlayerServerStats(Base):
    """
    Per-player presence stats for a game server, derived from continuous sampling.

    visit_count increments each time a player is seen after not being seen
    on the previous sample (a new session).
    """

    __tablename__ = "player_server_stats"
    __table_args__ = (
        UniqueConstraint("server_id", "steam_id", name="uq_player_server"),
        Index("ix_player_stats_server", "server_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    steam_id: Mapped[str] = mapped_column(String(32), nullable=False)
    last_name: Mapped[str] = mapped_column(String(255), default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Accumulated play time across all sessions (seconds)
    total_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Number of sessions (appear after absence)
    visit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Non-null while currently online on this server
    session_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # End of the session *before* the current one, captured when a new visit
    # starts. last_seen_at is overwritten every sample, so without this the
    # previous leave time is lost the moment a player re-joins. Null until a
    # player has been seen leaving and coming back at least once.
    previous_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ip: Mapped[str] = mapped_column(String(64), default="")
    last_score: Mapped[int] = mapped_column(Integer, default=0)


class MapConfig(Base):
    __tablename__ = "maps"
    __table_args__ = (UniqueConstraint("server_type", "map_name", name="uq_map_type_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_type: Mapped[str] = mapped_column(String(32), nullable=False, default="sandstorm")
    alias: Mapped[str] = mapped_column(String(128), nullable=False)
    map_name: Mapped[str] = mapped_column(String(128), nullable=False)
    mod_id: Mapped[int] = mapped_column(Integer, default=0)
    day: Mapped[bool] = mapped_column(Boolean, default=True)
    night: Mapped[bool] = mapped_column(Boolean, default=True)
    map_pic: Mapped[str] = mapped_column(String(255), default="")
    checkpointhardcore: Mapped[str] = mapped_column(String(255), default="")
    checkpointhardcore_ins: Mapped[str] = mapped_column(String(255), default="")
    checkpoint: Mapped[str] = mapped_column(String(255), default="")
    checkpoint_ins: Mapped[str] = mapped_column(String(255), default="")
    domination: Mapped[str] = mapped_column(String(255), default="")
    firefight_east: Mapped[str] = mapped_column(String(255), default="")
    firefight_west: Mapped[str] = mapped_column(String(255), default="")
    frontline: Mapped[str] = mapped_column(String(255), default="")
    outpost: Mapped[str] = mapped_column(String(255), default="")
    push: Mapped[str] = mapped_column(String(255), default="")
    push_ins: Mapped[str] = mapped_column(String(255), default="")
    skirmish: Mapped[str] = mapped_column(String(255), default="")
    teamdeathmatch: Mapped[str] = mapped_column(String(255), default="")
    survival: Mapped[str] = mapped_column(String(255), default="")
    ambush: Mapped[str] = mapped_column(String(255), default="")
    self_added: Mapped[bool] = mapped_column(Boolean, default=False)
    globalday: Mapped[bool] = mapped_column(Boolean, default=False)
    dusk: Mapped[bool] = mapped_column(Boolean, default=False)
    dawn: Mapped[bool] = mapped_column(Boolean, default=False)
    dark: Mapped[bool] = mapped_column(Boolean, default=False)
    fog: Mapped[bool] = mapped_column(Boolean, default=False)
    rain: Mapped[bool] = mapped_column(Boolean, default=False)
    winter: Mapped[bool] = mapped_column(Boolean, default=False)


class CustomButton(Base):
    """
    Quick RCON buttons.

    - server_id IS NULL → type default buttons for server_type
    - server_id set → per-server override set (if any exist for a server, they win)
    """

    __tablename__ = "custom_buttons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    command: Mapped[str] = mapped_column(String(512), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    server_type: Mapped[str] = mapped_column(String(32), nullable=False, default="sandstorm")
    server_id: Mapped[int | None] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=True, index=True
    )

    server: Mapped[Server | None] = relationship(back_populates="custom_buttons")


class CommandHistory(Base):
    __tablename__ = "command_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[int | None] = mapped_column(ForeignKey("servers.id", ondelete="SET NULL"), nullable=True)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    server: Mapped[Server | None] = relationship(back_populates="history")


class Setting(Base):
    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("key", name="uq_setting_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, default="")


class IdentityCache(Base):
    """Resolved platform usernames (Steam persona, etc.)."""

    __tablename__ = "identity_cache"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_identity_platform_id"),
        Index("ix_identity_external", "external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # steam | steamnwi | eos | unknown
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="steam")
    # Canonical id (SteamID64 digits, or EOS product user id blob)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    profile_url: Mapped[str] = mapped_column(String(512), default="")
    avatar_url: Mapped[str] = mapped_column(String(512), default="")
    # steam_api | presence | manual
    source: Mapped[str] = mapped_column(String(32), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PlayerActionLog(Base):
    """Kick / ban / permban / unban (and similar) history keyed by platform id."""

    __tablename__ = "player_action_logs"
    __table_args__ = (
        Index("ix_player_actions_identity", "platform", "external_id"),
        Index("ix_player_actions_server", "server_id"),
        Index("ix_player_actions_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="steam")
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # kick | ban | permban | unban
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    server_id: Mapped[int | None] = mapped_column(
        ForeignKey("servers.id", ondelete="SET NULL"), nullable=True
    )
    server_name: Mapped[str] = mapped_column(String(255), default="")
    player_name: Mapped[str] = mapped_column(String(255), default="")
    # The id exactly as sent to the game server. (platform, external_id) is
    # canonical but lossy - gdk_ and xsx_ both normalise to "xbox", and
    # Palworld's /unban needs the original string back.
    net_id: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    # e.g. ban duration minutes
    detail: Mapped[str] = mapped_column(String(255), default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlayerAdminNote(Base):
    """Admin free-text notes attached to a platform identity."""

    __tablename__ = "player_admin_notes"
    __table_args__ = (Index("ix_player_notes_identity", "platform", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="steam")
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ServerBanSnapshot(Base):
    """Per-server cached listbans metadata (raw text + fetch time)."""

    __tablename__ = "server_ban_snapshots"

    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True
    )
    raw_text: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(Text, default="")


class ServerBanEntry(Base):
    """Cached ban rows for a server (from last successful listbans)."""

    __tablename__ = "server_ban_entries"
    __table_args__ = (
        Index("ix_server_bans_server", "server_id"),
        UniqueConstraint("server_id", "raw_id", name="uq_server_ban_raw"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    sort_index: Mapped[int] = mapped_column(Integer, default=0)
    platform: Mapped[str] = mapped_column(String(64), default="")
    raw_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    net_id: Mapped[str] = mapped_column(String(255), default="")
    display_id: Mapped[str] = mapped_column(String(255), default="")
    duration: Mapped[str] = mapped_column(String(128), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    permanent: Mapped[bool] = mapped_column(Boolean, default=False)

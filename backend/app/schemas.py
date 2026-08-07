from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6)


class AuthStatus(BaseModel):
    authenticated: bool


class ServerFeaturesOut(BaseModel):
    map_travel: bool = False
    structured_player_list: bool = False
    player_score: bool = True
    kick_ban: bool = False
    ban_list: bool = False
    admin_say: bool = False
    a2s_query: bool = True
    admin_api: bool = False
    console: bool = True
    tick_rate_history: bool = False
    tls_optional: bool = False


class QuickButtonOut(BaseModel):
    label: str
    command: str


class ServerTypeOut(BaseModel):
    id: str
    label: str
    default_query_port: int
    default_rcon_port: int
    features: ServerFeaturesOut
    quick_buttons: list[QuickButtonOut] = Field(default_factory=list)
    secret_label: str = "RCON password"
    endpoint_style: str = "query_rcon"
    ban_list_source: str = "live"
    tick_rate_label: str = "Tick rate"
    tick_rate_unit: str = "tps"
    tick_rate_target: int = 30


class ServerOptionsIn(BaseModel):
    """Per-server connection extras (stored in servers.options_json)."""

    # Only meaningful for types that advertise features.tls_optional
    use_https: bool | None = None
    verify_tls: bool | None = None
    cert_fingerprint: str | None = Field(default=None, max_length=128)


class ServerOptionsOut(BaseModel):
    use_https: bool = False
    verify_tls: bool = False
    cert_fingerprint: str = ""


class ServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    host: str = Field(min_length=1, max_length=255)
    query_port: int = Field(ge=1, le=65535)
    rcon_port: int = Field(ge=1, le=65535)
    rcon_password: str = ""
    server_type: str = "sandstorm"
    preferred_gamemode: str | None = Field(default=None, max_length=64)
    options: ServerOptionsIn | None = None


class ServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    query_port: int | None = Field(default=None, ge=1, le=65535)
    rcon_port: int | None = Field(default=None, ge=1, le=65535)
    rcon_password: str | None = None
    server_type: str | None = None
    preferred_gamemode: str | None = None
    # Explicit clear of per-server preferred_gamemode (null override)
    clear_preferred_gamemode: bool = False
    options: ServerOptionsIn | None = None


class ServerOut(BaseModel):
    id: int
    name: str
    host: str
    query_port: int
    rcon_port: int
    server_type: str
    preferred_gamemode: str | None = None
    has_rcon_password: bool
    options: ServerOptionsOut = Field(default_factory=ServerOptionsOut)
    # Cached from last successful status poll (instant UI)
    last_hostname: str | None = None
    last_map: str | None = None
    last_lighting: str | None = None
    last_gamemode: str | None = None
    last_coop_or_versus: str | None = None
    last_players: int | None = None
    last_max_players: int | None = None
    last_online: bool | None = None
    last_status_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PlayerInfo(BaseModel):
    id: int
    name: str
    score: int = 0
    steamid: str = ""
    ip: str = ""
    session_seconds: int = 0
    session_pretty: str = "0s"
    total_seconds: int = 0
    total_pretty: str = "0s"
    visit_count: int = 0
    rank: int | None = None
    ranked_players: int = 0
    last_seen_at: str | None = None
    last_seen_pretty: str = "-"
    # End of the session before the current one. Every row in the player table
    # is someone online, so this is the only last-seen value that says anything.
    previous_seen_at: str | None = None
    previous_seen_pretty: str = "-"
    duration: float = 0.0
    duration_pretty: str = "00:00:00"
    # Game-specific per-player scalars with no column of their own, rendered as
    # extra columns. Mirrors ServerStatus.extra one level down.
    extra: dict[str, Any] | None = None


class ServerStatus(BaseModel):
    online: bool
    host: str
    query_port: int
    server_type: str = "sandstorm"
    features: ServerFeaturesOut = Field(default_factory=ServerFeaturesOut)
    hostname: str | None = None
    map: str | None = None
    lighting: str | None = None
    gamemode: str | None = None
    coop_or_versus: str | None = None
    players: int | None = None
    max_players: int | None = None
    bots: int | None = None
    ping_ms: int | None = None
    password_protected: bool | None = None
    vac: bool | None = None
    ranked: bool | None = None
    game_port: int | None = None
    version: str | None = None
    player_list: list[PlayerInfo] = Field(default_factory=list)
    error: str | None = None
    # True when values are served from DB cache (pre-live or offline fallback)
    from_cache: bool = False
    last_status_at: datetime | None = None
    # Game-specific scalars with no column of their own (tick rate, tier, ...)
    extra: dict[str, Any] | None = None


class SatisfactoryHealthOut(BaseModel):
    health: str = ""
    server_custom_data: str = ""


class SatisfactoryStateOut(BaseModel):
    active_session_name: str = ""
    num_connected_players: int = 0
    player_limit: int = 0
    tech_tier: int = 0
    active_schematic: str = ""
    game_phase: str = ""
    is_game_running: bool = False
    total_game_duration: int = 0
    is_game_paused: bool = False
    average_tick_rate: float = 0.0
    auto_load_session_name: str = ""


class SatisfactoryOptionsOut(BaseModel):
    server_options: dict[str, str] = Field(default_factory=dict)
    pending_server_options: dict[str, str] = Field(default_factory=dict)


class SatisfactoryOptionsUpdate(BaseModel):
    options: dict[str, str] = Field(min_length=1)


class SatisfactoryAdvancedOut(BaseModel):
    creative_mode_enabled: bool = False
    advanced_game_settings: dict[str, Any] = Field(default_factory=dict)


class SatisfactoryAdvancedUpdate(BaseModel):
    settings: dict[str, Any] = Field(min_length=1)
    # Applying these permanently marks the save as "edited"
    confirm: bool = False


class SatisfactorySessionsOut(BaseModel):
    sessions: list[dict[str, Any]] = Field(default_factory=list)
    current_session_index: int = -1


class SatisfactoryActionOut(BaseModel):
    ok: bool = True
    detail: str = ""


class SaveGameRequest(BaseModel):
    save_name: str = Field(min_length=1, max_length=255)


class LoadGameRequest(BaseModel):
    save_name: str = Field(min_length=1, max_length=255)
    enable_advanced_game_settings: bool = False


class ConfirmRequest(BaseModel):
    confirm: bool = False


class RenameServerRequest(BaseModel):
    server_name: str = Field(min_length=1, max_length=255)


class SetPasswordRequest(BaseModel):
    password: str = ""


class AutoLoadRequest(BaseModel):
    session_name: str = ""


class ClaimServerRequest(BaseModel):
    server_name: str = Field(min_length=1, max_length=255)
    admin_password: str = Field(min_length=1, max_length=255)


class NewGameRequest(BaseModel):
    session_name: str = Field(min_length=1, max_length=255)
    map_name: str = ""
    starting_location: str = ""
    skip_onboarding: bool = True
    confirm: bool = False


class PalworldInfoOut(BaseModel):
    version: str = ""
    server_name: str = ""
    description: str = ""
    # Post-0.2.x servers only
    world_guid: str = ""


class PalworldMetricsOut(BaseModel):
    """None means "this server version didn't report it", never a reading of 0."""

    server_fps: int | None = None
    current_players: int | None = None
    max_players: int | None = None
    frame_time_ms: float | None = None
    uptime: int | None = None
    # days is post-0.2.x, base_camps is 1.x-only
    days: int | None = None
    base_camps: int | None = None


class PalworldPlayerOut(BaseModel):
    name: str = ""
    # Bare SteamID64 when the platform is Steam, else the raw platform user ID
    steamid: str = ""
    user_id: str = ""
    account_name: str = ""
    player_id: str = ""
    ip: str = ""
    level: int | None = None
    ping: float | None = None
    building_count: int | None = None
    location_x: float | None = None
    location_y: float | None = None


class PalworldPlayersOut(BaseModel):
    players: list[PalworldPlayerOut] = Field(default_factory=list)


class PalworldSettingsOut(BaseModel):
    """Read-only: the REST API exposes no way to write settings.

    The server returns a curated subset of the INI (68 of ~119 keys in 1.0) and
    deliberately omits AdminPassword / ServerPassword / RCONPassword.
    """

    settings: dict[str, Any] = Field(default_factory=dict)


class PalworldWorldPlayer(BaseModel):
    name: str = ""
    user_id: str = ""
    level: int | None = None
    hp: int | None = None
    max_hp: int | None = None
    guild_name: str = ""
    location_x: float | None = None
    location_y: float | None = None
    location_z: float | None = None
    pal_count: int = 0


class PalworldBaseCampOut(BaseModel):
    guild_name: str = ""
    guild_id: str = ""
    location_x: float | None = None
    location_y: float | None = None
    location_z: float | None = None


class PalworldWorldOut(BaseModel):
    """Server-side summary of /v1/api/game-data - the raw payload can be huge."""

    enabled: bool = True
    hint: str = ""
    snapshot_time: str = ""
    fps: float | None = None
    average_fps: float | None = None
    actor_counts: dict[str, int] = Field(default_factory=dict)
    players: list[PalworldWorldPlayer] = Field(default_factory=list)
    base_camps: list[PalworldBaseCampOut] = Field(default_factory=list)


class PalworldActionOut(BaseModel):
    ok: bool = True
    detail: str = ""


class AnnounceRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class PalworldShutdownRequest(BaseModel):
    waittime: int = Field(default=30, ge=0, le=3600)
    message: str = Field(default="", max_length=1000)
    confirm: bool = False


class RconCommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=2000)


class RconCommandResponse(BaseModel):
    command: str
    response: str
    ok: bool
    error: str | None = None


class PlayerActionRequest(BaseModel):
    player_name: str = Field(min_length=1)
    reason: str = ""
    ban_minutes: int | None = Field(default=None, ge=1)
    # Platform net id when known (SteamID64, SteamNWI:…, EOS:…)
    net_id: str = ""


class UnbanRequest(BaseModel):
    net_id: str = Field(min_length=1)


class PlayerActionLogOut(BaseModel):
    id: int
    platform: str
    external_id: str
    action: str
    server_id: int | None = None
    server_name: str = ""
    player_name: str = ""
    reason: str = ""
    detail: str = ""
    ok: bool = True
    error: str = ""
    created_at: datetime

    model_config = {"from_attributes": True}


class PlayerNoteOut(BaseModel):
    id: int
    platform: str
    external_id: str
    body: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PlayerNoteCreate(BaseModel):
    """Full note document body. Empty string clears the note."""

    body: str = Field(default="", max_length=20000)


class IdentityDossierOut(BaseModel):
    platform: str
    external_id: str
    display_name: str = ""
    profile_url: str = ""
    avatar_url: str = ""
    has_info: bool = False
    actions: list[PlayerActionLogOut] = Field(default_factory=list)
    notes: list[PlayerNoteOut] = Field(default_factory=list)


class IdentityFlagsRequest(BaseModel):
    """Batch check whether identities have notes/history."""

    identities: list[dict[str, str]] = Field(default_factory=list)
    # each: { "platform": "steam", "external_id": "7656…" } or { "net_id": "SteamNWI:…" }


class IdentityFlagsOut(BaseModel):
    # key = "platform:external_id"
    flags: dict[str, bool] = Field(default_factory=dict)


class BanEntryOut(BaseModel):
    index: int
    platform: str
    raw_id: str
    net_id: str
    display_id: str
    duration: str
    reason: str
    permanent: bool = False
    # Resolved persona (Steam Web API / local presence cache)
    display_name: str = ""
    profile_url: str = ""
    avatar_url: str = ""
    name_source: str = ""


class BanListOut(BaseModel):
    server_id: int
    bans: list[BanEntryOut] = Field(default_factory=list)
    raw: str = ""
    ok: bool = True
    error: str | None = None
    steam_lookup_enabled: bool = False
    # True when payload came from DB cache (not a live listbans)
    from_cache: bool = False
    fetched_at: datetime | None = None
    page: int = 1
    page_size: int = 25
    total: int = 0
    total_pages: int = 1


class AdminSayRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)


class TravelRequest(BaseModel):
    map_id: int
    gamemode_key: str
    lighting: str = "Day"
    execute: bool = True


class TravelPreview(BaseModel):
    command: str
    map_alias: str
    map_name: str
    scenario: str
    gamemode_key: str
    lighting: str


class MapOut(BaseModel):
    id: int
    alias: str
    map_name: str
    mod_id: int
    day: bool
    night: bool
    self_added: bool
    server_type: str = "sandstorm"
    gamemodes: dict[str, str]
    lightings: list[str]

    model_config = {"from_attributes": True}


class CommandHistoryOut(BaseModel):
    id: int
    server_id: int | None
    command: str
    response: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TypeSettingsOut(BaseModel):
    preferred_gamemode: str = ""


class SettingsOut(BaseModel):
    query_timeout: float
    poll_interval_seconds: int
    stats_interval_seconds: int
    types: dict[str, TypeSettingsOut] = Field(default_factory=dict)


class TypeSettingsUpdate(BaseModel):
    preferred_gamemode: str | None = None


class SettingsUpdate(BaseModel):
    query_timeout: float | None = Field(default=None, ge=0.5, le=30)
    poll_interval_seconds: int | None = Field(default=None, ge=3, le=120)
    stats_interval_seconds: int | None = Field(default=None, ge=15, le=3600)
    types: dict[str, TypeSettingsUpdate] | None = None

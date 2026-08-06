from datetime import datetime

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
    kick_ban: bool = False
    admin_say: bool = False
    a2s_query: bool = True


class ServerTypeOut(BaseModel):
    id: str
    label: str
    default_query_port: int
    default_rcon_port: int
    features: ServerFeaturesOut


class ServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    host: str = Field(min_length=1, max_length=255)
    query_port: int = Field(ge=1, le=65535)
    rcon_port: int = Field(ge=1, le=65535)
    rcon_password: str = ""
    server_type: str = "sandstorm"
    preferred_gamemode: str | None = Field(default=None, max_length=64)


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


class ServerOut(BaseModel):
    id: int
    name: str
    host: str
    query_port: int
    rcon_port: int
    server_type: str
    preferred_gamemode: str | None = None
    has_rcon_password: bool
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
    last_seen_pretty: str = "—"
    duration: float = 0.0
    duration_pretty: str = "00:00:00"


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


class UnbanRequest(BaseModel):
    net_id: str = Field(min_length=1)


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


class CustomButtonOut(BaseModel):
    id: int
    label: str
    command: str
    sort_order: int
    server_type: str = "sandstorm"
    server_id: int | None = None

    model_config = {"from_attributes": True}


class CustomButtonUpdate(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    command: str = Field(min_length=1, max_length=512)


class CustomButtonCreate(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    command: str = Field(min_length=1, max_length=512)
    sort_order: int = 0


class ServerButtonsReplace(BaseModel):
    """Replace per-server button overrides. Empty list clears overrides (inherit type defaults)."""

    buttons: list[CustomButtonCreate] = Field(default_factory=list)


class TypeButtonsReplace(BaseModel):
    buttons: list[CustomButtonCreate] = Field(default_factory=list)


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

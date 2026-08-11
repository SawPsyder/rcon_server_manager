from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# bcrypt truncates past 72 bytes, so a longer password would have a silently
# ignored tail. Reject instead.
PASSWORD_MIN = 10
PASSWORD_MAX = 72


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)
    turnstile_token: str = ""


class TotpLoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)


class BootstrapClaimRequest(BaseModel):
    """Promote yourself to the first admin using ADMIN_PASSWORD."""

    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)
    display_name: str = Field(default="", max_length=120)
    admin_password: str = Field(min_length=1)
    turnstile_token: str = ""


class BootstrapStatus(BaseModel):
    available: bool


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    turnstile_token: str = ""


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)


class ResetPasswordResult(BaseModel):
    """Password was set; the client must sign in through the normal login path."""

    ok: bool = True


class ResetTokenStatus(BaseModel):
    """Whether a reset/invite token is still redeemable (probe only; does not consume)."""

    valid: bool


class CurrentUserOut(BaseModel):
    id: int
    email: str
    display_name: str
    role: str
    is_admin: bool
    totp_enabled: bool
    # Servers this user may operate. Empty and is_admin=True means "all".
    server_ids: list[int] = Field(default_factory=list)


class AuthStatus(BaseModel):
    authenticated: bool
    user: CurrentUserOut | None = None
    # Set when the password was right but a TOTP code is still required.
    mfa_required: bool = False


class PublicConfig(BaseModel):
    """Unauthenticated config the login screen needs before anyone is signed in."""

    turnstile_enabled: bool = False
    turnstile_site_key: str = ""
    smtp_enabled: bool = False
    bootstrap_available: bool = False


class TotpSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class TotpConfirmRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)


class TotpDisableRequest(BaseModel):
    current_password: str


class TotpSetupRequest(BaseModel):
    current_password: str


class TotpConfirmOut(BaseModel):
    recovery_codes: list[str]


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str
    role: str
    is_active: bool
    totp_enabled: bool
    has_password: bool
    # Temporary lock after failed sign-ins (distinct from is_active=False).
    is_locked: bool = False
    locked_until: datetime | None = None
    failed_logins: int = 0
    server_ids: list[int] = Field(default_factory=list)
    last_login_at: datetime | None = None
    created_at: datetime


class UserCreateRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    display_name: str = Field(default="", max_length=120)
    role: str = "user"
    server_ids: list[int] = Field(default_factory=list)


class UserAdminUpdate(BaseModel):
    """Admin-editable fields. Never reuse this on a self-service endpoint."""

    display_name: str | None = Field(default=None, max_length=120)
    role: str | None = None
    is_active: bool | None = None


class UserSelfUpdate(BaseModel):
    """Deliberately separate from UserAdminUpdate.

    Sharing one model between the admin and self endpoints is the classic
    mass-assignment escalation: {"role": "admin"} in a PATCH /me body.
    """

    display_name: str = Field(default="", max_length=120)


class GrantsUpdate(BaseModel):
    server_ids: list[int] = Field(default_factory=list)


class InviteLinkOut(BaseModel):
    """Returned when a link could not be emailed, so an admin can pass it on."""

    user: UserOut
    invite_url: str = ""
    emailed: bool = False


class MailSettingsOut(BaseModel):
    host: str = ""
    port: int = 587
    user: str = ""
    # The password itself is never returned.
    has_password: bool = False
    starttls: bool = True
    ssl: bool = False
    from_address: str = ""
    from_name: str = ""
    base_url: str = ""
    # Whether a message could actually be sent right now.
    enabled: bool = False
    # False while the settings still come from environment variables.
    configured: bool = False


class MailSettingsUpdate(BaseModel):
    host: str = Field(default="", max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    user: str = Field(default="", max_length=255)
    # Omit to keep the stored password; "" clears it.
    password: str | None = Field(default=None, max_length=255)
    starttls: bool = True
    ssl: bool = False
    from_address: str = Field(default="", max_length=320)
    from_name: str = Field(default="RCON Server Manager", max_length=120)
    base_url: str = Field(default="", max_length=255)


class PterodactylSettingsOut(BaseModel):
    base_url: str = ""
    # The key itself is never returned.
    has_api_key: bool = False
    verify_tls: bool = True
    # Whether a panel call could actually be made right now. This can be False
    # while has_api_key is True: rotating ENCRYPTION_KEY leaves an undecryptable
    # ciphertext behind, and decrypt_secret returns "" rather than raising.
    enabled: bool = False


class PterodactylSettingsUpdate(BaseModel):
    base_url: str = Field(default="", max_length=255)
    # Omit to keep the stored key; "" clears it.
    api_key: str | None = Field(default=None, max_length=255)
    verify_tls: bool = True


class PterodactylTestOut(BaseModel):
    """Result of the Test-connection button.

    Unlike the mail test this returns a body rather than 204: the number of
    visible servers is what tells an admin the key is the right *kind*, since
    an Application key fails outright while a Client key with no server access
    succeeds but sees nothing.
    """

    detail: str = ""
    server_count: int = 0


class PterodactylServerOut(BaseModel):
    """One panel server, for the linking dropdown."""

    uuid: str
    identifier: str = ""
    name: str = ""
    node: str = ""
    # "" is healthy; the panel sends null. Otherwise installing / suspended / ...
    status: str = ""
    is_suspended: bool = False
    # MiB, 0 = unlimited
    memory_limit_mb: int = 0
    disk_limit_mb: int = 0
    # Percent of one host CPU (100 = one core), 0 = unlimited
    cpu_limit: int = 0
    # Set when one of our servers already claims this panel server.
    linked_server_id: int | None = None


class PterodactylResourcesOut(BaseModel):
    """Live utilisation for a linked server.

    Limits are echoed as bytes (the panel reports MiB) so the UI does no unit
    maths, and are None when the panel says unlimited - which it encodes as 0,
    the value most likely to become a division.
    """

    name: str = ""
    # Admin-only; used to deep-link into the panel. "" for other viewers.
    identifier: str = ""
    state: str = "offline"
    is_suspended: bool = False
    # Non-empty means installing / transferring / restoring - power will 409.
    panel_status: str = ""
    memory_bytes: int = 0
    memory_limit_bytes: int | None = None
    disk_bytes: int = 0
    disk_limit_bytes: int | None = None
    # 100.0 is one full host core.
    cpu_absolute: float = 0.0
    cpu_limit: int | None = None
    # Cumulative since the container started; these reset on restart.
    network_rx_bytes: int = 0
    network_tx_bytes: int = 0
    uptime_ms: int = 0
    # How long ago this reading was fetched from the panel. A background poller
    # refreshes every linked server, so the answer is usually not "just now" -
    # saying so beats implying the number is live.
    age_seconds: float = 0.0


class PterodactylHistoryPoint(BaseModel):
    """One sample (or mid-of-chunk when the series was thinned to the chart cap)."""

    t: datetime
    cpu_absolute: float | None = None
    cpu_peak: float | None = None
    memory_bytes: int | None = None
    memory_peak: int | None = None
    samples: int = 0


class PterodactylHistoryOut(BaseModel):
    server_id: int
    range: str
    from_time: datetime
    to_time: datetime
    # Approximate spacing of returned points (compat field; not used for grouping).
    bucket_seconds: int
    points: list[PterodactylHistoryPoint] = Field(default_factory=list)
    # Summaries over every raw sample in range, including rows dropped by thinning.
    current_cpu_absolute: float | None = None
    peak_cpu_absolute: float | None = None
    avg_cpu_absolute: float | None = None
    current_memory_bytes: int | None = None
    peak_memory_bytes: int | None = None
    avg_memory_bytes: int | None = None


class PterodactylPowerRequest(BaseModel):
    signal: Literal["start", "stop", "restart", "kill"]
    # Required for the signals that interrupt play without a clean shutdown.
    confirm: bool = False


class PterodactylPowerOut(BaseModel):
    signal: str
    # The panel acknowledges asynchronously, so this never claims the state changed.
    detail: str = ""


class PterodactylStartupVariableOut(BaseModel):
    """One egg startup variable from the panel."""

    env_variable: str
    name: str = ""
    description: str = ""
    server_value: str = ""
    default_value: str = ""
    is_editable: bool = True
    rules: str = ""


class PterodactylStartupOut(BaseModel):
    """Startup variables for a linked container.

    ``has_map_defaults`` is True when the egg exposes both ``MAP_NAME`` and
    ``SCENARIO`` - the two keys the Sandstorm "Set as default map" button needs.
    """

    variables: list[PterodactylStartupVariableOut] = Field(default_factory=list)
    startup_command: str = ""
    has_map_defaults: bool = False


class PterodactylStartupVariableUpdate(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    value: str = Field(default="", max_length=2048)


class PterodactylDefaultMapRequest(BaseModel):
    """Set panel MAP_NAME + SCENARIO from a catalog map + gamemode."""

    map_id: int
    gamemode_key: str = Field(min_length=1, max_length=64)


class PterodactylDefaultMapOut(BaseModel):
    map_alias: str = ""
    map_name: str = ""
    scenario: str = ""
    gamemode_key: str = ""
    detail: str = ""


class ServerFeaturesOut(BaseModel):
    map_travel: bool = False
    structured_player_list: bool = False
    player_score: bool = True
    kick_ban: bool = False
    timed_ban: bool = False
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
    # Link to a Pterodactyl panel server. "" unlinks. Type-independent, unlike
    # the TLS trio above.
    pterodactyl_uuid: str | None = Field(default=None, max_length=64)
    # Cached at link time so the Servers table can name the link without
    # calling the panel.
    pterodactyl_identifier: str | None = Field(default=None, max_length=32)
    pterodactyl_name: str | None = Field(default=None, max_length=255)


class ServerOptionsOut(BaseModel):
    use_https: bool = False
    verify_tls: bool = False
    cert_fingerprint: str = ""
    # Panel inventory: admin-only, blank for a redacted viewer. Whether a link
    # exists at all is on ServerOut.pterodactyl_linked, which is never redacted.
    pterodactyl_uuid: str = ""
    pterodactyl_identifier: str = ""
    pterodactyl_name: str = ""


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
    # Redacted to null for non-admin viewers - see api/servers.py::_to_out.
    rcon_port: int | None = None
    server_type: str
    preferred_gamemode: str | None = None
    has_rcon_password: bool
    options: ServerOptionsOut = Field(default_factory=ServerOptionsOut)
    # Whether a Pterodactyl server is linked. Deliberately outside `options`,
    # which is redacted wholesale for non-admins: a granted operator may use
    # the resource panel, so the UI has to know it exists. The uuid behind it
    # stays admin-only.
    pterodactyl_linked: bool = False
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
    guild_id: str = ""
    location_x: float | None = None
    location_y: float | None = None
    location_z: float | None = None
    rotation_z: float | None = None
    pal_count: int = 0


class PalworldBaseCampOut(BaseModel):
    id: str = ""
    guild_name: str = ""
    guild_id: str = ""
    name: str = ""
    location_x: float | None = None
    location_y: float | None = None
    location_z: float | None = None


class PalworldMapEntity(BaseModel):
    """Positioned non-player actor for the admin world map (workers, wild, NPCs)."""

    id: str = ""
    name: str = ""
    species: str = ""
    level: int | None = None
    hp: int | None = None
    max_hp: int | None = None
    guild_name: str = ""
    guild_id: str = ""
    location_x: float | None = None
    location_y: float | None = None
    location_z: float | None = None
    rotation_z: float | None = None
    activity: str = ""


class PalworldWorldOut(BaseModel):
    """Server-side summary of /v1/api/game-data - the raw payload can be huge."""

    enabled: bool = True
    hint: str = ""
    snapshot_time: str = ""
    fps: float | None = None
    average_fps: float | None = None
    in_game_time: str = ""
    in_game_days: int | None = None
    actor_counts: dict[str, int] = Field(default_factory=dict)
    players: list[PalworldWorldPlayer] = Field(default_factory=list)
    base_camps: list[PalworldBaseCampOut] = Field(default_factory=list)
    workers: list[PalworldMapEntity] = Field(default_factory=list)
    wild_pals: list[PalworldMapEntity] = Field(default_factory=list)
    npcs: list[PalworldMapEntity] = Field(default_factory=list)
    # Party pals following a player (when present in the dump)
    otomo_pals: list[PalworldMapEntity] = Field(default_factory=list)


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
    author_user_id: int | None = None
    author_label: str = ""
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PlayerNoteCreate(BaseModel):
    """Body for the caller's own note. Empty string deletes their note."""

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
    app_timezone: str = "UTC"
    types: dict[str, TypeSettingsOut] = Field(default_factory=dict)


class TypeSettingsUpdate(BaseModel):
    preferred_gamemode: str | None = None


class SettingsUpdate(BaseModel):
    query_timeout: float | None = Field(default=None, ge=0.5, le=30)
    poll_interval_seconds: int | None = Field(default=None, ge=3, le=120)
    stats_interval_seconds: int | None = Field(default=None, ge=15, le=3600)
    app_timezone: str | None = Field(default=None, max_length=64)
    types: dict[str, TypeSettingsUpdate] | None = None


class ClientIpHeaderValue(BaseModel):
    """One candidate client-IP header and what this request carried for it."""

    name: str
    present: bool
    value: str | None = None


class ClientIpDebugOut(BaseModel):
    """Admin helper: which IP headers arrived and what client_ip() resolves to."""

    configured_header: str
    socket_peer: str
    resolved_client_ip: str
    headers: list[ClientIpHeaderValue] = Field(default_factory=list)


# ---- Server schedules ----

class ScheduleActionIn(BaseModel):
    action_type: str = Field(max_length=40)
    params: dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0


class ScheduleCheckIn(BaseModel):
    check_type: str = Field(max_length=40)
    params: dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0


class ScheduleActionOut(BaseModel):
    id: int | None = None
    action_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0


class ScheduleCheckOut(BaseModel):
    id: int | None = None
    check_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0


class ScheduleCreate(BaseModel):
    server_id: int
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    time_local: str = Field(default="04:00", max_length=5)
    days_of_week: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    # 0 = do not retry failed checks (skip until next window)
    retry_after_minutes: int = Field(default=10, ge=0, le=24 * 60)
    actions: list[ScheduleActionIn] = Field(min_length=1, max_length=50)
    checks: list[ScheduleCheckIn] = Field(default_factory=list, max_length=20)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class ScheduleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    time_local: str | None = Field(default=None, max_length=5)
    days_of_week: list[int] | None = None
    retry_after_minutes: int | None = Field(default=None, ge=0, le=24 * 60)
    actions: list[ScheduleActionIn] | None = Field(default=None, max_length=50)
    checks: list[ScheduleCheckIn] | None = Field(default=None, max_length=20)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @field_validator("actions")
    @classmethod
    def actions_not_empty_when_set(
        cls, value: list[ScheduleActionIn] | None
    ) -> list[ScheduleActionIn] | None:
        if value is not None and len(value) < 1:
            raise ValueError("actions must include at least one step")
        return value


class ScheduleEnable(BaseModel):
    enabled: bool


class ScheduleOut(BaseModel):
    id: int
    server_id: int
    server_name: str = ""
    server_type: str = ""
    pterodactyl_linked: bool = True
    name: str
    enabled: bool
    time_local: str
    days_of_week: list[int] = Field(default_factory=list)
    retry_after_minutes: int
    next_run_at: datetime
    last_run_at: datetime | None = None
    last_status: str = ""
    last_message: str = ""
    active_window_at: datetime | None = None
    app_timezone: str = "UTC"
    actions: list[ScheduleActionOut] = Field(default_factory=list)
    checks: list[ScheduleCheckOut] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ScheduleRunOut(BaseModel):
    id: int
    schedule_id: int | None
    server_id: int | None
    schedule_name: str = ""
    server_name: str = ""
    scheduled_for: datetime
    started_at: datetime
    finished_at: datetime | None
    status: str
    attempt: int
    detail: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class ScheduleMetaOut(BaseModel):
    app_timezone: str
    action_types: list[dict[str, Any]] = Field(default_factory=list)
    check_types: list[dict[str, Any]] = Field(default_factory=list)

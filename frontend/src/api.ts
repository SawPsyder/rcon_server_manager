export type ServerFeatures = {
  map_travel: boolean;
  structured_player_list: boolean;
  /** The game reports a per-player score, so the Score column means something. */
  player_score: boolean;
  kick_ban: boolean;
  /** Timed (duration-based) bans. Off for games where every ban is permanent (e.g. Palworld). */
  timed_ban: boolean;
  /** The transport can enumerate existing bans (Palworld can ban but not list). */
  ban_list: boolean;
  admin_say: boolean;
  a2s_query: boolean;
  /** Game-specific admin panel is available for this type. */
  admin_api: boolean;
  /** Free-text command console makes sense for this type. */
  console: boolean;
  /** Samples carry a tick rate, so the tick-rate history chart has data. */
  tick_rate_history: boolean;
  /** HTTP or HTTPS both possible (reverse proxy), so offer the scheme toggle. */
  tls_optional: boolean;
};

export type QuickButton = {
  label: string;
  command: string;
};

export type ServerTypeInfo = {
  id: string;
  label: string;
  default_query_port: number;
  default_rcon_port: number;
  features: ServerFeatures;
  quick_buttons: QuickButton[];
  /** UI label for the stored secret ("RCON password", "API token", ...). */
  secret_label: string;
  /** "query_rcon" = separate ports; "single_port" = one API port. */
  endpoint_style: "query_rcon" | "single_port" | string;
  /** "live" = queried from the server; "local" = derived from our own history. */
  ban_list_source: "live" | "local" | string;
  /** How the tick_rate series reads for this game (Source ticks vs server FPS). */
  tick_rate_label: string;
  tick_rate_unit: string;
  tick_rate_target: number;
};

/** Per-server connection extras (servers.options_json). */
export type ServerOptions = {
  /** Only meaningful for types that advertise features.tls_optional. */
  use_https: boolean;
  verify_tls: boolean;
  cert_fingerprint: string;
  /** Linked Pterodactyl container. Blank for non-admin viewers, and unlike the
   *  TLS fields above this applies to every server type. */
  pterodactyl_uuid: string;
  pterodactyl_identifier: string;
  pterodactyl_name: string;
};

export type Server = {
  id: number;
  name: string;
  host: string;
  query_port: number;
  /** Null for non-admin viewers - the backend redacts the control plane. */
  rcon_port: number | null;
  server_type: string;
  preferred_gamemode?: string | null;
  has_rcon_password: boolean;
  options: ServerOptions;
  /** Whether a Pterodactyl container is linked. Outside `options` on purpose:
   *  options are redacted wholesale for non-admins, but an operator may use
   *  the resource panel, so the UI still has to know it exists. */
  pterodactyl_linked: boolean;
  last_hostname?: string | null;
  last_map?: string | null;
  last_lighting?: string | null;
  last_gamemode?: string | null;
  last_coop_or_versus?: string | null;
  last_players?: number | null;
  last_max_players?: number | null;
  last_online?: boolean | null;
  last_status_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type PlayerInfo = {
  id: number;
  name: string;
  score: number;
  steamid?: string;
  ip?: string;
  session_seconds?: number;
  session_pretty?: string;
  total_seconds?: number;
  total_pretty?: string;
  visit_count?: number;
  rank?: number | null;
  ranked_players?: number;
  last_seen_at?: string | null;
  last_seen_pretty?: string;
  /** End of the session before the current one ("3d ago" / "First visit"). */
  previous_seen_at?: string | null;
  previous_seen_pretty?: string;
  duration: number;
  duration_pretty: string;
  /** Game-specific per-player scalars, rendered as extra columns. */
  extra?: Record<string, string | number | boolean | null> | null;
};

export type ServerStatus = {
  online: boolean;
  host: string;
  query_port: number;
  server_type?: string;
  features?: ServerFeatures;
  hostname?: string | null;
  map?: string | null;
  lighting?: string | null;
  gamemode?: string | null;
  coop_or_versus?: string | null;
  players?: number | null;
  max_players?: number | null;
  bots?: number | null;
  ping_ms?: number | null;
  password_protected?: boolean | null;
  vac?: boolean | null;
  ranked?: boolean | null;
  game_port?: number | null;
  version?: string | null;
  player_list: PlayerInfo[];
  error?: string | null;
  from_cache?: boolean;
  last_status_at?: string | null;
  /** Game-specific scalars with no column of their own (tick rate, tier, ...). */
  extra?: Record<string, string | number | boolean | null> | null;
};

export type SatisfactoryState = {
  active_session_name: string;
  num_connected_players: number;
  player_limit: number;
  tech_tier: number;
  active_schematic: string;
  game_phase: string;
  is_game_running: boolean;
  total_game_duration: number;
  is_game_paused: boolean;
  average_tick_rate: number;
  auto_load_session_name: string;
};

export type SatisfactoryOptions = {
  server_options: Record<string, string>;
  pending_server_options: Record<string, string>;
};

export type SatisfactoryAdvanced = {
  creative_mode_enabled: boolean;
  advanced_game_settings: Record<string, unknown>;
};

export type SatisfactorySaveHeader = {
  saveName?: string;
  saveVersion?: number;
  buildVersion?: number;
  mapName?: string;
  sessionName?: string;
  playDurationSeconds?: number;
  saveDateTime?: string;
  isModdedSave?: boolean;
  isEditedSave?: boolean;
  isCreativeModeEnabled?: boolean;
  [key: string]: unknown;
};

export type SatisfactorySession = {
  sessionName?: string;
  saveHeaders?: SatisfactorySaveHeader[];
  [key: string]: unknown;
};

export type SatisfactorySessions = {
  sessions: SatisfactorySession[];
  current_session_index: number;
};

export type SatisfactoryAction = {
  ok: boolean;
  detail: string;
};

export type PalworldWorldPlayer = {
  name: string;
  user_id: string;
  level: number | null;
  hp: number | null;
  max_hp: number | null;
  guild_name: string;
  guild_id: string;
  location_x: number | null;
  location_y: number | null;
  location_z: number | null;
  rotation_z: number | null;
  pal_count: number;
};

export type PalworldBaseCamp = {
  id: string;
  guild_name: string;
  guild_id: string;
  name: string;
  location_x: number | null;
  location_y: number | null;
  location_z: number | null;
};

/** Positioned non-player actor for the admin world map. */
export type PalworldMapEntity = {
  id: string;
  name: string;
  species: string;
  level: number | null;
  hp: number | null;
  max_hp: number | null;
  guild_name: string;
  guild_id: string;
  location_x: number | null;
  location_y: number | null;
  location_z: number | null;
  rotation_z: number | null;
  activity: string;
};

/** Server-side summary of /v1/api/game-data - the raw payload can be huge. */
export type PalworldWorld = {
  /** False when the server was launched without -enable-gamedata-api. */
  enabled: boolean;
  hint: string;
  snapshot_time: string;
  fps: number | null;
  average_fps: number | null;
  in_game_time: string;
  in_game_days: number | null;
  actor_counts: Record<string, number>;
  players: PalworldWorldPlayer[];
  base_camps: PalworldBaseCamp[];
  workers: PalworldMapEntity[];
  wild_pals: PalworldMapEntity[];
  npcs: PalworldMapEntity[];
  otomo_pals: PalworldMapEntity[];
};

export type PalworldAction = {
  ok: boolean;
  detail: string;
};

export type MapConfig = {
  id: number;
  alias: string;
  map_name: string;
  mod_id: number;
  day: boolean;
  night: boolean;
  self_added: boolean;
  server_type?: string;
  gamemodes: Record<string, string>;
  lightings: string[];
};

export type RconResult = {
  command: string;
  response: string;
  ok: boolean;
  error?: string | null;
};

export type BanEntry = {
  index: number;
  platform: string;
  raw_id: string;
  net_id: string;
  display_id: string;
  duration: string;
  reason: string;
  permanent: boolean;
  display_name?: string;
  profile_url?: string;
  avatar_url?: string;
  name_source?: string;
};

export type BanList = {
  server_id: number;
  bans: BanEntry[];
  raw: string;
  ok: boolean;
  error?: string | null;
  steam_lookup_enabled?: boolean;
  from_cache?: boolean;
  fetched_at?: string | null;
  page?: number;
  page_size?: number;
  total?: number;
  total_pages?: number;
};

export type PlayerActionLog = {
  id: number;
  platform: string;
  external_id: string;
  action: string;
  server_id?: number | null;
  server_name: string;
  player_name: string;
  reason: string;
  detail: string;
  ok: boolean;
  error: string;
  created_at: string;
};

export type PlayerNote = {
  id: number;
  platform: string;
  external_id: string;
  body: string;
  author_user_id?: number | null;
  author_label?: string;
  created_at: string;
  updated_at: string;
};

export type IdentityDossier = {
  platform: string;
  external_id: string;
  display_name: string;
  profile_url: string;
  avatar_url: string;
  has_info: boolean;
  actions: PlayerActionLog[];
  notes: PlayerNote[];
};

/** Normalize net id to platform + external_id for identity APIs. */
/** Palworld reports platform-prefixed user ids; mirrors identity.PLATFORM_PREFIXES. */
const PLATFORM_PREFIXES: Record<string, string> = {
  steam: "steam",
  gdk: "xbox", // Game Pass / Microsoft Store
  xsx: "xbox",
  xbl: "xbox",
  psn: "psn",
  eos: "eos",
  mac: "mac",
};

export function parseIdentity(netId: string): { platform: string; external_id: string } | null {
  const raw = (netId || "").trim();
  if (!raw) return null;
  const steamNwi = raw.match(/^SteamNWI:(\d{17})$/i);
  if (steamNwi) return { platform: "steam", external_id: steamNwi[1] };
  if (/^\d{17}$/.test(raw)) return { platform: "steam", external_id: raw };
  if (/^EOS:/i.test(raw)) return { platform: "eos", external_id: raw.slice(4) };

  // Before the loose 17-digit search below: an Xbox id that happens to be 17
  // digits must not be filed as a Steam account and sent to the Steam Web API.
  const prefixed = raw.match(/^([A-Za-z]{2,8})_([A-Za-z0-9._-]{4,})$/);
  if (prefixed) {
    const platform = PLATFORM_PREFIXES[prefixed[1].toLowerCase()];
    if (platform) return { platform, external_id: prefixed[2] };
  }

  const anySteam = raw.match(/(\d{17})/);
  if (anySteam) return { platform: "steam", external_id: anySteam[1] };
  return { platform: "unknown", external_id: raw };
}

export function identityKey(platform: string, externalId: string): string {
  return `${platform}:${externalId}`;
}

export type TypeSettings = {
  preferred_gamemode: string;
};

export type AppSettings = {
  query_timeout: number;
  poll_interval_seconds: number;
  stats_interval_seconds: number;
  app_timezone: string;
  types: Record<string, TypeSettings>;
};

export type ClientIpHeaderValue = {
  name: string;
  present: boolean;
  value: string | null;
};

/** Admin helper: IP headers on the current request and how client_ip resolves. */
export type ClientIpDebug = {
  configured_header: string;
  socket_peer: string;
  resolved_client_ip: string;
  headers: ClientIpHeaderValue[];
};

export type StatsRange = "24h" | "7d" | "30d" | "180d" | "1y";

export type PlayerStatPoint = {
  t: string;
  players: number;
  max_players: number;
  online: boolean;
  /** Names present at this sample (empty for legacy samples). */
  player_names?: string[];
  /** Null when the type reports no tick rate, or the server was offline/paused. */
  tick_rate?: number | null;
};

export type PlayerStats = {
  /** Present on admin API only; omitted from public share stats. */
  server_id?: number;
  range: StatsRange;
  from_time: string;
  to_time: string;
  points: PlayerStatPoint[];
  current_players: number | null;
  peak_players: number | null;
  avg_players: number | null;
  /** All null when nothing in range reported a tick rate. */
  current_tick_rate?: number | null;
  min_tick_rate?: number | null;
  avg_tick_rate?: number | null;
};

/** Player-weighted map popularity row (empty samples excluded). */
export type MapStatRow = {
  map_name: string;
  gamemode: string;
  alias: string | null;
  player_minutes: number;
  active_minutes: number;
  avg_players: number;
  peak_players: number;
  active_samples: number;
  /** Met min active-minutes floor used for default ranking. */
  qualified: boolean;
};

export type MapStats = {
  server_id: number;
  range: StatsRange;
  from_time: string;
  to_time: string;
  min_active_minutes: number;
  combine_gamemodes: boolean;
  /** Earliest map-tagged sample in range; null until capture has data. */
  data_since: string | null;
  rows: MapStatRow[];
};

export type ChartShare = {
  token: string;
  url_path: string;
  created_at: string;
};

export type PublicChartMeta = {
  token: string;
  server_name: string;
};

export type MapShare = {
  token: string;
  url_path: string;
  created_at: string;
};

export type PublicMapMeta = {
  token: string;
  server_name: string;
  server_type: string;
};

export type CurrentUser = {
  id: number;
  email: string;
  display_name: string;
  role: "admin" | "user";
  is_admin: boolean;
  totp_enabled: boolean;
  /** Servers this user may operate. Empty with is_admin means "all". */
  server_ids: number[];
};

export type AuthStatus = {
  authenticated: boolean;
  user: CurrentUser | null;
  mfa_required?: boolean;
};

export type PublicConfig = {
  turnstile_enabled: boolean;
  turnstile_site_key: string;
  smtp_enabled: boolean;
  bootstrap_available: boolean;
};

export type ManagedUser = {
  id: number;
  email: string;
  display_name: string;
  role: "admin" | "user";
  is_active: boolean;
  totp_enabled: boolean;
  has_password: boolean;
  /** Temporary lock after failed sign-ins (not the same as disabled). */
  is_locked: boolean;
  locked_until: string | null;
  failed_logins: number;
  server_ids: number[];
  last_login_at: string | null;
  created_at: string;
};

export type InviteResult = {
  user: ManagedUser;
  invite_url: string;
  emailed: boolean;
};

export type MailSettings = {
  host: string;
  port: number;
  user: string;
  /** The password itself is never sent to the client. */
  has_password: boolean;
  starttls: boolean;
  ssl: boolean;
  from_address: string;
  from_name: string;
  base_url: string;
  /** Whether a message could actually be sent right now. */
  enabled: boolean;
  /** False while the settings still come from environment variables. */
  configured: boolean;
};

export type PterodactylSettings = {
  base_url: string;
  /** The API key itself is never sent to the client. */
  has_api_key: boolean;
  verify_tls: boolean;
  /** Whether a panel call could be made right now. Can be false while
   *  has_api_key is true if ENCRYPTION_KEY changed and the stored key no
   *  longer decrypts. */
  enabled: boolean;
};

export type PterodactylSettingsUpdate = {
  base_url: string;
  /** Omit to keep the stored key; "" clears it. */
  api_key?: string;
  verify_tls: boolean;
};

export type PterodactylTestResult = {
  detail: string;
  server_count: number;
};

/** One container in the panel, for the server-linking dropdown. */
export type PterodactylPanelServer = {
  uuid: string;
  identifier: string;
  name: string;
  node: string;
  /** "" is healthy; otherwise installing / suspended / restoring_backup. */
  status: string;
  is_suspended: boolean;
  /** MiB; 0 means unlimited. */
  memory_limit_mb: number;
  disk_limit_mb: number;
  /** Percent of one host CPU (100 = one core); 0 means unlimited. */
  cpu_limit: number;
  /** Set when one of our servers already claims this container. */
  linked_server_id: number | null;
};

export type PterodactylResources = {
  name: string;
  /** Admin-only; used to deep-link into the panel. */
  identifier: string;
  state: string;
  is_suspended: boolean;
  /** Non-empty means installing / transferring - power actions will 409. */
  panel_status: string;
  memory_bytes: number;
  /** Null means unlimited. */
  memory_limit_bytes: number | null;
  disk_bytes: number;
  disk_limit_bytes: number | null;
  /** 100.0 is one full host core. */
  cpu_absolute: number;
  cpu_limit: number | null;
  /** Cumulative since the container started; resets on restart. */
  network_rx_bytes: number;
  network_tx_bytes: number;
  uptime_ms: number;
  /** Age of this reading. A background poller refreshes every linked server,
   *  so it is usually not zero - the card says so rather than implying live. */
  age_seconds: number;
};

/** One bucket of container history. Metrics are null where nothing was
 *  recorded, so an outage draws as a gap rather than an interpolated line. */
export type PterodactylHistoryPoint = {
  t: string;
  cpu_absolute: number | null;
  cpu_peak: number | null;
  memory_bytes: number | null;
  memory_peak: number | null;
  samples: number;
};

export type PterodactylHistory = {
  server_id: number;
  range: StatsRange;
  from_time: string;
  to_time: string;
  /** Internal downsample width; not shown in the UI (range tabs own the timespan). */
  bucket_seconds: number;
  points: PterodactylHistoryPoint[];
  current_cpu_absolute: number | null;
  peak_cpu_absolute: number | null;
  avg_cpu_absolute: number | null;
  current_memory_bytes: number | null;
  peak_memory_bytes: number | null;
  avg_memory_bytes: number | null;
};

export type PterodactylSignal = "start" | "stop" | "restart" | "kill";

export type PterodactylPowerResult = {
  signal: string;
  detail: string;
};

export type PterodactylStartupVariable = {
  env_variable: string;
  name: string;
  description: string;
  server_value: string;
  default_value: string;
  is_editable: boolean;
  rules: string;
};

export type PterodactylStartup = {
  variables: PterodactylStartupVariable[];
  startup_command: string;
  /** True when the egg exposes both MAP_NAME and SCENARIO. */
  has_map_defaults: boolean;
};

export type PterodactylDefaultMapResult = {
  map_alias: string;
  map_name: string;
  scenario: string;
  gamemode_key: string;
  detail: string;
};

export type MailSettingsUpdate = {
  host: string;
  port: number;
  user: string;
  /** Omit to keep the stored password; "" clears it. */
  password?: string;
  starttls: boolean;
  ssl: boolean;
  from_address: string;
  from_name: string;
  base_url: string;
};

/** Carries the HTTP status so callers can branch; extends Error so every
 *  existing `err instanceof Error ? err.message : …` handler still works. */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

let onUnauthorized: (() => void) | null = null;

/** Registered by AuthProvider so an expired session drops straight to /login
 *  instead of rendering "Not authenticated" inside whatever page was open. */
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    // Not for /api/auth/* - a wrong password there is a 401 too, and firing
    // the session-expired handler would bounce the user mid-login.
    if (res.status === 401 && !path.startsWith("/api/auth/")) onUnauthorized?.();
    throw new ApiError(
      res.status,
      typeof detail === "string" ? detail : JSON.stringify(detail),
    );
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}


export type ScheduleAction = {
  id?: number | null;
  action_type: string;
  params: Record<string, unknown>;
  sort_order: number;
};

export type ScheduleCheck = {
  id?: number | null;
  check_type: string;
  params: Record<string, unknown>;
  sort_order: number;
};

export type Schedule = {
  id: number;
  server_id: number;
  server_name: string;
  server_type: string;
  pterodactyl_linked: boolean;
  name: string;
  enabled: boolean;
  time_local: string;
  days_of_week: number[];
  retry_after_minutes: number;
  next_run_at: string;
  last_run_at: string | null;
  last_status: string;
  last_message: string;
  active_window_at: string | null;
  app_timezone: string;
  actions: ScheduleAction[];
  checks: ScheduleCheck[];
  created_at?: string | null;
  updated_at?: string | null;
};

export type ScheduleRun = {
  id: number;
  schedule_id: number | null;
  server_id: number | null;
  schedule_name?: string;
  server_name?: string;
  scheduled_for: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  attempt: number;
  detail: Record<string, unknown>;
  message: string;
};

export type ScheduleMeta = {
  app_timezone: string;
  action_types: {
    id: string;
    label: string;
    params: string[];
    server_types?: string[];
  }[];
  check_types: { id: string; label: string; params: string[] }[];
};

export type ScheduleCreate = {
  server_id: number;
  name: string;
  enabled?: boolean;
  time_local: string;
  days_of_week: number[];
  retry_after_minutes: number;
  actions: {
    action_type: string;
    params?: Record<string, unknown>;
    sort_order?: number;
  }[];
  checks: {
    check_type: string;
    params?: Record<string, unknown>;
    sort_order?: number;
  }[];
};

export const api = {
  authConfig: () => request<PublicConfig>("/api/auth/config"),
  authStatus: () => request<AuthStatus>("/api/auth/status"),
  me: () => request<AuthStatus>("/api/auth/me"),
  updateMe: (display_name: string) =>
    request<AuthStatus>("/api/auth/me", {
      method: "PATCH",
      body: JSON.stringify({ display_name }),
    }),
  bootstrapStatus: () => request<{ available: boolean }>("/api/auth/bootstrap"),
  bootstrapClaim: (data: {
    email: string;
    password: string;
    display_name: string;
    admin_password: string;
    turnstile_token: string;
  }) =>
    request<AuthStatus>("/api/auth/bootstrap-claim", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  login: (email: string, password: string, turnstile_token: string) =>
    request<AuthStatus>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password, turnstile_token }),
    }),
  loginTotp: (code: string) =>
    request<AuthStatus>("/api/auth/login/totp", {
      method: "POST",
      body: JSON.stringify({ code }),
    }),
  logout: () => request<AuthStatus>("/api/auth/logout", { method: "POST" }),
  logoutEverywhere: () =>
    request<AuthStatus>("/api/auth/logout-everywhere", { method: "POST" }),
  changePassword: (current_password: string, new_password: string) =>
    request<AuthStatus>("/api/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    }),
  forgotPassword: (email: string, turnstile_token: string) =>
    request<void>("/api/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ email, turnstile_token }),
    }),
  resetPassword: (token: string, password: string) =>
    request<{ ok: boolean }>("/api/auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ token, password }),
    }),
  checkResetToken: (token: string) =>
    request<{ valid: boolean }>(
      `/api/auth/reset-token/${encodeURIComponent(token)}`,
    ),
  deleteIdentityNote: (noteId: number) =>
    request<void>(`/api/identities/notes/${noteId}`, { method: "DELETE" }),

  totp: {
    setup: (current_password: string) =>
      request<{ secret: string; otpauth_uri: string }>("/api/auth/totp/setup", {
        method: "POST",
        body: JSON.stringify({ current_password }),
      }),
    confirm: (code: string) =>
      request<{ recovery_codes: string[] }>("/api/auth/totp/confirm", {
        method: "POST",
        body: JSON.stringify({ code }),
      }),
    disable: (current_password: string) =>
      request<AuthStatus>("/api/auth/totp/disable", {
        method: "POST",
        body: JSON.stringify({ current_password }),
      }),
  },

  users: {
    list: () => request<ManagedUser[]>("/api/users"),
    create: (data: {
      email: string;
      display_name: string;
      role: string;
      server_ids: number[];
    }) =>
      request<InviteResult>("/api/users", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    update: (
      id: number,
      data: Partial<{ display_name: string; role: string; is_active: boolean }>,
    ) =>
      request<ManagedUser>(`/api/users/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    remove: (id: number) =>
      request<void>(`/api/users/${id}`, { method: "DELETE" }),
    setGrants: (id: number, server_ids: number[]) =>
      request<ManagedUser>(`/api/users/${id}/grants`, {
        method: "PUT",
        body: JSON.stringify({ server_ids }),
      }),
    resetPassword: (id: number) =>
      request<InviteResult>(`/api/users/${id}/reset-password`, { method: "POST" }),
    clearTotp: (id: number) =>
      request<ManagedUser>(`/api/users/${id}/totp`, { method: "DELETE" }),
    forceLogout: (id: number) =>
      request<ManagedUser>(`/api/users/${id}/logout-everywhere`, { method: "POST" }),
    unlock: (id: number) =>
      request<ManagedUser>(`/api/users/${id}/unlock`, { method: "POST" }),
  },

  mail: {
    get: () => request<MailSettings>("/api/mail"),
    update: (data: MailSettingsUpdate) =>
      request<MailSettings>("/api/mail", {
        method: "PUT",
        body: JSON.stringify(data),
      }),
    test: () =>
      request<void>("/api/mail/test", {
        method: "POST",
      }),
  },

  /** Global panel credentials and inventory. Admin-only server-side. */
  pterodactyl: {
    get: () => request<PterodactylSettings>("/api/pterodactyl"),
    update: (data: PterodactylSettingsUpdate) =>
      request<PterodactylSettings>("/api/pterodactyl", {
        method: "PUT",
        body: JSON.stringify(data),
      }),
    test: () =>
      request<PterodactylTestResult>("/api/pterodactyl/test", { method: "POST" }),
    panelServers: (refresh = false) =>
      request<PterodactylPanelServer[]>(
        `/api/pterodactyl/servers${refresh ? "?refresh=true" : ""}`,
      ),
  },

  /** Per-server resources and power. Usable by a granted operator. */
  serverPterodactyl: {
    resources: (id: number) =>
      request<PterodactylResources>(`/api/servers/${id}/pterodactyl/resources`),
    history: (id: number, range: StatsRange = "24h") =>
      request<PterodactylHistory>(
        `/api/servers/${id}/pterodactyl/history?range=${range}`,
      ),
    power: (id: number, signal: PterodactylSignal, confirm = false) =>
      request<PterodactylPowerResult>(`/api/servers/${id}/pterodactyl/power`, {
        method: "POST",
        body: JSON.stringify({ signal, confirm }),
      }),
    startup: (id: number) =>
      request<PterodactylStartup>(`/api/servers/${id}/pterodactyl/startup`),
    updateStartupVariable: (id: number, key: string, value: string) =>
      request<PterodactylStartupVariable>(
        `/api/servers/${id}/pterodactyl/startup/variable`,
        {
          method: "PUT",
          body: JSON.stringify({ key, value }),
        },
      ),
    setDefaultMap: (
      id: number,
      body: { map_id: number; gamemode_key: string },
    ) =>
      request<PterodactylDefaultMapResult>(
        `/api/servers/${id}/pterodactyl/default-map`,
        {
          method: "POST",
          body: JSON.stringify(body),
        },
      ),
  },

  serverTypes: () => request<ServerTypeInfo[]>("/api/servers/types"),
  listServers: () => request<Server[]>("/api/servers"),
  createServer: (data: {
    name: string;
    host: string;
    query_port: number;
    rcon_port: number;
    rcon_password: string;
    server_type: string;
    preferred_gamemode?: string | null;
    options?: Partial<ServerOptions>;
  }) =>
    request<Server>("/api/servers", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateServer: (
    id: number,
    data: Partial<{
      name: string;
      host: string;
      query_port: number;
      rcon_port: number;
      rcon_password: string;
      server_type: string;
      preferred_gamemode: string | null;
      clear_preferred_gamemode: boolean;
      options: Partial<ServerOptions>;
    }>
  ) =>
    request<Server>(`/api/servers/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  deleteServer: (id: number) =>
    request<void>(`/api/servers/${id}`, { method: "DELETE" }),

  status: (id: number) => request<ServerStatus>(`/api/servers/${id}/status`),
  playerStats: (id: number, range: StatsRange = "24h") =>
    request<PlayerStats>(`/api/servers/${id}/player-stats?range=${range}`),
  mapStats: (
    id: number,
    range: StatsRange = "7d",
    opts?: { combineGamemodes?: boolean; minActiveMinutes?: number }
  ) => {
    const q = new URLSearchParams({ range });
    if (opts?.combineGamemodes) q.set("combine_gamemodes", "true");
    if (opts?.minActiveMinutes != null) {
      q.set("min_active_minutes", String(opts.minActiveMinutes));
    }
    return request<MapStats>(`/api/servers/${id}/map-stats?${q.toString()}`);
  },

  createChartShare: (id: number) =>
    request<ChartShare>(`/api/servers/${id}/chart-share`, { method: "POST" }),
  getChartShare: (id: number) => request<ChartShare>(`/api/servers/${id}/chart-share`),
  deleteChartShare: (id: number) =>
    request<void>(`/api/servers/${id}/chart-share`, { method: "DELETE" }),
  publicChartMeta: (token: string) =>
    request<PublicChartMeta>(`/api/public/charts/${encodeURIComponent(token)}/meta`),
  publicChartStats: (token: string, range: StatsRange = "24h") =>
    request<PlayerStats>(
      `/api/public/charts/${encodeURIComponent(token)}/stats?range=${range}`
    ),

  createMapShare: (id: number) =>
    request<MapShare>(`/api/servers/${id}/map-share`, { method: "POST" }),
  getMapShare: (id: number) => request<MapShare>(`/api/servers/${id}/map-share`),
  deleteMapShare: (id: number) =>
    request<void>(`/api/servers/${id}/map-share`, { method: "DELETE" }),
  publicMapMeta: (token: string) =>
    request<PublicMapMeta>(`/api/public/maps/${encodeURIComponent(token)}/meta`),
  publicMapWorld: (token: string) =>
    request<PalworldWorld>(`/api/public/maps/${encodeURIComponent(token)}/world`),
  rcon: (id: number, command: string) =>
    request<RconResult>(`/api/servers/${id}/rcon`, {
      method: "POST",
      body: JSON.stringify({ command }),
    }),
  say: (id: number, message: string) =>
    request<RconResult>(`/api/servers/${id}/say`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  kick: (id: number, player_name: string, reason = "", net_id = "") =>
    request<RconResult>(`/api/servers/${id}/players/kick`, {
      method: "POST",
      body: JSON.stringify({ player_name, reason, net_id }),
    }),
  ban: (
    id: number,
    player_name: string,
    ban_minutes: number,
    reason = "",
    net_id = ""
  ) =>
    request<RconResult>(`/api/servers/${id}/players/ban`, {
      method: "POST",
      body: JSON.stringify({ player_name, ban_minutes, reason, net_id }),
    }),
  permban: (id: number, player_name: string, reason = "", net_id = "") =>
    request<RconResult>(`/api/servers/${id}/players/permban`, {
      method: "POST",
      body: JSON.stringify({ player_name, reason, net_id }),
    }),
  unban: (id: number, net_id: string) =>
    request<RconResult>(`/api/servers/${id}/players/unban`, {
      method: "POST",
      body: JSON.stringify({ net_id }),
    }),
  bans: (id: number, opts?: { refresh?: boolean; page?: number; page_size?: number }) => {
    const params = new URLSearchParams();
    if (opts?.refresh) params.set("refresh", "true");
    if (opts?.page != null) params.set("page", String(opts.page));
    if (opts?.page_size != null) params.set("page_size", String(opts.page_size));
    const q = params.toString();
    return request<BanList>(`/api/servers/${id}/bans${q ? `?${q}` : ""}`);
  },

  identityFlags: (identities: { platform?: string; external_id?: string; net_id?: string; steamid?: string }[]) =>
    request<{ flags: Record<string, boolean> }>("/api/identities/flags", {
      method: "POST",
      body: JSON.stringify({ identities }),
    }),
  identityDossier: (platform: string, externalId: string) =>
    request<IdentityDossier>(
      `/api/identities/${encodeURIComponent(platform)}/${encodeURIComponent(externalId)}`
    ),
  /** Upsert the caller's own note (empty body deletes only their note). */
  setIdentityNote: (platform: string, externalId: string, body: string) =>
    request<PlayerNote | null>(
      `/api/identities/${encodeURIComponent(platform)}/${encodeURIComponent(externalId)}/notes`,
      { method: "PUT", body: JSON.stringify({ body }) }
    ),
  travel: (
    id: number,
    body: {
      map_id: number;
      gamemode_key: string;
      lighting: string;
      execute?: boolean;
    }
  ) =>
    request<RconResult>(`/api/servers/${id}/travel`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  travelPreview: (body: {
    map_id: number;
    gamemode_key: string;
    lighting: string;
  }) =>
    request<{ command: string }>("/api/travel/preview", {
      method: "POST",
      body: JSON.stringify({ ...body, execute: false }),
    }),

  maps: (serverType?: string) =>
    request<MapConfig[]>(
      serverType ? `/api/maps?server_type=${encodeURIComponent(serverType)}` : "/api/maps"
    ),
  gamemodeLabels: (serverType?: string) =>
    request<Record<string, string>>(
      serverType
        ? `/api/gamemode-labels?server_type=${encodeURIComponent(serverType)}`
        : "/api/gamemode-labels"
    ),
  schedules: {
    meta: () => request<ScheduleMeta>("/api/schedules/meta"),
    list: (serverId?: number) =>
      request<Schedule[]>(
        serverId != null
          ? `/api/schedules?server_id=${serverId}`
          : "/api/schedules"
      ),
    get: (id: number) => request<Schedule>(`/api/schedules/${id}`),
    create: (data: ScheduleCreate) =>
      request<Schedule>("/api/schedules", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    update: (
      id: number,
      data: Partial<ScheduleCreate> & { enabled?: boolean }
    ) =>
      request<Schedule>(`/api/schedules/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
      }),
    enable: (id: number, enabled: boolean) =>
      request<Schedule>(`/api/schedules/${id}/enable`, {
        method: "POST",
        body: JSON.stringify({ enabled }),
      }),
    remove: (id: number) =>
      request<{ ok: boolean }>(`/api/schedules/${id}`, { method: "DELETE" }),
    runs: (id: number, limit = 50) =>
      request<ScheduleRun[]>(`/api/schedules/${id}/runs?limit=${limit}`),
    /** Merged history across all schedules (optional schedule filter). */
    allRuns: (opts?: { limit?: number; scheduleId?: number }) => {
      const q = new URLSearchParams();
      q.set("limit", String(opts?.limit ?? 100));
      if (opts?.scheduleId != null) q.set("schedule_id", String(opts.scheduleId));
      return request<ScheduleRun[]>(`/api/schedules/runs?${q.toString()}`);
    },
    runNow: (id: number) =>
      request<Schedule>(`/api/schedules/${id}/run-now`, { method: "POST" }),
  },
  settings: () => request<AppSettings>("/api/settings"),
  updateSettings: (data: Partial<AppSettings>) =>
    request<AppSettings>("/api/settings", {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  clientIpDebug: () => request<ClientIpDebug>("/api/settings/client-ip"),
  history: (serverId: number) =>
    request<
      { id: number; server_id: number | null; command: string; response: string; created_at: string }[]
    >(`/api/servers/${serverId}/history`),

  /** Satisfactory HTTPS API passthrough (only for types with features.admin_api). */
  satisfactory: {
    health: (id: number) =>
      request<{ health: string; server_custom_data: string }>(
        `/api/servers/${id}/satisfactory/health`
      ),
    state: (id: number) =>
      request<SatisfactoryState>(`/api/servers/${id}/satisfactory/state`),
    options: (id: number) =>
      request<SatisfactoryOptions>(`/api/servers/${id}/satisfactory/options`),
    applyOptions: (id: number, options: Record<string, string>) =>
      request<SatisfactoryAction>(`/api/servers/${id}/satisfactory/options`, {
        method: "PUT",
        body: JSON.stringify({ options }),
      }),
    advancedSettings: (id: number) =>
      request<SatisfactoryAdvanced>(`/api/servers/${id}/satisfactory/advanced-settings`),
    applyAdvancedSettings: (
      id: number,
      settings: Record<string, unknown>,
      confirm: boolean
    ) =>
      request<SatisfactoryAction>(`/api/servers/${id}/satisfactory/advanced-settings`, {
        method: "PUT",
        body: JSON.stringify({ settings, confirm }),
      }),
    sessions: (id: number) =>
      request<SatisfactorySessions>(`/api/servers/${id}/satisfactory/sessions`),
    save: (id: number, save_name: string) =>
      request<SatisfactoryAction>(`/api/servers/${id}/satisfactory/save`, {
        method: "POST",
        body: JSON.stringify({ save_name }),
      }),
    load: (id: number, save_name: string, enable_advanced_game_settings = false) =>
      request<SatisfactoryAction>(`/api/servers/${id}/satisfactory/load`, {
        method: "POST",
        body: JSON.stringify({ save_name, enable_advanced_game_settings }),
      }),
    deleteSave: (id: number, saveName: string) =>
      request<SatisfactoryAction>(
        `/api/servers/${id}/satisfactory/saves/${encodeURIComponent(saveName)}?confirm=true`,
        { method: "DELETE" }
      ),
    deleteSession: (id: number, sessionName: string) =>
      request<SatisfactoryAction>(
        `/api/servers/${id}/satisfactory/sessions/${encodeURIComponent(sessionName)}?confirm=true`,
        { method: "DELETE" }
      ),
    rename: (id: number, server_name: string) =>
      request<SatisfactoryAction>(`/api/servers/${id}/satisfactory/rename`, {
        method: "POST",
        body: JSON.stringify({ server_name }),
      }),
    setAutoLoad: (id: number, session_name: string) =>
      request<SatisfactoryAction>(`/api/servers/${id}/satisfactory/auto-load`, {
        method: "POST",
        body: JSON.stringify({ session_name }),
      }),
    setClientPassword: (id: number, password: string) =>
      request<SatisfactoryAction>(`/api/servers/${id}/satisfactory/passwords/client`, {
        method: "POST",
        body: JSON.stringify({ password }),
      }),
    setAdminPassword: (id: number, password: string) =>
      request<SatisfactoryAction>(`/api/servers/${id}/satisfactory/passwords/admin`, {
        method: "POST",
        body: JSON.stringify({ password }),
      }),
    claim: (id: number, server_name: string, admin_password: string) =>
      request<SatisfactoryAction>(`/api/servers/${id}/satisfactory/claim`, {
        method: "POST",
        body: JSON.stringify({ server_name, admin_password }),
      }),
    newGame: (
      id: number,
      body: {
        session_name: string;
        map_name?: string;
        starting_location?: string;
        skip_onboarding?: boolean;
      }
    ) =>
      request<SatisfactoryAction>(`/api/servers/${id}/satisfactory/new-game`, {
        method: "POST",
        body: JSON.stringify({ ...body, confirm: true }),
      }),
    shutdown: (id: number) =>
      request<SatisfactoryAction>(`/api/servers/${id}/satisfactory/shutdown`, {
        method: "POST",
        body: JSON.stringify({ confirm: true }),
      }),
  },

  /** Palworld REST API passthrough (only for types with features.admin_api). */
  palworld: {
    // No info/metrics/players bindings: those reach the page through
    // ServerStatus.extra and PlayerInfo.extra, so the panel never calls the
    // passthrough routes. They stay served for anything scripting the API.
    settings: (id: number) =>
      request<{ settings: Record<string, string | number | boolean> }>(
        `/api/servers/${id}/palworld/settings`
      ),
    world: (id: number) => request<PalworldWorld>(`/api/servers/${id}/palworld/world`),
    announce: (id: number, message: string) =>
      request<PalworldAction>(`/api/servers/${id}/palworld/announce`, {
        method: "POST",
        body: JSON.stringify({ message }),
      }),
    save: (id: number) =>
      request<PalworldAction>(`/api/servers/${id}/palworld/save`, { method: "POST" }),
    shutdown: (id: number, waittime: number, message: string) =>
      request<PalworldAction>(`/api/servers/${id}/palworld/shutdown`, {
        method: "POST",
        body: JSON.stringify({ waittime, message, confirm: true }),
      }),
    stop: (id: number) =>
      request<PalworldAction>(`/api/servers/${id}/palworld/stop`, {
        method: "POST",
        body: JSON.stringify({ confirm: true }),
      }),
  },
};

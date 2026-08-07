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
};

export type Server = {
  id: number;
  name: string;
  host: string;
  query_port: number;
  rcon_port: number;
  server_type: string;
  preferred_gamemode?: string | null;
  has_rcon_password: boolean;
  options: ServerOptions;
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
  types: Record<string, TypeSettings>;
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
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

export const api = {
  authStatus: () => request<{ authenticated: boolean }>("/api/auth/status"),
  login: (password: string) =>
    request<{ authenticated: boolean }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),
  logout: () =>
    request<{ authenticated: boolean }>("/api/auth/logout", { method: "POST" }),
  changePassword: (current_password: string, new_password: string) =>
    request<{ authenticated: boolean }>("/api/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    }),

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
  /** Upsert the single admin note document (empty body clears). */
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
  settings: () => request<AppSettings>("/api/settings"),
  updateSettings: (data: Partial<AppSettings>) =>
    request<AppSettings>("/api/settings", {
      method: "PUT",
      body: JSON.stringify(data),
    }),
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

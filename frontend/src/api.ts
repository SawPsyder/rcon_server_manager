export type ServerFeatures = {
  map_travel: boolean;
  structured_player_list: boolean;
  kick_ban: boolean;
  admin_say: boolean;
  a2s_query: boolean;
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
  duration: number;
  duration_pretty: string;
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
};

export type BanList = {
  server_id: number;
  bans: BanEntry[];
  raw: string;
  ok: boolean;
  error?: string | null;
};

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
};

export type PlayerStats = {
  server_id: number;
  range: StatsRange;
  from_time: string;
  to_time: string;
  sample_count: number;
  points: PlayerStatPoint[];
  current_players: number | null;
  peak_players: number | null;
  avg_players: number | null;
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
  kick: (id: number, player_name: string, reason = "") =>
    request<RconResult>(`/api/servers/${id}/players/kick`, {
      method: "POST",
      body: JSON.stringify({ player_name, reason }),
    }),
  ban: (id: number, player_name: string, ban_minutes: number, reason = "") =>
    request<RconResult>(`/api/servers/${id}/players/ban`, {
      method: "POST",
      body: JSON.stringify({ player_name, ban_minutes, reason }),
    }),
  permban: (id: number, player_name: string, reason = "") =>
    request<RconResult>(`/api/servers/${id}/players/permban`, {
      method: "POST",
      body: JSON.stringify({ player_name, reason }),
    }),
  unban: (id: number, net_id: string) =>
    request<RconResult>(`/api/servers/${id}/players/unban`, {
      method: "POST",
      body: JSON.stringify({ net_id }),
    }),
  bans: (id: number) => request<BanList>(`/api/servers/${id}/bans`),
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
};

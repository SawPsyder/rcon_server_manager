import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import {
  api,
  AppSettings,
  BanEntry,
  identityKey,
  MapConfig,
  parseIdentity,
  QuickButton,
  Server,
  ServerStatus,
  ServerTypeInfo,
} from "../api";
import BanListPanel from "../components/BanListPanel";
import IdentityDossierModal from "../components/IdentityDossierModal";
import IdentityInfoButton from "../components/IdentityInfoButton";
import PlayerStatsChart from "../components/PlayerStatsChart";
import TickRateChart from "../components/TickRateChart";
import PalworldAdminPanel from "../components/PalworldAdminPanel";
import SatisfactoryAdminPanel from "../components/SatisfactoryAdminPanel";
import { overviewBackSearch } from "./OverviewPage";

type AdminPanelProps = { serverId: number; onChanged?: () => void };

/** Which admin panel a type gets. features.admin_api only says one exists. */
const ADMIN_PANELS: Record<string, React.ComponentType<AdminPanelProps>> = {
  satisfactory: SatisfactoryAdminPanel,
  palworld: PalworldAdminPanel,
};

/** snake_case PlayerInfo.extra keys → readable column headers. */
function playerExtraLabel(key: string): string {
  const known: Record<string, string> = {
    account_name: "Account",
    level: "Level",
    ping_ms: "Ping (ms)",
  };
  return known[key] || key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

/** snake_case status extras → readable stat labels. */
function extraStatLabel(key: string): string {
  const known: Record<string, string> = {
    health: "API health",
    average_tick_rate: "Tick rate",
    tech_tier: "Tech tier",
    active_schematic: "Milestone",
    total_game_duration: "Play time",
    is_game_running: "Game running",
    is_game_paused: "Paused",
    auto_load_session_name: "Auto-load",
    // Palworld (/v1/api/metrics + /info)
    version: "Version",
    server_fps: "Server FPS",
    frame_time_ms: "Frame time (ms)",
    uptime: "Uptime",
    in_game_days: "In-game days",
    base_camps: "Base camps",
    world_guid: "World GUID",
  };
  if (known[key]) return known[key];
  return key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function formatExtraStat(value: string | number | boolean | null): string {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return value === null ? "—" : String(value);
}

/** Seed status cards immediately from last cached poll (no wait for live query). */
function statusFromServerCache(server: Server): ServerStatus {
  return {
    online: server.last_online ?? false,
    host: server.host,
    query_port: server.query_port,
    server_type: server.server_type,
    hostname: server.last_hostname ?? null,
    map: server.last_map ?? null,
    lighting: server.last_lighting ?? null,
    gamemode: server.last_gamemode ?? null,
    coop_or_versus: server.last_coop_or_versus ?? null,
    players: server.last_players ?? null,
    max_players: server.last_max_players ?? null,
    player_list: [],
    from_cache: true,
    last_status_at: server.last_status_at ?? null,
  };
}

export default function ServerDetailPage() {
  const { serverId: serverIdParam } = useParams<{ serverId: string }>();
  const location = useLocation();
  const serverId = Number(serverIdParam);
  const validServerId = Number.isFinite(serverId) && serverId > 0 ? serverId : null;
  const backSearch = overviewBackSearch(location.state);

  const [servers, setServers] = useState<Server[]>([]);
  const [status, setStatus] = useState<ServerStatus | null>(null);
  const [maps, setMaps] = useState<MapConfig[]>([]);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [serverTypes, setServerTypes] = useState<ServerTypeInfo[]>([]);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [rconCmd, setRconCmd] = useState("");
  const [output, setOutput] = useState("");
  const [busy, setBusy] = useState(false);
  const [selectedPlayer, setSelectedPlayer] = useState<string>("");
  const [selectedNetId, setSelectedNetId] = useState<string>("");
  const [sayMsg, setSayMsg] = useState("");
  const [unbanId, setUnbanId] = useState("");
  const [bans, setBans] = useState<BanEntry[]>([]);
  const [bansRaw, setBansRaw] = useState("");
  const [bansError, setBansError] = useState("");
  const [bansLoading, setBansLoading] = useState(false);
  const [showBansRaw, setShowBansRaw] = useState(false);
  const [steamLookupEnabled, setSteamLookupEnabled] = useState(false);
  const [bansFromCache, setBansFromCache] = useState(false);
  const [bansFetchedAt, setBansFetchedAt] = useState<string | null>(null);
  const [bansPage, setBansPage] = useState(1);
  const [bansPageSize] = useState(25);
  const [bansTotal, setBansTotal] = useState(0);
  const [bansTotalPages, setBansTotalPages] = useState(1);
  const [identityFlags, setIdentityFlags] = useState<Record<string, boolean>>({});
  const [dossierOpen, setDossierOpen] = useState(false);
  const [dossierNetId, setDossierNetId] = useState("");
  const [dossierName, setDossierName] = useState("");

  const [mapId, setMapId] = useState<number | "">("");
  const [gamemode, setGamemode] = useState("");
  const [lighting, setLighting] = useState("Day");
  const [travelPreview, setTravelPreview] = useState("");

  const selectedServer = useMemo(
    () => (validServerId ? servers.find((s) => s.id === validServerId) || null : null),
    [servers, validServerId]
  );

  const serverType = selectedServer?.server_type || status?.server_type || "sandstorm";
  const typeLabel = useMemo(
    () => serverTypes.find((t) => t.id === serverType)?.label || serverType,
    [serverTypes, serverType]
  );

  // Prefer live status features; fall back to the type registry so charts/panels
  // still appear while status is cache-seeded or a poll fails.
  const features = useMemo(() => {
    const defaults = {
      map_travel: false,
      structured_player_list: false,
      player_score: true,
      kick_ban: false,
      ban_list: false,
      admin_say: false,
      a2s_query: true,
      admin_api: false,
      console: true,
      tick_rate_history: false,
      tls_optional: false,
    };
    const fromType = serverTypes.find((t) => t.id === serverType)?.features;
    return { ...defaults, ...fromType, ...status?.features };
  }, [serverTypes, serverType, status?.features]);
  const AdminPanel = features.admin_api ? ADMIN_PANELS[serverType] : undefined;
  const banListSource =
    serverTypes.find((t) => t.id === serverType)?.ban_list_source || "live";
  /** Per-game naming for the tick_rate series (Source ticks vs server FPS). */
  const tickRate = useMemo(() => {
    const t = serverTypes.find((x) => x.id === serverType);
    return {
      label: t?.tick_rate_label || "Tick rate",
      unit: t?.tick_rate_unit || "tps",
      target: t?.tick_rate_target || 30,
    };
  }, [serverTypes, serverType]);
  /** Game-specific per-player columns, in the order the adapter emitted them.
   *  Union across rows so a field only some players report still gets a column. */
  const playerExtraKeys = useMemo(() => {
    const keys: string[] = [];
    for (const p of status?.player_list || []) {
      for (const key of Object.keys(p.extra || {})) {
        if (!keys.includes(key)) keys.push(key);
      }
    }
    return keys;
  }, [status?.player_list]);
  /** Game-specific status scalars (tick rate, tech tier, ...) as stat cards. */
  const extraStats = useMemo(
    () => Object.entries(status?.extra || {}).filter(([, v]) => v !== null && v !== ""),
    [status]
  );

  const selectedMap = useMemo(
    () => maps.find((m) => m.id === mapId) || null,
    [maps, mapId]
  );

  const preferredGamemode = useMemo(() => {
    if (selectedServer?.preferred_gamemode) return selectedServer.preferred_gamemode;
    const st = selectedServer?.server_type || status?.server_type || "sandstorm";
    return settings?.types?.[st]?.preferred_gamemode || "";
  }, [selectedServer, status, settings]);

  const quickButtons: QuickButton[] = useMemo(() => {
    const st = selectedServer?.server_type || status?.server_type || "sandstorm";
    return serverTypes.find((t) => t.id === st)?.quick_buttons || [];
  }, [selectedServer, status, serverTypes]);

  const refreshIdentityFlags = useCallback(async (netIds: string[]) => {
    const identities = netIds
      .map((n) => parseIdentity(n))
      .filter((x): x is { platform: string; external_id: string } => Boolean(x))
      .map((x) => ({ platform: x.platform, external_id: x.external_id }));
    if (!identities.length) return;
    try {
      const res = await api.identityFlags(identities);
      setIdentityFlags((prev) => ({ ...prev, ...(res.flags || {}) }));
    } catch {
      /* ignore */
    }
  }, []);

  const openDossier = (netId: string, name?: string) => {
    if (!parseIdentity(netId)) return;
    setDossierNetId(netId);
    setDossierName(name || "");
    setDossierOpen(true);
  };

  const loadBase = useCallback(async () => {
    const [sv, st, ty] = await Promise.all([
      api.listServers(),
      api.settings(),
      api.serverTypes(),
    ]);
    setServers(sv);
    setSettings(st);
    setServerTypes(ty);
  }, []);

  const loadServerExtras = useCallback(async (server: Server) => {
    const st = server.server_type || "sandstorm";
    try {
      const [mp, lb] = await Promise.all([api.maps(st), api.gamemodeLabels(st)]);
      setMaps(mp);
      setLabels(lb);
    } catch (e) {
      setOutput(String(e));
    }
  }, []);

  const refreshStatus = useCallback(async () => {
    if (!validServerId) return;
    try {
      const s = await api.status(validServerId);
      setStatus(s);
      // Keep server list cache in sync so re-select stays instant
      setServers((prev) =>
        prev.map((srv) =>
          srv.id === validServerId
            ? {
                ...srv,
                last_hostname: s.hostname ?? srv.last_hostname,
                last_map: s.map ?? srv.last_map,
                last_lighting: s.lighting ?? srv.last_lighting,
                last_gamemode: s.gamemode ?? srv.last_gamemode,
                last_coop_or_versus: s.coop_or_versus ?? srv.last_coop_or_versus,
                last_players: s.players ?? srv.last_players,
                last_max_players: s.max_players ?? srv.last_max_players,
                last_online: s.online,
                last_status_at: s.last_status_at ?? srv.last_status_at,
              }
            : srv
        )
      );
      const steamIds = (s.player_list || [])
        .map((p) => p.steamid || "")
        .filter(Boolean);
      if (steamIds.length) refreshIdentityFlags(steamIds);
    } catch (e) {
      setStatus((prev) => ({
        online: false,
        host: prev?.host || "",
        query_port: prev?.query_port || 0,
        hostname: prev?.hostname,
        map: prev?.map,
        lighting: prev?.lighting,
        gamemode: prev?.gamemode,
        coop_or_versus: prev?.coop_or_versus,
        players: prev?.players,
        max_players: prev?.max_players,
        player_list: prev?.player_list || [],
        from_cache: true,
        error: e instanceof Error ? e.message : String(e),
      }));
    }
  }, [validServerId, refreshIdentityFlags]);

  useEffect(() => {
    loadBase().catch((e) => setOutput(String(e)));
  }, [loadBase]);

  useEffect(() => {
    if (!validServerId || !selectedServer) return;
    // Seed from list payload already returned by /api/servers (includes last_*)
    setStatus(statusFromServerCache(selectedServer));
    loadServerExtras(selectedServer);
    refreshStatus();
    // Only re-seed when the selected server id changes — not when cache fields update after poll
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional: avoid loop with setServers in refreshStatus
  }, [validServerId, selectedServer?.id, refreshStatus, loadServerExtras]);

  useEffect(() => {
    if (!validServerId || !settings) return;
    const t = window.setInterval(refreshStatus, settings.poll_interval_seconds * 1000);
    return () => window.clearInterval(t);
  }, [validServerId, settings, refreshStatus]);

  useEffect(() => {
    if (!selectedMap) {
      setGamemode("");
      setLighting("Day");
      return;
    }
    const keys = Object.keys(selectedMap.gamemodes);
    setGamemode(
      preferredGamemode && keys.includes(preferredGamemode)
        ? preferredGamemode
        : keys[0] || ""
    );
    setLighting(selectedMap.lightings[0] || "Day");
  }, [selectedMap, preferredGamemode]);

  useEffect(() => {
    if (!mapId || !gamemode) {
      setTravelPreview("");
      return;
    }
    api
      .travelPreview({
        map_id: Number(mapId),
        gamemode_key: gamemode,
        lighting,
      })
      .then((r) => setTravelPreview(r.command))
      .catch(() => setTravelPreview(""));
  }, [mapId, gamemode, lighting]);

  const showResult = (title: string, res: { ok: boolean; response: string; error?: string | null; command: string }) => {
    const body = res.ok ? res.response || "(no response body)" : res.error || "failed";
    setOutput(`[${title}] ${res.command}\n\n${body}`);
  };

  const applyBanList = useCallback(
    (res: Awaited<ReturnType<typeof api.bans>>) => {
      setSteamLookupEnabled(Boolean(res.steam_lookup_enabled));
      setBansFromCache(Boolean(res.from_cache));
      setBansFetchedAt(res.fetched_at || null);
      setBans(res.bans || []);
      setBansRaw(res.raw || "");
      setBansPage(res.page || 1);
      setBansTotal(res.total ?? 0);
      setBansTotalPages(res.total_pages || 1);
      if (!res.ok) {
        setBansError(res.error || "Failed to load bans");
      } else {
        setBansError(
          (res.bans || []).length === 0 && (res.total ?? 0) === 0 && res.raw
            ? "Response received but no ban rows could be parsed — try Show raw."
            : res.error || ""
        );
      }
      const ids = (res.bans || []).map((b) => b.raw_id).filter(Boolean);
      if (ids.length) refreshIdentityFlags(ids);
    },
    [refreshIdentityFlags]
  );

  /** Load bans from DB cache (default) or live RCON when refresh=true. */
  const loadBans = useCallback(
    async (opts?: { refresh?: boolean; page?: number }) => {
      if (!validServerId) return;
      const page = opts?.page ?? bansPage;
      const refresh = Boolean(opts?.refresh);
      setBansLoading(true);
      setBansError("");
      try {
        const res = await api.bans(validServerId, {
          refresh,
          page,
          page_size: bansPageSize,
        });
        applyBanList(res);
      } catch (e) {
        setBans([]);
        setBansError(e instanceof Error ? e.message : String(e));
      } finally {
        setBansLoading(false);
      }
    },
    [validServerId, bansPage, bansPageSize, applyBanList]
  );

  // Load cached bans when switching server
  useEffect(() => {
    if (!validServerId || !features.kick_ban) {
      setBans([]);
      setBansRaw("");
      setBansFetchedAt(null);
      setBansPage(1);
      setBansTotal(0);
      setBansTotalPages(1);
      return;
    }
    setBansPage(1);
    loadBans({ refresh: false, page: 1 });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when server changes
  }, [validServerId, features.kick_ban]);

  const runRcon = async (command: string, title = "RCON") => {
    if (!validServerId) return;
    const cmd = command.trim();
    // Manual listbans in console still refreshes the ban cache/table
    if (cmd.toLowerCase() === "listbans") {
      setBusy(true);
      try {
        const res = await api.bans(validServerId, {
          refresh: true,
          page: 1,
          page_size: bansPageSize,
        });
        applyBanList(res);
        setOutput(
          res.ok
            ? `[List Bans] listbans\n\nCached ${res.total ?? 0} ban(s). See table below.`
            : `[List Bans] listbans\n\n${res.error || "failed"}`
        );
      } catch (e) {
        setBansError(e instanceof Error ? e.message : String(e));
        setOutput(String(e));
      } finally {
        setBusy(false);
        setBansLoading(false);
      }
      return;
    }
    setBusy(true);
    try {
      const res = await api.rcon(validServerId, cmd);
      showResult(title, res);
      await refreshStatus();
    } catch (e) {
      setOutput(String(e));
    } finally {
      setBusy(false);
    }
  };

  const unbanPlayer = async (netId: string) => {
    if (!validServerId) return;
    setBusy(true);
    try {
      const res = await api.unban(validServerId, netId);
      showResult("Unban", res);
      if (res.ok) {
        await loadBans({ refresh: false, page: bansPage });
        refreshIdentityFlags([netId]);
      }
    } catch (e) {
      setOutput(String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!validServerId) {
    return (
      <div className="card">
        <h2>Invalid server</h2>
        <p className="muted">That server id is not valid.</p>
        <Link className="btn primary" to={{ pathname: "/", search: backSearch }}>
          Back to overview
        </Link>
      </div>
    );
  }

  if (servers.length > 0 && !selectedServer) {
    return (
      <div className="card">
        <h2>Server not found</h2>
        <p className="muted">No server with id {validServerId} is configured.</p>
        <Link className="btn primary" to={{ pathname: "/", search: backSearch }}>
          Back to overview
        </Link>
      </div>
    );
  }

  if (!servers.length) {
    return (
      <div className="card">
        <h2>No servers configured</h2>
        <p className="muted">Add a game server to start querying and using RCON.</p>
        <Link className="btn primary" to="/servers">
          Go to Servers
        </Link>
      </div>
    );
  }

  return (
    <div className="stack">
      <div className="row between wrap">
        <div className="row wrap" style={{ alignItems: "center" }}>
          <Link className="btn ghost" to={{ pathname: "/", search: backSearch }}>
            ← Back to overview
          </Link>
          <h1 className="server-detail-title">{selectedServer?.name || "Server"}</h1>
          <button className="btn" onClick={() => refreshStatus()} disabled={busy}>
            Refresh
          </button>
        </div>
        <div className={`pill ${status?.online ? "online" : "offline"}`}>
          {status?.online ? "ONLINE" : "OFFLINE"}
          {status?.ping_ms != null && status.online ? ` · ${status.ping_ms} ms` : ""}
          {status?.from_cache && !status?.ping_ms ? " · cached" : ""}
        </div>
      </div>

      <section className="stats">
        <div className="stat card">
          <div className="stat-label">Hostname</div>
          <div className="stat-value">{status?.hostname || "—"}</div>
        </div>
        {/* Conditional like Gamemode below: games with a single world report no
            map at all (Palworld), and an always-empty card is just noise. */}
        {(status?.map || status?.lighting) && (
          <div className="stat card">
            <div className="stat-label">Map</div>
            <div className="stat-value">
              {status?.map || "—"}
              {status?.lighting ? ` (${status.lighting})` : ""}
            </div>
          </div>
        )}
        <div className="stat card">
          <div className="stat-label">Players</div>
          <div className="stat-value">
            {status?.players ?? "—"}/{status?.max_players ?? "—"}
          </div>
        </div>
        {(status?.gamemode || status?.coop_or_versus) && (
          <div className="stat card">
            <div className="stat-label">Gamemode</div>
            <div className="stat-value">
              {status?.gamemode || "—"}
              {status?.coop_or_versus ? ` · ${status.coop_or_versus}` : ""}
            </div>
          </div>
        )}
        {extraStats.map(([key, value]) => (
          <div className="stat card" key={key}>
            <div className="stat-label">{extraStatLabel(key)}</div>
            <div className="stat-value">{formatExtraStat(value)}</div>
          </div>
        ))}
      </section>

      {status?.error && <div className="alert error">{status.error}</div>}

      <PlayerStatsChart serverId={validServerId} showShare />

      {/* Separate chart, not a second axis on the player one — tick rate and
          player count share no scale. */}
      {features.tick_rate_history && validServerId && (
        <TickRateChart
          serverId={validServerId}
          label={tickRate.label}
          unit={tickRate.unit}
          target={tickRate.target}
        />
      )}

      {/* Hidden only for games that expose no per-player data at all
          (Satisfactory's API returns a count, never a roster). */}
      {(features.structured_player_list || features.a2s_query) && (
      <section className="card">
        <h2>Players</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th title="Place by total time on this server (x/y = rank / tracked players)">
                  Rank
                </th>
                <th title="Player display name from the server">Name</th>
                {features.player_score && (
                  <th title="Current in-game score">Score</th>
                )}
                {playerExtraKeys.map((key) => (
                  <th key={key} title={playerExtraLabel(key)}>
                    {playerExtraLabel(key)}
                  </th>
                ))}
                <th title="Time in the current continuous join (this session)">
                  Session
                </th>
                <th title="All tracked playtime on this server">Total</th>
                <th title="Times seen after being absent from a sample (re-joins)">
                  Visits
                </th>
                {/* Everyone listed here is online, so "last seen" would read
                    "Online" on every row — show when they were previously on. */}
                <th title="End of the player's previous session, before this one">
                  Last visit
                </th>
                <th title="Last known IP address reported by the server">IP</th>
                {/* Not always a SteamID64: crossplay games report platform-
                    prefixed ids such as gdk_… (Xbox) or psn_… */}
                <th title="Platform account id used by kick, ban and unban">
                  Player ID
                </th>
              </tr>
            </thead>
            <tbody>
              {(status?.player_list || []).length === 0 ? (
                <tr>
                  <td
                    colSpan={8 + (features.player_score ? 1 : 0) + playerExtraKeys.length}
                    className="muted"
                  >
                    No players reported
                  </td>
                </tr>
              ) : (
                status!.player_list.map((p) => (
                  <tr
                    key={`${p.steamid || p.id}-${p.name}`}
                    className={selectedPlayer === p.name ? "selected" : ""}
                    onClick={() => {
                      setSelectedPlayer(p.name);
                      setSelectedNetId(p.steamid || "");
                    }}
                  >
                    <td>
                      <span
                        className={`rank-badge ${
                          p.rank === 1 ? "gold" : p.rank === 2 ? "silver" : p.rank === 3 ? "bronze" : ""
                        }`}
                      >
                        {p.rank != null && (p.ranked_players ?? 0) > 0
                          ? `${p.rank}/${p.ranked_players}`
                          : "—"}
                      </span>
                    </td>
                    <td>
                      <span className="name-with-info">
                        <span>{p.name}</span>
                        {p.steamid ? (
                          <IdentityInfoButton
                            hasInfo={(() => {
                              const id = parseIdentity(p.steamid);
                              return id
                                ? Boolean(
                                    identityFlags[identityKey(id.platform, id.external_id)]
                                  )
                                : false;
                            })()}
                            onClick={() => openDossier(p.steamid!, p.name)}
                          />
                        ) : null}
                      </span>
                    </td>
                    {features.player_score && <td>{p.score}</td>}
                    {playerExtraKeys.map((key) => (
                      <td key={key}>{formatExtraStat(p.extra?.[key] ?? null)}</td>
                    ))}
                    <td>{p.session_pretty || "0s"}</td>
                    <td>{p.total_pretty || "0s"}</td>
                    <td>{p.visit_count ?? 0}</td>
                    <td title={p.previous_seen_at || undefined}>
                      {p.previous_seen_pretty || "—"}
                    </td>
                    <td>
                      <code className="steam-id">{p.ip || "—"}</code>
                    </td>
                    <td>
                      <code className="steam-id">{p.steamid || "—"}</code>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        {features.kick_ban && (
          <>
            <div className="row wrap" style={{ marginTop: "0.75rem" }}>
              <button
                className="btn"
                disabled={!selectedPlayer || busy || !validServerId}
                onClick={() => {
                  const reason = prompt("Kick reason", "Kicked by admin") || "";
                  if (!validServerId || !selectedPlayer) return;
                  setBusy(true);
                  api
                    .kick(validServerId, selectedPlayer, reason, selectedNetId)
                    .then((r) => {
                      showResult("Kick", r);
                      refreshStatus();
                      if (selectedNetId) refreshIdentityFlags([selectedNetId]);
                    })
                    .finally(() => setBusy(false));
                }}
              >
                Kick
              </button>
              <button
                className="btn"
                disabled={!selectedPlayer || busy || !validServerId}
                onClick={() => {
                  const minutes = Number(prompt("Ban minutes", "60") || "60");
                  const reason = prompt("Ban reason", "Banned by admin") || "";
                  if (!validServerId || !selectedPlayer) return;
                  setBusy(true);
                  api
                    .ban(validServerId, selectedPlayer, minutes, reason, selectedNetId)
                    .then((r) => {
                      showResult("Ban", r);
                      refreshStatus();
                      if (selectedNetId) refreshIdentityFlags([selectedNetId]);
                    })
                    .finally(() => setBusy(false));
                }}
              >
                Ban
              </button>
              <button
                className="btn danger"
                disabled={!selectedPlayer || busy || !validServerId}
                onClick={() => {
                  if (!confirm(`Permban ${selectedPlayer}?`)) return;
                  const reason = prompt("Reason", "Permanently banned") || "";
                  if (!validServerId || !selectedPlayer) return;
                  setBusy(true);
                  api
                    .permban(validServerId, selectedPlayer, reason, selectedNetId)
                    .then((r) => {
                      showResult("Permban", r);
                      refreshStatus();
                      if (selectedNetId) refreshIdentityFlags([selectedNetId]);
                    })
                    .finally(() => setBusy(false));
                }}
              >
                Permban
              </button>
              {features.structured_player_list && (
                <button
                  className="btn ghost"
                  disabled={busy || !validServerId}
                  onClick={() => runRcon("listplayers", "ListPlayers")}
                >
                  List player IDs
                </button>
              )}
            </div>
            <div className="row wrap" style={{ marginTop: "0.75rem" }}>
              <input
                placeholder="Manual unban: Steam / SteamNWI:… / EOS:…"
                value={unbanId}
                onChange={(e) => setUnbanId(e.target.value)}
              />
              <button
                className="btn"
                disabled={!unbanId || busy || !validServerId}
                onClick={() => unbanPlayer(unbanId.trim())}
              >
                Unban
              </button>
            </div>
          </>
        )}
      </section>
      )}

      {features.map_travel && (
        <section className="card">
          <h2>Map travel</h2>
          <div className="form-grid">
            <label>
              Map
              <select
                value={mapId}
                onChange={(e) => setMapId(e.target.value ? Number(e.target.value) : "")}
              >
                <option value="">Select map…</option>
                {maps.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.alias}
                    {m.self_added ? " ★" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Gamemode
              <select
                value={gamemode}
                onChange={(e) => setGamemode(e.target.value)}
                disabled={!selectedMap}
              >
                {selectedMap &&
                  Object.keys(selectedMap.gamemodes).map((k) => (
                    <option key={k} value={k}>
                      {labels[k] || k}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              Lighting
              <select
                value={lighting}
                onChange={(e) => setLighting(e.target.value)}
                disabled={!selectedMap}
              >
                {(selectedMap?.lightings || ["Day"]).map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
            {travelPreview && (
              <div className="full code-block">{travelPreview}</div>
            )}
            <div className="full">
              <button
                className="btn primary"
                disabled={!validServerId || !mapId || !gamemode || busy}
                onClick={() => {
                  if (!validServerId || !mapId || !gamemode) return;
                  setBusy(true);
                  api
                    .travel(validServerId, {
                      map_id: Number(mapId),
                      gamemode_key: gamemode,
                      lighting,
                      execute: true,
                    })
                    .then((r) => {
                      showResult("Travel", r);
                      setTimeout(refreshStatus, 2000);
                    })
                    .finally(() => setBusy(false));
                }}
              >
                Change map
              </button>
            </div>
          </div>
        </section>
      )}

      {AdminPanel && validServerId && (
        <AdminPanel serverId={validServerId} onChanged={refreshStatus} />
      )}

      {features.console && (
      <section className="card">
        <h2>{features.a2s_query ? "RCON console" : `${typeLabel} console`}</h2>
        <div className="row wrap">
          {quickButtons.map((b) => (
            <button
              key={b.command}
              className="btn"
              disabled={busy || !validServerId}
              onClick={() => runRcon(b.command, b.label)}
            >
              {b.label}
            </button>
          ))}
        </div>
        <div className="row wrap" style={{ marginTop: "0.75rem" }}>
          <input
            className="grow"
            value={rconCmd}
            onChange={(e) => setRconCmd(e.target.value)}
            placeholder={
              features.a2s_query ? "Enter RCON command…" : "Enter console command…"
            }
            onKeyDown={(e) => {
              if (e.key === "Enter" && rconCmd.trim()) runRcon(rconCmd.trim());
            }}
          />
          <button
            className="btn primary"
            disabled={!rconCmd.trim() || busy || !validServerId}
            onClick={() => runRcon(rconCmd.trim())}
          >
            Execute
          </button>
        </div>
        {features.admin_say && (
          <div className="row wrap" style={{ marginTop: "0.75rem" }}>
            <input
              className="grow"
              value={sayMsg}
              onChange={(e) => setSayMsg(e.target.value)}
              placeholder="Admin say message"
              onKeyDown={(e) => {
                if (e.key === "Enter" && sayMsg.trim() && validServerId) {
                  setBusy(true);
                  api
                    .say(validServerId, sayMsg.trim())
                    .then((r) => {
                      showResult("Say", r);
                      setSayMsg("");
                    })
                    .finally(() => setBusy(false));
                }
              }}
            />
            <button
              className="btn"
              disabled={!sayMsg.trim() || busy || !validServerId}
              onClick={() => {
                if (!validServerId) return;
                setBusy(true);
                api
                  .say(validServerId, sayMsg.trim())
                  .then((r) => {
                    showResult("Say", r);
                    setSayMsg("");
                  })
                  .finally(() => setBusy(false));
              }}
            >
              Admin say
            </button>
          </div>
        )}
        <pre className="console-out">{output || "Command output will appear here."}</pre>
      </section>
      )}

      {/* kick_ban alone isn't enough: Palworld can ban but its REST API has no
          way to enumerate bans (they live in banlist.txt on the server's disk). */}
      {features.ban_list && (
        <BanListPanel
          bans={bans}
          loading={bansLoading}
          error={bansError}
          busy={busy}
          steamLookupEnabled={steamLookupEnabled}
          fromCache={bansFromCache}
          fetchedAt={bansFetchedAt}
          source={banListSource}
          page={bansPage}
          pageSize={bansPageSize}
          total={bansTotal}
          totalPages={bansTotalPages}
          identityFlags={identityFlags}
          onRefresh={() => loadBans({ refresh: true, page: 1 })}
          onPageChange={(p) => {
            setBansPage(p);
            loadBans({ refresh: false, page: p });
          }}
          onUnban={unbanPlayer}
          onOpenIdentity={openDossier}
          raw={bansRaw}
          showRaw={showBansRaw}
          onToggleRaw={() => setShowBansRaw((v) => !v)}
        />
      )}

      <IdentityDossierModal
        open={dossierOpen}
        netId={dossierNetId}
        fallbackName={dossierName}
        onClose={() => setDossierOpen(false)}
        onChanged={() => {
          if (dossierNetId) refreshIdentityFlags([dossierNetId]);
        }}
      />
    </div>
  );
}

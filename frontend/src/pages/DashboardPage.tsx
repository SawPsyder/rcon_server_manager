import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  AppSettings,
  MapConfig,
  QuickButton,
  Server,
  ServerStatus,
  ServerTypeInfo,
} from "../api";
import PlayerStatsChart from "../components/PlayerStatsChart";

const SELECTED_KEY = "rsm_selected_server";

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

export default function DashboardPage() {
  const [servers, setServers] = useState<Server[]>([]);
  const [serverId, setServerId] = useState<number | null>(null);
  const [status, setStatus] = useState<ServerStatus | null>(null);
  const [maps, setMaps] = useState<MapConfig[]>([]);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [serverTypes, setServerTypes] = useState<ServerTypeInfo[]>([]);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [rconCmd, setRconCmd] = useState("");
  const [output, setOutput] = useState("");
  const [busy, setBusy] = useState(false);
  const [selectedPlayer, setSelectedPlayer] = useState<string>("");
  const [sayMsg, setSayMsg] = useState("");
  const [unbanId, setUnbanId] = useState("");

  const [mapId, setMapId] = useState<number | "">("");
  const [gamemode, setGamemode] = useState("");
  const [lighting, setLighting] = useState("Day");
  const [travelPreview, setTravelPreview] = useState("");

  const selectedServer = useMemo(
    () => servers.find((s) => s.id === serverId) || null,
    [servers, serverId]
  );

  const features = status?.features || {
    map_travel: false,
    structured_player_list: false,
    kick_ban: false,
    admin_say: false,
    a2s_query: true,
  };

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

  const loadBase = useCallback(async () => {
    const [sv, st, ty] = await Promise.all([
      api.listServers(),
      api.settings(),
      api.serverTypes(),
    ]);
    setServers(sv);
    setSettings(st);
    setServerTypes(ty);

    const stored = localStorage.getItem(SELECTED_KEY);
    const preferred = stored ? Number(stored) : null;
    if (preferred && sv.some((s) => s.id === preferred)) {
      setServerId(preferred);
    } else if (sv.length) {
      setServerId(sv[0].id);
    }
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
    if (!serverId) return;
    try {
      const s = await api.status(serverId);
      setStatus(s);
      // Keep server list cache in sync so re-select stays instant
      setServers((prev) =>
        prev.map((srv) =>
          srv.id === serverId
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
  }, [serverId]);

  useEffect(() => {
    loadBase().catch((e) => setOutput(String(e)));
  }, [loadBase]);

  useEffect(() => {
    if (!serverId) return;
    localStorage.setItem(SELECTED_KEY, String(serverId));
    // Seed from list payload already returned by /api/servers (includes last_*)
    const server = servers.find((s) => s.id === serverId);
    if (server) {
      setStatus(statusFromServerCache(server));
      loadServerExtras(server);
    }
    refreshStatus();
    // Only re-seed when the selected server changes — not when cache fields update after poll
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional: avoid loop with setServers in refreshStatus
  }, [serverId, refreshStatus, loadServerExtras]);

  useEffect(() => {
    if (!serverId || !settings) return;
    const t = window.setInterval(refreshStatus, settings.poll_interval_seconds * 1000);
    return () => window.clearInterval(t);
  }, [serverId, settings, refreshStatus]);

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

  const runRcon = async (command: string, title = "RCON") => {
    if (!serverId) return;
    setBusy(true);
    try {
      const res = await api.rcon(serverId, command);
      showResult(title, res);
      await refreshStatus();
    } catch (e) {
      setOutput(String(e));
    } finally {
      setBusy(false);
    }
  };

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
        <div className="row wrap">
          <label className="inline">
            Server
            <select
              value={serverId ?? ""}
              onChange={(e) => setServerId(Number(e.target.value))}
            >
              {servers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
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
        <div className="stat card">
          <div className="stat-label">Map</div>
          <div className="stat-value">
            {status?.map || "—"}
            {status?.lighting ? ` (${status.lighting})` : ""}
          </div>
        </div>
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
      </section>

      {status?.error && <div className="alert error">{status.error}</div>}

      <PlayerStatsChart serverId={serverId} />

      <section className="card">
        <h2>Players</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th title="Place by total time on this server (x/y = rank / tracked players)">
                  Rank
                </th>
                <th title="In-game server slot / list ID">#</th>
                <th title="Player display name from the server">Name</th>
                <th title="Current in-game score">Score</th>
                <th title="Time in the current continuous join (this session)">
                  Session
                </th>
                <th title="All tracked playtime on this server">Total</th>
                <th title="Times seen after being absent from a sample (re-joins)">
                  Visits
                </th>
                <th title="End of last session, or Online if currently present">
                  Last seen
                </th>
                <th title="Last known IP address from RCON">IP</th>
                <th title="SteamID64">Steam ID</th>
              </tr>
            </thead>
            <tbody>
              {(status?.player_list || []).length === 0 ? (
                <tr>
                  <td colSpan={10} className="muted">
                    No players reported
                  </td>
                </tr>
              ) : (
                status!.player_list.map((p) => (
                  <tr
                    key={`${p.steamid || p.id}-${p.name}`}
                    className={selectedPlayer === p.name ? "selected" : ""}
                    onClick={() => setSelectedPlayer(p.name)}
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
                    <td>{p.id}</td>
                    <td>{p.name}</td>
                    <td>{p.score}</td>
                    <td>{p.session_pretty || "0s"}</td>
                    <td>{p.total_pretty || "0s"}</td>
                    <td>{p.visit_count ?? 0}</td>
                    <td title={p.last_seen_at || undefined}>
                      {p.last_seen_pretty || "—"}
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
                disabled={!selectedPlayer || busy || !serverId}
                onClick={() => {
                  const reason = prompt("Kick reason", "Kicked by admin") || "";
                  if (!serverId || !selectedPlayer) return;
                  setBusy(true);
                  api
                    .kick(serverId, selectedPlayer, reason)
                    .then((r) => {
                      showResult("Kick", r);
                      refreshStatus();
                    })
                    .finally(() => setBusy(false));
                }}
              >
                Kick
              </button>
              <button
                className="btn"
                disabled={!selectedPlayer || busy || !serverId}
                onClick={() => {
                  const minutes = Number(prompt("Ban minutes", "60") || "60");
                  const reason = prompt("Ban reason", "Banned by admin") || "";
                  if (!serverId || !selectedPlayer) return;
                  setBusy(true);
                  api
                    .ban(serverId, selectedPlayer, minutes, reason)
                    .then((r) => {
                      showResult("Ban", r);
                      refreshStatus();
                    })
                    .finally(() => setBusy(false));
                }}
              >
                Ban
              </button>
              <button
                className="btn danger"
                disabled={!selectedPlayer || busy || !serverId}
                onClick={() => {
                  if (!confirm(`Permban ${selectedPlayer}?`)) return;
                  const reason = prompt("Reason", "Permanently banned") || "";
                  if (!serverId || !selectedPlayer) return;
                  setBusy(true);
                  api
                    .permban(serverId, selectedPlayer, reason)
                    .then((r) => {
                      showResult("Permban", r);
                      refreshStatus();
                    })
                    .finally(() => setBusy(false));
                }}
              >
                Permban
              </button>
              {features.structured_player_list && (
                <button
                  className="btn ghost"
                  disabled={busy || !serverId}
                  onClick={() => runRcon("listplayers", "ListPlayers")}
                >
                  List player IDs
                </button>
              )}
            </div>
            <div className="row wrap" style={{ marginTop: "0.75rem" }}>
              <input
                placeholder="Steam NetID to unban"
                value={unbanId}
                onChange={(e) => setUnbanId(e.target.value)}
              />
              <button
                className="btn"
                disabled={!unbanId || busy || !serverId}
                onClick={() => {
                  if (!serverId) return;
                  setBusy(true);
                  api
                    .unban(serverId, unbanId)
                    .then((r) => showResult("Unban", r))
                    .finally(() => setBusy(false));
                }}
              >
                Unban
              </button>
            </div>
          </>
        )}
      </section>

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
                disabled={!serverId || !mapId || !gamemode || busy}
                onClick={() => {
                  if (!serverId || !mapId || !gamemode) return;
                  setBusy(true);
                  api
                    .travel(serverId, {
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

      <section className="card">
        <h2>RCON console</h2>
        <div className="row wrap">
          {quickButtons.map((b) => (
            <button
              key={b.command}
              className="btn"
              disabled={busy || !serverId}
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
            placeholder="Enter RCON command…"
            onKeyDown={(e) => {
              if (e.key === "Enter" && rconCmd.trim()) runRcon(rconCmd.trim());
            }}
          />
          <button
            className="btn primary"
            disabled={!rconCmd.trim() || busy || !serverId}
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
                if (e.key === "Enter" && sayMsg.trim() && serverId) {
                  setBusy(true);
                  api
                    .say(serverId, sayMsg.trim())
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
              disabled={!sayMsg.trim() || busy || !serverId}
              onClick={() => {
                if (!serverId) return;
                setBusy(true);
                api
                  .say(serverId, sayMsg.trim())
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
        <pre className="console-out">{output || "RCON output will appear here."}</pre>
      </section>
    </div>
  );
}

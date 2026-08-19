import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, AppSettings, Server, ServerTypeInfo } from "../api";
import PlayerStatsChart from "../components/PlayerStatsChart";

const OVERVIEW_SEARCH_KEY = "rsm_overview_search";

export default function OverviewPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [servers, setServers] = useState<Server[]>([]);
  const [serverTypes, setServerTypes] = useState<ServerTypeInfo[]>([]);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const gameFilter = searchParams.get("game") || "";
  const nameFilter = searchParams.get("q") || "";

  const typeLabel = useMemo(() => {
    const m = new Map<string, string>();
    for (const t of serverTypes) m.set(t.id, t.label);
    return m;
  }, [serverTypes]);

  const load = useCallback(async () => {
    try {
      const [sv, ty, st] = await Promise.all([
        api.listServers(),
        api.serverTypes(),
        api.settings(),
      ]);
      setServers(sv);
      setServerTypes(ty);
      setSettings(st);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Keep last_* player counts fresh on the overview
  useEffect(() => {
    if (!settings) return;
    const ms = Math.max(5, settings.poll_interval_seconds) * 1000;
    const t = window.setInterval(() => {
      api
        .listServers()
        .then(setServers)
        .catch(() => {
          /* ignore background poll errors */
        });
    }, ms);
    return () => window.clearInterval(t);
  }, [settings]);

  // Persist current filters for detail-page back link fallback
  useEffect(() => {
    sessionStorage.setItem(OVERVIEW_SEARCH_KEY, searchParams.toString());
  }, [searchParams]);

  const setGameFilter = (value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set("game", value);
    else next.delete("game");
    setSearchParams(next, { replace: true });
  };

  const setNameFilter = (value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set("q", value);
    else next.delete("q");
    setSearchParams(next, { replace: true });
  };

  const filtered = useMemo(() => {
    const q = nameFilter.trim().toLowerCase();
    return servers.filter((s) => {
      if (gameFilter && (s.server_type || "sandstorm") !== gameFilter) return false;
      if (q && !s.name.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [servers, gameFilter, nameFilter]);

  const openServer = (id: number) => {
    const search = searchParams.toString();
    const fromSearch = search ? `?${search}` : "";
    sessionStorage.setItem(OVERVIEW_SEARCH_KEY, search);
    navigate(`/server/${id}`, { state: { fromSearch } });
  };

  if (loading) {
    return (
      <div className="center-screen" style={{ minHeight: "40vh" }}>
        <div className="spinner" />
        <p className="muted">Loading servers…</p>
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
      <div className="card overview-filters">
        <div className="row wrap" style={{ gap: "1rem", alignItems: "flex-end" }}>
          <label className="inline">
            Game
            <select value={gameFilter} onChange={(e) => setGameFilter(e.target.value)}>
              <option value="">All games</option>
              {serverTypes.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="inline grow">
            Name
            <input
              type="search"
              placeholder="Filter by server name…"
              value={nameFilter}
              onChange={(e) => setNameFilter(e.target.value)}
              autoComplete="off"
            />
          </label>
          <span className="muted overview-filter-count">
            {filtered.length} of {servers.length}
          </span>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}

      {filtered.length === 0 ? (
        <div className="card">
          <p className="muted" style={{ margin: 0 }}>
            No servers match the current filters.
          </p>
        </div>
      ) : (
        <div className="server-overview-list">
          {filtered.map((s) => (
            <article
              key={s.id}
              className="server-overview-row card"
              role="link"
              tabIndex={0}
              onClick={() => openServer(s.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  openServer(s.id);
                }
              }}
              aria-label={`Open ${s.name}`}
            >
              <div className="server-overview-row-head row between wrap">
                <div className="server-overview-meta">
                  <h2 className="server-overview-name">{s.name}</h2>
                  <div className="muted server-overview-game">
                    {typeLabel.get(s.server_type || "sandstorm") || s.server_type || "Unknown"}
                  </div>
                </div>
                <div className="row wrap" style={{ gap: "0.5rem", alignItems: "center" }}>
                  <span className="chip">
                    {s.last_players ?? "-"}
                    {/* No cap for types that do not report one (Dune). */}
                    {(s.last_max_players ?? 0) > 0 ? `/${s.last_max_players}` : ""}
                  </span>
                  <div className={`pill ${s.last_online ? "online" : "offline"}`}>
                    {s.last_online ? "ONLINE" : "OFFLINE"}
                  </div>
                </div>
              </div>
              <div className="server-overview-chart">
                <PlayerStatsChart serverId={s.id} compact />
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

/** Resolve search string for Back to overview (location state or session). */
export function overviewBackSearch(locationState: unknown): string {
  const fromState =
    locationState &&
    typeof locationState === "object" &&
    "fromSearch" in locationState &&
    typeof (locationState as { fromSearch: unknown }).fromSearch === "string"
      ? (locationState as { fromSearch: string }).fromSearch
      : "";
  if (fromState) return fromState.startsWith("?") ? fromState : `?${fromState}`;
  const stored = sessionStorage.getItem(OVERVIEW_SEARCH_KEY) || "";
  if (!stored) return "";
  return stored.startsWith("?") ? stored : `?${stored}`;
}

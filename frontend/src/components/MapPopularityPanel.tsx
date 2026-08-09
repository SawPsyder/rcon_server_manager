import { useCallback, useEffect, useState } from "react";
import { api, MapStats, StatsRange } from "../api";

const RANGES: { value: StatsRange; label: string }[] = [
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "180d", label: "180d" },
  { value: "1y", label: "1y" },
];

const DEFAULT_REFRESH_MS = 60_000;

type Props = {
  serverId: number;
  refreshMs?: number;
};

function formatMinutes(n: number): string {
  if (!Number.isFinite(n)) return "-";
  if (n >= 100) return n.toFixed(0);
  if (n >= 10) return n.toFixed(1);
  return n.toFixed(2);
}

export default function MapPopularityPanel({
  serverId,
  refreshMs = DEFAULT_REFRESH_MS,
}: Props) {
  const [range, setRange] = useState<StatsRange>("7d");
  const [combineModes, setCombineModes] = useState(false);
  const [stats, setStats] = useState<MapStats | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!serverId) {
      setStats(null);
      return;
    }
    setError("");
    try {
      const data = await api.mapStats(serverId, range, {
        combineGamemodes: combineModes,
      });
      setStats(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [serverId, range, combineModes]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!serverId || refreshMs <= 0) return;
    const t = window.setInterval(load, refreshMs);
    return () => window.clearInterval(t);
  }, [serverId, refreshMs, load]);

  const rows = stats?.rows ?? [];
  const maxAvg = Math.max(0, ...rows.map((r) => r.avg_players));

  return (
    <section className="card">
      <div className="row between wrap" style={{ alignItems: "center", gap: "0.75rem" }}>
        <h2 style={{ margin: 0 }}>Map popularity</h2>
        <div className="row wrap" style={{ alignItems: "center", gap: "0.75rem" }}>
          <label className="toggle-switch" title="Merge all gamemodes for each map">
            <span className="toggle-switch-label">Combine modes</span>
            <input
              type="checkbox"
              checked={combineModes}
              onChange={(e) => setCombineModes(e.target.checked)}
            />
            <span className="toggle-switch-track" aria-hidden>
              <span className="toggle-switch-thumb" />
            </span>
          </label>
          <div className="range-tabs" role="tablist" aria-label="Map stats timespan">
            {RANGES.map((r) => (
              <button
                key={r.value}
                type="button"
                role="tab"
                aria-selected={range === r.value}
                className={`btn small ${range === r.value ? "primary" : "ghost"}`}
                onClick={() => setRange(r.value)}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {error ? (
        <div className="alert error" style={{ marginTop: "0.75rem" }}>
          {error}
        </div>
      ) : null}

      {!error && rows.length === 0 ? (
        <p className="muted" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
          No map-tagged playtime in this range yet.
        </p>
      ) : null}

      {rows.length > 0 ? (
        <div className="table-wrap" style={{ marginTop: "0.75rem" }}>
          <table>
            <thead>
              <tr>
                <th title="Rank among qualified maps by avg concurrent players">#</th>
                <th>Map</th>
                {!combineModes ? <th>Mode</th> : null}
                <th title="Mean players on samples with at least one human">Avg</th>
                <th title="Sum of (players × sample interval), in minutes">Player-min</th>
                <th title="Clock minutes the map had at least one player">Active min</th>
                <th>Peak</th>
                <th title="Relative avg concurrent (bar)">Demand</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const label = r.alias || r.map_name;
                const barPct = maxAvg > 0 ? Math.round((r.avg_players / maxAvg) * 100) : 0;
                return (
                  <tr
                    key={`${r.map_name}|${r.gamemode}|${i}`}
                    className={r.qualified ? undefined : "muted"}
                    title={
                      r.qualified
                        ? undefined
                        : `Below min exposure (${stats?.min_active_minutes ?? 30} active min)`
                    }
                  >
                    <td>{i + 1}</td>
                    <td>
                      <strong>{label}</strong>
                      {r.alias && r.alias !== r.map_name ? (
                        <span className="muted" style={{ marginLeft: "0.4rem", fontSize: "0.85em" }}>
                          {r.map_name}
                        </span>
                      ) : null}
                    </td>
                    {!combineModes ? <td>{r.gamemode || "—"}</td> : null}
                    <td>{r.avg_players.toFixed(1)}</td>
                    <td>{formatMinutes(r.player_minutes)}</td>
                    <td>{formatMinutes(r.active_minutes)}</td>
                    <td>{r.peak_players}</td>
                    <td style={{ minWidth: "6rem" }}>
                      <div
                        className="map-pop-bar"
                        role="img"
                        aria-label={`${barPct}% of top avg`}
                      >
                        <div
                          className="map-pop-bar-fill"
                          style={{
                            width: `${barPct}%`,
                            opacity: r.qualified ? 1 : 0.45,
                          }}
                        />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

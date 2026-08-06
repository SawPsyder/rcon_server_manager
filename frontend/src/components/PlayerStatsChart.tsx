import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, PlayerStats, StatsRange } from "../api";

const RANGES: { value: StatsRange; label: string }[] = [
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "180d", label: "180d" },
  { value: "1y", label: "1y" },
];

type Props = {
  serverId: number | null;
};

function formatTick(iso: string, range: StatsRange): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  if (range === "24h") {
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }
  if (range === "7d" || range === "30d") {
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  return d.toLocaleDateString(undefined, { month: "short", year: "2-digit" });
}

function formatTooltipTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

export default function PlayerStatsChart({ serverId }: Props) {
  const [range, setRange] = useState<StatsRange>("24h");
  const [stats, setStats] = useState<PlayerStats | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!serverId) {
      setStats(null);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const data = await api.playerStats(serverId, range);
      setStats(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [serverId, range]);

  useEffect(() => {
    load();
  }, [load]);

  // Refresh chart periodically so new samples appear
  useEffect(() => {
    if (!serverId) return;
    const t = window.setInterval(load, 60_000);
    return () => window.clearInterval(t);
  }, [serverId, load]);

  const chartData = useMemo(
    () =>
      (stats?.points || []).map((p) => ({
        t: p.t,
        players: p.players,
        max_players: p.max_players,
        online: p.online,
      })),
    [stats]
  );

  if (!serverId) {
    return null;
  }

  return (
    <section className="card chart-card">
      <div className="row between wrap">
        <div>
          <h2 style={{ marginBottom: 0 }}>Player count</h2>
          <p className="muted" style={{ margin: "0.25rem 0 0" }}>
            Background sampling runs continuously; history is kept indefinitely.
          </p>
        </div>
        <div className="range-tabs" role="tablist" aria-label="Chart timespan">
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

      <div className="chart-summary row wrap" style={{ marginTop: "0.75rem" }}>
        <span className="chip">
          Samples: <strong>{stats?.sample_count ?? "—"}</strong>
        </span>
        <span className="chip">
          Peak: <strong>{stats?.peak_players ?? "—"}</strong>
        </span>
        <span className="chip">
          Avg: <strong>{stats?.avg_players ?? "—"}</strong>
        </span>
        <span className="chip">
          Latest: <strong>{stats?.current_players ?? "—"}</strong>
        </span>
        {loading && <span className="muted">Updating…</span>}
      </div>

      {error && <div className="alert error" style={{ marginTop: "0.75rem" }}>{error}</div>}

      <div className="chart-wrap">
        {chartData.length === 0 ? (
          <div className="chart-empty muted">
            No samples yet for this timespan. Data appears after the collector runs
            (default every 60s).
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={chartData} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="playersFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#e8a23a" stopOpacity={0.45} />
                  <stop offset="100%" stopColor="#e8a23a" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#2a3544" strokeDasharray="3 3" />
              <XAxis
                dataKey="t"
                tickFormatter={(v) => formatTick(String(v), range)}
                minTickGap={28}
                stroke="#93a1b5"
                tick={{ fill: "#93a1b5", fontSize: 11 }}
              />
              <YAxis
                allowDecimals={false}
                width={36}
                stroke="#93a1b5"
                tick={{ fill: "#93a1b5", fontSize: 11 }}
                domain={[0, (dataMax: number) => Math.max(1, Math.ceil(dataMax))]}
              />
              <Tooltip
                contentStyle={{
                  background: "#151b24",
                  border: "1px solid #2a3544",
                  borderRadius: 8,
                  color: "#e7eef8",
                }}
                labelFormatter={(label) => formatTooltipTime(String(label))}
                formatter={(value: number, name: string) => [
                  value,
                  name === "players" ? "Players" : name,
                ]}
              />
              <Area
                type="monotone"
                dataKey="players"
                stroke="#e8a23a"
                strokeWidth={2}
                fill="url(#playersFill)"
                isAnimationActive={false}
                dot={false}
                activeDot={{ r: 4, fill: "#e8a23a" }}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </section>
  );
}

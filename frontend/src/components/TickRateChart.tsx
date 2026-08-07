import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
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

const DEFAULT_REFRESH_MS = 60_000;

/** What a healthy Satisfactory server simulates at. Dips below mean load. */
const TARGET_TICK_RATE = 30;

/** The app's secondary accent — the player chart owns amber. */
const LINE = "#3d8bfd";
const GRID = "#2a3544";
const AXIS = "#93a1b5";

type Props = {
  serverId: number;
  /** Poll interval for data refresh (default 60s). */
  refreshMs?: number;
  /** Override chart plot height in px. */
  height?: number;
};

type ChartPoint = {
  t: string;
  /** null renders as a gap — the server was offline, paused, or pre-upgrade. */
  tick: number | null;
  online: boolean;
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

function stopRowClick(e: React.SyntheticEvent) {
  e.stopPropagation();
}

function TickTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ payload?: ChartPoint }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload;
  if (!point) return null;
  const when = new Date(String(label ?? point.t));

  return (
    <div className="chart-tooltip" onClick={stopRowClick}>
      <div className="chart-tooltip-head">
        <div className="chart-tooltip-meta">
          <span
            className={`chart-tooltip-dot ${point.online ? "on" : "off"}`}
            aria-hidden
          />
          <span className="chart-tooltip-time">
            {Number.isNaN(when.getTime()) ? point.t : when.toLocaleString()}
          </span>
        </div>
        <div className="chart-tooltip-count">
          {point.tick == null ? (
            <span className="chart-tooltip-max">no reading</span>
          ) : (
            <>
              <strong>{point.tick.toFixed(1)}</strong>
              <span className="chart-tooltip-max">/{TARGET_TICK_RATE} tps</span>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function TickRateChart({
  serverId,
  refreshMs = DEFAULT_REFRESH_MS,
  height,
}: Props) {
  const [range, setRange] = useState<StatsRange>("24h");
  const [stats, setStats] = useState<PlayerStats | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!serverId) return;
    setLoading(true);
    setError("");
    try {
      setStats(await api.playerStats(serverId, range));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [serverId, range]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!serverId || refreshMs <= 0) return;
    const t = window.setInterval(load, refreshMs);
    return () => window.clearInterval(t);
  }, [serverId, refreshMs, load]);

  const chartData = useMemo<ChartPoint[]>(
    () =>
      (stats?.points || []).map((p) => ({
        t: p.t,
        tick: p.tick_rate ?? null,
        online: p.online,
      })),
    [stats]
  );

  // Samples exist but none carried a reading — say so instead of drawing nothing
  const hasReadings = chartData.some((p) => p.tick != null);
  const chartHeight = typeof height === "number" && height > 0 ? height : 220;

  return (
    <section className="card chart-card">
      <div className="row between wrap" style={{ alignItems: "center" }}>
        <div className="row wrap" style={{ alignItems: "center", gap: "0.6rem" }}>
          <h2 style={{ margin: 0, fontSize: "1rem" }}>Tick rate</h2>
          <div className="chart-summary row wrap">
            <span className="chip">
              Latest: <strong>{stats?.current_tick_rate ?? "—"}</strong>
            </span>
            <span className="chip">
              Lowest: <strong>{stats?.min_tick_rate ?? "—"}</strong>
            </span>
            <span className="chip">
              Avg: <strong>{stats?.avg_tick_rate ?? "—"}</strong>
            </span>
          </div>
        </div>
        <div
          className="range-tabs"
          role="tablist"
          aria-label="Tick rate timespan"
          onClick={stopRowClick}
          onKeyDown={stopRowClick}
        >
          {RANGES.map((r) => (
            <button
              key={r.value}
              type="button"
              role="tab"
              aria-selected={range === r.value}
              className={`btn small ${range === r.value ? "primary" : "ghost"}`}
              onClick={(e) => {
                e.stopPropagation();
                setRange(r.value);
              }}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="alert error" style={{ marginTop: "0.75rem" }}>
          {error}
        </div>
      )}

      <div className="chart-wrap" style={{ minHeight: chartHeight }}>
        {!hasReadings ? (
          <div className="chart-empty muted" style={{ minHeight: chartHeight }}>
            {loading ? "Loading…" : "No tick-rate readings in this range yet"}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={chartHeight}>
            <LineChart data={chartData} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
              <XAxis
                dataKey="t"
                tickFormatter={(v) => formatTick(String(v), range)}
                minTickGap={28}
                stroke={AXIS}
                tick={{ fill: AXIS, fontSize: 11 }}
              />
              <YAxis
                width={36}
                stroke={AXIS}
                tick={{ fill: AXIS, fontSize: 11 }}
                domain={[
                  0,
                  (dataMax: number) =>
                    Math.max(TARGET_TICK_RATE, Math.ceil(dataMax || 0)),
                ]}
              />
              <Tooltip
                content={<TickTooltip />}
                cursor={{ stroke: LINE, strokeWidth: 1, strokeOpacity: 0.45 }}
              />
              {/* Target, not a series: recessive and labelled in axis ink */}
              <ReferenceLine
                y={TARGET_TICK_RATE}
                stroke={AXIS}
                strokeDasharray="4 4"
                strokeOpacity={0.6}
                label={{
                  value: `${TARGET_TICK_RATE} tps target`,
                  position: "insideTopRight",
                  fill: AXIS,
                  fontSize: 11,
                }}
              />
              <Line
                type="monotone"
                dataKey="tick"
                stroke={LINE}
                strokeWidth={2}
                // Gaps are the signal: a break means offline or paused
                connectNulls={false}
                isAnimationActive={false}
                dot={false}
                activeDot={{ r: 4, fill: LINE }}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </section>
  );
}

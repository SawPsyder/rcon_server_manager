import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, PterodactylHistory, StatsRange } from "../api";
import { CHART_SYNC_ID, syncChartsByNearestTime } from "../lib/chartSync";

const RANGES: { value: StatsRange; label: string }[] = [
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "180d", label: "180d" },
  { value: "1y", label: "1y" },
];

const DEFAULT_REFRESH_MS = 60_000;

/** The player chart owns amber and the tick chart owns blue. */
const CPU_LINE = "#c792ea";
const MEM_LINE = "#7fd99a";
const GRID = "#2a3544";
const AXIS = "#93a1b5";

type Props = {
  serverId: number;
  /** Panel limits when the parent already has a resources reading.
   *  Null/omitted means unlimited or not yet known; if omitted, this chart
   *  loads limits once from the resources endpoint so it can stand alone. */
  cpuLimit?: number | null;
  memoryLimitBytes?: number | null;
  refreshMs?: number;
  height?: number;
};

type ChartPoint = {
  t: string;
  /** null renders as a gap - not linked yet, or the panel was unreachable. */
  cpu: number | null;
  mem: number | null;
  /** Absolutes for the tooltip, so it can show real numbers not just percent. */
  cpuRaw: number | null;
  memRaw: number | null;
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

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "-";
  if (!bytes) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 100 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function stopRowClick(e: React.SyntheticEvent) {
  e.stopPropagation();
}

function ResourceTooltip({
  active,
  payload,
  label,
  cpuIsPercent,
}: {
  active?: boolean;
  payload?: Array<{ payload?: ChartPoint }>;
  label?: string;
  cpuIsPercent: boolean;
}) {
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload;
  if (!point) return null;
  const when = new Date(String(label ?? point.t));

  return (
    <div className="chart-tooltip" onClick={stopRowClick}>
      <div className="chart-tooltip-head">
        <div className="chart-tooltip-meta">
          <span className="chart-tooltip-time">
            {Number.isNaN(when.getTime()) ? point.t : when.toLocaleString()}
          </span>
        </div>
      </div>
      <div className="chart-tooltip-count">
        {point.cpuRaw == null && point.memRaw == null ? (
          <span className="chart-tooltip-max">no reading</span>
        ) : (
          <>
            <div>
              CPU <strong>{point.cpuRaw?.toFixed(1) ?? "-"}%</strong>
              {cpuIsPercent && point.cpu != null && (
                <span className="chart-tooltip-max"> ({point.cpu.toFixed(0)}% of limit)</span>
              )}
            </div>
            <div>
              Memory <strong>{formatBytes(point.memRaw)}</strong>
              {point.mem != null && (
                <span className="chart-tooltip-max"> ({point.mem.toFixed(0)}% of limit)</span>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/**
 * CPU and memory history for a Pterodactyl-linked server.
 *
 * Fed by the backend poller, which samples every linked server every 20s
 * whether or not anyone is watching - so this chart and the live resource
 * card are the same readings, and history keeps accruing while the page is
 * closed. Like the player and tick charts, the API returns the samples in the
 * selected range and only thins when denser than the chart point cap.
 *
 * Placed by the server detail page (after player/FPS charts) rather than
 * nested inside the live container card, so the top of the page stays
 * "status → live container → charts".
 */
export default function ResourceHistoryChart({
  serverId,
  cpuLimit: cpuLimitProp,
  memoryLimitBytes: memoryLimitProp,
  refreshMs = DEFAULT_REFRESH_MS,
  height,
}: Props) {
  const [range, setRange] = useState<StatsRange>("24h");
  const [stats, setStats] = useState<PterodactylHistory | null>(null);
  const [fetchedCpuLimit, setFetchedCpuLimit] = useState<number | null>(null);
  const [fetchedMemoryLimit, setFetchedMemoryLimit] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const limitsFromParent =
    cpuLimitProp !== undefined || memoryLimitProp !== undefined;

  // When the page mounts the chart separately from the live panel, load limits
  // once so memory can still plot as % of allocation.
  useEffect(() => {
    if (!serverId || limitsFromParent) return;
    let cancelled = false;
    api.serverPterodactyl
      .resources(serverId)
      .then((r) => {
        if (cancelled) return;
        setFetchedCpuLimit(r.cpu_limit);
        setFetchedMemoryLimit(r.memory_limit_bytes);
      })
      .catch(() => {
        // History still works without limits; CPU plots absolute %, memory line hides.
      });
    return () => {
      cancelled = true;
    };
  }, [serverId, limitsFromParent]);

  const cpuLimit = cpuLimitProp !== undefined ? cpuLimitProp : fetchedCpuLimit;
  const memoryLimitBytes =
    memoryLimitProp !== undefined ? memoryLimitProp : fetchedMemoryLimit;

  const load = useCallback(async () => {
    if (!serverId) return;
    setLoading(true);
    setError("");
    try {
      setStats(await api.serverPterodactyl.history(serverId, range));
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

  // With no CPU limit set, cpu_absolute is already the most meaningful number
  // available (100 = one full core), so it is plotted as-is rather than
  // dropped. Memory without a limit has no percentage at all.
  const cpuIsPercent = !!cpuLimit && cpuLimit > 0;

  const chartData = useMemo<ChartPoint[]>(
    () =>
      (stats?.points || []).map((p) => {
        const cpuRaw = p.cpu_absolute;
        const memRaw = p.memory_bytes;
        return {
          t: p.t,
          cpu:
            cpuRaw == null
              ? null
              : cpuIsPercent
                ? (cpuRaw / (cpuLimit as number)) * 100
                : cpuRaw,
          mem:
            memRaw == null || !memoryLimitBytes
              ? null
              : (memRaw / memoryLimitBytes) * 100,
          cpuRaw,
          memRaw,
        };
      }),
    [stats, cpuLimit, cpuIsPercent, memoryLimitBytes],
  );

  const hasReadings = chartData.some((p) => p.cpu != null || p.mem != null);
  const chartHeight = typeof height === "number" && height > 0 ? height : 220;

  return (
    <section className="card chart-card">
      <div className="row between wrap" style={{ alignItems: "center" }}>
        <div className="row wrap" style={{ alignItems: "center", gap: "0.6rem" }}>
          <h2 style={{ margin: 0 }}>Container load</h2>
          <div className="chart-summary row wrap">
            <span className="chip">
              Peak: <strong>
                {stats?.peak_cpu_absolute != null ? `${stats.peak_cpu_absolute}%` : "-"}
              </strong>
            </span>
            <span className="chip">
              Avg: <strong>
                {stats?.avg_cpu_absolute != null ? `${stats.avg_cpu_absolute}%` : "-"}
              </strong>
            </span>
            <span className="chip">
              Latest: <strong>
                {stats?.current_cpu_absolute != null
                  ? `${stats.current_cpu_absolute}%`
                  : "-"}
              </strong>
            </span>
            <span className="chip" title="Peak memory in this range">
              Mem peak: <strong>{formatBytes(stats?.peak_memory_bytes ?? null)}</strong>
            </span>
          </div>
        </div>
        <div
          className="range-tabs"
          role="tablist"
          aria-label="Container load timespan"
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
            {loading ? "Loading…" : "No container readings in this range yet"}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={chartHeight}>
            <LineChart
              data={chartData}
              margin={{ top: 10, right: 12, left: 0, bottom: 0 }}
              syncId={CHART_SYNC_ID}
              syncMethod={syncChartsByNearestTime}
            >
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
              <XAxis
                dataKey="t"
                tickFormatter={(v) => formatTick(String(v), range)}
                minTickGap={28}
                stroke={AXIS}
                tick={{ fill: AXIS, fontSize: 11 }}
              />
              <YAxis
                width={40}
                stroke={AXIS}
                tick={{ fill: AXIS, fontSize: 11 }}
                unit="%"
                domain={[0, (dataMax: number) => Math.max(100, Math.ceil(dataMax || 0))]}
              />
              <Tooltip
                content={<ResourceTooltip cpuIsPercent={cpuIsPercent} />}
                cursor={{ stroke: CPU_LINE, strokeWidth: 1, strokeOpacity: 0.45 }}
              />
              <Legend wrapperStyle={{ fontSize: 11, color: AXIS }} />
              <Line
                type="monotone"
                dataKey="cpu"
                name={cpuIsPercent ? "CPU (% of limit)" : "CPU (% of one core)"}
                stroke={CPU_LINE}
                strokeWidth={2}
                // Gaps are the signal: a break means we had no reading.
                connectNulls={false}
                isAnimationActive={false}
                dot={false}
                activeDot={{ r: 4, fill: CPU_LINE }}
              />
              <Line
                type="monotone"
                dataKey="mem"
                name={memoryLimitBytes ? "Memory (% of limit)" : "Memory (no limit set)"}
                stroke={MEM_LINE}
                strokeWidth={2}
                connectNulls={false}
                isAnimationActive={false}
                dot={false}
                activeDot={{ r: 4, fill: MEM_LINE }}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </section>
  );
}

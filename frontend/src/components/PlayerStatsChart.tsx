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
import { CHART_SYNC_ID, syncChartsByNearestTime } from "../lib/chartSync";

const RANGES: { value: StatsRange; label: string }[] = [
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "180d", label: "180d" },
  { value: "1y", label: "1y" },
];

const DEFAULT_REFRESH_MS = 60_000;

type Props = {
  /** Admin (authenticated) chart for a server */
  serverId?: number | null;
  /** Public share token (no login) */
  shareToken?: string | null;
  /** Dense layout for overview rows */
  compact?: boolean;
  /** Poll interval for data refresh (default 60s) */
  refreshMs?: number;
  /** Show share link control (admin only) */
  showShare?: boolean;
  /** Strip card background/border (public embed / nobg) */
  plain?: boolean;
  /** Override chart plot height in px */
  height?: number;
};

type ChartPoint = {
  t: string;
  players: number;
  max_players: number;
  online: boolean;
  player_names: string[];
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

function stopRowClick(e: React.SyntheticEvent) {
  e.stopPropagation();
}

function ChartTooltip({
  active,
  payload,
  label,
  showNameHints,
}: {
  active?: boolean;
  payload?: Array<{ payload?: ChartPoint; value?: number }>;
  label?: string;
  /** Admin charts: show empty/missing-name hints. Public shares: count only. */
  showNameHints?: boolean;
}) {
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload;
  if (!point) return null;
  const names = point.player_names || [];
  const count = Math.round(Number(point.players) || 0);
  const max = Math.round(Number(point.max_players) || 0);
  const online = Boolean(point.online);

  return (
    <div className="chart-tooltip" onClick={stopRowClick}>
      <div className="chart-tooltip-head">
        <div className="chart-tooltip-meta">
          <span className={`chart-tooltip-dot ${online ? "on" : "off"}`} aria-hidden />
          <span className="chart-tooltip-time">
            {formatTooltipTime(String(label ?? point.t))}
          </span>
        </div>
        <div className="chart-tooltip-count">
          <strong>{count}</strong>
          {max > 0 ? <span className="chart-tooltip-max">/{max}</span> : null}
        </div>
      </div>
      {names.length > 0 ? (
        <ul className="chart-tooltip-names">
          {names.map((n, i) => (
            <li key={`${n}-${i}`}>
              <span className="chart-tooltip-avatar" aria-hidden>
                {(n.trim().charAt(0) || "?").toUpperCase()}
              </span>
              <span className="chart-tooltip-name">{n}</span>
            </li>
          ))}
        </ul>
      ) : showNameHints ? (
        count > 0 ? (
          <div className="chart-tooltip-empty">No names for this sample</div>
        ) : (
          <div className="chart-tooltip-empty">Empty</div>
        )
      ) : null}
    </div>
  );
}

export default function PlayerStatsChart({
  serverId = null,
  shareToken = null,
  compact = false,
  refreshMs = DEFAULT_REFRESH_MS,
  showShare = false,
  plain = false,
  height,
}: Props) {
  const [range, setRange] = useState<StatsRange>("24h");
  const [stats, setStats] = useState<PlayerStats | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [shareBusy, setShareBusy] = useState(false);
  const [shareMsg, setShareMsg] = useState("");

  const hasSource = Boolean(shareToken) || (serverId != null && serverId > 0);

  const load = useCallback(async () => {
    if (!hasSource) {
      setStats(null);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const data = shareToken
        ? await api.publicChartStats(shareToken, range)
        : await api.playerStats(serverId as number, range);
      setStats(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [hasSource, shareToken, serverId, range]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!hasSource || refreshMs <= 0) return;
    const t = window.setInterval(load, refreshMs);
    return () => window.clearInterval(t);
  }, [hasSource, refreshMs, load]);

  const onShare = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!serverId || shareBusy) return;
    setShareBusy(true);
    setShareMsg("");
    try {
      const share = await api.createChartShare(serverId);
      const url = `${window.location.origin}${share.url_path}`;
      try {
        await navigator.clipboard.writeText(url);
        setShareMsg("Link copied");
      } catch {
        setShareMsg(url);
      }
      window.setTimeout(() => setShareMsg(""), 4000);
    } catch (err) {
      setShareMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setShareBusy(false);
    }
  };

  const onRevokeShare = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!serverId || shareBusy) return;
    if (!confirm("Revoke the public chart link? Anyone with it will lose access.")) return;
    setShareBusy(true);
    setShareMsg("");
    try {
      await api.deleteChartShare(serverId);
      setShareMsg("Share revoked");
      window.setTimeout(() => setShareMsg(""), 3000);
    } catch (err) {
      setShareMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setShareBusy(false);
    }
  };

  const chartData = useMemo(
    () =>
      (stats?.points || []).map(
        (p): ChartPoint => ({
          t: p.t,
          players: p.players,
          max_players: p.max_players,
          online: p.online,
          player_names: p.player_names || [],
        })
      ),
    [stats]
  );

  const fillId = `playersFill-${shareToken || serverId || "none"}`;
  const chartHeight =
    typeof height === "number" && height > 0 ? height : compact ? 160 : 280;
  // Roster tooltips + missing-name hints only for authenticated admin charts
  const showNameHints = !shareToken;

  if (!hasSource) {
    return null;
  }

  const cardClass = [
    "card",
    "chart-card",
    compact ? "chart-card-compact" : "",
    plain ? "chart-card-plain" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <section className={cardClass}>
      <div className="row between wrap" style={{ alignItems: "center" }}>
        <div className="row wrap" style={{ alignItems: "center", gap: "0.6rem" }}>
          <h2 style={{ margin: 0 }}>Players</h2>
          <div className="chart-summary row wrap">
            <span className="chip">
              Peak: <strong>{stats?.peak_players ?? "-"}</strong>
            </span>
            <span className="chip">
              Avg: <strong>{stats?.avg_players ?? "-"}</strong>
            </span>
            <span className="chip">
              Latest: <strong>{stats?.current_players ?? "-"}</strong>
            </span>
          </div>
        </div>
        <div className="row wrap" style={{ alignItems: "center", gap: "0.5rem" }}>
          {showShare && serverId ? (
            <div className="chart-share-controls" onClick={stopRowClick}>
              <button
                type="button"
                className="btn small ghost"
                disabled={shareBusy}
                onClick={onShare}
                title="Copy public chart link"
              >
                Share
              </button>
              <button
                type="button"
                className="btn small ghost"
                disabled={shareBusy}
                onClick={onRevokeShare}
                title="Revoke public chart link"
              >
                Unshare
              </button>
              {shareMsg ? <span className="chart-share-msg muted">{shareMsg}</span> : null}
            </div>
          ) : null}
          <div
            className="range-tabs"
            role="tablist"
            aria-label="Chart timespan"
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
      </div>

      {error && (
        <div className="alert error" style={{ marginTop: "0.75rem" }}>
          {error}
        </div>
      )}

      <div
        className={`chart-wrap${compact ? " chart-wrap-compact" : ""}${plain ? " chart-wrap-plain" : ""}`}
        style={typeof height === "number" && height > 0 ? { minHeight: height } : undefined}
      >
        {chartData.length === 0 ? (
          <div
            className={`chart-empty muted${compact ? " chart-empty-compact" : ""}`}
            style={typeof height === "number" && height > 0 ? { minHeight: height } : undefined}
          >
            {loading ? "Loading…" : "No data yet"}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={chartHeight}>
            <AreaChart
              data={chartData}
              margin={{ top: 10, right: 12, left: 0, bottom: 0 }}
              syncId={CHART_SYNC_ID}
              syncMethod={syncChartsByNearestTime}
            >
              <defs>
                <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
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
                content={<ChartTooltip showNameHints={showNameHints} />}
                cursor={{ stroke: "#e8a23a", strokeWidth: 1, strokeOpacity: 0.45 }}
                allowEscapeViewBox={{ x: true, y: true }}
                wrapperStyle={{ zIndex: 20, outline: "none" }}
              />
              <Area
                type="monotone"
                dataKey="players"
                stroke="#e8a23a"
                strokeWidth={2}
                fill={`url(#${fillId})`}
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

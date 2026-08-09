import { useCallback, useEffect, useRef, useState } from "react";
import { api, type PterodactylResources, type PterodactylSignal } from "../api";
import { useAuth } from "../auth";

/** These poll our own backend, not the panel. A poller refreshes every linked
 *  server upstream every 20s, so this is a local cache read and can be brisk -
 *  10s halves the lag between the poller storing a reading and it appearing. */
const REFRESH_IDLE_MS = 10_000;
/** Briefly after a power action, to catch the transition promptly. */
const REFRESH_ACTIVE_MS = 3_000;
const ACTIVE_WINDOW_MS = 90_000;

type Props = { serverId: number; onChanged?: () => void };

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function formatBytes(bytes: number): string {
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

/** How long ago the backend read this from the panel. */
function formatAge(seconds: number): string {
  if (seconds < 5) return "just now";
  if (seconds < 90) return `read ${Math.round(seconds)}s ago`;
  return `read ${Math.round(seconds / 60)}m ago`;
}

function formatUptime(ms: number): string {
  if (ms <= 0) return "-";
  const total = Math.floor(ms / 1000);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m`;
  return `${total}s`;
}

/** Percentage against a limit, or null when the panel says unlimited (it
 *  encodes that as 0/None on memory, disk and CPU alike). */
function pct(value: number, limit: number | null | undefined): number | null {
  if (!limit || limit <= 0) return null;
  return (value / limit) * 100;
}

function limitNote(used: string, percent: number | null, limit: string | null): string {
  if (percent == null) return `${used} of unlimited`;
  return `${used} / ${limit} · ${percent.toFixed(0)}%`;
}

const STATE_PILL: Record<string, string> = {
  running: "online",
  starting: "pending",
  stopping: "pending",
  offline: "offline",
};

export default function PterodactylPanel({ serverId, onChanged }: Props) {
  const { isAdmin } = useAuth();
  const [data, setData] = useState<PterodactylResources | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  /** Timestamp of the last power action; drives the faster poll window. */
  const actedAt = useRef(0);
  /** Optimistic label shown until the panel catches up with the signal. */
  const [pending, setPending] = useState("");

  const refresh = useCallback(
    async (opts?: { quiet?: boolean }) => {
      try {
        const next = await api.serverPterodactyl.resources(serverId);
        setData(next);
        if (!opts?.quiet) setError("");
        // The signal has landed - stop claiming a transition.
        if (next.state === "running" || next.state === "offline") setPending("");
      } catch (e) {
        // A background poll keeps the last good snapshot: a single blip
        // shouldn't wipe the card an operator is reading.
        if (!opts?.quiet) setError(errorText(e));
      } finally {
        setLoaded(true);
      }
    },
    [serverId],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Self-rescheduling poll with an overlap guard - never setInterval, or a slow
  // response stacks requests. Paused while the tab is hidden; that no longer
  // saves panel quota (the backend poller runs regardless) but it does keep a
  // tab left open overnight from waking the server every ten seconds.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let inFlight = false;

    const delayMs = () =>
      Date.now() - actedAt.current < ACTIVE_WINDOW_MS
        ? REFRESH_ACTIVE_MS
        : REFRESH_IDLE_MS;

    const schedule = (ms: number) => {
      if (cancelled) return;
      timer = setTimeout(() => {
        void (async () => {
          if (!cancelled && !inFlight && !document.hidden) {
            inFlight = true;
            try {
              await refresh({ quiet: true });
            } finally {
              inFlight = false;
            }
          }
          schedule(delayMs());
        })();
      }, ms);
    };

    const onVisible = () => {
      if (!document.hidden) void refresh({ quiet: true });
    };
    document.addEventListener("visibilitychange", onVisible);
    schedule(delayMs());

    return () => {
      cancelled = true;
      if (timer != null) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refresh, data?.state]);

  const send = async (signal: PterodactylSignal, question: string) => {
    if (!confirm(question)) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api.serverPterodactyl.power(serverId, signal, true);
      setNotice(result.detail);
      actedAt.current = Date.now();
      setPending(signal === "start" ? "starting" : signal === "kill" ? "offline" : signal);
      await refresh({ quiet: true });
      onChanged?.();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  if (!loaded) {
    return (
      <section className="card stack" style={{ gap: "0.75rem" }}>
        <h2 style={{ margin: 0 }}>Container</h2>
        <p className="muted" style={{ margin: 0 }}>
          Loading container resources…
        </p>
      </section>
    );
  }

  if (!data) {
    return (
      <section className="card stack" style={{ gap: "0.75rem" }}>
        <h2 style={{ margin: 0 }}>Container</h2>
        <div className="alert error">{error || "No data from the panel."}</div>
      </section>
    );
  }

  const state = pending || data.state;
  const memPct = pct(data.memory_bytes, data.memory_limit_bytes);
  const diskPct = pct(data.disk_bytes, data.disk_limit_bytes);
  const cpuPct = pct(data.cpu_absolute, data.cpu_limit);
  // Any non-empty panel status (installing, transferring, restoring) makes
  // every power signal 409 - say so up front rather than via an error toast.
  const blocked = data.is_suspended || !!data.panel_status;
  const blockedWhy = data.is_suspended
    ? "This container is suspended in the panel."
    : data.panel_status
      ? `The panel reports this container as "${data.panel_status}".`
      : "";

  return (
    <section className="card stack" style={{ gap: "0.75rem" }}>
      <div className="row between wrap" style={{ alignItems: "center" }}>
        <h2 style={{ margin: 0 }}>Container</h2>
        <div className="row wrap" style={{ alignItems: "center", gap: "0.6rem" }}>
          <span className="muted" style={{ fontSize: "0.8rem" }}>
            {formatAge(data.age_seconds)}
          </span>
          <span className={`pill ${STATE_PILL[state] || "offline"}`}>
            {pending ? `${state}…` : state}
          </span>
        </div>
      </div>

      {notice && <div className="alert ok">{notice}</div>}
      {error && <div className="alert error">{error}</div>}
      {blocked && <div className="alert">{blockedWhy}</div>}

      <section className="stats">
        <div className="stat card">
          <div className="stat-label">CPU</div>
          <div className="stat-value">{data.cpu_absolute.toFixed(1)}%</div>
          <div className="muted" style={{ fontSize: "0.8rem" }}>
            {data.cpu_limit
              ? `of ${data.cpu_limit}% · ${cpuPct?.toFixed(0)}%`
              : "of unlimited"}
          </div>
        </div>
        <div className="stat card">
          <div className="stat-label">Memory</div>
          <div className="stat-value">{formatBytes(data.memory_bytes)}</div>
          <div className="muted" style={{ fontSize: "0.8rem" }}>
            {limitNote(
              formatBytes(data.memory_bytes),
              memPct,
              data.memory_limit_bytes ? formatBytes(data.memory_limit_bytes) : null,
            )}
          </div>
        </div>
        <div className="stat card">
          <div className="stat-label">Disk</div>
          <div className="stat-value">{formatBytes(data.disk_bytes)}</div>
          <div className="muted" style={{ fontSize: "0.8rem" }}>
            {limitNote(
              formatBytes(data.disk_bytes),
              diskPct,
              data.disk_limit_bytes ? formatBytes(data.disk_limit_bytes) : null,
            )}
          </div>
        </div>
        <div className="stat card">
          <div className="stat-label">Uptime</div>
          <div className="stat-value">
            {data.state === "offline" ? "-" : formatUptime(data.uptime_ms)}
          </div>
          <div className="muted" style={{ fontSize: "0.8rem" }}>
            {/* Counters restart with the container, so they are labelled as
                totals rather than presented as a transfer rate. */}↓{" "}
            {formatBytes(data.network_rx_bytes)} ↑{" "}
            {formatBytes(data.network_tx_bytes)} since start
          </div>
        </div>
      </section>

      <div className="row wrap">
        <button
          className="btn"
          disabled={busy || blocked}
          title={blocked ? blockedWhy : "Start the container"}
          onClick={() => void send("start", "Start this container?")}
        >
          Start
        </button>
        <button
          className="btn"
          disabled={busy || blocked}
          title={blocked ? blockedWhy : "Restart the container"}
          onClick={() =>
            void send(
              "restart",
              "Restart this container? Everyone currently playing will be disconnected.",
            )
          }
        >
          Restart
        </button>
        <button
          className="btn"
          disabled={busy || blocked}
          title={blocked ? blockedWhy : "Ask the server to shut down cleanly"}
          onClick={() =>
            void send(
              "stop",
              "Stop this container? The server shuts down cleanly and everyone is disconnected.",
            )
          }
        >
          Stop
        </button>
        {isAdmin && (
          <button
            className="btn danger"
            disabled={busy || blocked}
            title={
              blocked
                ? blockedWhy
                : "SIGKILL - no clean shutdown, unsaved progress is lost"
            }
            onClick={() =>
              void send(
                "kill",
                "Kill this container? It is terminated immediately with no clean " +
                  "shutdown, so anything not yet saved to disk is lost. Use Stop unless " +
                  "the server is already unresponsive.",
              )
            }
          >
            Kill
          </button>
        )}
      </div>
    </section>
  );
}

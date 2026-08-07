import { CSSProperties, ReactNode, useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import PlayerStatsChart from "../components/PlayerStatsChart";

const SHARE_REFRESH_MS = 5 * 60_000;
const DEFAULT_CHART_HEIGHT = 280;

/**
 * Public chart share URL query options (all optional):
 *
 *   notitle=1     Hide the server name heading
 *   nobg=1        Transparent / no page or card background styling (embed-friendly)
 *   width=800     Outer container width (px if bare number; also accepts %, vh, …)
 *   height=400    Chart height in px (bare number or "400px")
 *
 * Example:
 *   /share/c/<token>?notitle=1&nobg=1&width=640&height=280
 */

function flagEnabled(params: URLSearchParams, name: string): boolean {
  if (!params.has(name)) return false;
  const raw = params.get(name);
  if (raw == null || raw.trim() === "") return true; // bare ?notitle
  const v = raw.trim().toLowerCase();
  if (v === "0" || v === "false" || v === "no" || v === "off") return false;
  return v === "1" || v === "true" || v === "yes" || v === "on";
}

/** CSS size: bare number → px; otherwise allow common CSS length units. */
function parseCssSize(raw: string | null): string | undefined {
  if (raw == null) return undefined;
  const s = raw.trim();
  if (!s) return undefined;
  if (/^\d+(\.\d+)?$/.test(s)) return `${s}px`;
  if (/^\d+(\.\d+)?(px|%|vh|vw|rem|em|ch)$/i.test(s)) return s;
  return undefined;
}

function parseHeightPx(raw: string | null, fallback: number): number {
  if (raw == null) return fallback;
  const s = raw.trim();
  if (!s) return fallback;
  const m = s.match(/^(\d+(\.\d+)?)(px)?$/i);
  if (!m) return fallback;
  const n = Math.round(Number(m[1]));
  if (!Number.isFinite(n)) return fallback;
  return Math.min(4000, Math.max(80, n));
}

export default function SharedChartPage() {
  const { token } = useParams<{ token: string }>();
  const [searchParams] = useSearchParams();
  const [serverName, setServerName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const options = useMemo(() => {
    const titleParam = searchParams.get("title");
    const hideTitle =
      flagEnabled(searchParams, "notitle") ||
      flagEnabled(searchParams, "no_title") ||
      (titleParam != null &&
        ["0", "false", "no", "off"].includes(titleParam.trim().toLowerCase()));

    const plain =
      flagEnabled(searchParams, "nobg") ||
      flagEnabled(searchParams, "no_bg") ||
      flagEnabled(searchParams, "plain") ||
      (searchParams.get("background") != null &&
        ["0", "false", "no", "off", "none", "transparent"].includes(
          (searchParams.get("background") || "").trim().toLowerCase()
        ));

    return {
      hideTitle,
      plain,
      width: parseCssSize(searchParams.get("width")),
      height: parseHeightPx(searchParams.get("height"), DEFAULT_CHART_HEIGHT),
      heightSet: searchParams.has("height"),
    };
  }, [searchParams]);

  useEffect(() => {
    if (!options.plain) return;
    const root = document.documentElement;
    const body = document.body;
    root.classList.add("shared-embed-plain");
    body.classList.add("shared-embed-plain");
    return () => {
      root.classList.remove("shared-embed-plain");
      body.classList.remove("shared-embed-plain");
    };
  }, [options.plain]);

  useEffect(() => {
    if (!token) {
      setError("Invalid link");
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    api
      .publicChartMeta(token)
      .then((meta) => {
        if (!cancelled) {
          setServerName(meta.server_name || "Server");
          document.title = `${meta.server_name || "Server"} · Player chart`;
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setServerName("");
          document.title = "Chart not found";
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const pageClass = [
    "shared-chart-page",
    options.plain ? "shared-chart-plain" : "",
    options.hideTitle ? "shared-chart-no-title" : "",
  ]
    .filter(Boolean)
    .join(" ");

  const pageStyle: CSSProperties = {};
  if (options.width) pageStyle.width = options.width;
  if (options.width) pageStyle.maxWidth = "none";
  // When height is set with plain embed, size the outer shell to the chart (+ title if any)
  if (options.heightSet && options.plain && options.hideTitle) {
    pageStyle.height = `${options.height}px`;
    pageStyle.minHeight = 0;
  }

  const shell = (children: ReactNode) => (
    <div className={pageClass} style={pageStyle}>
      {children}
    </div>
  );

  if (!token) {
    return shell(<div className="card">Invalid link</div>);
  }

  if (loading) {
    return shell(
      <div className="center-screen" style={{ minHeight: options.plain ? "100%" : "40vh" }}>
        <div className="spinner" />
      </div>
    );
  }

  if (error) {
    return shell(
      <div className={options.plain ? undefined : "card"}>
        {!options.hideTitle ? <h1 className="shared-chart-title">Unavailable</h1> : null}
        <p className="muted" style={{ margin: 0 }}>
          This chart link is invalid or has been revoked.
        </p>
      </div>
    );
  }

  return shell(
    <>
      {!options.hideTitle ? (
        <header className="shared-chart-header">
          <h1 className="shared-chart-title">{serverName}</h1>
        </header>
      ) : null}
      <PlayerStatsChart
        shareToken={token}
        refreshMs={SHARE_REFRESH_MS}
        plain={options.plain}
        height={options.heightSet ? options.height : undefined}
      />
    </>
  );
}

import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import PlayerStatsChart from "../components/PlayerStatsChart";

const SHARE_REFRESH_MS = 5 * 60_000;

export default function SharedChartPage() {
  const { token } = useParams<{ token: string }>();
  const [serverName, setServerName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

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

  if (!token) {
    return (
      <div className="shared-chart-page">
        <div className="card">Invalid link</div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="shared-chart-page">
        <div className="center-screen" style={{ minHeight: "40vh" }}>
          <div className="spinner" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="shared-chart-page">
        <div className="card">
          <h1 className="shared-chart-title">Unavailable</h1>
          <p className="muted" style={{ margin: 0 }}>
            This chart link is invalid or has been revoked.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="shared-chart-page">
      <header className="shared-chart-header">
        <h1 className="shared-chart-title">{serverName}</h1>
      </header>
      <PlayerStatsChart shareToken={token} refreshMs={SHARE_REFRESH_MS} />
    </div>
  );
}

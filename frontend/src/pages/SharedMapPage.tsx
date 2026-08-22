import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError, PalworldWorld } from "../api";
import PalworldWorldMap, { MapSelection } from "../components/PalworldWorldMap";

/** Public share polls a bit slower than admin follow mode. */
const SHARE_REFRESH_MS = 3_000;

export default function SharedMapPage() {
  const { token } = useParams<{ token: string }>();
  const [serverName, setServerName] = useState("");
  const [world, setWorld] = useState<PalworldWorld | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<MapSelection>(null);
  const worldRef = useRef<PalworldWorld | null>(null);
  worldRef.current = world;

  const loadWorld = useCallback(async () => {
    if (!token) return;
    try {
      const data = await api.publicMapWorld(token);
      setWorld(data);
      setError("");
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      const message = e instanceof Error ? e.message : String(e);
      if (status === 404 || status === 403) {
        // Share was revoked or never valid — do not keep serving a stale overlay.
        setWorld(null);
        setError("This map link is invalid, revoked, or the server is offline.");
        return;
      }
      // Keep the last snapshot up so a single timeout does not blank an overlay.
      if (!worldRef.current) {
        setError(message);
      }
    }
  }, [token]);

  useEffect(() => {
    if (!token) {
      setError("Invalid link");
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    Promise.all([api.publicMapMeta(token), api.publicMapWorld(token)])
      .then(([meta, data]) => {
        if (cancelled) return;
        setServerName(meta.server_name || "Server");
        setWorld(data);
        document.title = `${meta.server_name || "Server"} · Live map`;
        setError("");
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        document.title = "Map not found";
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const canPoll = Boolean(world?.enabled);
  useEffect(() => {
    if (!token || loading || !canPoll) return;
    const id = window.setInterval(() => {
      void loadWorld();
    }, SHARE_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [token, loading, canPoll, loadWorld]);

  if (!token) {
    return (
      <div className="shared-map-page">
        <div className="card">Invalid link</div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="shared-map-page shared-map-center">
        <div className="spinner" />
        <p className="muted">Loading map…</p>
      </div>
    );
  }

  if (!world) {
    return (
      <div className="shared-map-page">
        <div className="card" style={{ maxWidth: 420, margin: "2rem auto" }}>
          <h1 className="shared-chart-title">Unavailable</h1>
          <p className="muted" style={{ margin: 0 }}>
            {error || "This map link is invalid, revoked, or the server is offline."}
          </p>
        </div>
      </div>
    );
  }

  if (!world.enabled) {
    return (
      <div className="shared-map-page">
        <div className="card" style={{ maxWidth: 480, margin: "2rem auto" }}>
          <h1 className="shared-chart-title">{serverName}</h1>
          <p className="muted">
            Live world data is not available (game-data API disabled on the server).
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="shared-map-page">
      <header className="shared-map-header">
        <div>
          <h1 className="shared-map-title">{serverName}</h1>
          <p className="muted shared-map-sub">Live Palworld map</p>
        </div>
        {world.snapshot_time && (
          <span className="muted" style={{ fontSize: "0.85rem" }}>
            Snapshot {world.snapshot_time}
          </span>
        )}
      </header>
      <div className="shared-map-body">
        <PalworldWorldMap
          world={world}
          selected={selected}
          onSelect={setSelected}
          variant="fullscreen"
        />
      </div>
    </div>
  );
}

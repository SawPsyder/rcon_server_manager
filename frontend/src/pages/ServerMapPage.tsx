import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, PalworldWorld, Server } from "../api";
import PalworldWorldMap, { MapSelection } from "../components/PalworldWorldMap";

const REFRESH_IDLE_MS = 5_000;
const REFRESH_ACTIVE_MS = 1_500;

/**
 * Authenticated full-page map view (opened from the admin panel "Full screen").
 * Same-tab navigation; return via “← Server”.
 */
export default function ServerMapPage() {
  const { serverId } = useParams<{ serverId: string }>();
  const id = Number(serverId);
  const [server, setServer] = useState<Server | null>(null);
  const [world, setWorld] = useState<PalworldWorld | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<MapSelection>(null);
  const [shareMsg, setShareMsg] = useState("");
  const [shareBusy, setShareBusy] = useState(false);

  const loadWorld = useCallback(async () => {
    if (!Number.isFinite(id) || id <= 0) return;
    try {
      const data = await api.palworld.world(id);
      setWorld(data);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [id]);

  useEffect(() => {
    if (!Number.isFinite(id) || id <= 0) {
      setError("Invalid server");
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    Promise.all([api.listServers(), api.palworld.world(id)])
      .then(([servers, data]) => {
        if (cancelled) return;
        const s = servers.find((x) => x.id === id) || null;
        setServer(s);
        setWorld(data);
        document.title = `${s?.name || "Server"} · Map`;
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    if (!world?.enabled || loading) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let inFlight = false;

    const delay = () =>
      selected?.kind === "player" ? REFRESH_ACTIVE_MS : REFRESH_IDLE_MS;

    const schedule = (ms: number) => {
      if (cancelled) return;
      timer = setTimeout(() => {
        void (async () => {
          if (!cancelled && !inFlight) {
            inFlight = true;
            try {
              await loadWorld();
            } finally {
              inFlight = false;
            }
          }
          schedule(delay());
        })();
      }, ms);
    };
    schedule(delay());
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [world?.enabled, loading, loadWorld, selected?.kind]);

  const onShare = async () => {
    if (!id || shareBusy) return;
    setShareBusy(true);
    setShareMsg("");
    try {
      const share = await api.createMapShare(id);
      const url = `${window.location.origin}${share.url_path}`;
      try {
        await navigator.clipboard.writeText(url);
        setShareMsg("Public link copied");
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

  const onUnshare = async () => {
    if (!id || shareBusy) return;
    if (!confirm("Revoke the public map link? Anyone with it will lose access.")) return;
    setShareBusy(true);
    setShareMsg("");
    try {
      await api.deleteMapShare(id);
      setShareMsg("Share revoked");
      window.setTimeout(() => setShareMsg(""), 3000);
    } catch (err) {
      setShareMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setShareBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="shared-map-page shared-map-center">
        <div className="spinner" />
      </div>
    );
  }

  if (error && !world) {
    return (
      <div className="shared-map-page">
        <div className="card" style={{ maxWidth: 480, margin: "2rem auto" }}>
          <p className="alert error">{error}</p>
          <Link to={Number.isFinite(id) ? `/server/${id}` : "/"} className="btn">
            Back
          </Link>
        </div>
      </div>
    );
  }

  if (!world?.enabled) {
    return (
      <div className="shared-map-page">
        <div className="card" style={{ maxWidth: 480, margin: "2rem auto" }}>
          <h1 className="shared-map-title">{server?.name || "Server"}</h1>
          <p className="muted">Game-data API is not enabled on this server.</p>
          <Link to={`/server/${id}`} className="btn">
            Back to server
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="shared-map-page">
      <header className="shared-map-header">
        <div className="row wrap" style={{ gap: "0.75rem", alignItems: "center" }}>
          <Link to={`/server/${id}`} className="btn small ghost">
            ← Server
          </Link>
          <div>
            <h1 className="shared-map-title">{server?.name || "Server"}</h1>
            <p className="muted shared-map-sub">Admin map · full screen · ← Server to leave</p>
          </div>
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
          toolbarExtra={
            <div className="chart-share-controls">
              <button
                type="button"
                className="btn small ghost"
                disabled={shareBusy}
                onClick={onShare}
                title="Copy public full-screen map link"
              >
                Share
              </button>
              <button
                type="button"
                className="btn small ghost"
                disabled={shareBusy}
                onClick={onUnshare}
                title="Revoke public map link"
              >
                Unshare
              </button>
              {shareMsg ? <span className="chart-share-msg muted">{shareMsg}</span> : null}
            </div>
          }
        />
      </div>
    </div>
  );
}

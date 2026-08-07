import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, PalworldWorld } from "../api";
import { worldToNormalized } from "../lib/palworldMapCoords";
import PalworldWorldMap, {
  MapSelection,
  PalworldWorldMapHandle,
  mapCampKey,
  mapPlayerKey,
} from "./PalworldWorldMap";

type Props = {
  serverId: number;
  /** Called after an action that changes what the status card shows. */
  onChanged?: () => void;
};

// This panel deliberately carries no overview or player tabs. Server scalars
// reach the status cards through ServerStatus.extra and per-player fields reach
// the shared Players table through PlayerInfo.extra, which is also where kick,
// ban, playtime and the identity dossier live. Duplicating either here just
// created two places to look at one thing.
type Tab = "world" | "settings" | "danger";

// World poll hits /palworld/world → game-data (summarised server-side). Idle
// can stay relaxed; when a player is selected (map follow / row highlight) we
// poll faster so markers and the follow camera keep up. Overlap is guarded so
// a slow response never stacks concurrent game-data calls.
const WORLD_REFRESH_IDLE_MS = 5_000;
const WORLD_REFRESH_ACTIVE_MS = 1_500;

const TABS: { id: Tab; label: string }[] = [
  { id: "world", label: "World" },
  { id: "settings", label: "Settings" },
  { id: "danger", label: "Danger zone" },
];

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function num(value: number | null | undefined, digits = 0): string {
  return value == null ? "-" : value.toFixed(digits);
}

/** Group the flat settings block so 68 keys aren't one undifferentiated wall. */
function settingsGroup(key: string): string {
  if (/^(Server|Public|RCON|RESTAPI|Region|BanList|AllowConnect|LogFormat|bUseAuth|bShowPlayerList|bIsUseBackup)/.test(key)) {
    return "Server";
  }
  if (/^(Player|bEnablePlayer|bEnableAimAssist|bEnableNonLogin|bExistPlayer|bCanPickup)/.test(key)) {
    return "Players";
  }
  if (/^(Pal|Enemy|Collection|bEnableInvader)/.test(key)) return "Pals & world";
  if (/^(Build|Base|Drop|Work|bActiveUNKO)/.test(key)) return "Building & items";
  if (/^(Guild|bAutoResetGuild|bEnableDefense|Coop|bEnableFriendlyFire|bIsPvP|bIsMultiplay)/.test(key)) {
    return "Guilds & multiplayer";
  }
  return "Gameplay";
}

function settingValue(value: unknown): string {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (value === "" || value == null) return "-";
  return String(value);
}

export default function PalworldAdminPanel({ serverId, onChanged }: Props) {
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("world");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [world, setWorld] = useState<PalworldWorld | null>(null);
  const [settings, setSettings] = useState<Record<string, string | number | boolean> | null>(
    null
  );

  const [announceText, setAnnounceText] = useState("");
  const [shutdownWait, setShutdownWait] = useState(30);
  const [shutdownMessage, setShutdownMessage] = useState("Server restarting");
  const [settingsFilter, setSettingsFilter] = useState("");
  const [mapSelection, setMapSelection] = useState<MapSelection>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [shareMsg, setShareMsg] = useState("");
  const [shareBusy, setShareBusy] = useState(false);
  const rowRefs = useRef<Map<string, HTMLTableRowElement>>(new Map());
  const mapRef = useRef<PalworldWorldMapHandle>(null);
  // Native dblclick = click + click. Second click used to toggle selection off
  // before fly-to ran. Treat two clicks on the same row within DBL_MS as focus.
  const DBL_MS = 400;
  const lastRowClick = useRef<{ key: string; t: number } | null>(null);

  const focusOnMap = useCallback(
    (kind: "player" | "camp", id: string, worldX: number, worldY: number) => {
      const { u, v } = worldToNormalized(worldX, worldY);
      setMapSelection({ kind, id });
      // Players: fly to max zoom and keep following; bases: fly only.
      mapRef.current?.focusOn(u, v, kind, kind === "player" ? id : undefined);
    },
    []
  );

  const onShareMap = useCallback(async () => {
    if (shareBusy) return;
    setShareBusy(true);
    setShareMsg("");
    try {
      const share = await api.createMapShare(serverId);
      const url = `${window.location.origin}${share.url_path}`;
      try {
        await navigator.clipboard.writeText(url);
        setShareMsg("Public map link copied");
      } catch {
        setShareMsg(url);
      }
      window.setTimeout(() => setShareMsg(""), 4000);
    } catch (err) {
      setShareMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setShareBusy(false);
    }
  }, [serverId, shareBusy]);

  const onUnshareMap = useCallback(async () => {
    if (shareBusy) return;
    if (!confirm("Revoke the public map link? Anyone with it will lose access.")) return;
    setShareBusy(true);
    setShareMsg("");
    try {
      await api.deleteMapShare(serverId);
      setShareMsg("Share revoked");
      window.setTimeout(() => setShareMsg(""), 3000);
    } catch (err) {
      setShareMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setShareBusy(false);
    }
  }, [serverId, shareBusy]);

  const openMapFullscreen = useCallback(() => {
    // Same tab — ServerMapPage has a “← Server” control to return.
    navigate(`/server/${serverId}/map`);
  }, [navigate, serverId]);

  const onWorldRowClick = useCallback(
    (kind: "player" | "camp", id: string, worldX: number | null, worldY: number | null) => {
      const key = `${kind}:${id}`;
      const now = performance.now();
      const last = lastRowClick.current;
      if (last && last.key === key && now - last.t < DBL_MS) {
        lastRowClick.current = null;
        if (worldX != null && worldY != null) {
          focusOnMap(kind, id, worldX, worldY);
        } else {
          setMapSelection({ kind, id });
        }
        return;
      }
      lastRowClick.current = { key, t: now };
      // Always select on the first click — never toggle off here. A second click
      // within DBL_MS flies the map; empty-map click clears selection.
      setMapSelection({ kind, id });
    },
    [focusOnMap]
  );

  const run = useCallback(
    async <T,>(action: () => Promise<T>, success?: string): Promise<T | null> => {
      setBusy(true);
      setError("");
      setNotice("");
      try {
        const result = await action();
        if (success) setNotice(success);
        return result;
      } catch (e) {
        setError(errorText(e));
        return null;
      } finally {
        setBusy(false);
      }
    },
    []
  );

  // Every tab loads on demand: each one is a live call to the game server, and
  // the status cards above already cover the at-a-glance numbers.
  const loadWorld = useCallback(
    async (opts?: { quiet?: boolean }) => {
      if (opts?.quiet) {
        // Background poll for the map: don't flip the whole panel into busy.
        try {
          const result = await api.palworld.world(serverId);
          setWorld(result);
        } catch {
          /* keep last good snapshot; next poll or manual reload will retry */
        }
        return;
      }
      const result = await run(() => api.palworld.world(serverId));
      if (result) setWorld(result);
    },
    [run, serverId]
  );

  const loadSettings = useCallback(async () => {
    const result = await run(() => api.palworld.settings(serverId));
    if (result) setSettings(result.settings);
  }, [run, serverId]);

  useEffect(() => {
    if (tab === "world" && world === null) void loadWorld();
    if (tab === "settings" && settings === null) loadSettings();
  }, [tab, world, settings, loadWorld, loadSettings]);

  // Adaptive world poll while the World tab is open.
  useEffect(() => {
    if (tab !== "world" || !autoRefresh || !world?.enabled) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let inFlight = false;

    const delayMs = () =>
      mapSelection?.kind === "player"
        ? WORLD_REFRESH_ACTIVE_MS
        : WORLD_REFRESH_IDLE_MS;

    const schedule = (ms: number) => {
      if (cancelled) return;
      timer = setTimeout(() => {
        void (async () => {
          if (!cancelled && !inFlight) {
            inFlight = true;
            try {
              await loadWorld({ quiet: true });
            } finally {
              inFlight = false;
            }
          }
          // Re-read selection each cycle so interval can speed up/slow down
          // without waiting for the effect to rebind.
          schedule(delayMs());
        })();
      }, ms);
    };

    // When focusing a player, refresh immediately so follow/smooth markers
    // don't wait a full idle interval for the first update.
    const initial =
      mapSelection?.kind === "player" ? 0 : delayMs();
    schedule(initial);

    return () => {
      cancelled = true;
      if (timer != null) clearTimeout(timer);
    };
  }, [tab, autoRefresh, world?.enabled, loadWorld, mapSelection?.kind]);

  useEffect(() => {
    if (!mapSelection) return;
    const row = rowRefs.current.get(`${mapSelection.kind}:${mapSelection.id}`);
    row?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [mapSelection]);

  const groupedSettings = useMemo(() => {
    if (!settings) return [];
    const needle = settingsFilter.trim().toLowerCase();
    const groups = new Map<string, [string, unknown][]>();
    for (const [key, value] of Object.entries(settings)) {
      if (needle && !key.toLowerCase().includes(needle)) continue;
      const group = settingsGroup(key);
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group)!.push([key, value]);
    }
    return [...groups.entries()]
      .map(([name, rows]) => [name, rows.sort((a, b) => a[0].localeCompare(b[0]))] as const)
      .sort((a, b) => a[0].localeCompare(b[0]));
  }, [settings, settingsFilter]);

  const announce = async () => {
    const message = announceText.trim();
    if (!message) return;
    const ok = await run(
      () => api.palworld.announce(serverId, message),
      "Announcement sent."
    );
    if (ok) setAnnounceText("");
  };

  const save = async () => {
    await run(() => api.palworld.save(serverId), "World saved.");
  };

  const shutdown = async () => {
    if (
      !confirm(
        `Shut the server down in ${shutdownWait}s? Everyone is disconnected once it does.`
      )
    )
      return;
    await run(
      () => api.palworld.shutdown(serverId, shutdownWait, shutdownMessage.trim()),
      `Shutdown scheduled in ${shutdownWait}s.`
    );
    onChanged?.();
  };

  const forceStop = async () => {
    if (
      !confirm(
        "Force stop the server immediately?\n\nThis does NOT save - everything since " +
          "the last save is lost. Save the world first unless you mean to discard it."
      )
    )
      return;
    await run(() => api.palworld.stop(serverId), "Server force stopped.");
    onChanged?.();
  };

  return (
    <section className="card">
      <div className="row between wrap">
        <h2 style={{ margin: 0 }}>Palworld server admin</h2>
        <div className="row wrap">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`btn small ${tab === t.id ? "primary" : "ghost"}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {notice && <div className="alert">{notice}</div>}

      {tab === "world" && (
        <div className="stack" style={{ marginTop: "0.75rem" }}>
          <div className="row wrap">
            <button
              className="btn"
              type="button"
              disabled={busy}
              onClick={() => void loadWorld()}
            >
              Reload
            </button>
            {world?.snapshot_time && (
              <span className="muted">Snapshot: {world.snapshot_time}</span>
            )}
          </div>

          {world && !world.enabled && (
            <div className="alert">
              <strong>The game-data API is switched off on this server.</strong>
              <p style={{ margin: "0.5rem 0 0" }}>
                Add <code>-enable-gamedata-api</code> to the dedicated server's launch
                arguments and restart it to see live world data here.
              </p>
              {world.hint && <p className="muted">{world.hint}</p>}
            </div>
          )}

          {world?.enabled && (
            <>
              <div className="stats">
                <div className="stat card">
                  <div className="stat-label">FPS</div>
                  <div className="stat-value">{num(world.fps, 1)}</div>
                </div>
                <div className="stat card">
                  <div className="stat-label">Average FPS</div>
                  <div className="stat-value">{num(world.average_fps, 1)}</div>
                </div>
                {world.in_game_time && (
                  <div className="stat card">
                    <div className="stat-label">In-game time</div>
                    <div className="stat-value" style={{ fontSize: "1.15rem" }}>
                      {world.in_game_time}
                    </div>
                  </div>
                )}
                {world.in_game_days != null && (
                  <div className="stat card">
                    <div className="stat-label">In-game day</div>
                    <div className="stat-value">{world.in_game_days.toLocaleString()}</div>
                  </div>
                )}
                {Object.entries(world.actor_counts).map(([kind, count]) => (
                  <div className="stat card" key={kind}>
                    <div className="stat-label">{kind}</div>
                    <div className="stat-value">{count}</div>
                  </div>
                ))}
              </div>

              <div className="stack pw-map-section">
                <div className="row between wrap">
                  <h3 style={{ margin: 0, fontSize: "1.05rem" }}>World map</h3>
                  <label
                    className="pw-map-filter"
                    title={
                      mapSelection?.kind === "player"
                        ? `Polling every ${WORLD_REFRESH_ACTIVE_MS / 1000}s while a player is selected`
                        : `Polling every ${WORLD_REFRESH_IDLE_MS / 1000}s (faster when a player is selected)`
                    }
                  >
                    <input
                      type="checkbox"
                      checked={autoRefresh}
                      onChange={(e) => setAutoRefresh(e.target.checked)}
                    />
                    Auto-refresh
                    <span className="muted" style={{ fontSize: "0.8rem" }}>
                      {mapSelection?.kind === "player"
                        ? `${WORLD_REFRESH_ACTIVE_MS / 1000}s`
                        : `${WORLD_REFRESH_IDLE_MS / 1000}s`}
                    </span>
                  </label>
                </div>
                <PalworldWorldMap
                  ref={mapRef}
                  world={world}
                  selected={mapSelection}
                  onSelect={setMapSelection}
                  toolbarExtra={
                    <div className="chart-share-controls">
                      <button
                        type="button"
                        className="btn small ghost"
                        disabled={shareBusy}
                        onClick={onShareMap}
                        title="Copy a public full-screen map link (live layers)"
                      >
                        Share
                      </button>
                      <button
                        type="button"
                        className="btn small ghost"
                        disabled={shareBusy}
                        onClick={onUnshareMap}
                        title="Revoke the public map link"
                      >
                        Unshare
                      </button>
                      <button
                        type="button"
                        className="btn small"
                        onClick={openMapFullscreen}
                        title="Open the map full screen (use ← Server to return)"
                      >
                        Full screen
                      </button>
                      {shareMsg ? (
                        <span className="chart-share-msg muted">{shareMsg}</span>
                      ) : null}
                    </div>
                  }
                />
                <p className="muted pw-map-footnote">
                  Map art © Pocketpair (Palworld). Toggle layers for workers, wild pals,
                  and NPCs. Double-click a player or base below to zoom to 3000%;
                  double-click a player to follow them live.{" "}
                  <strong>Share</strong> copies a public full-screen link;{" "}
                  <strong>Full screen</strong> opens the admin map (← Server to return).
                </p>
              </div>

              {world.players.length > 0 && (
                <div className="table-wrap pw-world-table">
                  <table>
                    <thead>
                      <tr>
                        <th>Player</th>
                        <th>Level</th>
                        <th>Health</th>
                        <th>Guild</th>
                        <th title="Pals owned by this player, from the actor list">
                          Pals
                        </th>
                        <th>X / Y / Z</th>
                      </tr>
                    </thead>
                    <tbody>
                      {world.players.map((p, i) => {
                        const id = mapPlayerKey(p, i);
                        const selected =
                          mapSelection?.kind === "player" && mapSelection.id === id;
                        const canFocus =
                          p.location_x != null && p.location_y != null;
                        return (
                          <tr
                            key={id}
                            ref={(el) => {
                              if (el) rowRefs.current.set(`player:${id}`, el);
                              else rowRefs.current.delete(`player:${id}`);
                            }}
                            className={selected ? "pw-row-selected" : undefined}
                            onClick={() =>
                              onWorldRowClick(
                                "player",
                                id,
                                p.location_x,
                                p.location_y
                              )
                            }
                            title={
                              canFocus
                                ? "Click to select · double-click to show on map"
                                : "No map coordinates for this player"
                            }
                            style={{ cursor: "pointer" }}
                          >
                            <td>{p.name || "-"}</td>
                            <td>{p.level ?? "-"}</td>
                            <td>
                              {p.hp == null ? "-" : `${p.hp} / ${p.max_hp ?? "?"}`}
                            </td>
                            <td className="muted">{p.guild_name || "-"}</td>
                            <td>{p.pal_count}</td>
                            <td className="muted">
                              {[p.location_x, p.location_y, p.location_z]
                                .map((v) => (v == null ? "-" : v.toFixed(0)))
                                .join(" / ")}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              {world.base_camps.length > 0 && (
                <div className="table-wrap pw-world-table">
                  <table>
                    <thead>
                      <tr>
                        <th>Base camp (guild)</th>
                        <th>Guild ID</th>
                        <th>X / Y / Z</th>
                      </tr>
                    </thead>
                    <tbody>
                      {world.base_camps.map((c, i) => {
                        const id = mapCampKey(c, i);
                        const selected =
                          mapSelection?.kind === "camp" && mapSelection.id === id;
                        const canFocus =
                          c.location_x != null && c.location_y != null;
                        return (
                          <tr
                            key={id}
                            ref={(el) => {
                              if (el) rowRefs.current.set(`camp:${id}`, el);
                              else rowRefs.current.delete(`camp:${id}`);
                            }}
                            className={selected ? "pw-row-selected" : undefined}
                            onClick={() =>
                              onWorldRowClick(
                                "camp",
                                id,
                                c.location_x,
                                c.location_y
                              )
                            }
                            title={
                              canFocus
                                ? "Click to select · double-click to show on map"
                                : "No map coordinates for this base camp"
                            }
                            style={{ cursor: "pointer" }}
                          >
                            <td>{c.guild_name || "-"}</td>
                            <td className="muted">
                              <code>{c.guild_id || "-"}</code>
                            </td>
                            <td className="muted">
                              {[c.location_x, c.location_y, c.location_z]
                                .map((v) => (v == null ? "-" : v.toFixed(0)))
                                .join(" / ")}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {tab === "settings" && (
        <div className="stack" style={{ marginTop: "0.75rem" }}>
          <div className="row wrap">
            <button className="btn" type="button" disabled={busy} onClick={loadSettings}>
              Reload
            </button>
            <input
              className="grow"
              value={settingsFilter}
              placeholder="Filter settings…"
              onChange={(e) => setSettingsFilter(e.target.value)}
            />
          </div>
          <p className="muted">
            Read-only: the REST API exposes no way to write settings. It also returns a
            subset of PalWorldSettings.ini and deliberately omits every password.
          </p>
          {groupedSettings.map(([group, rows]) => (
            <div className="table-wrap" key={group}>
              <table>
                <thead>
                  <tr>
                    <th colSpan={2}>{group}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(([key, value]) => (
                    <tr key={key}>
                      <th style={{ width: "22rem", fontWeight: 400 }}>
                        <code>{key}</code>
                      </th>
                      <td>{settingValue(value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
          {settings && groupedSettings.length === 0 && (
            <p className="muted">No settings match that filter.</p>
          )}
        </div>
      )}

      {tab === "danger" && (
        <div className="stack" style={{ marginTop: "0.75rem" }}>
          <div className="stack">
            <label className="full">
              Announce to everyone online
              <div className="row wrap">
                <input
                  className="grow"
                  value={announceText}
                  placeholder="Server restarting in 5 minutes"
                  onChange={(e) => setAnnounceText(e.target.value)}
                />
                <button
                  className="btn"
                  type="button"
                  disabled={busy || !announceText.trim()}
                  onClick={announce}
                >
                  Announce
                </button>
              </div>
            </label>
          </div>

          <div className="row wrap">
            <button className="btn" type="button" disabled={busy} onClick={save}>
              Save world
            </button>
            <span className="muted">
              Blocks until the world is written - expect a pause on a large save.
            </span>
          </div>

          <div className="stack">
            <label className="full">
              Graceful shutdown
              <div className="row wrap">
                <input
                  type="number"
                  min={0}
                  max={3600}
                  value={shutdownWait}
                  style={{ width: "6rem" }}
                  onChange={(e) => setShutdownWait(Number(e.target.value) || 0)}
                />
                <input
                  className="grow"
                  value={shutdownMessage}
                  placeholder="Message shown before shutdown"
                  onChange={(e) => setShutdownMessage(e.target.value)}
                />
                <button className="btn danger" type="button" disabled={busy} onClick={shutdown}>
                  Shut down
                </button>
              </div>
              <small className="muted">
                Counts down the given number of seconds, showing the message to players.
              </small>
            </label>
          </div>

          <div className="row wrap">
            <button className="btn danger" type="button" disabled={busy} onClick={forceStop}>
              Force stop
            </button>
            <span className="muted">
              Terminates immediately and <strong>does not save</strong>. Save the world
              first unless you mean to discard everything since the last save.
            </span>
          </div>
        </div>
      )}
    </section>
  );
}

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  DuneAction,
  DuneMapLocation,
  DuneMapMarker,
  DunePartition,
  DuneSettingItem,
  DuneSettings,
  DuneStatusGrid,
  PlayerInfo,
} from "../api";
import { DUNE_MAPS, type DuneMapKey } from "../lib/duneMapCoords";
import DuneWorldMap from "./DuneWorldMap";

type Props = {
  serverId: number;
  onChanged?: () => void;
  players?: PlayerInfo[];
};

type Tab = "map" | "instances" | "settings";

const TABS: { id: Tab; label: string }[] = [
  { id: "map", label: "Map" },
  { id: "instances", label: "Instances" },
  { id: "settings", label: "Settings" },
];

const SCALABLE = new Set(["DeepDesert_1", "SH_Arrakeen", "SH_HarkoVillage"]);
const MAP_POLL_MS = 4000;

const MAP_NAMES: Record<string, string> = {
  Survival_1: "Hagga Basin",
  Overmap: "Overland",
  SH_Arrakeen: "Arrakeen",
  SH_HarkoVillage: "Harko Village",
  SH_FallenLight: "Fallen Light",
  DeepDesert_1: "Deep Desert",
};

function mapLabel(map: string): string {
  if (MAP_NAMES[map]) return MAP_NAMES[map];
  return map.replace(/^(DLC_|CB_|SH_)/, "").replace(/_/g, " ");
}

function isSecondary(map: string): boolean {
  if (map === "Overmap" || map === "Survival_1") return false;
  if (SCALABLE.has(map)) return false;
  return true;
}

function instanceState(p: DunePartition): { label: string; tone: string } {
  if (p.parked) return { label: "parked", tone: "violet" };
  if (p.server_id && p.ready) return { label: "live", tone: "ok" };
  if (p.server_id) return { label: "starting", tone: "warn" };
  return { label: "cold", tone: "muted" };
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function isTruthySetting(value: string | null, type: string): boolean {
  const v = (value || "").trim().toLowerCase();
  if (type === "cvarbool") return v === "1" || v === "true";
  return v === "true" || v === "1" || v === "yes";
}

function isPasswordSetting(item: DuneSettingItem): boolean {
  return /password|secret|token/i.test(item.id) || /password|secret/i.test(item.label);
}

function rosterLabel(marker: DuneMapMarker, players: PlayerInfo[]): string {
  const row = players.find(
    (p) =>
      (marker.fls && String(p.extra?.fls_id || "") === marker.fls) ||
      (marker.name && p.name === marker.name)
  );
  const steam = typeof row?.extra?.steam_name === "string" ? row.extra.steam_name : "";
  const character = marker.name || row?.name || "";
  if (steam && character && steam !== character) return `${steam} (${character})`;
  return steam || character || marker.fls || marker.id;
}

export default function DuneAdminPanel({ serverId, onChanged, players = [] }: Props) {
  const [tab, setTab] = useState<Tab>("map");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [mapKey, setMapKey] = useState<DuneMapKey>("HaggaBasin");
  const [markers, setMarkers] = useState<DuneMapMarker[]>([]);
  const [locations, setLocations] = useState<DuneMapLocation[]>([]);
  const [target, setTarget] = useState("");
  const [pickMode, setPickMode] = useState(false);
  const [pending, setPending] = useState<{ x: number; y: number } | null>(null);
  const [pendingName, setPendingName] = useState("");

  const [grid, setGrid] = useState<DuneStatusGrid | null>(null);
  const [parts, setParts] = useState<DunePartition[]>([]);
  const [showSecondary, setShowSecondary] = useState(false);

  const [settings, setSettings] = useState<DuneSettings | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [settingsFilter, setSettingsFilter] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);

  const run = useCallback(
    async <T,>(fn: () => Promise<T>, okMsg?: string): Promise<T | null> => {
      setBusy(true);
      setError("");
      setNotice("");
      try {
        const result = await fn();
        if (okMsg) setNotice(okMsg);
        return result;
      } catch (err) {
        setError(errorText(err));
        return null;
      } finally {
        setBusy(false);
      }
    },
    []
  );

  const loadMap = useCallback(async () => {
    try {
      const [m, l] = await Promise.all([
        api.dune.markers(serverId, mapKey),
        api.dune.locations(serverId),
      ]);
      setMarkers(m.markers || []);
      setLocations(l.locations || []);
    } catch (err) {
      setError(errorText(err));
    }
  }, [serverId, mapKey]);

  const loadInstances = useCallback(async () => {
    try {
      const [s, p] = await Promise.all([
        api.dune.status(serverId),
        api.dune.partitions(serverId),
      ]);
      setGrid(s);
      setParts(p.partitions || []);
      if (p.ok === false && p.error) setError(p.error);
    } catch (err) {
      setError(errorText(err));
    }
  }, [serverId]);

  const loadSettings = useCallback(async () => {
    const result = await run(() => api.dune.settings(serverId));
    if (result) {
      setSettings(result);
      const next: Record<string, string> = {};
      for (const items of Object.values(result.categories || {})) {
        for (const item of items) {
          next[item.id] = item.value ?? item.default ?? "";
        }
      }
      setDrafts(next);
    }
  }, [run, serverId]);

  useEffect(() => {
    if (tab === "map") void loadMap();
    if (tab === "instances" && grid === null) void loadInstances();
    if (tab === "settings" && settings === null) void loadSettings();
  }, [tab, loadMap, loadInstances, loadSettings, grid, settings]);

  useEffect(() => {
    if (tab !== "map") return;
    const timer = setInterval(() => void loadMap(), MAP_POLL_MS);
    return () => clearInterval(timer);
  }, [tab, loadMap]);

  const savePending = async () => {
    if (!pending || !pendingName.trim()) return;
    const loc: DuneMapLocation = {
      name: pendingName.trim(),
      map: mapKey,
      x: pending.x,
      y: pending.y,
      z: 0,
    };
    const result = await run(() => api.dune.addLocation(serverId, loc), `Saved ${loc.name}.`);
    if (result) {
      setLocations(result.locations || []);
      setPending(null);
      setPendingName("");
      setPickMode(false);
    }
  };

  const teleport = async (loc: DuneMapLocation) => {
    if (!target) {
      setError("Select a player marker first (needs a linked FLS id).");
      return;
    }
    await run(
      () => api.dune.teleport(serverId, target, loc.name),
      `Teleporting ${target} to ${loc.name}.`
    );
  };

  const delLoc = async (name: string) => {
    const result = await run(() => api.dune.removeLocation(serverId, name));
    if (result) setLocations(result.locations || []);
  };

  const confirmIfNeeded = async (
    action: DuneAction,
    message: string,
    retry: () => Promise<DuneAction>
  ) => {
    if (action.requires_confirmation) {
      const n = action.players ?? 0;
      if (!confirm(`${n} player(s) online. ${message}`)) return false;
      const again = await retry();
      return again.ok;
    }
    return action.ok;
  };

  const scale = async (map: string, replicas: number, force = false) => {
    const result = await run(() => api.dune.scale(serverId, map, replicas, force));
    if (!result) return;
    const ok = await confirmIfNeeded(
      result,
      `Scale ${mapLabel(map)} to ${replicas}? Players will be disconnected.`,
      () => api.dune.scale(serverId, map, replicas, true)
    );
    if (ok) {
      setNotice(`Scaled ${mapLabel(map)} to ${replicas}.`);
      await loadInstances();
      onChanged?.();
    } else if (result.requires_confirmation) {
      setError("");
    }
  };

  const dimAct = async (pid: number, action: "up" | "down", force = false) => {
    const result = await run(() =>
      action === "up"
        ? api.dune.dimensionUp(serverId, pid)
        : api.dune.dimensionDown(serverId, pid, force)
    );
    if (!result) return;
    const ok =
      action === "down"
        ? await confirmIfNeeded(
            result,
            `Take partition ${pid} offline?`,
            () => api.dune.dimensionDown(serverId, pid, true)
          )
        : result.ok;
    if (ok) {
      await loadInstances();
      onChanged?.();
    }
  };

  const park = async (pid: number, force = false) => {
    const result = await run(() => api.dune.parkSietch(serverId, pid, force));
    if (!result) return;
    const ok = await confirmIfNeeded(
      result,
      `Park sietch ${pid}? Players disconnect; data is kept.`,
      () => api.dune.parkSietch(serverId, pid, true)
    );
    if (ok) await loadInstances();
  };

  const unpark = async (pid: number) => {
    const result = await run(() => api.dune.unparkSietch(serverId, pid), "Unparking sietch.");
    if (result?.ok) await loadInstances();
  };

  const removeSietch = async (pid: number, force = false) => {
    if (!force && !confirm(`Remove sietch ${pid}? This deletes its data.`)) return;
    const result = await run(() => api.dune.removeSietch(serverId, pid, force));
    if (!result) return;
    const ok = await confirmIfNeeded(
      result,
      `Remove sietch ${pid} anyway? Data is deleted.`,
      () => api.dune.removeSietch(serverId, pid, true)
    );
    if (ok) await loadInstances();
  };

  const addSietch = async () => {
    const result = await run(() => api.dune.addSietch(serverId), "Adding sietch.");
    if (result?.ok) await loadInstances();
  };

  const changedSettings = useMemo(() => {
    if (!settings) return [];
    const out: { id: string; value: string }[] = [];
    for (const items of Object.values(settings.categories || {})) {
      for (const item of items) {
        const base = item.value ?? item.default ?? "";
        const draft = drafts[item.id];
        if (draft !== undefined && draft !== base) out.push({ id: item.id, value: draft });
      }
    }
    return out;
  }, [settings, drafts]);

  const applySettings = async () => {
    if (!changedSettings.length) return;
    const payload: Record<string, string> = {};
    for (const row of changedSettings) payload[row.id] = row.value;
    const result = await run(() => api.dune.saveSettings(serverId, payload));
    if (!result) return;
    if (result.errors.length) {
      setError(result.errors.map((e) => `${e.id}: ${e.error}`).join("; "));
    }
    if (result.applied.length) {
      setNotice(
        result.restart_required
          ? `Applied ${result.applied.length} setting(s). Restart the battlegroup for them to take effect.`
          : `Applied ${result.applied.length} setting(s).`
      );
      await loadSettings();
    }
  };

  const partsByMap = useMemo(() => {
    const map = new Map<string, DunePartition[]>();
    for (const p of parts) {
      const arr = map.get(p.map) || [];
      arr.push(p);
      map.set(p.map, arr);
    }
    return map;
  }, [parts]);

  const allMaps = useMemo(() => {
    const names = new Set<string>();
    for (const row of grid?.maps || []) names.add(row.map);
    for (const map of partsByMap.keys()) names.add(map);
    return [...names];
  }, [grid, partsByMap]);

  const operational = allMaps.filter((m) => !isSecondary(m)).sort();
  const secondary = allMaps.filter((m) => isSecondary(m)).sort((a, b) => mapLabel(a).localeCompare(mapLabel(b)));
  const mapLocations = locations.filter((l) => l.map === mapKey);
  const uniqueMarkers = useMemo(() => {
    const seen = new Set<string>();
    const out: DuneMapMarker[] = [];
    for (const marker of markers) {
      const key = marker.fls || marker.id;
      if (!key || seen.has(key)) continue;
      seen.add(key);
      out.push({ ...marker, name: rosterLabel(marker, players) });
    }
    return out;
  }, [markers, players]);

  return (
    <section className="card">
      <div className="row between wrap">
        <h2 style={{ margin: 0 }}>Dune: Awakening</h2>
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

      {tab === "map" && (
        <div className="stack pw-map-section" style={{ marginTop: "0.75rem" }}>
          <div className="row wrap">
            {DUNE_MAPS.map((m) => (
              <button
                key={m.key}
                type="button"
                className={`btn small ${mapKey === m.key ? "primary" : "ghost"}`}
                onClick={() => setMapKey(m.key)}
              >
                {m.label}
              </button>
            ))}
            <button
              type="button"
              className={`btn small ${pickMode ? "primary" : "ghost"}`}
              onClick={() => {
                setPickMode(!pickMode);
                setPending(null);
              }}
            >
              {pickMode ? "Picking…" : "Add location"}
            </button>
            <span className="muted">
              Target: <code>{target || "(none)"}</code>
            </span>
          </div>
          <p className="muted pw-map-footnote">
            Dots are the last saved pawn position, not a live client arrow — they can lag by tens of seconds.
          </p>
          <DuneWorldMap
            mapKey={mapKey}
            markers={uniqueMarkers}
            locations={locations}
            selectedFls={target}
            pickMode={pickMode}
            pending={pending}
            onSelectPlayer={setTarget}
            onPick={setPending}
          />
          {pending && (
            <div className="row wrap">
              <span className="muted">
                New spot ({pending.x}, {pending.y})
              </span>
              <input
                placeholder="Name (e.g. Hagga outpost)"
                value={pendingName}
                onChange={(e) => setPendingName(e.target.value)}
              />
              <button className="btn primary" type="button" disabled={!pendingName.trim() || busy} onClick={() => void savePending()}>
                Save
              </button>
              <button className="btn ghost" type="button" onClick={() => { setPending(null); setPendingName(""); }}>
                Cancel
              </button>
            </div>
          )}
          <div className="grid-2">
            <div>
              <h3 style={{ margin: "0 0 0.4rem", fontSize: "0.95rem" }}>Locations</h3>
              {mapLocations.length === 0 && <p className="muted">None on this map.</p>}
              {mapLocations.map((l) => (
                <div key={l.name} className="row wrap" style={{ marginBottom: "0.35rem" }}>
                  <span className="grow">{l.name}</span>
                  <span className="muted" style={{ fontFamily: "monospace", fontSize: "0.8rem" }}>
                    {Math.round(l.x)}, {Math.round(l.y)}
                  </span>
                  <button className="btn small" type="button" disabled={busy} onClick={() => void teleport(l)}>
                    TP
                  </button>
                  <button className="btn small ghost" type="button" disabled={busy} onClick={() => void delLoc(l.name)}>
                    Remove
                  </button>
                </div>
              ))}
            </div>
            <div>
              <h3 style={{ margin: "0 0 0.4rem", fontSize: "0.95rem" }}>Players ({uniqueMarkers.length})</h3>
              {uniqueMarkers.length === 0 && <p className="muted">No saved positions on this map.</p>}
              {uniqueMarkers.map((m) => (
                <button
                  key={m.fls || m.id}
                  type="button"
                  className={`btn small ghost ${m.fls === target ? "primary" : ""}`}
                  disabled={!m.fls}
                  onClick={() => m.fls && setTarget(m.fls)}
                  style={{ display: "block", width: "100%", textAlign: "left", marginBottom: "0.25rem" }}
                >
                  {m.online ? "●" : "○"} {m.name || m.fls}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {tab === "instances" && (
        <div className="stack" style={{ marginTop: "0.75rem" }}>
          <div className="row between wrap">
            <p className="muted" style={{ margin: 0 }}>
              {grid
                ? `${grid.totalServers ?? 0} instances · ${grid.totalPlayers ?? 0} online`
                : "Loading…"}
              {grid?.warning ? ` · ${grid.warning}` : ""}
            </p>
            <div className="row wrap">
              <button className="btn small ghost" type="button" disabled={busy} onClick={() => void loadInstances()}>
                Refresh
              </button>
              <button className="btn small" type="button" disabled={busy} onClick={() => void addSietch()}>
                Add sietch
              </button>
            </div>
          </div>
          {operational.map((map) => {
            const st = grid?.maps.find((m) => m.map === map);
            const rows = (partsByMap.get(map) || []).slice().sort((a, b) => a.dimension - b.dimension);
            return (
              <div key={map} className="dune-instance-card">
                <div className="row between wrap">
                  <strong>{mapLabel(map)}</strong>
                  <span className="muted">
                    {st?.status || "—"} · {st?.current ?? 0}/{st?.desired ?? 0} live · {st?.players ?? 0} online
                  </span>
                </div>
                {rows.map((p) => {
                  const sv = instanceState(p);
                  const isSietch = p.map === "Survival_1" && p.dimension > 0;
                  return (
                    <div key={p.partition_id} className="row wrap dune-part-row">
                      <span className={`dune-pill ${sv.tone}`}>{sv.label}</span>
                      <code>#{p.partition_id}</code>
                      <span>{p.dimension === 0 ? "warm" : isSietch ? `sietch ${p.dimension}` : `tunnel ${p.dimension}`}</span>
                      {p.label && <span className="muted">{p.label}</span>}
                      {p.game_port != null && <span className="muted">:{p.game_port}</span>}
                      {p.players > 0 && <span>{p.players} online</span>}
                      {p.dimension > 0 && (
                        <span className="row wrap" style={{ marginLeft: "auto" }}>
                          {isSietch ? (
                            <>
                              {p.parked && (
                                <button className="btn small" type="button" disabled={busy} onClick={() => void unpark(p.partition_id)}>
                                  Unpark
                                </button>
                              )}
                              {!p.parked && !p.server_id && (
                                <button className="btn small" type="button" disabled={busy} onClick={() => void dimAct(p.partition_id, "up")}>
                                  Start
                                </button>
                              )}
                              {!p.parked && (
                                <button className="btn small ghost" type="button" disabled={busy} onClick={() => void park(p.partition_id)}>
                                  Park
                                </button>
                              )}
                              <button className="btn small danger" type="button" disabled={busy} onClick={() => void removeSietch(p.partition_id)}>
                                Remove
                              </button>
                            </>
                          ) : p.server_id ? (
                            <button className="btn small danger" type="button" disabled={busy} onClick={() => void dimAct(p.partition_id, "down")}>
                              Offline
                            </button>
                          ) : (
                            <button className="btn small" type="button" disabled={busy} onClick={() => void dimAct(p.partition_id, "up")}>
                              Start
                            </button>
                          )}
                        </span>
                      )}
                    </div>
                  );
                })}
                {SCALABLE.has(map) && (
                  <div className="row wrap" style={{ marginTop: "0.4rem" }}>
                    <button className="btn small" type="button" disabled={busy || (st?.desired ?? 0) >= 1} onClick={() => void scale(map, 1)}>
                      Start
                    </button>
                    <button className="btn small danger" type="button" disabled={busy || (st?.desired ?? 0) === 0} onClick={() => void scale(map, 0)}>
                      Stop
                    </button>
                    {[1, 2, 3, 4].map((n) => (
                      <button
                        key={n}
                        type="button"
                        className={`btn small ${(st?.desired ?? 0) === n ? "primary" : "ghost"}`}
                        disabled={busy}
                        onClick={() => void scale(map, n)}
                      >
                        ×{n}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
          {secondary.length > 0 && (
            <div>
              <button className="btn small ghost" type="button" onClick={() => setShowSecondary((v) => !v)}>
                {showSecondary ? "Hide" : "Show"} story & dungeons ({secondary.length})
              </button>
              {showSecondary &&
                secondary.map((map) => {
                  const rows = partsByMap.get(map) || [];
                  const primary = rows[0];
                  const sv = primary ? instanceState(primary) : { label: "cold", tone: "muted" };
                  return (
                    <div key={map} className="row wrap dune-part-row">
                      <span className={`dune-pill ${sv.tone}`}>{sv.label}</span>
                      <span>{mapLabel(map)}</span>
                      <span className="muted">{map}</span>
                    </div>
                  );
                })}
            </div>
          )}
        </div>
      )}

      {tab === "settings" && (
        <div className="stack" style={{ marginTop: "0.75rem" }}>
          <div className="row wrap">
            <input
              className="grow"
              placeholder="Filter settings…"
              value={settingsFilter}
              onChange={(e) => setSettingsFilter(e.target.value)}
            />
            <label className="inline">
              <input
                type="checkbox"
                checked={showAdvanced}
                onChange={(e) => setShowAdvanced(e.target.checked)}
              />
              Advanced
            </label>
            <button
              className="btn primary"
              type="button"
              disabled={busy || changedSettings.length === 0}
              onClick={() => void applySettings()}
            >
              Apply {changedSettings.length ? `(${changedSettings.length})` : ""}
            </button>
          </div>
          <p className="muted">
            {settings?.note || "Changes take effect on the next battlegroup restart."}
          </p>
          {settings &&
            Object.entries(settings.categories).map(([category, items]) => {
              const needle = settingsFilter.trim().toLowerCase();
              const visible = items.filter((item) => {
                if (!showAdvanced && item.advanced) return false;
                if (item.type === "struct") return showAdvanced;
                if (!needle) return true;
                return (
                  item.label.toLowerCase().includes(needle) ||
                  item.id.toLowerCase().includes(needle) ||
                  (item.key || "").toLowerCase().includes(needle)
                );
              });
              if (!visible.length) return null;
              return (
                <div key={category}>
                  <h3 style={{ margin: "0.5rem 0 0.35rem", fontSize: "0.95rem" }}>{category}</h3>
                  {visible.map((item) => (
                    <SettingRow
                      key={item.id}
                      item={item}
                      value={drafts[item.id] ?? ""}
                      onChange={(value) => setDrafts((d) => ({ ...d, [item.id]: value }))}
                    />
                  ))}
                </div>
              );
            })}
        </div>
      )}
    </section>
  );
}

function SettingRow({
  item,
  value,
  onChange,
}: {
  item: DuneSettingItem;
  value: string;
  onChange: (value: string) => void;
}) {
  const boolLike = item.type === "bool" || item.type === "cvarbool";
  const password = isPasswordSetting(item);
  return (
    <label className="dune-setting-row">
      <span>
        {item.label}
        {item.clientGated && <span className="muted"> (client)</span>}
        {!item.verified && <span className="muted"> (unverified)</span>}
      </span>
      {boolLike ? (
        <input
          type="checkbox"
          checked={isTruthySetting(value, item.type)}
          onChange={(e) =>
            onChange(
              item.type === "cvarbool"
                ? e.target.checked
                  ? "1"
                  : "0"
                : e.target.checked
                  ? "True"
                  : "False"
            )
          }
        />
      ) : item.enum && item.enum.length ? (
        <select value={value} onChange={(e) => onChange(e.target.value)}>
          {item.enum.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      ) : item.type === "struct" ? (
        <textarea rows={3} value={value} onChange={(e) => onChange(e.target.value)} />
      ) : (
        <input
          type={password ? "password" : item.type === "int" || item.type === "float" ? "text" : "text"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete={password ? "new-password" : undefined}
        />
      )}
    </label>
  );
}

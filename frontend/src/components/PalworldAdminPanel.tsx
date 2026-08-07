import { useCallback, useEffect, useMemo, useState } from "react";
import { api, PalworldWorld } from "../api";

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

const TABS: { id: Tab; label: string }[] = [
  { id: "world", label: "World" },
  { id: "settings", label: "Settings" },
  { id: "danger", label: "Danger zone" },
];

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function num(value: number | null | undefined, digits = 0): string {
  return value == null ? "—" : value.toFixed(digits);
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
  if (value === "" || value == null) return "—";
  return String(value);
}

export default function PalworldAdminPanel({ serverId, onChanged }: Props) {
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
  const loadWorld = useCallback(async () => {
    const result = await run(() => api.palworld.world(serverId));
    if (result) setWorld(result);
  }, [run, serverId]);

  const loadSettings = useCallback(async () => {
    const result = await run(() => api.palworld.settings(serverId));
    if (result) setSettings(result.settings);
  }, [run, serverId]);

  useEffect(() => {
    if (tab === "world" && world === null) loadWorld();
    if (tab === "settings" && settings === null) loadSettings();
  }, [tab, world, settings, loadWorld, loadSettings]);

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
        "Force stop the server immediately?\n\nThis does NOT save — everything since " +
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
            <button className="btn" type="button" disabled={busy} onClick={loadWorld}>
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
                {Object.entries(world.actor_counts).map(([kind, count]) => (
                  <div className="stat card" key={kind}>
                    <div className="stat-label">{kind}</div>
                    <div className="stat-value">{count}</div>
                  </div>
                ))}
              </div>

              {world.players.length > 0 && (
                <div className="table-wrap">
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
                      {world.players.map((p) => (
                        <tr key={p.user_id || p.name}>
                          <td>{p.name || "—"}</td>
                          <td>{p.level ?? "—"}</td>
                          <td>
                            {p.hp == null ? "—" : `${p.hp} / ${p.max_hp ?? "?"}`}
                          </td>
                          <td className="muted">{p.guild_name || "—"}</td>
                          <td>{p.pal_count}</td>
                          <td className="muted">
                            {[p.location_x, p.location_y, p.location_z]
                              .map((v) => (v == null ? "—" : v.toFixed(0)))
                              .join(" / ")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {world.base_camps.length > 0 && (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Base camp (guild)</th>
                        <th>Guild ID</th>
                        <th>X / Y / Z</th>
                      </tr>
                    </thead>
                    <tbody>
                      {world.base_camps.map((c, i) => (
                        <tr key={`${c.guild_id}-${i}`}>
                          <td>{c.guild_name || "—"}</td>
                          <td className="muted">
                            <code>{c.guild_id || "—"}</code>
                          </td>
                          <td className="muted">
                            {[c.location_x, c.location_y, c.location_z]
                              .map((v) => (v == null ? "—" : v.toFixed(0)))
                              .join(" / ")}
                          </td>
                        </tr>
                      ))}
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
              Blocks until the world is written — expect a pause on a large save.
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

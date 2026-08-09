import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  SatisfactoryAdvanced,
  SatisfactoryOptions,
  SatisfactorySaveHeader,
  SatisfactorySessions,
} from "../api";
import {
  ADVANCED_SETTING_FIELDS,
  CUSTOM_CHOICE,
  encodeAdvanced,
  encodeOption,
  FieldSpec,
  orderKeys,
  SelectChoice,
  SERVER_OPTION_FIELDS,
  shapeOf,
  specFor,
  STARTING_LOCATIONS,
  toDraft,
} from "../satisfactoryFields";

type Props = {
  serverId: number;
  /** Called after an action that changes what the status card shows. */
  onChanged?: () => void;
};

type Tab = "options" | "advanced" | "saves" | "danger";

const TABS: { id: Tab; label: string }[] = [
  { id: "options", label: "Server options" },
  { id: "advanced", label: "Advanced game settings" },
  { id: "saves", label: "Sessions & saves" },
  { id: "danger", label: "Danger zone" },
];

/** One editable setting, resolved from its key and the value the server sent. */
type Row = {
  key: string;
  raw: unknown;
  spec: FieldSpec;
  /** Canonical draft form of the server's current value - the diff baseline. */
  base: string;
  /** False for catalogue keys this server did not report (advanced settings). */
  present: boolean;
};

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function saveLabel(header: SatisfactorySaveHeader): string {
  const name = String(header.saveName || "(unnamed)");
  const when = header.saveDateTime ? String(header.saveDateTime) : "";
  return when ? `${name} - ${when}` : name;
}

function buildRows(
  catalogue: Record<string, FieldSpec>,
  current: Record<string, unknown>,
  /** Include catalogue keys the server did not report, so they can be set. */
  includeUnreported: boolean
): Row[] {
  const keys = new Set(Object.keys(current));
  if (includeUnreported) for (const key of Object.keys(catalogue)) keys.add(key);
  return orderKeys(catalogue, keys).map((key) => {
    const present = Object.prototype.hasOwnProperty.call(current, key);
    const raw = present ? current[key] : undefined;
    const spec = specFor(catalogue, key, raw);
    return { key, raw, spec, base: toDraft(raw, spec), present };
  });
}

/** Keys whose draft differs from what the server reported. */
function changedKeys(rows: Row[], drafts: Record<string, string>): Row[] {
  return rows.filter((row) => {
    const draft = drafts[row.key];
    return draft !== undefined && draft !== row.base;
  });
}

// --- controls -------------------------------------------------------------

type ControlProps = {
  spec: FieldSpec;
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
};

function EnumSelect({ spec, value, onChange, disabled }: ControlProps) {
  const [manual, setManual] = useState(false);
  const choices = useMemo<SelectChoice[]>(() => {
    if (spec.kind !== "enum") return [];
    const list = [...spec.choices];
    // Never strand a value: a server reporting something outside the catalogue
    // (older build, mod, future update) gets its own option rather than being
    // silently rewritten to whichever choice happens to be first.
    if (!list.some((c) => c.value === value)) {
      list.unshift(
        value === ""
          ? { value: "", label: "(not set)" }
          : { value, label: `${value} - reported by server` }
      );
    }
    return list;
  }, [spec, value]);

  if (manual) {
    return (
      <div className="row wrap">
        <input
          className="grow"
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
        <button
          className="btn small ghost"
          type="button"
          onClick={() => setManual(false)}
        >
          Pick from list
        </button>
      </div>
    );
  }

  return (
    <select
      value={value}
      disabled={disabled}
      onChange={(e) => {
        if (e.target.value === CUSTOM_CHOICE) setManual(true);
        else onChange(e.target.value);
      }}
    >
      {choices.map((choice) => (
        <option key={choice.value} value={choice.value}>
          {choice.label}
        </option>
      ))}
      <option value={CUSTOM_CHOICE}>Custom value…</option>
    </select>
  );
}

function SettingControl({ spec, value, onChange, disabled }: ControlProps) {
  if (spec.kind === "bool") {
    return (
      <select
        value={value === "true" ? "true" : "false"}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="true">Enabled</option>
        <option value="false">Disabled</option>
      </select>
    );
  }
  if (spec.kind === "enum") {
    return (
      <EnumSelect spec={spec} value={value} onChange={onChange} disabled={disabled} />
    );
  }
  if (spec.kind === "number") {
    return (
      <div className="row" style={{ alignItems: "center" }}>
        <input
          type="number"
          value={value}
          min={spec.min}
          max={spec.max}
          step={spec.step}
          disabled={disabled}
          style={{ width: "9rem" }}
          onChange={(e) => onChange(e.target.value)}
        />
        {spec.unit && <span className="muted">{spec.unit}</span>}
      </div>
    );
  }
  return (
    <input
      className="grow"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

/** Label cell: friendly name, raw key, help text and a modified marker. */
function SettingLabel({ row, modified }: { row: Row; modified: boolean }) {
  return (
    <>
      <div>
        {row.spec.label}
        {modified && <span className="muted"> · modified</span>}
      </div>
      <small className="muted">
        <code>{row.key}</code>
        {!row.present && " · not set on this server"}
      </small>
      {row.spec.help && (
        <>
          <br />
          <small className="muted">{row.spec.help}</small>
        </>
      )}
    </>
  );
}

// --- panel ----------------------------------------------------------------

export default function SatisfactoryAdminPanel({ serverId, onChanged }: Props) {
  const [tab, setTab] = useState<Tab>("options");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [options, setOptions] = useState<SatisfactoryOptions | null>(null);
  const [optionDrafts, setOptionDrafts] = useState<Record<string, string>>({});
  const [advanced, setAdvanced] = useState<SatisfactoryAdvanced | null>(null);
  const [advancedDrafts, setAdvancedDrafts] = useState<Record<string, string>>({});
  const [sessions, setSessions] = useState<SatisfactorySessions | null>(null);
  const [saveName, setSaveName] = useState("");

  /** Run an action, then surface its own message instead of a generic one. */
  const run = useCallback(
    async (action: () => Promise<{ detail?: string } | void>, after?: () => void) => {
      setBusy(true);
      setError("");
      setNotice("");
      try {
        const res = await action();
        const detail = res && "detail" in res ? res.detail : "";
        if (detail) setNotice(detail);
        after?.();
        onChanged?.();
      } catch (err) {
        setError(errorText(err));
      } finally {
        setBusy(false);
      }
    },
    [onChanged]
  );

  const loadOptions = useCallback(async () => {
    setError("");
    try {
      const res = await api.satisfactory.options(serverId);
      setOptions(res);
      setOptionDrafts({});
    } catch (err) {
      setError(errorText(err));
    }
  }, [serverId]);

  const loadAdvanced = useCallback(async () => {
    setError("");
    try {
      const res = await api.satisfactory.advancedSettings(serverId);
      setAdvanced(res);
      setAdvancedDrafts({});
    } catch (err) {
      setError(errorText(err));
    }
  }, [serverId]);

  const loadSessions = useCallback(async () => {
    setError("");
    try {
      setSessions(await api.satisfactory.sessions(serverId));
    } catch (err) {
      setError(errorText(err));
    }
  }, [serverId]);

  useEffect(() => {
    if (tab === "options" && !options) void loadOptions();
    if (tab === "advanced" && !advanced) void loadAdvanced();
    if (tab === "saves" && !sessions) void loadSessions();
  }, [tab, options, advanced, sessions, loadOptions, loadAdvanced, loadSessions]);

  // Reset everything when switching servers
  useEffect(() => {
    setOptions(null);
    setAdvanced(null);
    setSessions(null);
    setOptionDrafts({});
    setAdvancedDrafts({});
    setError("");
    setNotice("");
  }, [serverId]);

  // Server options: only rows the server actually reported. ApplyServerOptions
  // is a passthrough to the game's own config, so inventing keys is unsafe.
  const optionRows = useMemo(
    () => buildRows(SERVER_OPTION_FIELDS, options?.server_options ?? {}, false),
    [options]
  );
  const changedOptionRows = useMemo(
    () => changedKeys(optionRows, optionDrafts),
    [optionRows, optionDrafts]
  );
  const optionPayload = useMemo(() => {
    const out: Record<string, string> = {};
    for (const row of changedOptionRows) {
      out[row.key] = encodeOption(optionDrafts[row.key], row.spec, String(row.raw ?? ""));
    }
    return out;
  }, [changedOptionRows, optionDrafts]);

  // Advanced settings: the catalogue is always listed. An empty response means
  // the save has them switched off, and listing the keys is how you turn one on.
  const advancedRows = useMemo(
    () =>
      advanced
        ? buildRows(ADVANCED_SETTING_FIELDS, advanced.advanced_game_settings, true)
        : [],
    [advanced]
  );
  const changedAdvancedRows = useMemo(
    () => changedKeys(advancedRows, advancedDrafts),
    [advancedRows, advancedDrafts]
  );
  const advancedPayload = useMemo(() => {
    const shape = shapeOf(advanced?.advanced_game_settings ?? {});
    const out: Record<string, unknown> = {};
    for (const row of changedAdvancedRows) {
      out[row.key] = encodeAdvanced(
        advancedDrafts[row.key],
        row.spec,
        row.raw,
        shape
      );
    }
    return out;
  }, [advanced, changedAdvancedRows, advancedDrafts]);

  const changedSet = useMemo(
    () => new Set(changedOptionRows.map((r) => r.key)),
    [changedOptionRows]
  );
  const changedAdvancedSet = useMemo(
    () => new Set(changedAdvancedRows.map((r) => r.key)),
    [changedAdvancedRows]
  );

  return (
    <section className="card">
      <div className="row between wrap">
        <h2 style={{ margin: 0 }}>Server Details</h2>
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

      {tab === "options" && (
        <div className="stack" style={{ marginTop: "0.75rem" }}>
          <div className="row wrap">
            <button className="btn" type="button" disabled={busy} onClick={loadOptions}>
              Reload
            </button>
            <button
              className="btn"
              type="button"
              disabled={busy || changedOptionRows.length === 0}
              onClick={() => setOptionDrafts({})}
            >
              Discard changes
            </button>
            <button
              className="btn primary"
              type="button"
              disabled={busy || changedOptionRows.length === 0}
              onClick={() =>
                run(
                  () => api.satisfactory.applyOptions(serverId, optionPayload),
                  loadOptions
                )
              }
            >
              Apply {changedOptionRows.length || ""} change
              {changedOptionRows.length === 1 ? "" : "s"}
            </button>
          </div>
          {!options ? (
            <p className="muted">Loading server options…</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Option</th>
                    <th>Value</th>
                    <th>Pending until restart</th>
                  </tr>
                </thead>
                <tbody>
                  {optionRows.length === 0 ? (
                    <tr>
                      <td colSpan={3} className="muted">
                        The server reported no options.
                      </td>
                    </tr>
                  ) : (
                    optionRows.map((row) => (
                      <tr key={row.key}>
                        <td>
                          <SettingLabel row={row} modified={changedSet.has(row.key)} />
                        </td>
                        <td>
                          <SettingControl
                            spec={row.spec}
                            value={optionDrafts[row.key] ?? row.base}
                            disabled={busy}
                            onChange={(next) =>
                              setOptionDrafts((prev) => ({ ...prev, [row.key]: next }))
                            }
                          />
                        </td>
                        <td className="muted">
                          {options.pending_server_options[row.key] ?? "-"}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {tab === "advanced" && (
        <div className="stack" style={{ marginTop: "0.75rem" }}>
          <div className="alert">
            Applying advanced game settings permanently flags the save as edited and
            disables achievements for it. This cannot be undone.
          </div>
          <div className="row wrap">
            <button className="btn" type="button" disabled={busy} onClick={loadAdvanced}>
              Reload
            </button>
            <button
              className="btn"
              type="button"
              disabled={busy || changedAdvancedRows.length === 0}
              onClick={() => setAdvancedDrafts({})}
            >
              Discard changes
            </button>
            <button
              className="btn danger"
              type="button"
              disabled={busy || changedAdvancedRows.length === 0}
              onClick={() => {
                const names = changedAdvancedRows.map((r) => r.spec.label).join(", ");
                if (
                  !confirm(
                    `Apply ${names}? The save will be permanently marked as edited.`
                  )
                )
                  return;
                void run(
                  () =>
                    api.satisfactory.applyAdvancedSettings(
                      serverId,
                      advancedPayload,
                      true
                    ),
                  loadAdvanced
                );
              }}
            >
              Apply {changedAdvancedRows.length || ""} and mark save as edited
            </button>
          </div>
          {!advanced ? (
            <p className="muted">Loading advanced game settings…</p>
          ) : (
            <>
              <p className="muted">
                Creative mode: {advanced.creative_mode_enabled ? "enabled" : "disabled"}
                {Object.keys(advanced.advanced_game_settings).length === 0 &&
                  " - this save has no advanced settings yet, so applying any of the below is what enables them."}
              </p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Setting</th>
                      <th>Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {advancedRows.map((row) => (
                      <tr key={row.key}>
                        <td>
                          <SettingLabel
                            row={row}
                            modified={changedAdvancedSet.has(row.key)}
                          />
                        </td>
                        <td>
                          <SettingControl
                            spec={row.spec}
                            value={advancedDrafts[row.key] ?? row.base}
                            disabled={busy}
                            onChange={(next) =>
                              setAdvancedDrafts((prev) => ({ ...prev, [row.key]: next }))
                            }
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}

      {tab === "saves" && (
        <div className="stack" style={{ marginTop: "0.75rem" }}>
          <div className="row wrap">
            <input
              className="grow"
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
              placeholder="Save name"
            />
            <button
              className="btn primary"
              type="button"
              disabled={busy || !saveName.trim()}
              onClick={() =>
                run(
                  () => api.satisfactory.save(serverId, saveName.trim()),
                  loadSessions
                )
              }
            >
              Save now
            </button>
            <button className="btn" type="button" disabled={busy} onClick={loadSessions}>
              Reload sessions
            </button>
          </div>

          {!sessions ? (
            <p className="muted">Loading sessions…</p>
          ) : sessions.sessions.length === 0 ? (
            <p className="muted">No sessions on this server yet.</p>
          ) : (
            sessions.sessions.map((session, index) => {
              const name = String(session.sessionName || `Session ${index + 1}`);
              const headers = session.saveHeaders || [];
              const isCurrent = index === sessions.current_session_index;
              return (
                <div className="card" key={`${name}-${index}`}>
                  <div className="row between wrap">
                    <h3 style={{ margin: 0 }}>
                      {name} {isCurrent ? "· active" : ""}
                    </h3>
                    <div className="row wrap">
                      <button
                        className="btn small"
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          run(() => api.satisfactory.setAutoLoad(serverId, name))
                        }
                      >
                        Auto-load this session
                      </button>
                      <button
                        className="btn small danger"
                        type="button"
                        disabled={busy}
                        onClick={() => {
                          if (
                            !confirm(
                              `Delete session "${name}" and every save it contains?`
                            )
                          )
                            return;
                          void run(
                            () => api.satisfactory.deleteSession(serverId, name),
                            loadSessions
                          );
                        }}
                      >
                        Delete session
                      </button>
                    </div>
                  </div>
                  {headers.length === 0 ? (
                    <p className="muted">No saves in this session.</p>
                  ) : (
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>Save</th>
                            <th>Play time</th>
                            <th>Flags</th>
                            <th />
                          </tr>
                        </thead>
                        <tbody>
                          {headers.map((header, hIndex) => {
                            const save = String(header.saveName || "");
                            const seconds = Number(header.playDurationSeconds || 0);
                            const flags = [
                              header.isModdedSave ? "modded" : "",
                              header.isEditedSave ? "edited" : "",
                              header.isCreativeModeEnabled ? "creative" : "",
                            ].filter(Boolean);
                            return (
                              <tr key={`${save}-${hIndex}`}>
                                <td title={saveLabel(header)}>{save || "(unnamed)"}</td>
                                <td>{Math.round(seconds / 3600)} h</td>
                                <td className="muted">{flags.join(", ") || "-"}</td>
                                <td className="row right">
                                  <button
                                    className="btn small"
                                    type="button"
                                    disabled={busy || !save}
                                    onClick={() => {
                                      if (
                                        !confirm(
                                          `Load "${save}"? Everyone is disconnected while the server reloads.`
                                        )
                                      )
                                        return;
                                      void run(() =>
                                        api.satisfactory.load(serverId, save)
                                      );
                                    }}
                                  >
                                    Load
                                  </button>
                                  <button
                                    className="btn small danger"
                                    type="button"
                                    disabled={busy || !save}
                                    onClick={() => {
                                      if (!confirm(`Delete save "${save}"?`)) return;
                                      void run(
                                        () =>
                                          api.satisfactory.deleteSave(serverId, save),
                                        loadSessions
                                      );
                                    }}
                                  >
                                    Delete
                                  </button>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      )}

      {tab === "danger" && (
        <DangerZone serverId={serverId} busy={busy} run={run} />
      )}
    </section>
  );
}

type DangerProps = {
  serverId: number;
  busy: boolean;
  run: (
    action: () => Promise<{ detail?: string } | void>,
    after?: () => void
  ) => Promise<void>;
};

function DangerZone({ serverId, busy, run }: DangerProps) {
  const [newName, setNewName] = useState("");
  const [clientPassword, setClientPassword] = useState("");
  const [adminPassword, setAdminPassword] = useState("");
  const [newSession, setNewSession] = useState("");
  const [startingLocation, setStartingLocation] = useState("");
  const [skipOnboarding, setSkipOnboarding] = useState(true);
  const [claimName, setClaimName] = useState("");
  const [claimPassword, setClaimPassword] = useState("");

  return (
    <div className="stack" style={{ marginTop: "0.75rem" }}>
      <div className="form-grid">
        <label className="full">
          Rename server
          <div className="row wrap">
            <input
              className="grow"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="New server name"
            />
            <button
              className="btn"
              type="button"
              disabled={busy || !newName.trim()}
              onClick={() =>
                run(() => api.satisfactory.rename(serverId, newName.trim()), () =>
                  setNewName("")
                )
              }
            >
              Rename
            </button>
          </div>
        </label>

        <label className="full">
          Client join password (blank clears it)
          <div className="row wrap">
            <input
              className="grow"
              type="password"
              value={clientPassword}
              onChange={(e) => setClientPassword(e.target.value)}
            />
            <button
              className="btn"
              type="button"
              disabled={busy}
              onClick={() =>
                run(
                  () => api.satisfactory.setClientPassword(serverId, clientPassword),
                  () => setClientPassword("")
                )
              }
            >
              Set client password
            </button>
          </div>
        </label>

        <label className="full">
          Admin password
          <div className="row wrap">
            <input
              className="grow"
              type="password"
              value={adminPassword}
              onChange={(e) => setAdminPassword(e.target.value)}
            />
            <button
              className="btn"
              type="button"
              disabled={busy || !adminPassword}
              onClick={() =>
                run(
                  () => api.satisfactory.setAdminPassword(serverId, adminPassword),
                  () => setAdminPassword("")
                )
              }
            >
              Set admin password
            </button>
          </div>
          <small className="muted">
            If this app authenticates with the admin password, the stored secret is
            rotated to match automatically.
          </small>
        </label>

        <label className="full">
          Claim an unclaimed server
          <div className="row wrap">
            <input
              className="grow"
              value={claimName}
              onChange={(e) => setClaimName(e.target.value)}
              placeholder="Server name"
            />
            <input
              className="grow"
              type="password"
              value={claimPassword}
              onChange={(e) => setClaimPassword(e.target.value)}
              placeholder="Admin password to set"
            />
            <button
              className="btn"
              type="button"
              disabled={busy || !claimName.trim() || !claimPassword}
              onClick={() =>
                run(
                  () =>
                    api.satisfactory.claim(
                      serverId,
                      claimName.trim(),
                      claimPassword
                    ),
                  () => {
                    setClaimName("");
                    setClaimPassword("");
                  }
                )
              }
            >
              Claim
            </button>
          </div>
        </label>
      </div>

      <div className="alert error">
        The actions below interrupt play. Both ask for confirmation first.
      </div>
      <div className="form-grid">
        <label>
          New session name
          <input
            value={newSession}
            onChange={(e) => setNewSession(e.target.value)}
            placeholder="e.g. Second Factory"
          />
        </label>
        <label>
          Starting location
          <select
            value={startingLocation}
            onChange={(e) => setStartingLocation(e.target.value)}
          >
            {STARTING_LOCATIONS.map((choice) => (
              <option key={choice.value} value={choice.value}>
                {choice.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Onboarding
          <select
            value={skipOnboarding ? "skip" : "play"}
            onChange={(e) => setSkipOnboarding(e.target.value === "skip")}
          >
            <option value="skip">Skip the tutorial</option>
            <option value="play">Play the tutorial</option>
          </select>
        </label>
        <label className="full">
          <button
            className="btn danger"
            type="button"
            disabled={busy || !newSession.trim()}
            onClick={() => {
              if (
                !confirm(
                  `Start a new game "${newSession.trim()}"? The running session is abandoned unless you saved it.`
                )
              )
                return;
              void run(
                () =>
                  api.satisfactory.newGame(serverId, {
                    session_name: newSession.trim(),
                    starting_location: startingLocation,
                    skip_onboarding: skipOnboarding,
                  }),
                () => setNewSession("")
              );
            }}
          >
            Create new game
          </button>
        </label>
      </div>
      <div className="row wrap">
        <button
          className="btn danger"
          type="button"
          disabled={busy}
          onClick={() => {
            if (
              !confirm(
                "Shut down the game server? It will not come back until something restarts the process."
              )
            )
              return;
            void run(() => api.satisfactory.shutdown(serverId));
          }}
        >
          Shut down server
        </button>
      </div>
    </div>
  );
}

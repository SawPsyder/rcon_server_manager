import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link } from "react-router-dom";
import {
  api,
  MapConfig,
  Schedule,
  ScheduleCreate,
  ScheduleMeta,
  ScheduleRun,
  Server,
} from "../api";
import TimePicker from "../components/TimePicker";

const WEEKDAYS = [
  { id: 0, label: "Mon", short: "M" },
  { id: 1, label: "Tue", short: "T" },
  { id: 2, label: "Wed", short: "W" },
  { id: 3, label: "Thu", short: "T" },
  { id: 4, label: "Fri", short: "F" },
  { id: 5, label: "Sat", short: "S" },
  { id: 6, label: "Sun", short: "S" },
];

const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];

const RANGES = ["24h", "7d", "30d", "180d", "1y"] as const;

/** Fallback when no map catalog lightings are loaded (travel_popular). */
const FALLBACK_LIGHTINGS = ["Day", "Night"];

/** Friendlier labels than backend meta (≤ can render poorly). */
const CHECK_LABELS: Record<string, string> = {
  players_lte: "Max players",
  players_gte: "Min players",
  players_eq: "Exact players",
  server_online: "Server online",
  server_offline: "Server offline",
  container_state: "Container state",
};

/** Build travel params for a map, keeping prior choices when still valid. */
function travelParamsForMap(
  map: MapConfig | undefined,
  prev?: Record<string, unknown>
): Record<string, unknown> {
  if (!map) {
    return {
      map_id: "",
      gamemode_key: String(prev?.gamemode_key || "checkpoint"),
      lighting: String(prev?.lighting || "Day"),
    };
  }
  const modeKeys = Object.keys(map.gamemodes || {});
  const lights = map.lightings?.length ? map.lightings : FALLBACK_LIGHTINGS;
  const prevMode = String(prev?.gamemode_key || "");
  const prevLight = String(prev?.lighting || "");
  return {
    map_id: map.id,
    gamemode_key: modeKeys.includes(prevMode)
      ? prevMode
      : modeKeys[0] || "checkpoint",
    lighting: lights.includes(prevLight) ? prevLight : lights[0] || "Day",
  };
}

function optionsWithCurrent(options: string[], current: string): string[] {
  if (!current || options.includes(current)) return options;
  return [current, ...options];
}

type ActionDraft = {
  action_type: string;
  params: Record<string, unknown>;
};

type CheckDraft = {
  check_type: string;
  params: Record<string, unknown>;
};

type FormState = {
  server_id: number | "";
  name: string;
  enabled: boolean;
  time_local: string;
  days_of_week: number[];
  retry_on_fail: boolean;
  retry_after_minutes: number;
  actions: ActionDraft[];
  checks: CheckDraft[];
};

const emptyForm = (): FormState => ({
  server_id: "",
  name: "",
  enabled: true,
  time_local: "04:00",
  days_of_week: [...ALL_DAYS],
  retry_on_fail: true,
  retry_after_minutes: 10,
  actions: [{ action_type: "power", params: { signal: "restart" } }],
  checks: [],
});

const LEGACY_POWER: Record<string, string> = {
  power_start: "start",
  power_stop: "stop",
  power_restart: "restart",
  power_kill: "kill",
};

const POWER_SIGNALS = [
  { id: "start", label: "Start" },
  { id: "stop", label: "Stop" },
  { id: "restart", label: "Restart" },
  { id: "kill", label: "Kill" },
] as const;

function normalizeActionDraft(
  actionType: string,
  params: Record<string, unknown>
): ActionDraft {
  if (LEGACY_POWER[actionType]) {
    return {
      action_type: "power",
      params: { signal: LEGACY_POWER[actionType] },
    };
  }
  if (actionType === "power") {
    return {
      action_type: "power",
      params: { signal: String(params.signal || "restart") },
    };
  }
  if (actionType === "wait") {
    return {
      action_type: "wait",
      params: { seconds: Number(params.seconds ?? 60) || 60 },
    };
  }
  return { action_type: actionType, params: { ...params } };
}

function defaultParamsForAction(actionType: string): Record<string, unknown> {
  switch (actionType) {
    case "power":
      return { signal: "restart" };
    case "wait":
      return { seconds: 60 };
    case "say":
      return { message: "Server restarting soon." };
    case "rcon":
      return { command: "" };
    case "travel":
      return { map_id: "", gamemode_key: "checkpoint", lighting: "Day" };
    case "travel_popular":
      return { range: "7d", combine_gamemodes: false, lighting: "Day" };
    case "set_startup_popular":
      return { range: "7d", combine_gamemodes: false };
    default:
      return {};
  }
}

type ActionTypeMeta = ScheduleMeta["action_types"][number];

function actionAllowedForServerType(
  action: ActionTypeMeta | ActionDraft | string,
  serverType: string | undefined | null,
  catalog: ActionTypeMeta[]
): boolean {
  const id =
    typeof action === "string"
      ? action
      : "action_type" in action
        ? action.action_type
        : action.id;
  const def = catalog.find((a) => a.id === id);
  if (!def) return !serverType;
  if (!def.server_types || def.server_types.length === 0) return true;
  if (!serverType) return false;
  return def.server_types.includes(serverType);
}

function filterActionsForServerType(
  actions: ActionDraft[],
  serverType: string | undefined | null,
  catalog: ActionTypeMeta[]
): ActionDraft[] {
  const kept = actions.filter((a) =>
    actionAllowedForServerType(a.action_type, serverType, catalog)
  );
  if (kept.length) return kept;
  return [{ action_type: "power", params: defaultParamsForAction("power") }];
}

function formatWhen(iso: string | null | undefined, tz: string): string {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: tz || "UTC",
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(iso));
  } catch {
    return new Date(iso).toLocaleString();
  }
}

function statusPill(status: string): string {
  if (status === "success") return "online";
  if (status === "failed" || status === "partial") return "offline";
  if (
    status === "checks_failed" ||
    status === "skipped" ||
    status === "waiting" ||
    status === "running" ||
    status === "cancelled"
  ) {
    return "pending";
  }
  return "pending";
}

function formatDays(days: number[]): string {
  if (!days.length || days.length === 7) return "Every day";
  return days
    .map((d) => WEEKDAYS.find((w) => w.id === d)?.label || String(d))
    .join(", ");
}

function formatStatusLabel(status: string): string {
  return status.replace(/_/g, " ");
}

function checkLabel(id: string, fallback: string): string {
  return CHECK_LABELS[id] || fallback;
}

function isFormValid(form: FormState): boolean {
  if (form.server_id === "") return false;
  if (!form.name.trim()) return false;
  if (!form.actions.length) return false;
  if (!form.days_of_week.length) return false;
  for (const a of form.actions) {
    if (a.action_type === "power" && !String(a.params.signal || "").trim()) {
      return false;
    }
    if (a.action_type === "wait" && !(Number(a.params.seconds) > 0)) {
      return false;
    }
    if (a.action_type === "say" && !String(a.params.message || "").trim()) {
      return false;
    }
    if (a.action_type === "rcon" && !String(a.params.command || "").trim()) {
      return false;
    }
    if (a.action_type === "travel") {
      if (!Number(a.params.map_id)) return false;
      if (!String(a.params.gamemode_key || "").trim()) return false;
    }
  }
  return true;
}

type PageTab = "schedules" | "history";

export default function SchedulesPage() {
  const [tab, setTab] = useState<PageTab>("schedules");
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [servers, setServers] = useState<Server[]>([]);
  const [meta, setMeta] = useState<ScheduleMeta | null>(null);
  const [maps, setMaps] = useState<MapConfig[]>([]);
  const [gamemodeLabels, setGamemodeLabels] = useState<Record<string, string>>(
    {}
  );
  const [form, setForm] = useState<FormState>(emptyForm());
  const [editId, setEditId] = useState<number | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [runs, setRuns] = useState<ScheduleRun[]>([]);
  const [historyFilter, setHistoryFilter] = useState<number | "">("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [menuOpenId, setMenuOpenId] = useState<number | null>(null);
  const editorRef = useRef<HTMLElement | null>(null);

  const linkedServers = useMemo(
    () => servers.filter((s) => s.pterodactyl_linked),
    [servers]
  );

  const appTz = meta?.app_timezone || "UTC";

  const selectedServer = useMemo(
    () =>
      form.server_id === ""
        ? null
        : servers.find((s) => s.id === form.server_id) || null,
    [form.server_id, servers]
  );

  const showEditor = editorOpen || editId != null;
  const everyDay = form.days_of_week.length === 7;
  const canSubmit = isFormValid(form) && linkedServers.length > 0 && !busy;

  const load = useCallback(async () => {
    const [sv, sc, m] = await Promise.all([
      api.listServers(),
      api.schedules.list(),
      api.schedules.meta(),
    ]);
    setServers(sv);
    setSchedules(sc);
    setMeta(m);
  }, []);

  useEffect(() => {
    load().catch((e) => setError(String(e)));
  }, [load]);

  useEffect(() => {
    if (!selectedServer || selectedServer.server_type !== "sandstorm") {
      setMaps([]);
      setGamemodeLabels({});
      return;
    }
    Promise.all([api.maps("sandstorm"), api.gamemodeLabels("sandstorm")])
      .then(([mp, labels]) => {
        setMaps(mp);
        setGamemodeLabels(labels);
      })
      .catch(() => {
        setMaps([]);
        setGamemodeLabels({});
      });
  }, [selectedServer?.id, selectedServer?.server_type]);

  /** Union of lightings across the map catalog for map-less actions. */
  const catalogLightings = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const m of maps) {
      for (const l of m.lightings || []) {
        if (!seen.has(l)) {
          seen.add(l);
          out.push(l);
        }
      }
    }
    return out.length ? out : FALLBACK_LIGHTINGS;
  }, [maps]);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const rows = await api.schedules.allRuns({
        limit: 200,
        scheduleId:
          historyFilter === "" ? undefined : Number(historyFilter),
      });
      setRuns(rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load history");
      setRuns([]);
    } finally {
      setHistoryLoading(false);
    }
  }, [historyFilter]);

  useEffect(() => {
    if (tab !== "history") return;
    void loadHistory();
  }, [tab, loadHistory]);

  // Empty list → open editor; existing schedules → list-first unless already editing.
  useEffect(() => {
    if (schedules.length === 0 && linkedServers.length > 0 && editId == null) {
      setEditorOpen(true);
    }
  }, [schedules.length, linkedServers.length, editId]);

  // Close overflow menu on outside click.
  useEffect(() => {
    if (menuOpenId == null) return;
    const onDoc = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest?.(`[data-schedules-menu="${menuOpenId}"]`)) return;
      setMenuOpenId(null);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [menuOpenId]);

  const toggleDay = (day: number) => {
    setForm((prev) => {
      const has = prev.days_of_week.includes(day);
      const days = has
        ? prev.days_of_week.filter((d) => d !== day)
        : [...prev.days_of_week, day].sort();
      return { ...prev, days_of_week: days.length ? days : [day] };
    });
  };

  const setEveryDay = () => {
    setForm((prev) => ({ ...prev, days_of_week: [...ALL_DAYS] }));
  };

  const openNew = () => {
    setEditId(null);
    setForm(emptyForm());
    setEditorOpen(true);
    setMsg("");
    setError("");
    queueMicrotask(() =>
      editorRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })
    );
  };

  const startEdit = (s: Schedule) => {
    setTab("schedules");
    setEditId(s.id);
    setEditorOpen(true);
    setMenuOpenId(null);
    const retryMinutes = s.retry_after_minutes > 0 ? s.retry_after_minutes : 10;
    setForm({
      server_id: s.server_id,
      name: s.name,
      enabled: s.enabled,
      time_local: s.time_local,
      days_of_week: s.days_of_week.length
        ? s.days_of_week
        : [...ALL_DAYS],
      retry_on_fail: s.retry_after_minutes > 0,
      retry_after_minutes: retryMinutes,
      actions: filterActionsForServerType(
        s.actions.map((a) =>
          normalizeActionDraft(a.action_type, a.params || {})
        ),
        s.server_type || null,
        meta?.action_types || []
      ),
      checks: s.checks.map((c) => ({
        check_type: c.check_type,
        params: { ...(c.params || {}) },
      })),
    });
    setMsg("");
    setError("");
    queueMicrotask(() =>
      editorRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })
    );
  };

  const resetForm = () => {
    setEditId(null);
    setForm(emptyForm());
    // List-first when schedules exist
    setEditorOpen(schedules.length === 0);
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!isFormValid(form)) {
      setError("Fill in server, name, and at least one valid action");
      return;
    }
    setBusy(true);
    setError("");
    setMsg("");
    const payload: ScheduleCreate = {
      server_id: Number(form.server_id),
      name: form.name.trim(),
      enabled: form.enabled,
      time_local: form.time_local,
      days_of_week: form.days_of_week,
      retry_after_minutes: form.retry_on_fail
        ? Math.max(1, form.retry_after_minutes || 10)
        : 0,
      actions: form.actions.map((a, i) => ({
        action_type: a.action_type,
        params: a.params,
        sort_order: i,
      })),
      checks: form.checks.map((c, i) => ({
        check_type: c.check_type,
        params: c.params,
        sort_order: i,
      })),
    };
    try {
      if (editId) {
        await api.schedules.update(editId, payload);
        setMsg("Schedule updated");
      } else {
        await api.schedules.create(payload);
        setMsg("Schedule created");
      }
      setEditId(null);
      setForm(emptyForm());
      setEditorOpen(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: number) => {
    if (!confirm("Delete this schedule?")) return;
    setMenuOpenId(null);
    try {
      await api.schedules.remove(id);
      if (editId === id) resetForm();
      if (historyFilter === id) setHistoryFilter("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const toggleEnabled = async (s: Schedule) => {
    setMenuOpenId(null);
    try {
      await api.schedules.enable(s.id, !s.enabled);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Toggle failed");
    }
  };

  const runNow = async (id: number) => {
    if (!confirm("Run this schedule now? Checks still apply.")) return;
    setMenuOpenId(null);
    setBusy(true);
    try {
      await api.schedules.runNow(id);
      setMsg("Run queued — open the History tab in a moment for details");
      await load();
      if (tab === "history") await loadHistory();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Run failed");
    } finally {
      setBusy(false);
    }
  };

  const openHistory = (scheduleId?: number) => {
    setMenuOpenId(null);
    if (scheduleId != null) setHistoryFilter(scheduleId);
    setTab("history");
  };

  const setAction = (index: number, patch: Partial<ActionDraft>) => {
    setForm((prev) => {
      const actions = [...prev.actions];
      actions[index] = { ...actions[index], ...patch };
      return { ...prev, actions };
    });
  };

  const setCheck = (index: number, patch: Partial<CheckDraft>) => {
    setForm((prev) => {
      const checks = [...prev.checks];
      checks[index] = { ...checks[index], ...patch };
      return { ...prev, checks };
    });
  };

  const moveAction = (index: number, dir: -1 | 1) => {
    setForm((prev) => {
      const j = index + dir;
      if (j < 0 || j >= prev.actions.length) return prev;
      const actions = [...prev.actions];
      [actions[index], actions[j]] = [actions[j], actions[index]];
      return { ...prev, actions };
    });
  };

  const actionCatalog = meta?.action_types || [];
  const checkOptions = meta?.check_types || [];

  const selectedServerType = selectedServer?.server_type || "";

  const actionOptions = useMemo(() => {
    if (!selectedServerType) {
      return actionCatalog.filter(
        (a) => !a.server_types || a.server_types.length === 0
      );
    }
    return actionCatalog.filter((a) =>
      actionAllowedForServerType(a, selectedServerType, actionCatalog)
    );
  }, [actionCatalog, selectedServerType]);

  const onServerChange = (nextId: number | "") => {
    const nextServer =
      nextId === "" ? null : servers.find((s) => s.id === nextId) || null;
    const nextType = nextServer?.server_type || "";
    const catalog = meta?.action_types || [];

    setForm((prev) => {
      const removedCount = prev.actions.filter(
        (a) =>
          !actionAllowedForServerType(
            a.action_type,
            nextType || null,
            catalog
          )
      ).length;
      const actions = filterActionsForServerType(
        prev.actions,
        nextType || null,
        catalog
      );
      queueMicrotask(() => {
        if (removedCount > 0) {
          setMsg(
            removedCount === 1
              ? "Removed 1 action that is not supported on this server type."
              : `Removed ${removedCount} actions that are not supported on this server type.`
          );
        } else {
          setMsg("");
        }
      });
      return {
        ...prev,
        server_id: nextId,
        actions,
      };
    });
  };

  const enabledCount = schedules.filter((s) => s.enabled).length;

  const renderEditor = () => (
    <section className="card schedules-editor" ref={editorRef}>
      <div className="schedules-card-head">
        <div>
          <h2 className="schedules-card-title">
            {editId ? "Edit schedule" : "New schedule"}
          </h2>
          {editId ? (
            <p className="muted schedules-card-hint">
              #{editId}
              {form.name ? ` · ${form.name}` : ""}
            </p>
          ) : null}
        </div>
        <button
          type="button"
          className="btn ghost small"
          onClick={() => {
            resetForm();
            if (schedules.length > 0) setEditorOpen(false);
          }}
        >
          {editId ? "Cancel" : schedules.length > 0 ? "Close" : "Clear"}
        </button>
      </div>

      <form className="stack schedule-form" onSubmit={onSubmit}>
        {/* Server */}
        <div className="form-section">
          <h3 className="form-section-title">Server</h3>
          <div className="schedules-server-row">
            <label>
              Server
              <select
                value={form.server_id}
                onChange={(e) =>
                  onServerChange(e.target.value ? Number(e.target.value) : "")
                }
                required
                disabled={!!editId || !linkedServers.length}
              >
                <option value="">Select linked server…</option>
                {linkedServers.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} ({s.server_type})
                  </option>
                ))}
              </select>
            </label>
            <label>
              Name
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                required
                maxLength={120}
                placeholder="Nightly restart"
                disabled={!linkedServers.length}
              />
            </label>
            <div className="schedules-enabled">
              <label className="toggle-switch">
                <span className="toggle-switch-label">Enabled</span>
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(e) =>
                    setForm({ ...form, enabled: e.target.checked })
                  }
                  disabled={!linkedServers.length}
                />
                <span className="toggle-switch-track" aria-hidden>
                  <span className="toggle-switch-thumb" />
                </span>
              </label>
            </div>
          </div>
        </div>

        {/* When */}
        <div className="form-section">
          <h3 className="form-section-title">When</h3>
          <div className="schedules-when">
            <div className="schedules-when-time">
              <div className="schedules-field-label">Time ({appTz})</div>
              <TimePicker
                value={form.time_local}
                onChange={(time_local) => setForm({ ...form, time_local })}
                disabled={!linkedServers.length}
              />
            </div>
            <div className="schedules-when-days">
              <div className="schedules-field-label" id="schedules-days-label">
                Days
              </div>
              <div
                className={`schedules-days${everyDay ? " is-every" : ""}`}
                role="group"
                aria-labelledby="schedules-days-label"
              >
                <button
                  type="button"
                  className={`schedules-day schedules-day-every${everyDay ? " is-active" : ""}`}
                  aria-pressed={everyDay}
                  disabled={!linkedServers.length}
                  onClick={setEveryDay}
                >
                  Every day
                </button>
                {WEEKDAYS.map((d) => {
                  const on = form.days_of_week.includes(d.id);
                  return (
                    <button
                      key={d.id}
                      type="button"
                      className={`schedules-day${on ? " is-active" : ""}`}
                      aria-pressed={on}
                      title={d.label}
                      disabled={!linkedServers.length}
                      onClick={() => toggleDay(d.id)}
                    >
                      <span className="schedules-day-full">{d.label}</span>
                      <span className="schedules-day-short" aria-hidden>
                        {d.short}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </div>

        {/* Retry */}
        <div className="form-section">
          <h3 className="form-section-title">Retry</h3>
          <div className="schedules-retry">
            <label
              className="toggle-switch"
              title="If checks fail, try again later instead of skipping the window"
            >
              <span className="toggle-switch-label">Retry until checks pass</span>
              <input
                type="checkbox"
                checked={form.retry_on_fail}
                onChange={(e) =>
                  setForm({ ...form, retry_on_fail: e.target.checked })
                }
                disabled={!linkedServers.length}
              />
              <span className="toggle-switch-track" aria-hidden>
                <span className="toggle-switch-thumb" />
              </span>
            </label>
            {form.retry_on_fail && (
              <label className="schedules-retry-input">
                Retry cycle (minutes)
                <input
                  type="number"
                  min={1}
                  max={1440}
                  value={form.retry_after_minutes}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      retry_after_minutes: Number(e.target.value) || 10,
                    })
                  }
                  disabled={!linkedServers.length}
                />
              </label>
            )}
          </div>
        </div>

        {/* Checks */}
        <div className="form-section">
          <div className="schedules-section-head">
            <h3 className="form-section-title" style={{ margin: 0 }}>
              Checks
            </h3>
            <button
              type="button"
              className="btn small"
              disabled={!linkedServers.length}
              onClick={() =>
                setForm({
                  ...form,
                  checks: [
                    ...form.checks,
                    { check_type: "players_lte", params: { value: 0 } },
                  ],
                })
              }
            >
              Add check
            </button>
          </div>

          <div className="schedules-items">
            {!form.checks.length && (
              <div className="schedules-items-empty muted">
                No checks — actions always run at the target time.
              </div>
            )}
            {form.checks.map((c, i) => (
              <div key={i} className="schedule-item">
                <div className="schedule-item-main schedule-row schedule-row-check">
                  <label className="schedule-item-type">
                    Type
                    <select
                      value={c.check_type}
                      onChange={(e) =>
                        setCheck(i, {
                          check_type: e.target.value,
                          params: e.target.value.startsWith("players_")
                            ? { value: Number(c.params.value ?? 0) }
                            : e.target.value === "container_state"
                              ? { state: "running" }
                              : {},
                        })
                      }
                    >
                      {checkOptions.map((opt) => (
                        <option key={opt.id} value={opt.id}>
                          {checkLabel(opt.id, opt.label)}
                        </option>
                      ))}
                    </select>
                  </label>
                  {c.check_type.startsWith("players_") && (
                    <label className="schedule-item-value">
                      Value
                      <input
                        type="number"
                        min={0}
                        value={Number(c.params.value ?? 0)}
                        onChange={(e) =>
                          setCheck(i, {
                            params: { value: Number(e.target.value) },
                          })
                        }
                      />
                    </label>
                  )}
                  {c.check_type === "container_state" && (
                    <label className="schedule-item-value">
                      State
                      <select
                        value={String(c.params.state || "running")}
                        onChange={(e) =>
                          setCheck(i, { params: { state: e.target.value } })
                        }
                      >
                        {["running", "offline", "starting", "stopping"].map(
                          (s) => (
                            <option key={s} value={s}>
                              {s}
                            </option>
                          )
                        )}
                      </select>
                    </label>
                  )}
                  <div className="field-actions">
                    <span className="field-actions-label" aria-hidden>
                      ·
                    </span>
                    <div className="field-actions-btns">
                      <button
                        type="button"
                        className="btn ghost small"
                        onClick={() =>
                          setForm({
                            ...form,
                            checks: form.checks.filter((_, j) => j !== i),
                          })
                        }
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Actions */}
        <div className="form-section">
          <div className="schedules-section-head">
            <h3 className="form-section-title" style={{ margin: 0 }}>
              Actions
            </h3>
            {form.server_id !== "" && (
              <button
                type="button"
                className="btn small"
                onClick={() =>
                  setForm({
                    ...form,
                    actions: [
                      ...form.actions,
                      {
                        action_type: "power",
                        params: defaultParamsForAction("power"),
                      },
                    ],
                  })
                }
              >
                Add action
              </button>
            )}
          </div>

          {form.server_id === "" ? (
            <div className="schedules-items-empty schedules-actions-gate muted">
              <strong className="schedules-actions-gate-title">
                Select a server first
              </strong>
              <span>
                Power, wait, and game-specific actions appear after you choose a
                linked server.
              </span>
            </div>
          ) : (
            <div className="schedules-items">
              {form.actions.map((a, i) => (
                <div key={i} className="schedule-item">
                  <div className="schedule-item-main schedule-row schedule-row-action">
                    <div className="schedule-item-index" aria-hidden>
                      {i + 1}
                    </div>
                    <label className="schedule-item-type">
                      Type
                      <select
                        value={a.action_type}
                        onChange={(e) => {
                          const nextType = e.target.value;
                          let params = defaultParamsForAction(nextType);
                          if (nextType === "travel") {
                            params = travelParamsForMap(
                              maps[0],
                              params
                            );
                          }
                          setAction(i, {
                            action_type: nextType,
                            params,
                          });
                        }}
                      >
                        {actionOptions.map((opt) => (
                          <option key={opt.id} value={opt.id}>
                            {opt.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    {a.action_type === "power" && (
                      <label className="schedule-item-value">
                        Signal
                        <select
                          value={String(a.params.signal || "restart")}
                          onChange={(e) =>
                            setAction(i, {
                              params: {
                                ...a.params,
                                signal: e.target.value,
                              },
                            })
                          }
                        >
                          {POWER_SIGNALS.map((s) => (
                            <option key={s.id} value={s.id}>
                              {s.label}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                    {a.action_type === "wait" && (
                      <label className="schedule-item-value">
                        Seconds
                        <input
                          type="number"
                          min={1}
                          max={3600}
                          value={Number(a.params.seconds ?? 60)}
                          onChange={(e) =>
                            setAction(i, {
                              params: {
                                ...a.params,
                                seconds: Number(e.target.value) || 60,
                              },
                            })
                          }
                        />
                      </label>
                    )}
                    <div className="field-actions">
                      <span className="field-actions-label" aria-hidden>
                        ·
                      </span>
                      <div className="field-actions-btns">
                        <button
                          type="button"
                          className="btn ghost small"
                          disabled={i === 0}
                          title="Move up"
                          onClick={() => moveAction(i, -1)}
                        >
                          Up
                        </button>
                        <button
                          type="button"
                          className="btn ghost small"
                          disabled={i >= form.actions.length - 1}
                          title="Move down"
                          onClick={() => moveAction(i, 1)}
                        >
                          Down
                        </button>
                        <button
                          type="button"
                          className="btn ghost small"
                          onClick={() =>
                            setForm({
                              ...form,
                              actions: form.actions.filter((_, j) => j !== i),
                            })
                          }
                        >
                          Remove
                        </button>
                      </div>
                    </div>
                  </div>

                  {a.action_type === "say" && (
                    <label className="schedule-item-extra">
                      Message
                      <input
                        value={String(a.params.message || "")}
                        onChange={(e) =>
                          setAction(i, {
                            params: {
                              ...a.params,
                              message: e.target.value,
                            },
                          })
                        }
                      />
                    </label>
                  )}
                  {a.action_type === "rcon" && (
                    <label className="schedule-item-extra">
                      Command
                      <input
                        value={String(a.params.command || "")}
                        onChange={(e) =>
                          setAction(i, {
                            params: {
                              ...a.params,
                              command: e.target.value,
                            },
                          })
                        }
                      />
                    </label>
                  )}
                  {a.action_type === "travel" && (() => {
                    const mapId = Number(a.params.map_id || "") || 0;
                    const travelMap =
                      maps.find((m) => m.id === mapId) || undefined;
                    const modeKeys = Object.keys(travelMap?.gamemodes || {});
                    const currentMode = String(a.params.gamemode_key || "");
                    const modeOptions = optionsWithCurrent(
                      modeKeys,
                      currentMode
                    );
                    const lightOpts = travelMap?.lightings?.length
                      ? travelMap.lightings
                      : FALLBACK_LIGHTINGS;
                    const currentLight = String(
                      a.params.lighting || "Day"
                    );
                    const lightingOptions = optionsWithCurrent(
                      lightOpts,
                      currentLight
                    );
                    return (
                      <div className="schedule-row schedule-item-extra">
                        <label>
                          Map
                          <select
                            value={mapId || ""}
                            onChange={(e) => {
                              const nextId = e.target.value
                                ? Number(e.target.value)
                                : 0;
                              const nextMap =
                                maps.find((m) => m.id === nextId) ||
                                undefined;
                              setAction(i, {
                                params: travelParamsForMap(
                                  nextMap,
                                  a.params
                                ),
                              });
                            }}
                          >
                            <option value="">Select…</option>
                            {maps.map((m) => (
                              <option key={m.id} value={m.id}>
                                {m.alias}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          Gamemode
                          <select
                            value={currentMode}
                            disabled={!travelMap}
                            onChange={(e) =>
                              setAction(i, {
                                params: {
                                  ...a.params,
                                  gamemode_key: e.target.value,
                                },
                              })
                            }
                          >
                            {!travelMap && (
                              <option value="">Select map first…</option>
                            )}
                            {modeOptions.map((k) => (
                              <option key={k} value={k}>
                                {gamemodeLabels[k] || k}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          Lighting
                          <select
                            value={currentLight}
                            disabled={!travelMap}
                            onChange={(e) =>
                              setAction(i, {
                                params: {
                                  ...a.params,
                                  lighting: e.target.value,
                                },
                              })
                            }
                          >
                            {lightingOptions.map((l) => (
                              <option key={l} value={l}>
                                {l}
                              </option>
                            ))}
                          </select>
                        </label>
                      </div>
                    );
                  })()}
                  {(a.action_type === "travel_popular" ||
                    a.action_type === "set_startup_popular") && (
                    <div className="schedule-row schedule-item-extra">
                      <label>
                        Popularity range
                        <select
                          value={String(a.params.range || "7d")}
                          onChange={(e) =>
                            setAction(i, {
                              params: {
                                ...a.params,
                                range: e.target.value,
                              },
                            })
                          }
                        >
                          {RANGES.map((r) => (
                            <option key={r} value={r}>
                              {r}
                            </option>
                          ))}
                        </select>
                      </label>
                      {a.action_type === "travel_popular" && (
                        <label>
                          Lighting
                          <select
                            value={String(a.params.lighting || "Day")}
                            onChange={(e) =>
                              setAction(i, {
                                params: {
                                  ...a.params,
                                  lighting: e.target.value,
                                },
                              })
                            }
                          >
                            {optionsWithCurrent(
                              catalogLightings,
                              String(a.params.lighting || "Day")
                            ).map((l) => (
                              <option key={l} value={l}>
                                {l}
                              </option>
                            ))}
                          </select>
                        </label>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="form-section schedules-form-footer">
          <div className="row wrap" style={{ gap: "0.5rem" }}>
            <button className="btn primary" disabled={!canSubmit}>
              {busy
                ? "Saving…"
                : editId
                  ? "Save changes"
                  : "Create schedule"}
            </button>
            {(editId || schedules.length > 0) && (
              <button
                type="button"
                className="btn ghost"
                onClick={() => {
                  resetForm();
                  if (schedules.length > 0) setEditorOpen(false);
                }}
              >
                Cancel
              </button>
            )}
          </div>
        </div>
      </form>
    </section>
  );

  return (
    <div className="stack schedules-page">
      <header className="schedules-header">
        <div className="schedules-header-text">
          <h1 className="schedules-title">Schedules</h1>
        </div>
        <div className="schedules-header-meta">
          {tab === "schedules" && schedules.length > 0 && (
            <span className="schedules-stat">
              <strong>{schedules.length}</strong>
              <span className="muted">
                {schedules.length === 1 ? "schedule" : "schedules"}
              </span>
              {enabledCount !== schedules.length && (
                <>
                  <span className="muted">·</span>
                  <strong>{enabledCount}</strong>
                  <span className="muted">on</span>
                </>
              )}
            </span>
          )}
          {tab === "schedules" && linkedServers.length > 0 && !showEditor && (
            <button type="button" className="btn primary small" onClick={openNew}>
              New schedule
            </button>
          )}
          {tab === "schedules" &&
            linkedServers.length > 0 &&
            showEditor &&
            schedules.length > 0 &&
            !editId && (
            <button
              type="button"
              className="btn ghost small"
              onClick={() => {
                setEditorOpen(false);
                setForm(emptyForm());
              }}
            >
              Hide form
            </button>
          )}
        </div>
      </header>

      <div className="row wrap">
        {(
          [
            { id: "schedules" as const, label: "Schedules" },
            { id: "history" as const, label: "History" },
          ] as const
        ).map((t) => (
          <button
            key={t.id}
            type="button"
            className={`btn small ${tab === t.id ? "primary" : "ghost"}`}
            onClick={() => {
              setTab(t.id);
              setMsg("");
              setError("");
              setMenuOpenId(null);
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {msg && <div className="alert ok">{msg}</div>}
      {error && <div className="alert error">{error}</div>}

      {tab === "schedules" && !linkedServers.length && (
        <div className="schedules-empty card">
          <div className="schedules-empty-icon" aria-hidden>
            ⚡
          </div>
          <h2 className="schedules-empty-title">No linked servers</h2>
          <p className="muted schedules-empty-copy">
            Schedules need a Pterodactyl container. Link one under{" "}
            <Link to="/servers">Servers</Link>, then create a schedule here.
          </p>
        </div>
      )}

      {/* Editor above the list */}
      {tab === "schedules" &&
        linkedServers.length > 0 &&
        showEditor &&
        renderEditor()}

      {/* Flat list on the page (no card/table-wrap) so the More menu can
          overflow without creating a nested scroll container. */}
      {tab === "schedules" && schedules.length > 0 && (
        <table className="schedules-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Server</th>
              <th>When</th>
              <th>Next run</th>
              <th>Last</th>
              <th className="col-actions" />
            </tr>
          </thead>
          <tbody>
            {schedules.map((s) => {
              const isEditing = editId === s.id;
              const menuOpen = menuOpenId === s.id;
              return (
                <tr
                  key={s.id}
                  className={isEditing ? "is-editing" : undefined}
                >
                  <td>
                    <div className="schedules-name-cell">
                      <div className="schedules-name-row">
                        <strong className="schedules-name">{s.name}</strong>
                        <span
                          className={`pill ${s.enabled ? "online" : "offline"}`}
                        >
                          {s.enabled ? "On" : "Off"}
                        </span>
                      </div>
                      {!s.pterodactyl_linked && (
                        <div className="schedules-warn">Server unlinked</div>
                      )}
                    </div>
                  </td>
                  <td>
                    <span className="schedules-server">
                      {s.server_name || s.server_id}
                    </span>
                    {s.server_type ? (
                      <div className="muted schedules-type">
                        {s.server_type}
                      </div>
                    ) : null}
                  </td>
                  <td>
                    <div className="schedules-when-cell">
                      <span className="schedules-time">{s.time_local}</span>
                      <span className="muted schedules-days-text">
                        {formatDays(s.days_of_week)}
                      </span>
                    </div>
                  </td>
                  <td title={s.next_run_at || undefined}>
                    {s.enabled
                      ? formatWhen(s.next_run_at, s.app_timezone || appTz)
                      : "—"}
                  </td>
                  <td>
                    {s.last_status ? (
                      <span
                        className={`pill ${statusPill(s.last_status)}`}
                        title={s.last_message || undefined}
                      >
                        {formatStatusLabel(s.last_status)}
                      </span>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td
                    className="col-actions"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <div className="schedules-row-actions">
                      <button
                        type="button"
                        className="btn small"
                        onClick={() => startEdit(s)}
                      >
                        Edit
                      </button>
                      <div
                        className="schedules-menu"
                        data-schedules-menu={s.id}
                      >
                        <button
                          type="button"
                          className="btn small ghost"
                          aria-expanded={menuOpen}
                          aria-haspopup="menu"
                          onClick={() =>
                            setMenuOpenId(menuOpen ? null : s.id)
                          }
                        >
                          More
                        </button>
                        {menuOpen && (
                          <div className="schedules-menu-panel" role="menu">
                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => openHistory(s.id)}
                            >
                              History
                            </button>
                            <button
                              type="button"
                              role="menuitem"
                              disabled={!s.enabled || busy}
                              onClick={() => void runNow(s.id)}
                            >
                              Run now
                            </button>
                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => void toggleEnabled(s)}
                            >
                              {s.enabled ? "Disable" : "Enable"}
                            </button>
                            <button
                              type="button"
                              role="menuitem"
                              className="is-danger"
                              onClick={() => void remove(s.id)}
                            >
                              Delete
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {tab === "schedules" &&
        schedules.length === 0 &&
        linkedServers.length > 0 &&
        !showEditor && (
        <div className="schedules-list-empty card">
          <p className="muted" style={{ margin: 0 }}>
            No schedules yet.{" "}
            <button type="button" className="btn small primary" onClick={openNew}>
              New schedule
            </button>
          </p>
        </div>
      )}

      {tab === "history" && (
        <div className="schedules-history">
          <div className="schedules-history-toolbar">
            <label className="schedules-history-filter">
              Schedule
              <select
                value={historyFilter}
                onChange={(e) =>
                  setHistoryFilter(
                    e.target.value ? Number(e.target.value) : ""
                  )
                }
              >
                <option value="">All schedules</option>
                {schedules.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="btn small ghost"
              disabled={historyLoading}
              onClick={() => void loadHistory()}
            >
              {historyLoading ? "Loading…" : "Refresh"}
            </button>
          </div>
          {!runs.length && !historyLoading ? (
            <p className="muted" style={{ margin: "0.25rem 0 0" }}>
              No runs recorded yet.
            </p>
          ) : (
            <table className="schedules-table schedules-history-table">
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Schedule</th>
                  <th>Server</th>
                  <th>Window</th>
                  <th>Attempt</th>
                  <th>Status</th>
                  <th>Message</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id}>
                    <td>{formatWhen(r.started_at, appTz)}</td>
                    <td>
                      <span className="schedules-server">
                        {r.schedule_name ||
                          (r.schedule_id != null
                            ? `#${r.schedule_id}`
                            : "—")}
                      </span>
                    </td>
                    <td className="muted">
                      {r.server_name ||
                        (r.server_id != null ? `#${r.server_id}` : "—")}
                    </td>
                    <td>{formatWhen(r.scheduled_for, appTz)}</td>
                    <td className="schedules-attempt">{r.attempt}</td>
                    <td>
                      <span className={`pill ${statusPill(r.status)}`}>
                        {formatStatusLabel(r.status)}
                      </span>
                    </td>
                    <td className="muted schedules-run-msg">{r.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}


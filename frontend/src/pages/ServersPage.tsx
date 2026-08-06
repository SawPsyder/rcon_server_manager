import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, ButtonDraft, Server, ServerTypeInfo } from "../api";

type FormState = {
  name: string;
  host: string;
  query_port: number;
  rcon_port: number;
  rcon_password: string;
  server_type: string;
  preferred_gamemode: string;
};

const emptyForm = (types: ServerTypeInfo[]): FormState => {
  const t = types[0];
  return {
    name: "",
    host: "",
    query_port: t?.default_query_port ?? 27131,
    rcon_port: t?.default_rcon_port ?? 27015,
    rcon_password: "",
    server_type: t?.id ?? "sandstorm",
    preferred_gamemode: "",
  };
};

export default function ServersPage() {
  const [servers, setServers] = useState<Server[]>([]);
  const [types, setTypes] = useState<ServerTypeInfo[]>([]);
  const [form, setForm] = useState<FormState>(emptyForm([]));
  const [editId, setEditId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [overrideButtons, setOverrideButtons] = useState(false);
  const [buttonDrafts, setButtonDrafts] = useState<ButtonDraft[]>([]);

  const typeById = useMemo(() => {
    const m = new Map<string, ServerTypeInfo>();
    types.forEach((t) => m.set(t.id, t));
    return m;
  }, [types]);

  const selectedType = typeById.get(form.server_type);

  const load = async () => {
    const [sv, ty] = await Promise.all([api.listServers(), api.serverTypes()]);
    setServers(sv);
    setTypes(ty);
    setForm((prev) => {
      if (prev.server_type || !ty[0]) return prev.name ? prev : emptyForm(ty);
      return emptyForm(ty);
    });
  };

  useEffect(() => {
    load().catch((e) => setError(String(e)));
  }, []);

  const onTypeChange = (typeId: string) => {
    const t = typeById.get(typeId);
    setForm((prev) => {
      const prevType = typeById.get(prev.server_type);
      const next = { ...prev, server_type: typeId };
      // Prefill ports when they still match the previous type defaults (or on blank create)
      if (
        t &&
        (!editId ||
          (prevType &&
            prev.query_port === prevType.default_query_port &&
            prev.rcon_port === prevType.default_rcon_port))
      ) {
        next.query_port = t.default_query_port;
        next.rcon_port = t.default_rcon_port;
      }
      if (!t?.features.map_travel) {
        next.preferred_gamemode = "";
      }
      return next;
    });
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const preferred =
        selectedType?.features.map_travel && form.preferred_gamemode.trim()
          ? form.preferred_gamemode.trim()
          : null;

      let serverId = editId;
      if (editId) {
        const payload: Parameters<typeof api.updateServer>[1] = {
          name: form.name,
          host: form.host,
          query_port: Number(form.query_port),
          rcon_port: Number(form.rcon_port),
          server_type: form.server_type,
        };
        if (form.rcon_password) payload.rcon_password = form.rcon_password;
        if (preferred) {
          payload.preferred_gamemode = preferred;
        } else {
          payload.clear_preferred_gamemode = true;
        }
        await api.updateServer(editId, payload);
      } else {
        const created = await api.createServer({
          name: form.name,
          host: form.host,
          query_port: Number(form.query_port),
          rcon_port: Number(form.rcon_port),
          rcon_password: form.rcon_password,
          server_type: form.server_type,
          preferred_gamemode: preferred,
        });
        serverId = created.id;
      }

      if (serverId != null) {
        if (overrideButtons) {
          await api.replaceServerButtons(
            serverId,
            buttonDrafts
              .filter((b) => b.label.trim() && b.command.trim())
              .map((b, i) => ({
                label: b.label.trim(),
                command: b.command.trim(),
                sort_order: i,
              }))
          );
        } else if (editId) {
          // Inherit type defaults
          await api.replaceServerButtons(editId, []);
        }
      }

      setForm(emptyForm(types));
      setEditId(null);
      setOverrideButtons(false);
      setButtonDrafts([]);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  const startEdit = async (s: Server) => {
    setEditId(s.id);
    setForm({
      name: s.name,
      host: s.host,
      query_port: s.query_port,
      rcon_port: s.rcon_port,
      rcon_password: "",
      server_type: s.server_type || "sandstorm",
      preferred_gamemode: s.preferred_gamemode || "",
    });
    try {
      const resolved = await api.serverButtons(s.id);
      const isOverride = resolved.some((b) => b.server_id === s.id);
      setOverrideButtons(isOverride);
      setButtonDrafts(
        isOverride
          ? resolved.map((b) => ({ label: b.label, command: b.command, sort_order: b.sort_order }))
          : []
      );
    } catch {
      setOverrideButtons(false);
      setButtonDrafts([]);
    }
  };

  const remove = async (id: number) => {
    if (!confirm("Delete this server?")) return;
    await api.deleteServer(id);
    if (editId === id) {
      setEditId(null);
      setForm(emptyForm(types));
      setOverrideButtons(false);
      setButtonDrafts([]);
    }
    await load();
  };

  return (
    <div className="stack">
      <section className="card">
        <h2>{editId ? "Edit server" : "Add server"}</h2>
        <form className="form-grid" onSubmit={onSubmit}>
          <label>
            Server type
            <select
              value={form.server_type}
              onChange={(e) => onTypeChange(e.target.value)}
              required
            >
              {types.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
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
            />
          </label>
          <label>
            Host / IP
            <input
              value={form.host}
              onChange={(e) => setForm({ ...form, host: e.target.value })}
              required
            />
          </label>
          <label>
            Query port
            <input
              type="number"
              value={form.query_port}
              onChange={(e) => setForm({ ...form, query_port: Number(e.target.value) })}
              required
            />
          </label>
          <label>
            RCON port
            <input
              type="number"
              value={form.rcon_port}
              onChange={(e) => setForm({ ...form, rcon_port: Number(e.target.value) })}
              required
            />
          </label>
          <label className="full">
            RCON password {editId ? "(leave blank to keep)" : ""}
            <input
              type="password"
              value={form.rcon_password}
              onChange={(e) => setForm({ ...form, rcon_password: e.target.value })}
              required={!editId}
            />
          </label>

          {selectedType?.features.map_travel && (
            <label className="full">
              Preferred gamemode (optional override)
              <input
                value={form.preferred_gamemode}
                onChange={(e) => setForm({ ...form, preferred_gamemode: e.target.value })}
                placeholder="Leave blank to use type default (Settings)"
              />
            </label>
          )}

          <div className="full stack" style={{ gap: "0.5rem" }}>
            <label className="inline">
              <input
                type="checkbox"
                checked={overrideButtons}
                onChange={(e) => {
                  setOverrideButtons(e.target.checked);
                  if (!e.target.checked) setButtonDrafts([]);
                  else if (buttonDrafts.length === 0) {
                    setButtonDrafts([{ label: "", command: "" }]);
                  }
                }}
              />
              Override quick RCON buttons for this server
            </label>
            {overrideButtons && (
              <div className="stack">
                <p className="muted" style={{ margin: 0 }}>
                  When set, these replace the type defaults for this server only. Uncheck to
                  inherit type defaults.
                </p>
                {buttonDrafts.map((b, idx) => (
                  <div key={idx} className="form-grid compact">
                    <label>
                      Label
                      <input
                        value={b.label}
                        onChange={(e) => {
                          const next = [...buttonDrafts];
                          next[idx] = { ...next[idx], label: e.target.value };
                          setButtonDrafts(next);
                        }}
                      />
                    </label>
                    <label>
                      Command
                      <input
                        value={b.command}
                        onChange={(e) => {
                          const next = [...buttonDrafts];
                          next[idx] = { ...next[idx], command: e.target.value };
                          setButtonDrafts(next);
                        }}
                      />
                    </label>
                    <div className="row">
                      <button
                        type="button"
                        className="btn small danger"
                        onClick={() =>
                          setButtonDrafts(buttonDrafts.filter((_, i) => i !== idx))
                        }
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                ))}
                <button
                  type="button"
                  className="btn small"
                  onClick={() => setButtonDrafts([...buttonDrafts, { label: "", command: "" }])}
                >
                  Add button
                </button>
              </div>
            )}
          </div>

          {error && <div className="alert error full">{error}</div>}
          <div className="row full">
            <button className="btn primary" disabled={busy}>
              {editId ? "Update" : "Add"}
            </button>
            {editId && (
              <button
                type="button"
                className="btn ghost"
                onClick={() => {
                  setEditId(null);
                  setForm(emptyForm(types));
                  setOverrideButtons(false);
                  setButtonDrafts([]);
                }}
              >
                Cancel
              </button>
            )}
          </div>
        </form>
      </section>

      <section className="card">
        <h2>Configured servers</h2>
        {servers.length === 0 ? (
          <p className="muted">No servers yet. Add one to get started.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th>Host</th>
                  <th>Query</th>
                  <th>RCON</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {servers.map((s) => (
                  <tr key={s.id}>
                    <td>{s.name}</td>
                    <td>{typeById.get(s.server_type)?.label || s.server_type}</td>
                    <td>
                      <code>
                        {s.host}:{s.query_port}
                      </code>
                    </td>
                    <td>{s.query_port}</td>
                    <td>
                      {s.rcon_port} {s.has_rcon_password ? "🔒" : "⚠️"}
                    </td>
                    <td className="row right">
                      <button className="btn small" onClick={() => startEdit(s)}>
                        Edit
                      </button>
                      <button className="btn small danger" onClick={() => remove(s.id)}>
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

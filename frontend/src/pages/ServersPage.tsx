import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, PterodactylPanelServer, Server, ServerTypeInfo } from "../api";

type FormState = {
  name: string;
  host: string;
  query_port: number;
  rcon_port: number;
  rcon_password: string;
  server_type: string;
  preferred_gamemode: string;
  use_https: boolean;
  verify_tls: boolean;
  cert_fingerprint: string;
  /** Linked Pterodactyl container uuid; "" means not linked. */
  pterodactyl_uuid: string;
  /** Display labels cached at link time. Carried through the form so an
   *  unrelated edit while the panel is unreachable does not blank them. */
  pterodactyl_identifier: string;
  pterodactyl_name: string;
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
    use_https: false,
    verify_tls: false,
    cert_fingerprint: "",
    pterodactyl_uuid: "",
    pterodactyl_identifier: "",
    pterodactyl_name: "",
  };
};

export default function ServersPage() {
  const [servers, setServers] = useState<Server[]>([]);
  const [types, setTypes] = useState<ServerTypeInfo[]>([]);
  const [form, setForm] = useState<FormState>(emptyForm([]));
  const [editId, setEditId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Panel inventory for the link dropdown. null = not loaded / unavailable, so
  // the field hides itself rather than showing an empty select.
  const [panelServers, setPanelServers] = useState<PterodactylPanelServer[] | null>(
    null,
  );
  const [panelBusy, setPanelBusy] = useState(false);
  const [panelError, setPanelError] = useState("");

  const typeById = useMemo(() => {
    const m = new Map<string, ServerTypeInfo>();
    types.forEach((t) => m.set(t.id, t));
    return m;
  }, [types]);

  const selectedType = typeById.get(form.server_type);
  // Games whose admin API and query share one port (Satisfactory: 7777)
  const singlePort = selectedType?.endpoint_style === "single_port";
  const secretLabel = selectedType?.secret_label || "RCON password";

  const load = async () => {
    const [sv, ty] = await Promise.all([api.listServers(), api.serverTypes()]);
    setServers(sv);
    setTypes(ty);
    setForm((prev) => {
      if (prev.server_type || !ty[0]) return prev.name ? prev : emptyForm(ty);
      return emptyForm(ty);
    });
  };

  /** Fetch the panel inventory. Failure is not an error on this page - the
   *  integration is optional, so the field simply stays hidden. */
  const loadPanelServers = async (refresh = false) => {
    setPanelBusy(true);
    setPanelError("");
    try {
      setPanelServers(await api.pterodactyl.panelServers(refresh));
    } catch (err) {
      setPanelServers(null);
      // Only worth surfacing on an explicit Refresh; on first load a
      // "not configured" 400 is the normal state.
      if (refresh) {
        setPanelError(err instanceof Error ? err.message : "Could not reach the panel");
      }
    } finally {
      setPanelBusy(false);
    }
  };

  useEffect(() => {
    load().catch((e) => setError(String(e)));
    void loadPanelServers();
  }, []);

  const onTypeChange = (typeId: string) => {
    const t = typeById.get(typeId);
    setForm((prev) => {
      const prevType = typeById.get(prev.server_type);
      const next = { ...prev, server_type: typeId };
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

      // One API port for single-port games - the backend keeps both columns in sync
      const queryPort = Number(form.query_port);
      const rconPort = singlePort ? queryPort : Number(form.rcon_port);
      // TLS settings only mean something over HTTPS; a plain-HTTP type would
      // otherwise persist a pin that is never checked.
      const tlsOn = !selectedType?.features.tls_optional || form.use_https;
      // The Pterodactyl link applies to every server type, so options are
      // always sent; the TLS trio is merged in only for types that have an
      // admin API to reach. (The backend merges partially, so omitted keys
      // keep their stored values.)
      const options: NonNullable<Parameters<typeof api.updateServer>[1]["options"]> = {
        pterodactyl_uuid: form.pterodactyl_uuid,
        // Cached labels so the table can name the link without calling the
        // panel. Cleared server-side when the uuid is "".
        pterodactyl_identifier: form.pterodactyl_identifier,
        pterodactyl_name: form.pterodactyl_name,
      };
      if (selectedType?.features.admin_api) {
        options.use_https = form.use_https;
        options.verify_tls = tlsOn && form.verify_tls;
        options.cert_fingerprint = tlsOn ? form.cert_fingerprint.trim() : "";
      }

      if (editId) {
        const payload: Parameters<typeof api.updateServer>[1] = {
          name: form.name,
          host: form.host,
          query_port: queryPort,
          rcon_port: rconPort,
          server_type: form.server_type,
        };
        if (form.rcon_password) payload.rcon_password = form.rcon_password;
        payload.options = options;
        if (preferred) {
          payload.preferred_gamemode = preferred;
        } else {
          payload.clear_preferred_gamemode = true;
        }
        await api.updateServer(editId, payload);
      } else {
        await api.createServer({
          name: form.name,
          host: form.host,
          query_port: queryPort,
          rcon_port: rconPort,
          rcon_password: form.rcon_password,
          server_type: form.server_type,
          preferred_gamemode: preferred,
          options,
        });
      }

      setForm(emptyForm(types));
      setEditId(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  const startEdit = (s: Server) => {
    setEditId(s.id);
    setForm({
      name: s.name,
      host: s.host,
      query_port: s.query_port,
      // Only ever null for non-admins, who cannot reach this page at all.
      rcon_port: s.rcon_port ?? s.query_port,
      rcon_password: "",
      server_type: s.server_type || "sandstorm",
      preferred_gamemode: s.preferred_gamemode || "",
      use_https: s.options?.use_https ?? false,
      verify_tls: s.options?.verify_tls ?? false,
      cert_fingerprint: s.options?.cert_fingerprint ?? "",
      pterodactyl_uuid: s.options?.pterodactyl_uuid ?? "",
      pterodactyl_identifier: s.options?.pterodactyl_identifier ?? "",
      pterodactyl_name: s.options?.pterodactyl_name ?? "",
    });
  };

  const remove = async (id: number) => {
    if (!confirm("Delete this server?")) return;
    await api.deleteServer(id);
    if (editId === id) {
      setEditId(null);
      setForm(emptyForm(types));
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
            {singlePort ? "API port" : "Query port"}
            <input
              type="number"
              value={form.query_port}
              onChange={(e) => setForm({ ...form, query_port: Number(e.target.value) })}
              required
            />
          </label>
          {!singlePort && (
            <label>
              RCON port
              <input
                type="number"
                value={form.rcon_port}
                onChange={(e) => setForm({ ...form, rcon_port: Number(e.target.value) })}
                required
              />
            </label>
          )}
          <label className="full">
            {secretLabel} {editId ? "(leave blank to keep)" : ""}
            <input
              type="password"
              value={form.rcon_password}
              onChange={(e) => setForm({ ...form, rcon_password: e.target.value })}
              required={!editId}
            />
          </label>

          {selectedType?.features.admin_api && (
            <>
              {/* Types that speak plain HTTP by default (Palworld) can still be
                  reached over HTTPS through a reverse proxy. */}
              {selectedType.features.tls_optional && (
                <label className="full">
                  <input
                    type="checkbox"
                    checked={form.use_https}
                    onChange={(e) => setForm({ ...form, use_https: e.target.checked })}
                  />{" "}
                  Use HTTPS
                  <small className="muted">
                    {selectedType.label} serves plain HTTP. Turn this on only if the
                    API sits behind a TLS-terminating reverse proxy.
                  </small>
                </label>
              )}
              <label className="full">
                <input
                  type="checkbox"
                  checked={form.verify_tls}
                  disabled={selectedType.features.tls_optional && !form.use_https}
                  onChange={(e) => setForm({ ...form, verify_tls: e.target.checked })}
                />{" "}
                Verify TLS certificate
                <small className="muted">
                  {selectedType.features.tls_optional
                    ? "Only applies over HTTPS."
                    : `${selectedType.label} serves a self-signed certificate unless you
                       installed your own, so leave this off and pin the fingerprint instead.`}
                </small>
              </label>
              <label className="full">
                Pinned certificate fingerprint (SHA-256, optional)
                <input
                  value={form.cert_fingerprint}
                  disabled={selectedType.features.tls_optional && !form.use_https}
                  onChange={(e) =>
                    setForm({ ...form, cert_fingerprint: e.target.value })
                  }
                  placeholder="aa:bb:cc:… - leave blank to skip certificate checks"
                />
              </label>
            </>
          )}

          {/* Type-independent: any server can run in a Pterodactyl container.
              Hidden entirely when the integration is not configured, unless
              this server is already linked - in which case the admin needs to
              see (and be able to remove) the dangling link. */}
          {(panelServers !== null || form.pterodactyl_uuid) && (
            <div className="field full">
              <label htmlFor="ptero-link">Pterodactyl container (optional)</label>
              <div className="row wrap" style={{ gap: "0.5rem" }}>
                <select
                  id="ptero-link"
                  style={{ flex: "1 1 20rem" }}
                  value={form.pterodactyl_uuid}
                  onChange={(e) => {
                    const uuid = e.target.value;
                    const chosen = panelServers?.find((p) => p.uuid === uuid);
                    setForm((f) => ({
                      ...f,
                      pterodactyl_uuid: uuid,
                      pterodactyl_identifier: uuid ? (chosen?.identifier ?? "") : "",
                      pterodactyl_name: uuid ? (chosen?.name ?? "") : "",
                    }));
                  }}
                >
                  <option value="">— Not linked —</option>
                  {/* A link whose container is missing from the inventory
                      (panel unreachable, or the container was deleted) still
                      needs an entry, or selecting it would silently unlink. */}
                  {form.pterodactyl_uuid &&
                    !panelServers?.some((p) => p.uuid === form.pterodactyl_uuid) && (
                      <option value={form.pterodactyl_uuid}>
                        {form.pterodactyl_name || form.pterodactyl_uuid} (not
                        visible in the panel right now)
                      </option>
                    )}
                  {(panelServers ?? []).map((p) => {
                    const takenByOther =
                      p.linked_server_id !== null && p.linked_server_id !== editId;
                    return (
                      <option key={p.uuid} value={p.uuid} disabled={takenByOther}>
                        {p.name}
                        {p.node ? ` — ${p.node}` : ""} ({p.identifier})
                        {takenByOther ? " ⚠ already linked" : ""}
                        {p.is_suspended ? " — suspended" : ""}
                      </option>
                    );
                  })}
                </select>
                <button
                  type="button"
                  className="btn small ghost"
                  disabled={panelBusy}
                  onClick={() => void loadPanelServers(true)}
                >
                  {panelBusy ? "Loading…" : "Refresh"}
                </button>
              </div>
              <small className="muted">
                Adds container CPU / memory and start, stop and restart controls
                to this server's page. Configure the panel under Settings →
                Pterodactyl.
              </small>
              {panelError && <div className="alert error">{panelError}</div>}
            </div>
          )}

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
                  <th>Admin</th>
                  <th>Pterodactyl</th>
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
                      {typeById.get(s.server_type)?.endpoint_style === "single_port"
                        ? "same port"
                        : s.rcon_port}{" "}
                      {s.has_rcon_password ? "🔒" : "⚠️"}
                    </td>
                    <td>
                      {s.pterodactyl_linked ? (
                        s.options?.pterodactyl_name || "linked"
                      ) : (
                        <span className="muted">—</span>
                      )}
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

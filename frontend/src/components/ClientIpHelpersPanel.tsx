import { useCallback, useEffect, useState } from "react";
import { api, type ClientIpDebug } from "../api";

export default function ClientIpHelpersPanel() {
  const [data, setData] = useState<ClientIpDebug | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      setData(await api.clientIpDebug());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load client IP headers");
      setData(null);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="stack">
      {error && <div className="alert error">{error}</div>}

      <section className="card">
        <div className="row between wrap">
          <div>
            <h2>Client IP headers</h2>
            <p className="muted" style={{ margin: 0 }}>
              Rate limiting and Turnstile use the address from{" "}
              <code>CLIENT_IP_HEADER</code> when set, otherwise the TCP peer. Use
              this table to see which headers your reverse proxy actually sends,
              then set the environment variable to the right name (Cloudflare:{" "}
              <code>CF-Connecting-IP</code>). Leave it empty when nothing sits in
              front of the app.
            </p>
          </div>
          <button className="btn small" type="button" disabled={busy} onClick={() => void load()}>
            {busy ? "Refreshing…" : "Refresh"}
          </button>
        </div>

        {!data && !error && <p className="muted">Loading…</p>}

        {data && (
          <>
            <div className="form-grid settings-form" style={{ marginTop: "1rem" }}>
              <label>
                Configured header
                <input
                  readOnly
                  value={data.configured_header || "(not set — using TCP peer)"}
                />
              </label>
              <label>
                Resolved client IP
                <input readOnly value={data.resolved_client_ip || "—"} />
              </label>
              <label>
                TCP peer (socket)
                <input readOnly value={data.socket_peer || "—"} />
              </label>
            </div>

            <p className="muted" style={{ marginTop: "1rem", marginBottom: "0.5rem" }}>
              Headers on <em>this</em> browser request. A missing header here does
              not mean the proxy never sets it for other clients — open this page
              through the same path users take (e.g. via Cloudflare).
            </p>

            <div className="table-wrap">
              <table className="helpers-ip-table">
                <thead>
                  <tr>
                    <th>Header</th>
                    <th>Present</th>
                    <th>Value</th>
                  </tr>
                </thead>
                <tbody>
                  {data.headers.map((h) => {
                    const isConfigured =
                      data.configured_header &&
                      h.name.toLowerCase() === data.configured_header.toLowerCase();
                    return (
                      <tr
                        key={h.name}
                        className={isConfigured ? "selected" : undefined}
                        style={{ cursor: "default" }}
                      >
                        <td>
                          <code>{h.name}</code>
                          {isConfigured ? (
                            <span className="pill online" style={{ marginLeft: "0.5rem" }}>
                              configured
                            </span>
                          ) : null}
                        </td>
                        <td>{h.present ? "yes" : "no"}</td>
                        <td>
                          {h.present ? (
                            <code className="steam-id">{h.value ?? ""}</code>
                          ) : (
                            <span className="muted">—</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <p className="muted" style={{ marginTop: "1rem", fontSize: "0.85rem" }}>
              Only set <code>CLIENT_IP_HEADER</code> when every request reaches the
              app through a proxy that overwrites that header. If the app port is
              reachable directly, clients can spoof the header and bypass per-IP
              limits.
            </p>
          </>
        )}
      </section>
    </div>
  );
}

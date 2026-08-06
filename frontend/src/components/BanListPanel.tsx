import { BanEntry } from "../api";

type Props = {
  bans: BanEntry[];
  loading: boolean;
  error: string;
  busy: boolean;
  onRefresh: () => void;
  onUnban: (netId: string) => void;
  raw?: string;
  showRaw: boolean;
  onToggleRaw: () => void;
};

export default function BanListPanel({
  bans,
  loading,
  error,
  busy,
  onRefresh,
  onUnban,
  raw,
  showRaw,
  onToggleRaw,
}: Props) {
  return (
    <section className="card">
      <div className="row between wrap">
        <h2 style={{ margin: 0 }}>Banned players</h2>
        <div className="row wrap">
          <button className="btn" type="button" disabled={busy || loading} onClick={onRefresh}>
            {loading ? "Loading…" : "Refresh bans"}
          </button>
          {raw != null && raw !== "" && (
            <button className="btn ghost" type="button" onClick={onToggleRaw}>
              {showRaw ? "Hide raw" : "Show raw"}
            </button>
          )}
        </div>
      </div>
      <p className="muted" style={{ marginTop: "0.35rem" }}>
        Parsed from RCON <code>listbans</code>. Unban sends{" "}
        <code>unban &quot;…&quot;</code> with the full network id.
      </p>

      {error && <div className="alert error">{error}</div>}

      <div className="table-wrap" style={{ marginTop: "0.75rem" }}>
        <table>
          <thead>
            <tr>
              <th title="Ban list order">#</th>
              <th title="Platform / id system">Platform</th>
              <th title="Identifier used for unban">Net ID</th>
              <th title="Permanent or temporary duration">Duration</th>
              <th title="Ban reason text from the server">Reason</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {loading && bans.length === 0 ? (
              <tr>
                <td colSpan={6} className="muted">
                  Loading ban list…
                </td>
              </tr>
            ) : bans.length === 0 ? (
              <tr>
                <td colSpan={6} className="muted">
                  No bans parsed. Click Refresh bans or check raw output if the server replied.
                </td>
              </tr>
            ) : (
              bans.map((b) => (
                <tr key={b.raw_id}>
                  <td>{b.index}</td>
                  <td>
                    <span className={`platform-pill ${b.platform.toLowerCase().includes("epic") ? "eos" : "steam"}`}>
                      {b.platform}
                    </span>
                  </td>
                  <td>
                    <code className="steam-id" title={b.raw_id}>
                      {b.display_id || b.raw_id}
                    </code>
                    {b.display_id !== b.raw_id && (
                      <div className="muted" style={{ fontSize: "0.75rem" }}>
                        {b.raw_id}
                      </div>
                    )}
                  </td>
                  <td>
                    <span className={b.permanent ? "ban-permanent" : ""}>{b.duration}</span>
                  </td>
                  <td className="ban-reason">{b.reason}</td>
                  <td className="row right">
                    <button
                      className="btn small danger"
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        if (
                          !confirm(
                            `Unban ${b.platform} id?\n${b.raw_id}\n\nReason was: ${b.reason}`
                          )
                        ) {
                          return;
                        }
                        onUnban(b.net_id);
                      }}
                    >
                      Unban
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {bans.length > 0 && (
        <p className="muted" style={{ marginTop: "0.5rem" }}>
          {bans.length} ban{bans.length === 1 ? "" : "s"}
        </p>
      )}

      {showRaw && raw != null && (
        <pre className="console-out" style={{ marginTop: "0.75rem" }}>
          {raw || "(empty)"}
        </pre>
      )}
    </section>
  );
}

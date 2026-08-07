import { BanEntry, identityKey, parseIdentity } from "../api";
import IdentityInfoButton from "./IdentityInfoButton";

type Props = {
  bans: BanEntry[];
  loading: boolean;
  error: string;
  busy: boolean;
  steamLookupEnabled?: boolean;
  fromCache?: boolean;
  fetchedAt?: string | null;
  /** "live" = queried from the server; "local" = derived from our own history. */
  source?: "live" | "local" | string;
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
  identityFlags?: Record<string, boolean>;
  onRefresh: () => void;
  onPageChange: (page: number) => void;
  onUnban: (netId: string) => void;
  onOpenIdentity: (netId: string, name?: string) => void;
  raw?: string;
  showRaw: boolean;
  onToggleRaw: () => void;
};

export default function BanListPanel({
  bans,
  loading,
  error,
  busy,
  steamLookupEnabled,
  fromCache,
  fetchedAt,
  source = "live",
  page,
  pageSize,
  total,
  totalPages,
  identityFlags = {},
  onRefresh,
  onPageChange,
  onUnban,
  onOpenIdentity,
  raw,
  showRaw,
  onToggleRaw,
}: Props) {
  const fetchedLabel = (() => {
    if (!fetchedAt) return null;
    try {
      return new Date(fetchedAt).toLocaleString();
    } catch {
      return fetchedAt;
    }
  })();

  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  return (
    <section className="card">
      <div className="row between wrap">
        <h2 style={{ margin: 0 }}>Banned players</h2>
        <div className="row wrap">
          <button className="btn" type="button" disabled={busy || loading} onClick={onRefresh}>
            {loading ? "Loading…" : source === "local" ? "Reload" : "Refresh from server"}
          </button>
          {raw != null && raw !== "" && (
            <button className="btn ghost" type="button" onClick={onToggleRaw}>
              {showRaw ? "Hide raw" : "Show raw"}
            </button>
          )}
        </div>
      </div>
      <p className="muted" style={{ marginTop: "0.35rem" }}>
        {source === "local" ? (
          // The game exposes no way to read its real ban list, so this is
          // derived from our own moderation log.
          <>Only bans through this dashboard are shown. </>
        ) : (
          <>
            Cached per server. Default view is the cache;{" "}
            <strong>Refresh from server</strong> runs live <code>listbans</code>.{" "}
          </>
        )}
        {fromCache && fetchedLabel ? (
          <>
            Cache from <strong>{fetchedLabel}</strong>.
          </>
        ) : fetchedLabel ? (
          <>
            Last fetched <strong>{fetchedLabel}</strong>.
          </>
        ) : null}{" "}
        Steam names:{" "}
        {steamLookupEnabled ? (
          <>Web API + local identity cache.</>
        ) : (
          <>
            local cache/play history only — set <code>STEAM_WEB_API_KEY</code> for full lookup.
          </>
        )}
      </p>

      {error && <div className="alert error">{error}</div>}

      <div className="table-wrap" style={{ marginTop: "0.75rem" }}>
        <table>
          <thead>
            <tr>
              <th title="Ban list order">#</th>
              <th title="Platform / id system">Platform</th>
              <th title="Resolved persona / display name">Name</th>
              <th title="Identifier used for unban">Net ID</th>
              <th title="Permanent or temporary duration">Duration</th>
              <th title="Ban reason text from the server">Reason</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {loading && bans.length === 0 ? (
              <tr>
                <td colSpan={7} className="muted">
                  Loading ban list…
                </td>
              </tr>
            ) : bans.length === 0 ? (
              <tr>
                <td colSpan={7} className="muted">
                  {source === "local"
                    ? "No bans issued from this dashboard yet."
                    : "No bans on this page. Use Refresh from server if the cache is empty."}
                </td>
              </tr>
            ) : (
              bans.map((b) => (
                <tr key={b.raw_id}>
                  <td>{b.index}</td>
                  <td>
                    <span
                      className={`platform-pill ${
                        b.platform.toLowerCase().includes("epic") ? "eos" : "steam"
                      }`}
                    >
                      {b.platform}
                    </span>
                  </td>
                  <td>
                    <div className="name-with-info">
                      {b.avatar_url ? (
                        <img
                          src={b.avatar_url}
                          alt=""
                          width={28}
                          height={28}
                          className="avatar"
                          referrerPolicy="no-referrer"
                        />
                      ) : null}
                      <div>
                        {b.display_name && b.profile_url ? (
                          <a
                            href={b.profile_url}
                            target="_blank"
                            rel="noreferrer"
                            className="name-link"
                          >
                            {b.display_name}
                          </a>
                        ) : b.display_name ? (
                          <strong>{b.display_name}</strong>
                        ) : (
                          <span className="muted">—</span>
                        )}
                        {b.name_source ? (
                          <div className="muted" style={{ fontSize: "0.7rem" }}>
                            via {b.name_source}
                          </div>
                        ) : null}
                      </div>
                      <IdentityInfoButton
                        hasInfo={(() => {
                          const id = parseIdentity(b.raw_id);
                          return id
                            ? Boolean(identityFlags[identityKey(id.platform, id.external_id)])
                            : false;
                        })()}
                        onClick={() => onOpenIdentity(b.raw_id, b.display_name)}
                      />
                    </div>
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
                            `Unban ${b.display_name || b.platform}?\n${b.raw_id}\n\nReason was: ${b.reason}`
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

      <div className="row between wrap" style={{ marginTop: "0.75rem" }}>
        <p className="muted" style={{ margin: 0 }}>
          {total === 0
            ? "0 bans"
            : `Showing ${from}–${to} of ${total} ban${total === 1 ? "" : "s"}`}
        </p>
        <div className="row wrap">
          <button
            type="button"
            className="btn small"
            disabled={loading || page <= 1}
            onClick={() => onPageChange(page - 1)}
          >
            Previous
          </button>
          <span className="muted" style={{ fontSize: "0.85rem" }}>
            Page {page} / {Math.max(1, totalPages)}
          </span>
          <button
            type="button"
            className="btn small"
            disabled={loading || page >= totalPages}
            onClick={() => onPageChange(page + 1)}
          >
            Next
          </button>
        </div>
      </div>

      {showRaw && raw != null && (
        <pre className="console-out" style={{ marginTop: "0.75rem" }}>
          {raw || "(empty)"}
        </pre>
      )}
    </section>
  );
}

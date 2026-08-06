import { FormEvent, useEffect, useState } from "react";
import { api, IdentityDossier, parseIdentity } from "../api";

type Props = {
  open: boolean;
  netId: string;
  fallbackName?: string;
  onClose: () => void;
  onChanged?: () => void;
};

function formatWhen(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function IdentityDossierModal({
  open,
  netId,
  fallbackName,
  onClose,
  onChanged,
}: Props) {
  const [dossier, setDossier] = useState<IdentityDossier | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const ident = parseIdentity(netId);

  const load = async () => {
    if (!ident) {
      setError("No platform id available for this player.");
      setDossier(null);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const d = await api.identityDossier(ident.platform, ident.external_id);
      setDossier(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setDossier(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) {
      load();
      setNote("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, netId]);

  if (!open) return null;

  const onAddNote = async (e: FormEvent) => {
    e.preventDefault();
    if (!ident || !note.trim()) return;
    setBusy(true);
    try {
      await api.addIdentityNote(ident.platform, ident.external_id, note.trim());
      setNote("");
      await load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onDeleteNote = async (id: number) => {
    if (!confirm("Delete this note?")) return;
    setBusy(true);
    try {
      await api.deleteIdentityNote(id);
      await load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const titleName =
    dossier?.display_name || fallbackName || ident?.external_id || netId;

  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-label="Player info"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="row between wrap">
          <div className="row" style={{ gap: "0.65rem" }}>
            {dossier?.avatar_url ? (
              <img
                src={dossier.avatar_url}
                alt=""
                width={40}
                height={40}
                className="avatar"
                referrerPolicy="no-referrer"
              />
            ) : null}
            <div>
              <h2 style={{ margin: 0 }}>{titleName}</h2>
              <div className="muted" style={{ fontSize: "0.85rem" }}>
                {ident ? (
                  <>
                    <span className="platform-pill steam" style={{ marginRight: "0.35rem" }}>
                      {ident.platform}
                    </span>
                    <code>{ident.external_id}</code>
                  </>
                ) : (
                  <code>{netId}</code>
                )}
              </div>
              {dossier?.profile_url ? (
                <a
                  className="name-link"
                  href={dossier.profile_url}
                  target="_blank"
                  rel="noreferrer"
                  style={{ fontSize: "0.85rem" }}
                >
                  Open profile
                </a>
              ) : null}
            </div>
          </div>
          <button type="button" className="btn ghost" onClick={onClose}>
            Close
          </button>
        </div>

        {error && <div className="alert error" style={{ marginTop: "0.75rem" }}>{error}</div>}
        {loading && <p className="muted">Loading…</p>}

        {!loading && dossier && (
          <>
            <section style={{ marginTop: "1.1rem" }}>
              <h3 style={{ margin: "0 0 0.5rem" }}>Moderation history</h3>
              {dossier.actions.length === 0 ? (
                <p className="muted">No kick / ban / unban actions recorded yet.</p>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>When</th>
                        <th>Action</th>
                        <th>Server</th>
                        <th>Name</th>
                        <th>Detail</th>
                        <th>Reason</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {dossier.actions.map((a) => (
                        <tr key={a.id}>
                          <td className="muted" style={{ whiteSpace: "nowrap" }}>
                            {formatWhen(a.created_at)}
                          </td>
                          <td>
                            <span className={`action-pill action-${a.action}`}>{a.action}</span>
                          </td>
                          <td>{a.server_name || "—"}</td>
                          <td>{a.player_name || "—"}</td>
                          <td className="muted">{a.detail || "—"}</td>
                          <td className="ban-reason">{a.reason || "—"}</td>
                          <td>{a.ok ? "ok" : <span className="ban-permanent">fail</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section style={{ marginTop: "1.1rem" }}>
              <h3 style={{ margin: "0 0 0.5rem" }}>Admin notes</h3>
              {dossier.notes.length === 0 ? (
                <p className="muted">No notes yet.</p>
              ) : (
                <div className="stack" style={{ gap: "0.5rem" }}>
                  {dossier.notes.map((n) => (
                    <div key={n.id} className="note-card">
                      <div className="row between">
                        <span className="muted" style={{ fontSize: "0.8rem" }}>
                          {formatWhen(n.created_at)}
                        </span>
                        <button
                          type="button"
                          className="btn small danger"
                          disabled={busy}
                          onClick={() => onDeleteNote(n.id)}
                        >
                          Delete
                        </button>
                      </div>
                      <div style={{ whiteSpace: "pre-wrap", marginTop: "0.35rem" }}>{n.body}</div>
                    </div>
                  ))}
                </div>
              )}
              <form className="stack" style={{ marginTop: "0.75rem" }} onSubmit={onAddNote}>
                <label>
                  Add note
                  <textarea
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    rows={3}
                    placeholder="Admin message / context for this player…"
                    required
                  />
                </label>
                <div className="row">
                  <button className="btn primary" type="submit" disabled={busy || !note.trim()}>
                    Save note
                  </button>
                </div>
              </form>
            </section>
          </>
        )}
      </div>
    </div>
  );
}

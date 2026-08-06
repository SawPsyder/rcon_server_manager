import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, IdentityDossier, parseIdentity, PlayerActionLog } from "../api";

type Props = {
  open: boolean;
  netId: string;
  fallbackName?: string;
  onClose: () => void;
  onChanged?: () => void;
};

const HISTORY_PAGE_SIZE = 10;

function formatWhen(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function formatActionLine(a: PlayerActionLog): string {
  const parts = [
    formatWhen(a.created_at),
    a.server_name || null,
    a.player_name || null,
    a.detail || null,
    a.reason || null,
    a.ok ? null : a.error ? `fail: ${a.error}` : "fail",
  ].filter((p): p is string => Boolean(p && String(p).trim()));
  return parts.join(" · ");
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
  const [savedNote, setSavedNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [historyPage, setHistoryPage] = useState(1);

  const ident = parseIdentity(netId);

  const load = async () => {
    if (!ident) {
      setError("No platform id available for this player.");
      setDossier(null);
      setNote("");
      setSavedNote("");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const d = await api.identityDossier(ident.platform, ident.external_id);
      setDossier(d);
      // Single editor: use newest note, or join legacy multi-notes once
      const bodies = (d.notes || []).map((n) => n.body).filter(Boolean);
      const text =
        bodies.length <= 1 ? bodies[0] || "" : bodies.slice().reverse().join("\n\n");
      setNote(text);
      setSavedNote(text);
      setHistoryPage(1);
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
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, netId]);

  const actions = dossier?.actions || [];
  const historyTotalPages = Math.max(1, Math.ceil(actions.length / HISTORY_PAGE_SIZE));
  const historyPageSafe = Math.min(historyPage, historyTotalPages);
  const pageActions = useMemo(() => {
    const start = (historyPageSafe - 1) * HISTORY_PAGE_SIZE;
    return actions.slice(start, start + HISTORY_PAGE_SIZE);
  }, [actions, historyPageSafe]);

  if (!open) return null;

  const dirty = note !== savedNote;

  const onSaveNote = async (e: FormEvent) => {
    e.preventDefault();
    if (!ident) return;
    setBusy(true);
    setError("");
    try {
      await api.setIdentityNote(ident.platform, ident.external_id, note);
      setSavedNote(note);
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

        {error && (
          <div className="alert error" style={{ marginTop: "0.75rem" }}>
            {error}
          </div>
        )}
        {loading && <p className="muted">Loading…</p>}

        {!loading && (
          <>
            <section style={{ marginTop: "1.1rem" }}>
              <h3 style={{ margin: "0 0 0.5rem" }}>Admin notes</h3>
              <form className="stack" onSubmit={onSaveNote}>
                <textarea
                  className="note-editor"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  rows={8}
                  placeholder="Notes about this player…"
                  disabled={!ident || busy}
                />
                <div className="row wrap">
                  <button
                    className="btn primary"
                    type="submit"
                    disabled={!ident || busy || !dirty}
                  >
                    {busy ? "Saving…" : "Save notes"}
                  </button>
                  {dirty && <span className="muted">Unsaved changes</span>}
                </div>
              </form>
            </section>

            <section style={{ marginTop: "1.25rem" }}>
              <h3 style={{ margin: "0 0 0.5rem" }}>Moderation history</h3>
              {actions.length === 0 ? (
                <p className="muted" style={{ margin: 0 }}>
                  No kick / ban / unban actions recorded yet.
                </p>
              ) : (
                <>
                  <ul className="history-lines">
                    {pageActions.map((a) => (
                      <li key={a.id} className="history-line">
                        <span className={`action-pill action-${a.action}`}>{a.action}</span>
                        <span className="history-line-text">{formatActionLine(a)}</span>
                      </li>
                    ))}
                  </ul>
                  {historyTotalPages > 1 && (
                    <div className="row between wrap history-pager">
                      <button
                        type="button"
                        className="btn small"
                        disabled={historyPageSafe <= 1}
                        onClick={() => setHistoryPage((p) => Math.max(1, p - 1))}
                      >
                        Previous
                      </button>
                      <span className="muted">
                        Page {historyPageSafe} of {historyTotalPages}
                        {" · "}
                        {actions.length} event{actions.length === 1 ? "" : "s"}
                      </span>
                      <button
                        type="button"
                        className="btn small"
                        disabled={historyPageSafe >= historyTotalPages}
                        onClick={() =>
                          setHistoryPage((p) => Math.min(historyTotalPages, p + 1))
                        }
                      >
                        Next
                      </button>
                    </div>
                  )}
                </>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  );
}

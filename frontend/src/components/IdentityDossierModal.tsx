import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, IdentityDossier, parseIdentity, PlayerActionLog, PlayerNote } from "../api";
import { useAuth } from "../auth";

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

function formatRelative(iso: string): string {
  try {
    const then = new Date(iso).getTime();
    const now = Date.now();
    const sec = Math.round((now - then) / 1000);
    if (!Number.isFinite(sec)) return formatWhen(iso);
    if (sec < 45) return "just now";
    if (sec < 90) return "1 minute ago";
    if (sec < 3600) return `${Math.round(sec / 60)} minutes ago`;
    if (sec < 5400) return "1 hour ago";
    if (sec < 86400) return `${Math.round(sec / 3600)} hours ago`;
    if (sec < 172800) return "1 day ago";
    if (sec < 86400 * 30) return `${Math.round(sec / 86400)} days ago`;
    return formatWhen(iso);
  } catch {
    return formatWhen(iso);
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

function noteAuthorName(note: PlayerNote): string {
  return (note.author_label || "").trim() || "Unknown";
}

export default function IdentityDossierModal({
  open,
  netId,
  fallbackName,
  onClose,
  onChanged,
}: Props) {
  const { user, isAdmin } = useAuth();
  const [dossier, setDossier] = useState<IdentityDossier | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [myNote, setMyNote] = useState("");
  const [savedMyNote, setSavedMyNote] = useState("");
  const [myNoteId, setMyNoteId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [historyPage, setHistoryPage] = useState(1);

  const ident = parseIdentity(netId);
  const myUserId = user?.id ?? null;

  const load = async () => {
    if (!ident) {
      setError("No platform id available for this player.");
      setDossier(null);
      setMyNote("");
      setSavedMyNote("");
      setMyNoteId(null);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const d = await api.identityDossier(ident.platform, ident.external_id);
      setDossier(d);
      const mine =
        myUserId == null
          ? undefined
          : (d.notes || []).find((n) => n.author_user_id === myUserId);
      const text = mine?.body || "";
      setMyNote(text);
      setSavedMyNote(text);
      setMyNoteId(mine?.id ?? null);
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
  }, [open, netId, myUserId]);

  const actions = dossier?.actions || [];
  const historyTotalPages = Math.max(1, Math.ceil(actions.length / HISTORY_PAGE_SIZE));
  const historyPageSafe = Math.min(historyPage, historyTotalPages);
  const pageActions = useMemo(() => {
    const start = (historyPageSafe - 1) * HISTORY_PAGE_SIZE;
    return actions.slice(start, start + HISTORY_PAGE_SIZE);
  }, [actions, historyPageSafe]);

  const othersNotes = useMemo(() => {
    const notes = dossier?.notes || [];
    return notes
      .filter((n) => n.author_user_id !== myUserId)
      .slice()
      .sort(
        (a, b) =>
          new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime() ||
          b.id - a.id,
      );
  }, [dossier?.notes, myUserId]);

  if (!open) return null;

  const dirty = myNote !== savedMyNote;
  const newestOther = othersNotes[0];

  const onSaveNote = async (e: FormEvent) => {
    e.preventDefault();
    if (!ident) return;
    setBusy(true);
    setError("");
    try {
      await api.setIdentityNote(ident.platform, ident.external_id, myNote);
      setSavedMyNote(myNote);
      await load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onDeleteOwnNote = async () => {
    if (!ident || myNoteId == null) return;
    if (!window.confirm("Delete your note for this player?")) return;
    setBusy(true);
    setError("");
    try {
      await api.deleteIdentityNote(myNoteId);
      setMyNote("");
      setSavedMyNote("");
      setMyNoteId(null);
      await load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onDeleteOrphan = async (note: PlayerNote) => {
    if (!isAdmin || note.author_user_id != null) return;
    if (!window.confirm("Delete this legacy note with no author?")) return;
    setBusy(true);
    setError("");
    try {
      await api.deleteIdentityNote(note.id);
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
              <div className="row between wrap" style={{ marginBottom: "0.5rem" }}>
                <h3 style={{ margin: 0 }}>Notes</h3>
                {newestOther ? (
                  <span className="muted" style={{ fontSize: "0.8rem" }}>
                    Last team update {formatRelative(newestOther.updated_at)}
                    {newestOther.author_label
                      ? ` · ${noteAuthorName(newestOther)}`
                      : ""}
                  </span>
                ) : null}
              </div>

              <form className="stack note-own" onSubmit={onSaveNote}>
                <div className="row between wrap">
                  <span className="note-author-label">Your note</span>
                  {myNoteId != null && savedMyNote ? (
                    <span className="muted" style={{ fontSize: "0.8rem" }}>
                      Updated {formatRelative(
                        (dossier?.notes || []).find((n) => n.id === myNoteId)?.updated_at ||
                          new Date().toISOString(),
                      )}
                    </span>
                  ) : (
                    <span className="muted" style={{ fontSize: "0.8rem" }}>
                      Only you can edit this
                    </span>
                  )}
                </div>
                <textarea
                  className="note-editor"
                  value={myNote}
                  onChange={(e) => setMyNote(e.target.value)}
                  rows={5}
                  placeholder="Your private-to-edit notes about this player (everyone can read them)…"
                  disabled={!ident || busy}
                />
                <div className="row wrap">
                  <button
                    className="btn primary"
                    type="submit"
                    disabled={!ident || busy || !dirty}
                  >
                    {busy ? "Saving…" : "Save your note"}
                  </button>
                  {myNoteId != null && (
                    <button
                      className="btn ghost"
                      type="button"
                      disabled={busy}
                      onClick={() => void onDeleteOwnNote()}
                    >
                      Delete
                    </button>
                  )}
                  {dirty && <span className="muted">Unsaved changes</span>}
                </div>
              </form>

              {othersNotes.length > 0 ? (
                <ul className="note-list" style={{ marginTop: "0.9rem" }}>
                  {othersNotes.map((n) => (
                    <li key={n.id} className="note-card">
                      <div className="row between wrap note-card-head">
                        <strong>{noteAuthorName(n)}</strong>
                        <span
                          className="muted"
                          style={{ fontSize: "0.8rem" }}
                          title={formatWhen(n.updated_at)}
                        >
                          Updated {formatRelative(n.updated_at)}
                        </span>
                      </div>
                      <div className="note-card-body">
                        {n.body.trim() ? n.body : <span className="muted">(empty)</span>}
                      </div>
                      {isAdmin && n.author_user_id == null ? (
                        <div className="row" style={{ marginTop: "0.45rem" }}>
                          <button
                            type="button"
                            className="btn small ghost"
                            disabled={busy}
                            onClick={() => void onDeleteOrphan(n)}
                          >
                            Remove legacy note
                          </button>
                        </div>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted" style={{ margin: "0.75rem 0 0", fontSize: "0.85rem" }}>
                  No notes from other operators yet.
                </p>
              )}
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

import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  api,
  IdentityDossier,
  IdentityProfile,
  parseIdentity,
  PlayerActionLog,
  PlayerNote,
} from "../api";
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

function platformLabel(platform: string): string {
  const known: Record<string, string> = {
    steam: "Steam",
    xbox: "Xbox",
    psn: "PlayStation",
    eos: "Epic",
    mac: "Mac",
    unknown: "Unknown",
  };
  return known[platform] || platform;
}

function ProfileSection({
  profile,
  myUserId,
  isAdmin,
  busy,
  setBusy,
  setError,
  onReload,
  canUnlink,
}: {
  profile: IdentityProfile;
  myUserId: number | null;
  isAdmin: boolean;
  busy: boolean;
  setBusy: (v: boolean) => void;
  setError: (v: string) => void;
  onReload: () => Promise<void>;
  canUnlink: boolean;
}) {
  const [myNote, setMyNote] = useState("");
  const [savedMyNote, setSavedMyNote] = useState("");
  const [myNoteId, setMyNoteId] = useState<number | null>(null);
  const [historyPage, setHistoryPage] = useState(1);

  useEffect(() => {
    const mine =
      myUserId == null
        ? undefined
        : (profile.notes || []).find((n) => n.author_user_id === myUserId);
    const text = mine?.body || "";
    setMyNote(text);
    setSavedMyNote(text);
    setMyNoteId(mine?.id ?? null);
    setHistoryPage(1);
  }, [profile, myUserId]);

  const actions = profile.actions || [];
  const historyTotalPages = Math.max(1, Math.ceil(actions.length / HISTORY_PAGE_SIZE));
  const historyPageSafe = Math.min(historyPage, historyTotalPages);
  const pageActions = useMemo(() => {
    const start = (historyPageSafe - 1) * HISTORY_PAGE_SIZE;
    return actions.slice(start, start + HISTORY_PAGE_SIZE);
  }, [actions, historyPageSafe]);

  const othersNotes = useMemo(() => {
    return (profile.notes || [])
      .filter((n) => n.author_user_id !== myUserId)
      .slice()
      .sort(
        (a, b) =>
          new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime() ||
          b.id - a.id,
      );
  }, [profile.notes, myUserId]);

  const dirty = myNote !== savedMyNote;
  const newestOther = othersNotes[0];

  const onSaveNote = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.setIdentityNote(profile.platform, profile.external_id, myNote);
      setSavedMyNote(myNote);
      await onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onDeleteOwnNote = async () => {
    if (myNoteId == null) return;
    if (!window.confirm("Delete your note for this account?")) return;
    setBusy(true);
    setError("");
    try {
      await api.deleteIdentityNote(myNoteId);
      setMyNote("");
      setSavedMyNote("");
      setMyNoteId(null);
      await onReload();
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
      await onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onUnlink = async () => {
    if (
      !window.confirm(
        `Unlink this ${platformLabel(profile.platform)} account from the linked person?`,
      )
    ) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.unlinkIdentity(profile.platform, profile.external_id);
      await onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="identity-profile-section">
      <div className="row between wrap identity-profile-head">
        <div className="row wrap" style={{ gap: "0.5rem", alignItems: "center" }}>
          {profile.avatar_url ? (
            <img
              src={profile.avatar_url}
              alt=""
              width={32}
              height={32}
              className="avatar"
              referrerPolicy="no-referrer"
            />
          ) : null}
          <div>
            <div className="row wrap" style={{ gap: "0.35rem", alignItems: "center" }}>
              <span className={`platform-pill ${profile.platform}`}>
                {platformLabel(profile.platform)}
              </span>
              <strong>{profile.display_name || profile.external_id}</strong>
            </div>
            <div className="muted" style={{ fontSize: "0.82rem" }}>
              <code>{profile.net_id || profile.external_id}</code>
              {profile.profile_url ? (
                <>
                  {" · "}
                  <a
                    className="name-link"
                    href={profile.profile_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open profile
                  </a>
                </>
              ) : null}
            </div>
          </div>
        </div>
        {canUnlink ? (
          <button
            type="button"
            className="btn small ghost"
            disabled={busy}
            onClick={() => void onUnlink()}
          >
            Unlink
          </button>
        ) : null}
      </div>

      <div className="identity-profile-body">
        <div className="row between wrap" style={{ marginBottom: "0.45rem" }}>
          <h4 style={{ margin: 0 }}>Notes</h4>
          {newestOther ? (
            <span className="muted" style={{ fontSize: "0.8rem" }}>
              Last team update {formatRelative(newestOther.updated_at)}
              {newestOther.author_label ? ` · ${noteAuthorName(newestOther)}` : ""}
            </span>
          ) : null}
        </div>

        <form className="stack note-own" onSubmit={onSaveNote}>
          <div className="row between wrap">
            <span className="note-author-label">Your note</span>
            {myNoteId != null && savedMyNote ? (
              <span className="muted" style={{ fontSize: "0.8rem" }}>
                Updated{" "}
                {formatRelative(
                  (profile.notes || []).find((n) => n.id === myNoteId)?.updated_at ||
                    new Date().toISOString(),
                )}
              </span>
            ) : (
              <span className="muted" style={{ fontSize: "0.8rem" }}>
                Only you can edit this · scoped to this account
              </span>
            )}
          </div>
          <textarea
            className="note-editor"
            value={myNote}
            onChange={(e) => setMyNote(e.target.value)}
            rows={4}
            placeholder="Notes for this platform account (everyone can read them)…"
            disabled={busy}
          />
          <div className="row wrap">
            <button className="btn primary" type="submit" disabled={busy || !dirty}>
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
          <ul className="note-list" style={{ marginTop: "0.75rem" }}>
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
          <p className="muted" style={{ margin: "0.65rem 0 0", fontSize: "0.85rem" }}>
            No notes from other operators on this account.
          </p>
        )}

        <h4 style={{ margin: "1rem 0 0.45rem" }}>Moderation history</h4>
        {actions.length === 0 ? (
          <p className="muted" style={{ margin: 0 }}>
            No kick / ban / unban actions recorded for this account.
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
      </div>
    </section>
  );
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
  const [busy, setBusy] = useState(false);
  const [linkNetId, setLinkNetId] = useState("");

  const ident = parseIdentity(netId);
  const myUserId = user?.id ?? null;

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
      setLinkNetId("");
      void load();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, netId, myUserId]);

  if (!open) return null;

  const profiles: IdentityProfile[] =
    dossier?.profiles && dossier.profiles.length > 0
      ? dossier.profiles
      : dossier
        ? [
            {
              platform: dossier.platform,
              external_id: dossier.external_id,
              net_id: netId,
              display_name: dossier.display_name,
              profile_url: dossier.profile_url,
              avatar_url: dossier.avatar_url,
              has_info: dossier.has_info,
              actions: dossier.actions || [],
              notes: dossier.notes || [],
            },
          ]
        : [];

  const multi = profiles.length > 1;
  const titleName =
    dossier?.display_name ||
    profiles.find((p) => p.display_name)?.display_name ||
    fallbackName ||
    ident?.external_id ||
    netId;

  const onLink = async (e: FormEvent) => {
    e.preventDefault();
    if (!ident) return;
    const raw = linkNetId.trim();
    if (!raw) return;
    setBusy(true);
    setError("");
    try {
      const d = await api.linkIdentity(ident.platform, ident.external_id, {
        net_id: raw,
      });
      setDossier(d);
      setLinkNetId("");
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const reloadAndNotify = async () => {
    await load();
    onChanged?.();
  };

  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="modal-card identity-dossier-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Player info"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="row between wrap">
          <div>
            <h2 style={{ margin: 0 }}>{titleName}</h2>
            <div className="muted" style={{ fontSize: "0.85rem", marginTop: "0.25rem" }}>
              {multi ? (
                <>
                  Linked accounts ·{" "}
                  {profiles.map((p) => platformLabel(p.platform)).join(" · ")}
                </>
              ) : ident ? (
                <>
                  <span className={`platform-pill ${ident.platform}`} style={{ marginRight: "0.35rem" }}>
                    {platformLabel(ident.platform)}
                  </span>
                  <code>{ident.external_id}</code>
                </>
              ) : (
                <code>{netId}</code>
              )}
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
            <section className="identity-link-panel" style={{ marginTop: "1rem" }}>
              <h3 style={{ margin: "0 0 0.4rem" }}>Linked accounts</h3>
              <p className="muted" style={{ margin: "0 0 0.55rem", fontSize: "0.85rem" }}>
                Mark other platform IDs as the same person so they rank as one player.
                Notes and moderation history stay per account below.
              </p>
              <form className="row wrap identity-link-form" onSubmit={onLink}>
                <input
                  type="text"
                  value={linkNetId}
                  onChange={(e) => setLinkNetId(e.target.value)}
                  placeholder="SteamID64, gdk_…, psn_…, or steam_…"
                  disabled={busy || !ident}
                  style={{ flex: "1 1 14rem", minWidth: "12rem" }}
                />
                <button
                  type="submit"
                  className="btn primary"
                  disabled={busy || !ident || !linkNetId.trim()}
                >
                  {busy ? "Linking…" : "Link account"}
                </button>
              </form>
            </section>

            <div className="identity-profiles" style={{ marginTop: "1rem" }}>
              {profiles.map((profile) => (
                <ProfileSection
                  key={`${profile.platform}:${profile.external_id}`}
                  profile={profile}
                  myUserId={myUserId}
                  isAdmin={isAdmin}
                  busy={busy}
                  setBusy={setBusy}
                  setError={setError}
                  onReload={reloadAndNotify}
                  canUnlink={multi}
                />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";
import { api, type ManagedUser, type Server, type ServerTypeInfo } from "../api";
import ServerAccessPicker from "./ServerAccessPicker";

/**
 * Everything you can do to one user, in one place.
 *
 * The alternative - a row of eight buttons per table row - made the list
 * unreadable and put "Delete" one pixel from "Reset password".
 */
export default function UserManageModal({
  user,
  servers,
  types,
  currentUserId,
  onClose,
  onChanged,
  onLink,
}: {
  user: ManagedUser | null;
  servers: Server[];
  types: ServerTypeInfo[];
  currentUserId: number | undefined;
  onClose: () => void;
  onChanged: () => Promise<void> | void;
  /** Surfaces an invite/reset link when mail could not deliver it. */
  onLink: (url: string, note: string) => void;
}) {
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState("user");
  const [grants, setGrants] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!user) return;
    setDisplayName(user.display_name);
    setRole(user.role);
    setGrants(user.server_ids);
    setError("");
    setNotice("");
  }, [user]);

  // Escape to close, which the older modal in this codebase does not do.
  useEffect(() => {
    if (!user) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [user, onClose]);

  if (!user) return null;

  const isSelf = user.id === currentUserId;

  const run = async (action: () => Promise<string | void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const note = await action();
      if (note) setNotice(note);
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  const dirty =
    displayName !== user.display_name ||
    role !== user.role ||
    grants.slice().sort().join(",") !== user.server_ids.slice().sort().join(",");

  const save = () =>
    run(async () => {
      if (displayName !== user.display_name || role !== user.role) {
        await api.users.update(user.id, {
          display_name: displayName,
          ...(role !== user.role ? { role } : {}),
        });
      }
      if (role !== "admin") {
        await api.users.setGrants(user.id, grants);
      }
      return "Saved.";
    });

  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-label={`Manage ${user.email}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="row between wrap">
          <div>
            <h2 style={{ margin: 0 }}>{user.display_name || user.email}</h2>
            <div className="muted" style={{ fontSize: "0.85rem" }}>
              {user.email}
              {isSelf && " · this is you"}
            </div>
          </div>
          <button className="btn ghost small" onClick={onClose}>
            Close
          </button>
        </div>

        {notice && <div className="alert ok">{notice}</div>}
        {error && <div className="alert error">{error}</div>}

        <section className="user-modal-section">
          <h3>Profile</h3>
          <div className="form-grid">
            <label>
              Display name
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
              />
            </label>
            <label>
              Role
              <select
                value={role}
                disabled={isSelf}
                onChange={(e) => setRole(e.target.value)}
              >
                <option value="user">User</option>
                <option value="admin">Administrator</option>
              </select>
              {isSelf && (
                <span className="muted" style={{ fontSize: "0.8rem" }}>
                  You cannot change your own role.
                </span>
              )}
            </label>
          </div>
        </section>

        <section className="user-modal-section">
          <h3>Server access</h3>
          {role === "admin" ? (
            <p className="muted">
              Administrators can reach every server, so there is nothing to grant.
              {user.role !== "admin" && user.server_ids.length > 0 && (
                <>
                  {" "}
                  Their existing {user.server_ids.length}{" "}
                  {user.server_ids.length === 1 ? "grant is" : "grants are"} kept, and
                  apply again if you change them back to a user.
                </>
              )}
            </p>
          ) : (
            <ServerAccessPicker
              servers={servers}
              types={types}
              selected={grants}
              onChange={setGrants}
            />
          )}
        </section>

        {/* Anchored to the editable region above rather than floating in the
            gap between sections, and it says what happens when nothing has
            been touched instead of just sitting there greyed out. */}
        <div className="row between wrap modal-save-bar">
          <span className="muted" style={{ fontSize: "0.85rem" }}>
            {dirty ? "You have unsaved changes." : "No changes to save."}
          </span>
          <button className="btn primary" disabled={busy || !dirty} onClick={save}>
            {busy ? "Saving…" : "Save changes"}
          </button>
        </div>

        <section className="user-modal-section">
          <h3>Access and recovery</h3>
          {user.is_locked && (
            <div className="alert" style={{ marginBottom: "0.75rem" }}>
              <strong>Temporarily locked</strong>
              <div className="muted" style={{ fontSize: "0.85rem", marginTop: "0.25rem" }}>
                Too many failed sign-in attempts
                {user.locked_until
                  ? ` · unlocks around ${new Date(user.locked_until).toLocaleString()}`
                  : ""}
                {user.failed_logins > 0 ? ` · ${user.failed_logins} failures recorded` : ""}.
                They cannot sign in until the lock expires or you unlock them.
              </div>
            </div>
          )}
          <div className="row wrap">
            {user.is_locked && (
              <button
                className="btn primary small"
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    await api.users.unlock(user.id);
                    return "Temporary lock cleared. They can sign in again.";
                  })
                }
              >
                Unlock account
              </button>
            )}
            <button
              className="btn ghost small"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  const res = await api.users.resetPassword(user.id);
                  if (res.emailed) return `Reset link emailed to ${user.email}.`;
                  onLink(
                    res.invite_url,
                    res.invite_url
                      ? "Mail is not configured, so send this reset link yourself."
                      : "",
                  );
                  return res.invite_url
                    ? "Reset link created."
                    : "No link could be built - set the application URL under Email.";
                })
              }
            >
              {user.has_password ? "Send password reset" : "Resend invitation"}
            </button>
            <button
              className="btn ghost small"
              disabled={busy || !user.totp_enabled}
              title={user.totp_enabled ? undefined : "This user has no 2FA enabled"}
              onClick={() =>
                run(async () => {
                  if (!confirm(`Turn off two-factor authentication for ${user.email}?`))
                    return;
                  await api.users.clearTotp(user.id);
                  return "Two-factor authentication disabled.";
                })
              }
            >
              Clear 2FA
            </button>
            <button
              className="btn ghost small"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  await api.users.forceLogout(user.id);
                  return "All sessions revoked.";
                })
              }
            >
              Sign out everywhere
            </button>
          </div>
        </section>

        <section className="user-modal-section danger-zone">
          <h3>Danger zone</h3>
          <div className="row between wrap">
            <div className="grow">
              <strong>{user.is_active ? "Disable this account" : "Enable this account"}</strong>
              <div className="muted" style={{ fontSize: "0.85rem" }}>
                {user.is_active
                  ? "Signs them out immediately and blocks new sign-ins."
                  : "Lets them sign in again. They will need to sign in fresh."}
              </div>
            </div>
            <button
              className="btn small danger"
              disabled={busy || isSelf}
              title={isSelf ? "You cannot disable your own account" : undefined}
              onClick={() =>
                run(async () => {
                  await api.users.update(user.id, { is_active: !user.is_active });
                  return user.is_active ? "Account disabled." : "Account enabled.";
                })
              }
            >
              {user.is_active ? "Disable" : "Enable"}
            </button>
          </div>

          <div className="row between wrap" style={{ marginTop: "0.75rem" }}>
            <div className="grow">
              <strong>Delete this account</strong>
              <div className="muted" style={{ fontSize: "0.85rem" }}>
                Permanent. Their moderation history is kept but no longer attributed.
              </div>
            </div>
            <button
              className="btn small danger"
              disabled={busy || isSelf}
              title={isSelf ? "You cannot delete your own account" : undefined}
              onClick={() =>
                run(async () => {
                  if (!confirm(`Delete ${user.email}? This cannot be undone.`)) return;
                  await api.users.remove(user.id);
                  onClose();
                  return "User deleted.";
                })
              }
            >
              Delete
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}

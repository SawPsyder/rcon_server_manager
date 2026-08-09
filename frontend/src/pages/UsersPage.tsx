import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ManagedUser, type Server, type ServerTypeInfo } from "../api";
import { useAuth } from "../auth";
import ServerAccessPicker from "../components/ServerAccessPicker";
import UserManageModal from "../components/UserManageModal";

function formatDate(iso: string | null): string {
  if (!iso) return "Never";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

/** Status pills for the users table. Disabled wins over temporary lock. */
function statusOf(u: ManagedUser): { label: string; tone: string } {
  if (!u.is_active) return { label: "Disabled", tone: "offline" };
  if (!u.has_password) return { label: "Invited", tone: "pending" };
  if (u.is_locked) return { label: "Temp locked", tone: "locked" };
  return { label: "Active", tone: "online" };
}

export default function UsersPage() {
  const { user: me, config } = useAuth();

  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [servers, setServers] = useState<Server[]>([]);
  const [types, setTypes] = useState<ServerTypeInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");

  const [showInvite, setShowInvite] = useState(false);
  const [invite, setInvite] = useState({ email: "", name: "", role: "user" });
  const [inviteServers, setInviteServers] = useState<number[]>([]);

  /** A link we could not email, held until the admin dismisses it. */
  const [shareLink, setShareLink] = useState({ url: "", note: "" });
  const [managing, setManaging] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const [u, s, t] = await Promise.all([
        api.users.list(),
        api.listServers(),
        api.serverTypes(),
      ]);
      setUsers(u);
      setServers(s);
      setTypes(t);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load users");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (action: () => Promise<string | void>) => {
    setBusy(true);
    setError("");
    setMsg("");
    try {
      const note = await action();
      if (note) setMsg(note);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  const sendInvite = (e: FormEvent) => {
    e.preventDefault();
    setShareLink({ url: "", note: "" });
    void run(async () => {
      const res = await api.users.create({
        email: invite.email,
        display_name: invite.name,
        role: invite.role,
        server_ids: invite.role === "admin" ? [] : inviteServers,
      });
      setInvite({ email: "", name: "", role: "user" });
      setInviteServers([]);
      setShowInvite(false);
      if (res.emailed) return `Invitation emailed to ${res.user.email}.`;
      setShareLink({
        url: res.invite_url,
        note: res.invite_url
          ? "Email is not set up, so send this invitation link yourself."
          : "",
      });
      return res.invite_url
        ? `${res.user.email} created.`
        : `${res.user.email} created, but no link could be built - set the application URL under Email.`;
    });
  };

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return users;
    return users.filter(
      (u) =>
        u.email.toLowerCase().includes(q) || u.display_name.toLowerCase().includes(q),
    );
  }, [users, query]);

  const serverNames = (ids: number[]) =>
    ids
      .map((id) => servers.find((s) => s.id === id)?.name)
      .filter(Boolean)
      .join(", ");

  const managedUser = users.find((u) => u.id === managing) ?? null;
  const adminCount = users.filter((u) => u.role === "admin" && u.is_active).length;

  if (loading) {
    return (
      <div className="center-screen">
        <div className="spinner" />
        <p>Loading users…</p>
      </div>
    );
  }

  return (
    <div className="stack">
      {msg && <div className="alert ok">{msg}</div>}
      {error && <div className="alert error">{error}</div>}

      {shareLink.url && (
        <section className="card">
          <div className="row between wrap">
            <h2 style={{ margin: 0 }}>One-time link</h2>
            <button className="btn ghost small" onClick={() => setShareLink({ url: "", note: "" })}>
              Dismiss
            </button>
          </div>
          <p className="muted">{shareLink.note}</p>
          <div className="code-block">{shareLink.url}</div>
          <div className="row right">
            <button
              className="btn small ghost"
              onClick={() => void navigator.clipboard?.writeText(shareLink.url)}
            >
              Copy link
            </button>
          </div>
        </section>
      )}

      {config && !config.smtp_enabled && (
        <div className="alert">
          Email is not set up, so invitations and password resets are shown here as
          one-time links for you to pass on. Configure it under{" "}
          <Link to="/settings">Settings → Email</Link>.
        </div>
      )}

      <section className="card">
        <div className="row between wrap">
          <div>
            <h2 style={{ margin: 0 }}>
              {users.length} {users.length === 1 ? "user" : "users"}
            </h2>
            <p className="muted" style={{ margin: 0 }}>
              {adminCount} {adminCount === 1 ? "administrator" : "administrators"} ·{" "}
              {users.length - adminCount} with granted access
            </p>
          </div>
          <div className="row wrap">
            <input
              type="search"
              placeholder="Filter by name or email"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <button
              className={`btn ${showInvite ? "ghost" : "primary"}`}
              onClick={() => setShowInvite((v) => !v)}
            >
              {showInvite ? "Cancel" : "Invite user"}
            </button>
          </div>
        </div>

        {showInvite && (
          <form className="form-grid invite-form" onSubmit={sendInvite}>
            <label>
              Email
              <input
                type="email"
                required
                autoFocus
                value={invite.email}
                onChange={(e) => setInvite({ ...invite, email: e.target.value })}
              />
            </label>
            <label>
              Display name
              <input
                type="text"
                placeholder="Optional"
                value={invite.name}
                onChange={(e) => setInvite({ ...invite, name: e.target.value })}
              />
            </label>
            <label className="full">
              Role
              <select
                value={invite.role}
                onChange={(e) => setInvite({ ...invite, role: e.target.value })}
              >
                <option value="user">User — only the servers you grant</option>
                <option value="admin">Administrator — full access</option>
              </select>
            </label>
            {invite.role !== "admin" && (
              <div className="field full">
                <label>Server access</label>
                <ServerAccessPicker
                  servers={servers}
                  types={types}
                  selected={inviteServers}
                  onChange={setInviteServers}
                />
              </div>
            )}
            <div className="full">
              <button className="btn primary" disabled={busy || !invite.email}>
                {busy ? "Working…" : "Send invitation"}
              </button>
            </div>
          </form>
        )}
      </section>

      <section className="card">
        {filtered.length === 0 ? (
          <p className="muted">
            {query
              ? `No users match "${query}".`
              : "No users yet. Invite someone to get started."}
          </p>
        ) : (
          <div className="table-wrap">
            <table className="users-table">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>2FA</th>
                  <th>Servers</th>
                  <th>Last sign-in</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {filtered.map((u) => {
                  const status = statusOf(u);
                  const isSelf = u.id === me?.id;
                  return (
                    <tr key={u.id}>
                      <td>
                        <div className="user-cell">
                          <span className="user-name">
                            {u.display_name || u.email}
                            {isSelf && <span className="muted"> (you)</span>}
                          </span>
                          {u.display_name && (
                            <span className="muted user-email">{u.email}</span>
                          )}
                        </div>
                      </td>
                      <td>
                        {u.role === "admin" ? (
                          <span className="chip role-admin">Admin</span>
                        ) : (
                          <span className="muted">User</span>
                        )}
                      </td>
                      <td>
                        <span className={`pill ${status.tone}`}>{status.label}</span>
                      </td>
                      <td>
                        {u.totp_enabled ? "On" : <span className="muted">Off</span>}
                      </td>
                      <td>
                        {u.role === "admin" ? (
                          <span className="muted">All</span>
                        ) : u.server_ids.length === 0 ? (
                          <span className="muted">None</span>
                        ) : (
                          // Names on hover: the count alone never answers
                          // the question you actually have about a user.
                          <span title={serverNames(u.server_ids)}>
                            {u.server_ids.length}
                          </span>
                        )}
                      </td>
                      <td className="muted">{formatDate(u.last_login_at)}</td>
                      {/* The button goes in a wrapper, not on the td:
                          `display:flex` on a table cell drops it out of the
                          table layout and the column stops lining up. */}
                      <td className="col-actions">
                        <div className="row right">
                          <button
                            className="btn small ghost"
                            onClick={() => setManaging(u.id)}
                          >
                            Manage
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <UserManageModal
        user={managedUser}
        servers={servers}
        types={types}
        currentUserId={me?.id}
        onClose={() => setManaging(null)}
        onChanged={load}
        onLink={(url, note) => setShareLink({ url, note })}
      />
    </div>
  );
}

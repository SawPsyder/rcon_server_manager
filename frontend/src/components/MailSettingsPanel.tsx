import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, type MailSettings } from "../api";
import { useAuth } from "../auth";

const BLANK: MailSettings = {
  host: "",
  port: 587,
  user: "",
  has_password: false,
  starttls: true,
  ssl: false,
  from_address: "",
  from_name: "RCON Server Manager",
  base_url: "",
  enabled: false,
  configured: false,
};

export default function MailSettingsPanel({ onSaved }: { onSaved?: () => void }) {
  const { user } = useAuth();
  const [form, setForm] = useState<MailSettings>(BLANK);
  const [loaded, setLoaded] = useState(false);
  /** Left blank to keep the stored password; the server never sends it back. */
  const [password, setPassword] = useState("");
  const [clearPassword, setClearPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setForm(await api.mail.get());
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load mail settings");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const set = <K extends keyof MailSettings>(key: K, value: MailSettings[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg("");
    setError("");
    try {
      const next = await api.mail.update({
        host: form.host,
        port: form.port,
        user: form.user,
        // undefined keeps the stored password; "" clears it.
        password: clearPassword ? "" : password ? password : undefined,
        starttls: form.starttls,
        ssl: form.ssl,
        from_address: form.from_address,
        from_name: form.from_name,
        base_url: form.base_url,
      });
      setForm(next);
      setPassword("");
      setClearPassword(false);
      setMsg("Mail settings saved.");
      onSaved?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save mail settings");
    } finally {
      setBusy(false);
    }
  };

  const sendTest = async () => {
    setTesting(true);
    setMsg("");
    setError("");
    try {
      await api.mail.test();
      const dest = user?.email ? ` to ${user.email}` : "";
      setMsg(`Test email sent${dest}. Check the inbox, and the spam folder.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send the test email");
    } finally {
      setTesting(false);
    }
  };

  if (!loaded) {
    return <p className="muted">Loading mail settings…</p>;
  }

  return (
    <div className="stack">
      {msg && <div className="alert ok">{msg}</div>}
      {error && <div className="alert error">{error}</div>}

      <section className="card">
        <div className="row between wrap">
          <div>
            <h2>Email delivery</h2>
            <p className="muted" style={{ margin: 0 }}>
              Used to send invitations and password resets. Leave the host empty to
              turn email off - links are then shown in the UI for you to pass on.
            </p>
          </div>
          <span className={`pill ${form.enabled ? "online" : "offline"}`}>
            {form.enabled ? "Active" : "Not sending"}
          </span>
        </div>

        {!form.configured && form.host && (
          <div className="alert">
            These values still come from environment variables. Saving this form
            moves them into the database, and the environment is ignored from then on.
          </div>
        )}

        <form className="form-grid settings-form" onSubmit={save}>
          <label className="full">
            Application URL
            <input
              type="url"
              placeholder="https://ssm.example.org"
              value={form.base_url}
              onChange={(e) => set("base_url", e.target.value)}
            />
            <span className="muted" style={{ fontSize: "0.8rem" }}>
              Where this app is reachable (https://… required, except localhost).
              Invitation and reset links are built from it - never from the browser's
              address bar, so a spoofed request cannot redirect a reset link.
            </span>
          </label>

          <label>
            SMTP host
            <input
              type="text"
              placeholder="smtp.example.org"
              value={form.host}
              onChange={(e) => set("host", e.target.value)}
            />
          </label>
          <label>
            Port
            <input
              type="number"
              min={1}
              max={65535}
              value={form.port}
              onChange={(e) => set("port", Number(e.target.value))}
            />
          </label>

          <label>
            Username
            <input
              type="text"
              autoComplete="off"
              value={form.user}
              onChange={(e) => set("user", e.target.value)}
            />
          </label>
          {/* The "remove" checkbox is a sibling, not a child, of the password
              label - nesting one label inside another is invalid and makes the
              click target ambiguous. */}
          <div className="field">
            <label htmlFor="mail-password">
              Password {form.has_password && !clearPassword ? "(leave blank to keep)" : ""}
            </label>
            <input
              id="mail-password"
              type="password"
              autoComplete="new-password"
              disabled={clearPassword}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={form.has_password ? "••••••••" : ""}
            />
            {form.has_password && (
              <label className="inline" style={{ fontSize: "0.8rem" }}>
                <input
                  type="checkbox"
                  checked={clearPassword}
                  onChange={(e) => {
                    setClearPassword(e.target.checked);
                    if (e.target.checked) setPassword("");
                  }}
                />
                Remove the stored password
              </label>
            )}
          </div>

          <label>
            From address
            <input
              type="email"
              placeholder="noreply@example.org"
              value={form.from_address}
              onChange={(e) => set("from_address", e.target.value)}
            />
          </label>
          <label>
            From name
            <input
              type="text"
              value={form.from_name}
              onChange={(e) => set("from_name", e.target.value)}
            />
          </label>

          <label className="full">
            Encryption
            <select
              value={form.ssl ? "ssl" : form.starttls ? "starttls" : "none"}
              onChange={(e) => {
                const v = e.target.value;
                setForm((f) => ({
                  ...f,
                  ssl: v === "ssl",
                  starttls: v === "starttls",
                }));
              }}
            >
              <option value="starttls">STARTTLS (usually port 587)</option>
              <option value="ssl">Implicit TLS / SSL (usually port 465)</option>
              <option value="none">None (not recommended)</option>
            </select>
          </label>

          <div className="full row wrap">
            <button className="btn primary" disabled={busy}>
              {busy ? "Saving…" : "Save mail settings"}
            </button>
            <button
              type="button"
              className="btn ghost"
              disabled={testing || busy || !form.host}
              title={
                !form.host
                  ? "Configure an SMTP host first"
                  : user?.email
                    ? `Sends a test message to ${user.email} using the saved settings (save changes first)`
                    : "Sends a test message to your account email using the saved settings (save changes first)"
              }
              onClick={() => void sendTest()}
            >
              {testing ? "Sending…" : "Send test email"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

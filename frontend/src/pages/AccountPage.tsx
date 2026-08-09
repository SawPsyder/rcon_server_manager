import { FormEvent, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { api } from "../api";
import { useAuth } from "../auth";

export default function AccountPage() {
  const { user, refresh, logout } = useAuth();

  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const [displayName, setDisplayName] = useState(user?.display_name ?? "");
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" });

  // TOTP enrolment is two steps: generate a secret, then prove a code works
  // before it is switched on.
  const [setupPassword, setSetupPassword] = useState("");
  const [enrolment, setEnrolment] = useState<{ secret: string; uri: string } | null>(null);
  const [totpCode, setTotpCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [disablePassword, setDisablePassword] = useState("");

  const run = async (action: () => Promise<string | void>) => {
    setBusy(true);
    setError("");
    setMsg("");
    try {
      const note = await action();
      if (note) setMsg(note);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  const saveProfile = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api.updateMe(displayName);
      await refresh();
      return "Profile updated.";
    });
  };

  const changePassword = (e: FormEvent) => {
    e.preventDefault();
    if (pw.next !== pw.confirm) {
      setError("The two new passwords do not match");
      return;
    }
    void run(async () => {
      await api.changePassword(pw.current, pw.next);
      setPw({ current: "", next: "", confirm: "" });
      return "Password updated. Other devices have been signed out.";
    });
  };

  const startTotp = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const res = await api.totp.setup(setupPassword);
      setEnrolment({ secret: res.secret, uri: res.otpauth_uri });
      setSetupPassword("");
      return "Add the key to your authenticator app, then confirm a code below.";
    });
  };

  const confirmTotp = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const res = await api.totp.confirm(totpCode.trim());
      setRecoveryCodes(res.recovery_codes);
      setEnrolment(null);
      setTotpCode("");
      await refresh();
      return "Two-factor authentication is on. Save your recovery codes now.";
    });
  };

  const disableTotp = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api.totp.disable(disablePassword);
      setDisablePassword("");
      setRecoveryCodes([]);
      await refresh();
      return "Two-factor authentication is off.";
    });
  };

  return (
    <div className="stack">
      {msg && <div className="alert ok">{msg}</div>}
      {error && <div className="alert error">{error}</div>}

      <section className="card">
        <h2>Your account</h2>
        <p className="muted">
          Signed in as {user?.email}
          {user?.is_admin ? " · Administrator" : ""}
        </p>
        <form className="form-grid" onSubmit={saveProfile}>
          <label>
            Display name
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </label>
          <div className="full">
            <button className="btn primary" disabled={busy}>
              Save
            </button>
          </div>
        </form>
      </section>

      <section className="card">
        <h2>Change password</h2>
        <form className="form-grid" onSubmit={changePassword}>
          <label>
            Current password
            <input
              type="password"
              required
              autoComplete="current-password"
              value={pw.current}
              onChange={(e) => setPw({ ...pw, current: e.target.value })}
            />
          </label>
          <label>
            New password
            <input
              type="password"
              required
              minLength={10}
              autoComplete="new-password"
              value={pw.next}
              onChange={(e) => setPw({ ...pw, next: e.target.value })}
              placeholder="At least 10 characters"
            />
          </label>
          <label>
            Confirm new password
            <input
              type="password"
              required
              minLength={10}
              autoComplete="new-password"
              value={pw.confirm}
              onChange={(e) => setPw({ ...pw, confirm: e.target.value })}
            />
          </label>
          <div className="full">
            <button className="btn primary" disabled={busy}>
              Update password
            </button>
          </div>
        </form>
      </section>

      <section className="card">
        <h2>Two-factor authentication</h2>

        {recoveryCodes.length > 0 && (
          <div className="stack">
            <div className="alert ok">
              Save these recovery codes somewhere safe. Each one works once, and
              they are not shown again.
            </div>
            <div className="code-block">{recoveryCodes.join("\n")}</div>
            <div className="row right">
              <button
                className="btn small ghost"
                onClick={() =>
                  void navigator.clipboard?.writeText(recoveryCodes.join("\n"))
                }
              >
                Copy codes
              </button>
              <button
                className="btn small ghost"
                onClick={() => setRecoveryCodes([])}
              >
                I have saved them
              </button>
            </div>
          </div>
        )}

        {user?.totp_enabled ? (
          <form className="form-grid" onSubmit={disableTotp}>
            <p className="muted full">
              Two-factor authentication is on. You will be asked for a code each
              time you sign in.
            </p>
            <label>
              Current password
              <input
                type="password"
                required
                value={disablePassword}
                onChange={(e) => setDisablePassword(e.target.value)}
              />
            </label>
            <div className="full">
              <button className="btn danger" disabled={busy}>
                Turn off two-factor authentication
              </button>
            </div>
          </form>
        ) : enrolment ? (
          <div className="stack">
            <p className="muted">
              Scan the QR code with your authenticator app, then enter the code
              it shows. If you cannot scan, use the setup key or URL below.
            </p>
            <div className="totp-qr" aria-label="Authenticator setup QR code">
              <QRCodeSVG
                value={enrolment.uri}
                size={192}
                level="M"
                includeMargin
                bgColor="#ffffff"
                fgColor="#0a0e14"
              />
            </div>
            <label>
              Setup key
              <div className="code-block">{enrolment.secret}</div>
            </label>
            <label>
              Setup URL
              <div className="code-block">{enrolment.uri}</div>
            </label>
            <form className="form-grid" onSubmit={confirmTotp}>
              <label>
                Code from your app
                <input
                  type="text"
                  required
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value)}
                  placeholder="123456"
                />
              </label>
              <div className="full row">
                <button className="btn primary" disabled={busy || !totpCode.trim()}>
                  Confirm and turn on
                </button>
                <button
                  type="button"
                  className="btn ghost"
                  onClick={() => setEnrolment(null)}
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        ) : (
          <form className="form-grid" onSubmit={startTotp}>
            <p className="muted full">
              Add a time-based code from an authenticator app as a second step
              when signing in.
            </p>
            <label>
              Current password
              <input
                type="password"
                required
                value={setupPassword}
                onChange={(e) => setSetupPassword(e.target.value)}
              />
            </label>
            <div className="full">
              <button className="btn primary" disabled={busy || !setupPassword}>
                Set up two-factor authentication
              </button>
            </div>
          </form>
        )}
      </section>

      <section className="card">
        <h2>Sessions</h2>
        <p className="muted">
          Signs you out of every browser, including this one. Use this if you
          think someone else has access to your account.
        </p>
        <button
          className="btn danger"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              await api.logoutEverywhere();
              await logout();
            })
          }
        >
          Sign out everywhere
        </button>
      </section>
    </div>
  );
}

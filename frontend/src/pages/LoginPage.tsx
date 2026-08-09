import { FormEvent, useRef, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import TurnstileWidget, { type TurnstileHandle } from "../components/TurnstileWidget";

type Mode = "login" | "totp" | "forgot" | "claim";

type LoginLocationState = {
  notice?: string;
};

export default function LoginPage() {
  const { authenticated, config, login, submitTotp, claimBootstrap } = useAuth();
  const location = useLocation();
  const locationNotice =
    (location.state as LoginLocationState | null)?.notice?.trim() || "";

  // Null until the user picks a screen; the default is derived from server
  // state below so a fresh install does not open on a form that cannot work.
  const [mode, setMode] = useState<Mode | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [adminPassword, setAdminPassword] = useState("");
  const [turnstileToken, setTurnstileToken] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState(locationNotice);
  const [busy, setBusy] = useState(false);

  const turnstileRef = useRef<TurnstileHandle | null>(null);

  if (authenticated) return <Navigate to="/" replace />;

  // Which form to show is a function of server state, so wait for it rather
  // than flashing a sign-in form that may be about to be replaced.
  if (!config) {
    return (
      <div className="center-screen login-bg">
        <div className="spinner" />
        <p className="muted">Loading…</p>
      </div>
    );
  }

  // With no administrator yet there is no account to sign in with, so the
  // claim form is the only thing that can succeed - open on it.
  const activeMode: Mode = mode ?? (config.bootstrap_available ? "claim" : "login");

  const turnstileOn = config.turnstile_enabled;
  // The TOTP step cannot carry a token - the one from step 1 was redeemed there.
  const needsToken = turnstileOn && activeMode !== "totp";
  const tokenReady = !needsToken || turnstileToken.length > 0;

  /** Tokens are single-use, so a failed submit must arm a fresh one. */
  const resetTurnstile = () => {
    setTurnstileToken("");
    turnstileRef.current?.reset();
  };

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      resetTurnstile();
    } finally {
      setBusy(false);
    }
  };

  const onLogin = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const mfaRequired = await login(email, password, turnstileToken);
      if (mfaRequired) {
        setMode("totp");
        setPassword("");
        setNotice("Enter the code from your authenticator app.");
      }
    });
  };

  const onTotp = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await submitTotp(code.trim());
    });
  };

  const onForgot = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api.forgotPassword(email, turnstileToken);
      // Deliberately the same message whether or not the account exists.
      setNotice(
        config?.smtp_enabled
          ? "If that address has an account, a reset link is on its way."
          : "Password reset requested. Email is not configured on this server, so ask an administrator for a reset link.",
      );
      resetTurnstile();
    });
  };

  const onClaim = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await claimBootstrap({
        email,
        password,
        display_name: displayName,
        admin_password: adminPassword,
        turnstile_token: turnstileToken,
      });
    });
  };

  const turnstile =
    needsToken && config?.turnstile_site_key ? (
      <TurnstileWidget
        ref={turnstileRef}
        siteKey={config.turnstile_site_key}
        onToken={setTurnstileToken}
      />
    ) : null;

  const alerts = (
    <>
      {notice && <div className="alert ok">{notice}</div>}
      {error && <div className="alert error">{error}</div>}
    </>
  );

  // First run: no admin exists yet, so ADMIN_PASSWORD can be exchanged for one.
  if (activeMode === "claim") {
    return (
      <div className="center-screen login-bg">
        <form className="card login-card" onSubmit={onClaim}>
          <h1>Set up your administrator account</h1>
          <p className="muted">
            No accounts exist yet. Choose your sign-in details, then confirm with
            the <code>ADMIN_PASSWORD</code> from your server configuration. That
            password stops working once this is done.
          </p>
          <label>
            Email
            <input
              type="email"
              autoFocus
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label>
            Display name
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Optional"
            />
          </label>
          <label>
            New password
            <input
              type="password"
              required
              minLength={10}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="At least 10 characters"
            />
          </label>
          <label>
            ADMIN_PASSWORD
            <input
              type="password"
              required
              value={adminPassword}
              onChange={(e) => setAdminPassword(e.target.value)}
            />
          </label>
          {turnstile}
          {alerts}
          <button className="btn primary" disabled={busy || !tokenReady}>
            {busy ? "Creating…" : "Create administrator"}
          </button>
          {/* On a genuine fresh install there is nothing to go back to - a
              sign-in form cannot succeed with no accounts. Only offer it if
              the user navigated here manually. */}
          {mode !== null && (
            <div className="login-actions">
              <button
                type="button"
                className="btn ghost small"
                onClick={() => {
                  setMode("login");
                  setError("");
                  setNotice("");
                  resetTurnstile();
                }}
              >
                Back to sign in
              </button>
            </div>
          )}
        </form>
      </div>
    );
  }

  if (activeMode === "totp") {
    return (
      <div className="center-screen login-bg">
        <form className="card login-card" onSubmit={onTotp}>
          <h1>Two-factor authentication</h1>
          <p className="muted">
            Enter the 6-digit code from your authenticator app, or one of your
            recovery codes.
          </p>
          <label>
            Code
            <input
              type="text"
              autoFocus
              required
              autoComplete="one-time-code"
              inputMode="text"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="123456"
            />
          </label>
          {alerts}
          <button className="btn primary" disabled={busy || !code.trim()}>
            {busy ? "Verifying…" : "Verify"}
          </button>
          <div className="login-actions">
            <button
              type="button"
              className="btn ghost small"
              onClick={() => {
                setMode("login");
                setCode("");
                setError("");
                setNotice("");
                resetTurnstile();
              }}
            >
              Start again
            </button>
          </div>
        </form>
      </div>
    );
  }

  if (activeMode === "forgot") {
    return (
      <div className="center-screen login-bg">
        <form className="card login-card" onSubmit={onForgot}>
          <h1>Reset your password</h1>
          <p className="muted">We will email you a link to set a new password.</p>
          <label>
            Email
            <input
              type="email"
              autoFocus
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          {turnstile}
          {alerts}
          <button className="btn primary" disabled={busy || !email || !tokenReady}>
            {busy ? "Sending…" : "Send reset link"}
          </button>
          <div className="login-actions">
            <button
              type="button"
              className="btn ghost small"
              onClick={() => {
                setMode("login");
                setError("");
                setNotice("");
                resetTurnstile();
              }}
            >
              Back to sign in
            </button>
          </div>
        </form>
      </div>
    );
  }

  return (
    <div className="center-screen login-bg">
      <form className="card login-card" onSubmit={onLogin}>
        <h1>Sign in</h1>
        <p className="muted">RCON Server Manager</p>
        <label>
          Email
          <input
            type="email"
            autoFocus
            required
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label>
          Password
          <input
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {turnstile}
        {alerts}
        <button
          className="btn primary"
          disabled={busy || !email || !password || !tokenReady}
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <div className="login-actions">
          <button
            type="button"
            className="btn ghost small"
            onClick={() => {
              setMode("forgot");
              setError("");
              setNotice("");
              resetTurnstile();
            }}
          >
            Forgot password?
          </button>
          {config?.bootstrap_available && (
            <button
              type="button"
              className="btn ghost small"
              onClick={() => {
                setMode("claim");
                setError("");
                setNotice("");
                resetTurnstile();
              }}
            >
              First run? Create an administrator
            </button>
          )}
        </div>
      </form>
    </div>
  );
}

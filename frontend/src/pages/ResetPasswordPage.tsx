import { FormEvent, useEffect, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";

/**
 * Serves both /reset/:token and /invite/:token - the backend treats an invite
 * and a password reset as the same single-use token, and the only difference
 * here is the wording.
 *
 * After a successful password set the user is sent to the normal login path
 * (including TOTP when enrolled). Auto-login would bypass MFA.
 */
export default function ResetPasswordPage({ invite = false }: { invite?: boolean }) {
  const { token = "" } = useParams();
  const { authenticated } = useAuth();
  const navigate = useNavigate();

  const [checking, setChecking] = useState(true);
  const [valid, setValid] = useState(false);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // Keep the token out of the address bar (and out of any Referer header)
    // as soon as it has been read.
    window.history.replaceState(null, "", invite ? "/invite" : "/reset");
  }, [invite]);

  useEffect(() => {
    let cancelled = false;
    api
      .checkResetToken(token)
      .then((res) => {
        if (!cancelled) setValid(res.valid);
      })
      .catch(() => {
        if (!cancelled) setValid(false);
      })
      .finally(() => {
        if (!cancelled) setChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (authenticated) return <Navigate to="/" replace />;

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (password !== confirm) {
      setError("The two passwords do not match");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.resetPassword(token, password);
      navigate("/login", {
        replace: true,
        state: {
          notice: invite
            ? "Password set. Sign in to finish setting up your account."
            : "Password updated. Sign in with your new password.",
        },
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not set the password");
    } finally {
      setBusy(false);
    }
  };

  if (checking) {
    return (
      <div className="center-screen">
        <div className="spinner" />
        <p>Checking your link…</p>
      </div>
    );
  }

  if (!valid) {
    return (
      <div className="center-screen login-bg">
        <div className="card login-card">
          <h1>This link is no longer valid</h1>
          <p className="muted">
            Invite and reset links expire, and each one can only be used once.
            Ask an administrator for a new link, or request a password reset.
          </p>
          <button className="btn primary" onClick={() => navigate("/login")}>
            Back to sign in
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="center-screen login-bg">
      <form className="card login-card" onSubmit={onSubmit}>
        <h1>{invite ? "Welcome" : "Choose a new password"}</h1>
        <p className="muted">
          {invite
            ? "Set a password to finish setting up your account, then sign in."
            : "Pick a new password for your account, then sign in."}
        </p>
        <label>
          New password
          <input
            type="password"
            autoFocus
            required
            minLength={10}
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="At least 10 characters"
          />
        </label>
        <label>
          Confirm password
          <input
            type="password"
            required
            minLength={10}
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </label>
        {error && <div className="alert error">{error}</div>}
        <button className="btn primary" disabled={busy || !password || !confirm}>
          {busy ? "Saving…" : "Set password"}
        </button>
      </form>
    </div>
  );
}

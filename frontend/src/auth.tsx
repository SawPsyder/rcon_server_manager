import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, setUnauthorizedHandler, type CurrentUser, type PublicConfig } from "./api";

type AuthContextValue = {
  loading: boolean;
  authenticated: boolean;
  user: CurrentUser | null;
  isAdmin: boolean;
  /** Public config (Turnstile site key, SMTP + bootstrap state). Null until loaded. */
  config: PublicConfig | null;
  /** True between a correct password and a successful TOTP code. */
  mfaPending: boolean;
  refresh: () => Promise<void>;
  reloadConfig: () => Promise<void>;
  /** Resolves to true when a TOTP code is still required. */
  login: (email: string, password: string, turnstileToken: string) => Promise<boolean>;
  submitTotp: (code: string) => Promise<void>;
  claimBootstrap: (data: {
    email: string;
    password: string;
    display_name: string;
    admin_password: string;
    turnstile_token: string;
  }) => Promise<void>;
  logout: () => Promise<void>;
  canAccessServer: (id: number) => boolean;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [mfaPending, setMfaPending] = useState(false);

  const reloadConfig = useCallback(async () => {
    try {
      setConfig(await api.authConfig());
    } catch {
      // Backend unreachable. Fall back to a plain login form rather than
      // leaving config null forever, which would block the screen.
      setConfig({
        turnstile_enabled: false,
        turnstile_site_key: "",
        smtp_enabled: false,
        bootstrap_available: false,
      });
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const s = await api.authStatus();
      setUser(s.authenticated ? s.user : null);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    void reloadConfig();
  }, [refresh, reloadConfig]);

  // A 401 on any non-auth call means the session died (expired, revoked, or the
  // account was deactivated). Drop to signed-out so Protected redirects.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUser(null);
      setMfaPending(false);
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = async (email: string, password: string, turnstileToken: string) => {
    const res = await api.login(email, password, turnstileToken);
    if (res.mfa_required) {
      setMfaPending(true);
      return true;
    }
    setUser(res.user);
    setMfaPending(false);
    return false;
  };

  const submitTotp = async (code: string) => {
    const res = await api.loginTotp(code);
    setUser(res.user);
    setMfaPending(false);
  };

  const claimBootstrap = async (data: {
    email: string;
    password: string;
    display_name: string;
    admin_password: string;
    turnstile_token: string;
  }) => {
    const res = await api.bootstrapClaim(data);
    setUser(res.user);
    // The claim window has just closed; keep the login screen in step.
    await reloadConfig();
  };

  const logout = async () => {
    await api.logout();
    setUser(null);
    setMfaPending(false);
  };

  const canAccessServer = useCallback(
    (id: number) => {
      if (!user) return false;
      if (user.is_admin) return true;
      return user.server_ids.includes(id);
    },
    [user],
  );

  return (
    <AuthContext.Provider
      value={{
        loading,
        authenticated: user !== null,
        user,
        isAdmin: user?.is_admin ?? false,
        config,
        mfaPending,
        refresh,
        reloadConfig,
        login,
        submitTotp,
        claimBootstrap,
        logout,
        canAccessServer,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside provider");
  return ctx;
}

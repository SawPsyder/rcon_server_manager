import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api } from "./api";

type AuthContextValue = {
  loading: boolean;
  authenticated: boolean;
  refresh: () => Promise<void>;
  login: (password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const s = await api.authStatus();
      setAuthenticated(s.authenticated);
    } catch {
      setAuthenticated(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const login = async (password: string) => {
    await api.login(password);
    setAuthenticated(true);
  };

  const logout = async () => {
    await api.logout();
    setAuthenticated(false);
  };

  return (
    <AuthContext.Provider value={{ loading, authenticated, refresh, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside provider");
  return ctx;
}

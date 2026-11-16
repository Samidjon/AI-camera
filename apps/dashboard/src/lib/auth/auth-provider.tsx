"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api, ApiError, NetworkError } from "@/lib/api/client";
import type { CurrentUser } from "@/lib/api/types";

// Foundation-level session storage. Access tokens are short-lived (ACI_ACCESS_TOKEN_EXPIRE_MINUTES).
// Moving to httpOnly cookies + refresh tokens is planned with the full RBAC work (MVP 07).
const TOKEN_KEY = "aci.access_token";

export type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; user: CurrentUser; token: string }
  /** Token exists but the central server can't be reached — show stale/offline state, keep the token. */
  | { status: "server_unreachable"; token: string; message: string };

interface AuthContextValue {
  state: AuthState;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  retry: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function readToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function writeToken(token: string | null) {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable (private mode) — session lasts for this tab only */
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  const resolveSession = useCallback(async (token: string | null, signal?: AbortSignal) => {
    if (!token) {
      setState({ status: "anonymous" });
      return;
    }
    try {
      const user = await api.me(token, signal);
      setState({ status: "authenticated", user, token });
    } catch (err) {
      if (signal?.aborted) return;
      if (err instanceof ApiError && err.status === 401) {
        writeToken(null);
        setState({ status: "anonymous" });
      } else if (err instanceof NetworkError || (err instanceof ApiError && err.status >= 500)) {
        setState({ status: "server_unreachable", token, message: err.message });
      } else {
        setState({ status: "server_unreachable", token, message: (err as Error).message });
      }
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    // Session state lives in localStorage (an external system), so it is resolved after mount,
    // in a microtask (never synchronously inside the effect body).
    void Promise.resolve().then(() => resolveSession(readToken(), controller.signal));
    return () => controller.abort();
  }, [resolveSession, attempt]);

  const login = useCallback(
    async (email: string, password: string) => {
      const { access_token } = await api.login(email, password);
      writeToken(access_token);
      await resolveSession(access_token);
    },
    [resolveSession],
  );

  const logout = useCallback(() => {
    writeToken(null);
    setState({ status: "anonymous" });
  }, []);

  const retry = useCallback(() => {
    setState({ status: "loading" });
    setAttempt((n) => n + 1);
  }, []);

  const value = useMemo(() => ({ state, login, logout, retry }), [state, login, logout, retry]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

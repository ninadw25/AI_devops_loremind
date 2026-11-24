/**
 * AuthContext - holds the authenticated session (token + user + resolved
 * identity), persisted to localStorage so a refresh keeps you signed in.
 *
 * The provider lives above the Router (in main.tsx); navigation/role-routing is
 * done by the screens/guards via the `identity` exposed here.
 */
import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";
import * as authApi from "@/services/authApi";

const STORAGE_KEY = "lore_auth";

interface AuthState {
  token: string;
  sessionToken: string;
  user: authApi.AuthUser;
  identity: authApi.Identity;
}

interface AuthContextValue {
  isAuthenticated: boolean;
  user: authApi.AuthUser | null;
  identity: authApi.Identity | null;
  token: string | null;
  login: (email: string, password: string) => Promise<authApi.Identity>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

function loadState(): AuthState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as AuthState) : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState | null>(loadState);

  const login = useCallback(async (email: string, password: string) => {
    const result = await authApi.login(email, password);
    const next: AuthState = {
      token: result.token,
      sessionToken: result.sessionToken,
      user: result.user,
      identity: result.identity,
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    setState(next);
    return result.identity;
  }, []);

  const logout = useCallback(() => {
    const current = loadState();
    if (current?.sessionToken) void authApi.logout(current.sessionToken);
    localStorage.removeItem(STORAGE_KEY);
    setState(null);
  }, []);

  const value: AuthContextValue = {
    isAuthenticated: !!state?.token,
    user: state?.user ?? null,
    identity: state?.identity ?? null,
    token: state?.token ?? null,
    login,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}

/** The default landing route for a given identity (RBAC home). */
export function roleHome(identity: authApi.Identity | null): string {
  if (identity?.isLoreAdmin) return "/admin";
  if (import.meta.env.VITE_DEMO_MODE === "true") return "/ask";
  return "/dashboard";
}

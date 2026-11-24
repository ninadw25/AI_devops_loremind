/**
 * authApi - RBAC login against the client-admin backend.
 *
 * client-admin proxies credentials to rbac-service (Better Auth), mints a JWT,
 * and returns the caller's resolved `identity` (platform role, org role/id, and
 * isClientAdmin/isLoreAdmin flags) so the frontend can route to the correct
 * dashboard in a single call - no separate /me round-trip.
 */

const CLIENT_ADMIN_URL = import.meta.env.VITE_CLIENT_ADMIN_URL || "http://localhost:4000";

export type PlatformRole = "lore_superadmin" | "client_superadmin" | null;

/** Resolved role picture returned by the backend at login. */
export interface Identity {
  userId: string;
  email: string | null;
  platformRole: PlatformRole;
  userRole: string | null;
  orgRole: string | null;
  orgId: string | null;
  status: string | null;
  isClientAdmin: boolean;
  isLoreAdmin: boolean;
}

export interface AuthUser {
  id: string;
  email: string;
  name?: string;
  role?: string;
}

export interface LoginResult {
  token: string;
  sessionToken: string;
  user: AuthUser;
  identity: Identity;
}

/** Error carrying the backend's status + machine code for friendly messages. */
export class AuthApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "AuthApiError";
    this.status = status;
    this.code = code;
  }
}


// Course demo: no rbac-service locally, so a fixed demo account is accepted client-side.
// Only active when VITE_DEMO_MODE=true; the engine decodes (never verifies) this token.
const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === "true";
const DEMO_EMAIL = "demo@lore.local";
const DEMO_PASSWORD = "Lore@Demo2026";
function demoLogin(email: string, password: string): LoginResult {
  if (email.toLowerCase() !== DEMO_EMAIL || password !== DEMO_PASSWORD) {
    throw new AuthApiError(401, "INVALID_CREDENTIALS", "Sign-in failed. Check your credentials.");
  }
  const b64 = (o: object) => btoa(JSON.stringify(o)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
  const token = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: "demo", org: "ezobooks", exp: 4102444800 })}.demo`;
  localStorage.setItem("loremind_connected_sources", JSON.stringify(["gmail", "gitlab"]));   // the sources the demo graph holds
  return {
    token, sessionToken: token,
    user: { id: "demo", email: DEMO_EMAIL, name: "Demo User" },
    identity: { userId: "demo", email: DEMO_EMAIL, platformRole: "client_superadmin", userRole: "admin",
      orgRole: "owner", orgId: "ezobooks", status: "active", isClientAdmin: true, isLoreAdmin: false },
  } as LoginResult;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** A single sign-in attempt. */
async function attemptLogin(email: string, password: string): Promise<LoginResult> {
  if (DEMO_MODE) return demoLogin(email, password);
  let res: Response;
  try {
    res = await fetch(`${CLIENT_ADMIN_URL}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
  } catch {
    throw new AuthApiError(0, "NETWORK", "Cannot reach the authentication service.");
  }

  const body = await res.json().catch(() => null);
  if (!res.ok || !body?.success) {
    const code = body?.error?.code ?? "ERROR";
    const message = body?.error?.message ?? "Sign-in failed. Check your credentials.";
    throw new AuthApiError(res.status, code, message);
  }
  return body.data as LoginResult;
}

/**
 * Sign in with email + password. Returns the JWT, refresh session token, the
 * user, and the resolved identity used for role-based routing.
 *
 * Retries transient failures (network / 5xx) a few times: the rbac-service the
 * backend proxies to can momentarily restart, so a retry rides over it. Auth
 * failures (e.g. wrong password, 4xx) are NOT retried.
 */
export async function login(email: string, password: string): Promise<LoginResult> {
  const MAX_ATTEMPTS = 4;
  let lastErr: unknown;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      return await attemptLogin(email, password);
    } catch (err) {
      lastErr = err;
      const transient = err instanceof AuthApiError && (err.status === 0 || err.status >= 500);
      if (!transient || attempt === MAX_ATTEMPTS) throw err;
      await sleep(1200);
    }
  }
  throw lastErr;
}

/** Best-effort sign-out (revokes the session token at rbac-service). */
export async function logout(sessionToken: string): Promise<void> {
  try {
    await fetch(`${CLIENT_ADMIN_URL}/api/v1/auth/logout`, {
      method: "POST",
      headers: { Authorization: `Bearer ${sessionToken}` },
    });
  } catch {
    // ignore - logout is best-effort; the client clears local state regardless.
  }
}

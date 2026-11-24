/**
 * adminHttp - shared fetch helper for the client-admin backend.
 *
 * Attaches the Bearer JWT from the persisted auth session, posts/parses JSON,
 * unwraps the `{ success, data, error }` envelope, and throws AdminApiError with
 * the backend's status + machine code on failure.
 */
const CLIENT_ADMIN_URL = import.meta.env.VITE_CLIENT_ADMIN_URL || "http://localhost:4000";
const AUTH_STORAGE_KEY = "lore_auth";

function getToken(): string | null {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    return raw ? (JSON.parse(raw).token as string) : null;
  } catch {
    return null;
  }
}

export class AdminApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "AdminApiError";
    this.status = status;
    this.code = code;
  }
}

export async function adminFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = { ...(options.headers as Record<string, string>) };
  if (options.body) headers["Content-Type"] = "application/json";
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(`${CLIENT_ADMIN_URL}${path}`, { ...options, headers });
  } catch {
    throw new AdminApiError(0, "NETWORK", "Cannot reach the admin service.");
  }

  const body = await res.json().catch(() => null);
  if (!res.ok || (body && body.success === false)) {
    throw new AdminApiError(
      res.status,
      body?.error?.code ?? "ERROR",
      body?.error?.message ?? `Request failed (${res.status})`,
    );
  }
  return (body?.data ?? body) as T;
}

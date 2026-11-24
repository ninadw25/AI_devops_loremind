/**
 * loreAdminApi - the lore-superadmin console data.
 *
 * Talks to the lore-admin backend (:4001), which in turn sources ALL client data
 * from client-admin over HTTP (never tenant DBs). Auth = the Better Auth JWT.
 */
const LORE_ADMIN_URL = import.meta.env.VITE_LORE_ADMIN_URL || "http://localhost:4001";
const AUTH_STORAGE_KEY = "lore_auth";

function getToken(): string | null {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    return raw ? (JSON.parse(raw).token as string) : null;
  } catch {
    return null;
  }
}

export class LoreAdminApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "LoreAdminApiError";
    this.status = status;
  }
}

export type ServiceStatus = "up" | "degraded" | "down" | "unknown";

export interface HealthResult {
  service: string;
  status: ServiceStatus;
  latency: number;
  lastChecked: string;
  details: Record<string, unknown>;
}
export interface HealthSummary {
  up: number;
  degraded: number;
  down: number;
  unknown: number;
  services: HealthResult[];
}
export interface OrgOverview {
  orgId: string;
  name: string;
  slug: string | null;
  userCount: number;
  plan: { key: string; name: string; status: string };
  openTickets: number;
  /** Health of the client-facing services this org depends on. */
  health: HealthSummary;
}
export interface PlatformOverview {
  health: HealthSummary;
  totalOrgs: number;
  totalUsers: number;
  orgs: OrgOverview[];
}
export interface TicketWithOrg {
  id: string;
  organizationId: string;
  orgName: string;
  category: string;
  subject: string;
  body: string;
  serviceId: string | null;
  priority: string;
  status: string;
  createdAt: string | null;
}

async function loreFetch<T>(path: string): Promise<T> {
  const token = getToken();
  let res: Response;
  try {
    res = await fetch(`${LORE_ADMIN_URL}${path}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch {
    throw new LoreAdminApiError(0, "Cannot reach the lore-admin service.");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok || (body && body.success === false)) {
    throw new LoreAdminApiError(res.status, body?.error?.message ?? `Request failed (${res.status})`);
  }
  return (body?.data ?? body) as T;
}

export function getOverview(): Promise<PlatformOverview> {
  return loreFetch<PlatformOverview>("/api/v1/overview");
}

export function getTickets(): Promise<TicketWithOrg[]> {
  return loreFetch<TicketWithOrg[]>("/api/v1/tickets");
}

// ── Platform analytics (revenue + token usage) ──────────────────────────────

export interface AnalyticsOverview {
  totalClients: number;
  totalUsers: number;
  totalTokensThisMonth: number;
  totalRevenueCents: number;
  periodMonth: string;
}

export interface ClientRevenue {
  organizationId: string;
  planKey: string;
  basePriceCents: number;
  tokensUsed: number;
  overageCents: number;
  totalCents: number;
}

export interface OrgTokenUsage {
  organizationId: string;
  tokens: number;
}

/** Platform-wide totals for the current month (clients, users, tokens, revenue). */
export function getAnalyticsOverview(): Promise<AnalyticsOverview> {
  return loreFetch<AnalyticsOverview>("/api/v1/analytics/overview");
}

/** Per-client revenue breakdown (base plan + overage), descending by total. */
export function getRevenue(): Promise<ClientRevenue[]> {
  return loreFetch<ClientRevenue[]>("/api/v1/analytics/revenue");
}

/** Top-N orgs by token usage this month. */
export function getTopUsage(limit = 5): Promise<OrgTokenUsage[]> {
  return loreFetch<OrgTokenUsage[]>(`/api/v1/analytics/usage/top?limit=${limit}`);
}

// ── Cross-tenant ingestion health ───────────────────────────────────────────

export interface PlatformIngestionSource {
  org: string;
  orgName: string;
  type: string;
  displayName: string;
  enabled: boolean;
  status: string;
  lastRunAt: string | null;
  lastError: string | null;
}
export interface PlatformIngestionRun {
  id: string;
  org: string;
  orgName: string;
  sourceType: string | null;
  sourceName: string | null;
  trigger: string;
  status: string;
  stats: Record<string, unknown> | null;
  error: string | null;
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
}
export interface PlatformIngestion {
  summary: {
    totalSources: number;
    activeSources: number;
    erroredSources: number;
    runsLast24h: number;
    failedRunsLast24h: number;
  };
  sources: PlatformIngestionSource[];
  recentRuns: PlatformIngestionRun[];
}

/** Cross-tenant ingestion health (connectors + recent runs across all orgs). */
export function getIngestion(): Promise<PlatformIngestion> {
  return loreFetch<PlatformIngestion>("/api/v1/ingestion");
}

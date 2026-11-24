/** Security API (client-admin) - access & compliance: authorization audit +
 *  content super-admin ("read all") access log. */
import { adminFetch } from "./adminHttp";

export interface SecuritySummary {
  windowDays: number;
  recentEvents: number;
  deniedAttempts: number;
  grantChanges: number;
  readAllQueries: number;
  readAllUsers: number;
}

export interface AuthEvent {
  id: string;
  actorUserId: string | null;
  actorName: string | null;
  userId: string | null;
  userName: string | null;
  resource: string | null;
  action: string | null;
  allowed: boolean | null;
  reason: string | null;
  operation: string;
  targetType: string | null;
  targetId: string | null;
  createdAt: string;
}

export interface ReadAllAccess {
  id: string;
  userId: string;
  userName: string | null;
  query: string;
  resultsCount: number;
  scopeCount: number;
  createdAt: string;
}

/** Access & compliance KPIs over a rolling window (default 30 days). */
export function getSecuritySummary(days = 30): Promise<SecuritySummary> {
  return adminFetch<SecuritySummary>(`/api/v1/security/summary?days=${days}`);
}

/** Recent RBAC authorization-audit events (grants/revokes + allowed/denied checks). */
export function listSecurityEvents(limit = 30): Promise<AuthEvent[]> {
  return adminFetch<AuthEvent[]>(`/api/v1/security/events?limit=${limit}`);
}

/** Content super-admin ("read all") access log - surveillance-sensitive reads. */
export function listReadAllAccess(limit = 30): Promise<ReadAllAccess[]> {
  return adminFetch<ReadAllAccess[]>(`/api/v1/security/read-all?limit=${limit}`);
}

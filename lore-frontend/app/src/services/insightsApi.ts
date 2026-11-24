/** Product-insights API (client-admin) - adoption, query log, activity. */
import { adminFetch } from "./adminHttp";

export interface InsightsSummary {
  periodMonth: string;
  totalQueries: number;
  activeUsers: number;
  memberCount: number;
  avgLatencyMs: number;
  failedQueries: number;
  tokensUsed: number;
  queriesToday: number;
}

export interface QueryLogEntry {
  turnId: string;
  threadId: string;
  userId: string;
  question: string;
  status: string;
  latencyMs: number | null;
  model: string;
  createdAt: string;
}

export interface ActivityPoint {
  day: string;
  queries: number;
}

/** Current-month adoption + quality KPIs for the caller's org. */
export function getInsightsSummary(): Promise<InsightsSummary> {
  return adminFetch<InsightsSummary>("/api/v1/insights/summary");
}

/** Recent questions asked across the org (newest first). */
export function getRecentQueries(limit = 25, offset = 0): Promise<QueryLogEntry[]> {
  return adminFetch<QueryLogEntry[]>(`/api/v1/insights/queries?limit=${limit}&offset=${offset}`);
}

/** Daily query counts over the last `days` days (oldest first, zero-filled). */
export function getActivity(days = 14): Promise<ActivityPoint[]> {
  return adminFetch<ActivityPoint[]>(`/api/v1/insights/activity?days=${days}`);
}

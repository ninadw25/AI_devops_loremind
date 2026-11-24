/** Data-sources API (client-admin) - ingestion connectors + run history. */
import { adminFetch } from "./adminHttp";

export interface IngestionSource {
  id: string;
  type: string;
  displayName: string;
  enabled: boolean;
  status: string;
  lastRunAt: string | null;
  lastError: string | null;
  config: Record<string, unknown> | null;
}

export interface IngestionRun {
  id: string;
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

/** The org's configured ingestion connectors. */
export function listSources(): Promise<IngestionSource[]> {
  return adminFetch<IngestionSource[]>("/api/v1/sources");
}

/** Recent ingestion runs (newest first). */
export function listRuns(limit = 20): Promise<IngestionRun[]> {
  return adminFetch<IngestionRun[]>(`/api/v1/sources/runs?limit=${limit}`);
}

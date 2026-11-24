/** Encrypted env-var (PAT/token/key) API (client-admin) + ingestion trigger. */
import { adminFetch } from "./adminHttp";

export interface EnvVarMeta {
  key: string;
  last4: string;
  masked: boolean;
  updatedAt: string | null;
}

/** List stored secret keys, MASKED (values are never returned). */
export function listEnvVars(): Promise<EnvVarMeta[]> {
  return adminFetch<EnvVarMeta[]>("/api/v1/env-vars");
}

/** Store/replace a secret (encrypted at rest). Returns only key + last4. */
export function setEnvVar(key: string, value: string): Promise<{ key: string; last4: string }> {
  return adminFetch<{ key: string; last4: string }>("/api/v1/env-vars", {
    method: "POST",
    body: JSON.stringify({ key, value }),
  });
}

export function deleteEnvVar(key: string): Promise<{ success: true }> {
  return adminFetch<{ success: true }>(`/api/v1/env-vars/${encodeURIComponent(key)}`, {
    method: "DELETE",
  });
}

export interface IngestionRunResult {
  accepted: boolean;
  source: string;
  detail?: string;
}

export interface RunIngestionInput {
  source: string;
  repo?: string;
  gitlabId?: string;
  jiraProjectKey?: string;
}

/** Kick off ingestion for a configured source (e.g. gitlab) using stored keys. */
export function runIngestion(input: RunIngestionInput): Promise<IngestionRunResult> {
  return adminFetch<IngestionRunResult>("/api/v1/ingestion/run", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

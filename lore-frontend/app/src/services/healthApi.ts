/** Service-health API (client-admin). */
import { adminFetch } from "./adminHttp";

export type ServiceStatus = "up" | "degraded" | "down" | "unknown";

export interface HealthResult {
  service: string; // service id
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

export interface ServiceMeta {
  id: string;
  name: string;
  kind: string;
}

/** Aggregate health across all registered services. */
export function getHealthSummary(): Promise<HealthSummary> {
  return adminFetch<HealthSummary>("/api/v1/services/health/summary");
}

/** Client-visible service metadata (id/name/kind) for friendly labels. */
export function listServices(): Promise<ServiceMeta[]> {
  return adminFetch<ServiceMeta[]>("/api/v1/services");
}

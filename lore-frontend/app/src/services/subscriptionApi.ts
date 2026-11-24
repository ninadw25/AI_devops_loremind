/** Subscription API (client-admin) + static plan catalog for the upgrade UI. */
import { adminFetch } from "./adminHttp";

export interface PlanLimits {
  monthlyTokenLimit: number;
  monthlyPriceCents: number;
  overageCostPer1kCents: number;
  features: Record<string, unknown>;
}

export interface SubscriptionView {
  planKey: string;
  name: string;
  limits: PlanLimits;
  billingCycle: string;
  status: string;
  renewalDate: string | null;
  cancelAtPeriodEnd: boolean;
}

/** Static plan catalog (mirrors client-admin config/planLimits.ts) for the UI. */
export const PLAN_CATALOG = [
  { key: "free", name: "Free", priceCents: 0, tokenLimit: 1_000_000, seats: 3 },
  { key: "pro", name: "Pro", priceCents: 4900, tokenLimit: 10_000_000, seats: 25 },
  { key: "enterprise", name: "Enterprise", priceCents: 49900, tokenLimit: 100_000_000, seats: -1 },
] as const;

export function getSubscription(): Promise<SubscriptionView> {
  return adminFetch<SubscriptionView>("/api/v1/subscription");
}

export function upgradePlan(planKey: string): Promise<SubscriptionView> {
  return adminFetch<SubscriptionView>("/api/v1/subscription/upgrade", {
    method: "POST",
    body: JSON.stringify({ planKey }),
  });
}

export function cancelPlan(): Promise<SubscriptionView> {
  return adminFetch<SubscriptionView>("/api/v1/subscription/cancel", { method: "POST" });
}

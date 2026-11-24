/**
 * LoreAdminHome - the Loremind platform (lore_superadmin) console.
 *
 * Shows, per client org: subscribed-user count, plan, open-ticket count, and a
 * platform service-health rollup with down-service flags, plus the inbox of
 * tickets raised by client admins. ALL data comes from the lore-admin backend,
 * which sources it via client-admin HTTP routes only (no tenant DB access).
 */
import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  LogOut, ShieldCheck, RefreshCw, Loader2, Building2, Users, Activity, AlertTriangle, Ticket,
  Database, CheckCircle2, XCircle,
} from "lucide-react";
import { useAuth } from "@/auth/AuthContext";
import {
  getOverview,
  getTickets,
  getAnalyticsOverview,
  getRevenue,
  getIngestion,
  type HealthSummary,
  type PlatformOverview,
  type TicketWithOrg,
  type AnalyticsOverview,
  type ClientRevenue,
  type PlatformIngestion,
} from "@/services/loreAdminApi";

/** Cents → "$1,234.56". */
function usd(cents: number): string {
  return (cents / 100).toLocaleString(undefined, { style: "currency", currency: "USD" });
}
/** Compact number (1234 -> 1.2k). */
function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function ticketStatusPill(status: string): string {
  if (status === "open") return "bg-amber-50 text-amber-700 border-amber-200";
  if (status === "in_progress") return "bg-blue-50 text-blue-700 border-blue-200";
  return "bg-emerald-50 text-emerald-700 border-emerald-200";
}
function categoryLabel(cat: string): string {
  if (cat === "service_down") return "Service down";
  if (cat === "subscription_change") return "Subscription";
  return "General";
}

/**
 * Per-client service-health pill: green when every client-facing service is up,
 * amber on degradation, rose when one is down (names shown on hover). Reflects
 * the shared product stack (rbac / core / lore-engine) this org depends on.
 */
function ClientHealthBadge({ health }: { health: HealthSummary }) {
  const total = health.up + health.degraded + health.down + health.unknown;
  const down = health.services.filter((s) => s.status === "down").map((s) => s.service);
  const tone =
    health.down > 0
      ? "border-rose-200 bg-rose-50 text-rose-700"
      : health.degraded > 0
        ? "border-amber-200 bg-amber-50 text-amber-700"
        : "border-emerald-200 bg-emerald-50 text-emerald-700";
  return (
    <span
      title={down.length ? `Down: ${down.join(", ")}` : "All client-facing services healthy"}
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${tone}`}
    >
      <Activity className="w-3 h-3" strokeWidth={2.2} />
      {health.down > 0
        ? `${health.down} down`
        : health.degraded > 0
          ? `${health.degraded} degraded`
          : `${health.up}/${total} up`}
    </span>
  );
}

export default function LoreAdminHome() {
  const { user, identity, logout } = useAuth();
  const navigate = useNavigate();

  const [overview, setOverview] = useState<PlatformOverview | null>(null);
  const [tickets, setTickets] = useState<TicketWithOrg[]>([]);
  const [analytics, setAnalytics] = useState<AnalyticsOverview | null>(null);
  const [revenue, setRevenue] = useState<ClientRevenue[]>([]);
  const [ingestion, setIngestion] = useState<PlatformIngestion | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ov, tk, an, rev, ing] = await Promise.all([
        getOverview(),
        getTickets(),
        getAnalyticsOverview(),
        getRevenue(),
        getIngestion(),
      ]);
      setOverview(ov);
      setTickets(tk);
      setAnalytics(an);
      setRevenue(rev);
      setIngestion(ing);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load console");
    } finally {
      setLoading(false);
    }
  }, []);

  /** Friendly org name from the overview rollup, falling back to the id. */
  const orgName = (orgId: string): string =>
    overview?.orgs.find((o) => o.orgId === orgId)?.name ?? orgId.slice(0, 8);

  useEffect(() => {
    void load();
  }, [load]);

  function handleSignOut() {
    logout();
    navigate("/login", { replace: true });
  }

  const downServices = overview?.health.services.filter((s) => s.status === "down") ?? [];
  const kpis = [
    { label: "Clients", value: String(overview?.totalOrgs ?? 0), icon: Building2, tone: "text-blue-600" },
    { label: "Subscribed users", value: String(overview?.totalUsers ?? 0), icon: Users, tone: "text-indigo-600" },
    { label: "MRR", value: analytics ? usd(analytics.totalRevenueCents) : "-", icon: Building2, tone: "text-emerald-600" },
    { label: "Tokens this month", value: analytics ? compact(analytics.totalTokensThisMonth) : "-", icon: Activity, tone: "text-indigo-600" },
    { label: "Services up", value: String(overview?.health.up ?? 0), icon: Activity, tone: "text-emerald-600" },
    { label: "Services down", value: String(overview?.health.down ?? 0), icon: AlertTriangle, tone: "text-rose-600" },
  ];

  return (
    <div className="min-h-screen bg-[#f8f9fb]">
      <header className="h-[72px] bg-white/80 backdrop-blur-md border-b border-slate-200/80 sticky top-0 z-30 flex items-center justify-between px-8">
        <div className="flex items-center gap-3">
          <img src="/images/logo.png" alt="Loremind" className="h-8 w-auto object-contain" />
          <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-blue-700 bg-blue-50/60 border border-blue-200/60 rounded-full px-2.5 py-1">
            <ShieldCheck className="w-3.5 h-3.5" strokeWidth={2.2} />
            Lore Admin
          </span>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => void load()}
            disabled={loading}
            className="flex items-center gap-2 px-3 h-9 rounded-lg text-[13px] font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900 transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} strokeWidth={2} /> Refresh
          </button>
          <div className="leading-tight text-right hidden sm:block">
            <div className="text-[12px] font-semibold text-slate-900">{user?.name ?? user?.email ?? "Administrator"}</div>
            <div className="text-[10px] text-slate-500">Platform Super Admin</div>
          </div>
          <button
            onClick={handleSignOut}
            className="flex items-center gap-2 px-3 h-9 rounded-lg text-[13px] font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900 transition-colors"
          >
            <LogOut className="w-[17px] h-[17px] text-slate-500" strokeWidth={2} /> Sign out
          </button>
        </div>
      </header>

      <main className="px-8 py-8 max-w-[1200px] mx-auto">
        <h1 className="text-[24px] font-semibold text-slate-900 leading-tight" style={{ fontFamily: "var(--font-display)" }}>
          Platform overview
        </h1>
        <p className="text-[13px] text-slate-500 mt-1">
          Signed in as {identity?.email} · all client data is read via client-admin routes only.
        </p>

        {error && (
          <div className="mt-5 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-[13px] text-rose-700">
            {error}
          </div>
        )}

        {loading && !overview ? (
          <div className="flex items-center justify-center py-24 text-slate-400">
            <Loader2 className="w-6 h-6 animate-spin" />
          </div>
        ) : overview ? (
          <>
            {/* KPI tiles */}
            <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4 mt-6">
              {kpis.map((k) => (
                <div key={k.label} className="rounded-xl border border-slate-200/70 bg-white shadow-sm p-5">
                  <div className="flex items-center justify-between">
                    <span className="text-[12px] font-medium text-slate-500">{k.label}</span>
                    <k.icon className={`w-[18px] h-[18px] ${k.tone}`} strokeWidth={2} />
                  </div>
                  <div className="text-[30px] font-semibold text-slate-900 mt-1" style={{ fontFamily: "var(--font-display)" }}>
                    {k.value}
                  </div>
                </div>
              ))}
            </div>

            {/* Service-down alert */}
            {downServices.length > 0 && (
              <div className="mt-5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 flex items-start gap-2.5">
                <AlertTriangle className="w-4 h-4 text-rose-500 mt-0.5 shrink-0" strokeWidth={2} />
                <div className="text-[13px] text-rose-700">
                  <span className="font-semibold">{downServices.length} platform service(s) reporting issues:</span>{" "}
                  {downServices.map((s) => s.service).join(", ")}.
                </div>
              </div>
            )}

            {/* Clients */}
            <div className="mt-6 rounded-xl border border-slate-200/70 bg-white shadow-sm overflow-hidden">
              <div className="px-5 py-3.5 border-b border-slate-200/70 text-[13px] font-semibold text-slate-700">
                Clients
              </div>
              <div className="divide-y divide-slate-100">
                {overview.orgs.map((o) => (
                  <div key={o.orgId} className="flex items-center justify-between gap-4 px-5 py-3.5">
                    <div className="min-w-0">
                      <div className="text-[14px] font-medium text-slate-900">{o.name}</div>
                      <div className="text-[11.5px] text-slate-400">{o.slug ?? o.orgId}</div>
                    </div>
                    <div className="flex items-center gap-5 shrink-0 text-[12.5px]">
                      <div className="text-center">
                        <div className="font-semibold text-slate-900">{o.userCount}</div>
                        <div className="text-[10px] text-slate-400 uppercase tracking-wide">Users</div>
                      </div>
                      <ClientHealthBadge health={o.health} />
                      <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-semibold text-slate-600 capitalize">
                        {o.plan.name}
                      </span>
                      {o.openTickets > 0 && (
                        <span className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-[11px] font-semibold text-amber-700">
                          <Ticket className="w-3 h-3" /> {o.openTickets} open
                        </span>
                      )}
                    </div>
                  </div>
                ))}
                {overview.orgs.length === 0 && (
                  <div className="px-5 py-10 text-center text-[13px] text-slate-400">No clients yet.</div>
                )}
              </div>
            </div>

            {/* Revenue by client */}
            <div className="mt-6 rounded-xl border border-slate-200/70 bg-white shadow-sm overflow-hidden">
              <div className="px-5 py-3.5 border-b border-slate-200/70 text-[13px] font-semibold text-slate-700">
                Revenue by client - {analytics?.periodMonth ?? "this month"}
              </div>
              <div className="divide-y divide-slate-100">
                {revenue.map((r) => (
                  <div key={r.organizationId} className="flex items-center justify-between gap-4 px-5 py-3.5">
                    <div className="min-w-0">
                      <div className="text-[14px] font-medium text-slate-900">{orgName(r.organizationId)}</div>
                      <div className="text-[11.5px] text-slate-400">
                        {compact(r.tokensUsed)} tokens
                        {r.overageCents > 0 ? ` · ${usd(r.overageCents)} overage` : ""}
                      </div>
                    </div>
                    <div className="flex items-center gap-5 shrink-0 text-[12.5px]">
                      <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-semibold text-slate-600 capitalize">
                        {r.planKey}
                      </span>
                      <div className="text-right">
                        <div className="font-semibold text-slate-900">{usd(r.totalCents)}</div>
                        <div className="text-[10px] text-slate-400 uppercase tracking-wide">/ mo</div>
                      </div>
                    </div>
                  </div>
                ))}
                {revenue.length === 0 && (
                  <div className="px-5 py-10 text-center text-[13px] text-slate-400">No revenue recorded yet.</div>
                )}
              </div>
            </div>

            {/* Ingestion health across tenants */}
            <div className="mt-6 rounded-xl border border-slate-200/70 bg-white shadow-sm overflow-hidden">
              <div className="px-5 py-3.5 border-b border-slate-200/70 text-[13px] font-semibold text-slate-700 flex items-center gap-2">
                <Database className="w-4 h-4 text-slate-400" /> Ingestion health - across tenants
                {ingestion && ingestion.summary.failedRunsLast24h > 0 && (
                  <span className="ml-2 inline-flex items-center gap-1 rounded-full border border-rose-200 bg-rose-50 px-2 py-0.5 text-[10px] font-semibold text-rose-700">
                    <AlertTriangle className="w-3 h-3" /> {ingestion.summary.failedRunsLast24h} failed / 24h
                  </span>
                )}
              </div>
              {ingestion && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-slate-100 border-b border-slate-100">
                  {[
                    { label: "Sources", value: ingestion.summary.totalSources },
                    { label: "Active", value: ingestion.summary.activeSources },
                    { label: "Errored", value: ingestion.summary.erroredSources },
                    { label: "Runs / 24h", value: ingestion.summary.runsLast24h },
                  ].map((s) => (
                    <div key={s.label} className="bg-white px-5 py-3">
                      <div className="text-[11px] text-slate-400 uppercase tracking-wide">{s.label}</div>
                      <div className="text-[18px] font-semibold text-slate-900">{s.value}</div>
                    </div>
                  ))}
                </div>
              )}
              <div className="divide-y divide-slate-100">
                {(ingestion?.recentRuns ?? []).slice(0, 8).map((r) => (
                  <div key={r.id} className="flex items-start justify-between gap-4 px-5 py-3">
                    <div className="min-w-0">
                      <div className="text-[13.5px] font-medium text-slate-900">
                        {r.orgName}
                        <span className="text-slate-400 font-normal"> · </span>
                        <span className="capitalize">{r.sourceType ?? r.sourceName ?? "source"}</span>
                      </div>
                      <div className="text-[11.5px] text-slate-400 mt-0.5">
                        {new Date(r.createdAt).toLocaleString()}
                        {r.status === "failed" && r.error ? ` · ${r.error}` : ""}
                      </div>
                    </div>
                    <span
                      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[10px] font-semibold capitalize shrink-0 ${
                        r.status === "succeeded"
                          ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                          : r.status === "failed"
                            ? "border-rose-200 bg-rose-50 text-rose-700"
                            : "border-slate-200 bg-slate-50 text-slate-500"
                      }`}
                    >
                      {r.status === "succeeded" ? (
                        <CheckCircle2 className="w-3 h-3" />
                      ) : r.status === "failed" ? (
                        <XCircle className="w-3 h-3" />
                      ) : null}
                      {r.status}
                    </span>
                  </div>
                ))}
                {(!ingestion || ingestion.recentRuns.length === 0) && (
                  <div className="px-5 py-10 text-center text-[13px] text-slate-400">No ingestion runs yet.</div>
                )}
              </div>
            </div>

            {/* Tickets inbox */}
            <div className="mt-6 rounded-xl border border-slate-200/70 bg-white shadow-sm overflow-hidden">
              <div className="px-5 py-3.5 border-b border-slate-200/70 text-[13px] font-semibold text-slate-700 flex items-center gap-2">
                <Ticket className="w-4 h-4 text-slate-400" /> Tickets from client admins
              </div>
              <div className="divide-y divide-slate-100">
                {tickets.map((t) => (
                  <div key={t.id} className="flex items-start justify-between gap-4 px-5 py-3.5">
                    <div className="min-w-0">
                      <div className="text-[13.5px] font-medium text-slate-900">{t.subject}</div>
                      <div className="text-[11.5px] text-slate-400 mt-0.5">
                        {t.orgName} · {categoryLabel(t.category)} · {t.createdAt ? new Date(t.createdAt).toLocaleString() : ""}
                      </div>
                    </div>
                    <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[10px] font-semibold capitalize shrink-0 ${ticketStatusPill(t.status)}`}>
                      {t.status.replace("_", " ")}
                    </span>
                  </div>
                ))}
                {tickets.length === 0 && (
                  <div className="px-5 py-10 text-center text-[13px] text-slate-400">No tickets raised.</div>
                )}
              </div>
            </div>
          </>
        ) : null}
      </main>
    </div>
  );
}

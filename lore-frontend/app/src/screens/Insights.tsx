import { useCallback, useEffect, useState } from "react";
import {
  Loader2, RefreshCw, MessageSquare, Users, Zap, AlertTriangle, Clock, Coins,
} from "lucide-react";
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from "recharts";
import AppShell from "@/components/app-shell/AppShell";
import { Button } from "@/components/ui/button";
import {
  getActivity, getInsightsSummary, getRecentQueries,
  type ActivityPoint, type InsightsSummary, type QueryLogEntry,
} from "@/services/insightsApi";

/** Compact number (1234 -> 1.2k). */
function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function statusPill(status: string): string {
  if (status === "completed") return "bg-emerald-50 text-emerald-700 border-emerald-200";
  if (status === "failed") return "bg-rose-50 text-rose-700 border-rose-200";
  return "bg-amber-50 text-amber-700 border-amber-200";
}

/** Show the identity minus any email domain for compactness. */
function shortUser(userId: string): string {
  return userId.includes("@") ? userId.split("@")[0] : userId.slice(0, 8);
}

export default function Insights() {
  const [summary, setSummary] = useState<InsightsSummary | null>(null);
  const [activity, setActivity] = useState<ActivityPoint[]>([]);
  const [queries, setQueries] = useState<QueryLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, a, q] = await Promise.all([
        getInsightsSummary(),
        getActivity(14),
        getRecentQueries(25, 0),
      ]);
      setSummary(s);
      setActivity(a);
      setQueries(q);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load insights");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const kpis = summary
    ? [
        { label: "Queries this month", value: fmt(summary.totalQueries), icon: MessageSquare, tone: "text-blue-600" },
        { label: "Active users", value: `${summary.activeUsers}/${summary.memberCount}`, icon: Users, tone: "text-indigo-600" },
        { label: "Queries today", value: fmt(summary.queriesToday), icon: Zap, tone: "text-amber-600" },
        { label: "Avg latency", value: `${(summary.avgLatencyMs / 1000).toFixed(1)}s`, icon: Clock, tone: "text-slate-600" },
        { label: "Failed queries", value: fmt(summary.failedQueries), icon: AlertTriangle, tone: "text-rose-600" },
        { label: "Tokens this month", value: fmt(summary.tokensUsed), icon: Coins, tone: "text-emerald-600" },
      ]
    : [];

  return (
    <AppShell
      title="Insights"
      subtitle="How your organization is using Lore"
      actions={
        <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      }
    >
      {loading && !summary ? (
        <div className="flex items-center justify-center py-24 text-slate-400">
          <Loader2 className="w-6 h-6 animate-spin" />
        </div>
      ) : error ? (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-[13px] text-rose-700">
          {error}
        </div>
      ) : summary ? (
        <>
          {/* KPI tiles */}
          <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4 mb-6">
            {kpis.map((k) => (
              <div key={k.label} className="rounded-xl border border-slate-200/70 bg-white shadow-sm p-5">
                <div className="flex items-center justify-between">
                  <span className="text-[12px] font-medium text-slate-500">{k.label}</span>
                  <k.icon className={`w-[18px] h-[18px] ${k.tone}`} strokeWidth={2} />
                </div>
                <div className="text-[26px] font-semibold text-slate-900 mt-1" style={{ fontFamily: "var(--font-display)" }}>
                  {k.value}
                </div>
              </div>
            ))}
          </div>

          {/* Activity chart */}
          <div className="rounded-xl border border-slate-200/70 bg-white shadow-sm p-5 mb-6">
            <div className="text-[13px] font-semibold text-slate-700 mb-4">Query activity - last 14 days</div>
            <div style={{ width: "100%", height: 220 }}>
              <ResponsiveContainer>
                <AreaChart data={activity} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
                  <defs>
                    <linearGradient id="q" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#2563eb" stopOpacity={0.25} />
                      <stop offset="100%" stopColor="#2563eb" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" vertical={false} />
                  <XAxis
                    dataKey="day"
                    tickFormatter={(d: string) => d.slice(5)}
                    tick={{ fontSize: 11, fill: "#94a3b8" }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "#94a3b8" }} axisLine={false} tickLine={false} width={36} />
                  <Tooltip
                    contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e2e8f0" }}
                    labelStyle={{ color: "#475569" }}
                  />
                  <Area type="monotone" dataKey="queries" stroke="#2563eb" strokeWidth={2} fill="url(#q)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Recent questions */}
          <div className="rounded-xl border border-slate-200/70 bg-white shadow-sm overflow-hidden">
            <div className="px-5 py-3.5 border-b border-slate-200/70 text-[13px] font-semibold text-slate-700 flex items-center gap-2">
              <MessageSquare className="w-4 h-4 text-slate-400" /> Recent questions
            </div>
            <div className="divide-y divide-slate-100">
              {queries.map((q) => (
                <div key={q.turnId} className="flex items-start justify-between gap-4 px-5 py-3.5">
                  <div className="min-w-0">
                    <div className="text-[13.5px] font-medium text-slate-900 truncate">{q.question}</div>
                    <div className="text-[11.5px] text-slate-400 mt-0.5">
                      {shortUser(q.userId)} · {new Date(q.createdAt).toLocaleString()}
                      {q.latencyMs != null ? ` · ${(q.latencyMs / 1000).toFixed(1)}s` : ""}
                    </div>
                  </div>
                  <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[10px] font-semibold capitalize shrink-0 ${statusPill(q.status)}`}>
                    {q.status}
                  </span>
                </div>
              ))}
              {queries.length === 0 && (
                <div className="px-5 py-10 text-center text-[13px] text-slate-400">No questions asked yet.</div>
              )}
            </div>
          </div>
        </>
      ) : null}
    </AppShell>
  );
}

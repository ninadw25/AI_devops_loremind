import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw, Mail, GitBranch, Database, CheckCircle2, XCircle, Clock } from "lucide-react";
import AppShell from "@/components/app-shell/AppShell";
import { Button } from "@/components/ui/button";
import {
  listRuns, listSources, type IngestionRun, type IngestionSource,
} from "@/services/sourcesApi";

/** Icon per source type. */
function sourceIcon(type: string) {
  if (type === "gmail") return Mail;
  if (type === "gitlab") return GitBranch;
  return Database;
}

function runStatusPill(status: string): string {
  if (status === "succeeded") return "bg-emerald-50 text-emerald-700 border-emerald-200";
  if (status === "failed") return "bg-rose-50 text-rose-700 border-rose-200";
  if (status === "running") return "bg-blue-50 text-blue-700 border-blue-200";
  return "bg-slate-100 text-slate-500 border-slate-200";
}

/** Render a stats jsonb bag as "k: v · k: v". */
function statsLine(stats: Record<string, unknown> | null): string {
  if (!stats) return "";
  return Object.entries(stats)
    .map(([k, v]) => `${v} ${k}`)
    .join(" · ");
}

function relTime(iso: string | null): string {
  if (!iso) return "never";
  const diff = Date.now() - new Date(iso).getTime();
  const h = Math.floor(diff / 3_600_000);
  if (h < 1) return "just now";
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export default function Sources() {
  const [sources, setSources] = useState<IngestionSource[]>([]);
  const [runs, setRuns] = useState<IngestionRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, r] = await Promise.all([listSources(), listRuns(20)]);
      setSources(s);
      setRuns(r);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load sources");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <AppShell
      title="Data Sources"
      subtitle="Where Lore learns your organization's knowledge"
      actions={
        <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      }
    >
      {loading && sources.length === 0 ? (
        <div className="flex items-center justify-center py-24 text-slate-400">
          <Loader2 className="w-6 h-6 animate-spin" />
        </div>
      ) : error ? (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-[13px] text-rose-700">
          {error}
        </div>
      ) : (
        <>
          {/* Connector cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
            {sources.map((s) => {
              const Icon = sourceIcon(s.type);
              const healthy = !s.lastError && s.enabled;
              return (
                <div key={s.id} className="rounded-xl border border-slate-200/70 bg-white shadow-sm p-5">
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-lg bg-slate-50 border border-slate-200 flex items-center justify-center">
                        <Icon className="w-5 h-5 text-slate-600" strokeWidth={2} />
                      </div>
                      <div>
                        <div className="text-[15px] font-semibold text-slate-900">{s.displayName}</div>
                        <div className="text-[11.5px] text-slate-400 capitalize">{s.type}</div>
                      </div>
                    </div>
                    <span
                      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${
                        healthy
                          ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                          : "border-rose-200 bg-rose-50 text-rose-700"
                      }`}
                    >
                      {healthy ? <CheckCircle2 className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
                      {s.enabled ? (s.lastError ? "Error" : "Connected") : "Disabled"}
                    </span>
                  </div>
                  <div className="mt-4 flex items-center gap-2 text-[12px] text-slate-500">
                    <Clock className="w-3.5 h-3.5" />
                    Last synced {relTime(s.lastRunAt)}
                  </div>
                  {s.lastError && (
                    <div className="mt-2 text-[12px] text-rose-600 truncate">{s.lastError}</div>
                  )}
                </div>
              );
            })}
            {sources.length === 0 && (
              <div className="col-span-full rounded-xl border border-dashed border-slate-200 bg-white px-5 py-10 text-center text-[13px] text-slate-400">
                No data sources connected yet.
              </div>
            )}
          </div>

          {/* Run history */}
          <div className="rounded-xl border border-slate-200/70 bg-white shadow-sm overflow-hidden">
            <div className="px-5 py-3.5 border-b border-slate-200/70 text-[13px] font-semibold text-slate-700">
              Recent ingestion runs
            </div>
            <div className="divide-y divide-slate-100">
              {runs.map((r) => (
                <div key={r.id} className="flex items-start justify-between gap-4 px-5 py-3.5">
                  <div className="min-w-0">
                    <div className="text-[13.5px] font-medium text-slate-900">
                      {r.sourceName ?? r.sourceType ?? "Unknown source"}
                      <span className="ml-2 text-[11px] font-normal text-slate-400 capitalize">{r.trigger}</span>
                    </div>
                    <div className="text-[11.5px] text-slate-400 mt-0.5">
                      {new Date(r.createdAt).toLocaleString()}
                      {r.status === "failed" && r.error ? ` · ${r.error}` : ""}
                      {r.status !== "failed" && r.stats ? ` · ${statsLine(r.stats)}` : ""}
                    </div>
                  </div>
                  <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[10px] font-semibold capitalize shrink-0 ${runStatusPill(r.status)}`}>
                    {r.status}
                  </span>
                </div>
              ))}
              {runs.length === 0 && (
                <div className="px-5 py-10 text-center text-[13px] text-slate-400">No ingestion runs yet.</div>
              )}
            </div>
          </div>
        </>
      )}
    </AppShell>
  );
}

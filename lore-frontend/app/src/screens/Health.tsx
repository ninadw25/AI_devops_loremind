import { useEffect, useState, useCallback } from "react";
import { Loader2, RefreshCw, AlertTriangle, CheckCircle2, MinusCircle, HelpCircle, X } from "lucide-react";
import AppShell from "@/components/app-shell/AppShell";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  getHealthSummary,
  listServices,
  type HealthResult,
  type HealthSummary,
  type ServiceStatus,
} from "@/services/healthApi";
import { createTicket } from "@/services/ticketsApi";

const STATUS_STYLE: Record<ServiceStatus, { pill: string; label: string }> = {
  up: { pill: "bg-emerald-50 text-emerald-700 border-emerald-200", label: "Operational" },
  degraded: { pill: "bg-amber-50 text-amber-700 border-amber-200", label: "Degraded" },
  down: { pill: "bg-rose-50 text-rose-700 border-rose-200", label: "Down" },
  unknown: { pill: "bg-slate-100 text-slate-500 border-slate-200", label: "Unknown" },
};

const TILES: { key: ServiceStatus; label: string; icon: typeof CheckCircle2; tone: string }[] = [
  { key: "up", label: "Operational", icon: CheckCircle2, tone: "text-emerald-600" },
  { key: "degraded", label: "Degraded", icon: AlertTriangle, tone: "text-amber-600" },
  { key: "down", label: "Down", icon: MinusCircle, tone: "text-rose-600" },
  { key: "unknown", label: "Unknown", icon: HelpCircle, tone: "text-slate-400" },
];

export default function Health() {
  const [summary, setSummary] = useState<HealthSummary | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Raise-concern dialog state
  const [target, setTarget] = useState<{ id: string; name: string } | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [raised, setRaised] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, svcs] = await Promise.all([getHealthSummary(), listServices()]);
      setSummary(s);
      setNames(Object.fromEntries(svcs.map((v) => [v.id, v.name])));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load health");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function openConcern(svc: HealthResult) {
    const name = names[svc.service] ?? svc.service;
    setTarget({ id: svc.service, name });
    setSubject(`Service "${name}" is ${svc.status}`);
    setBody(`The ${name} service is reporting status "${svc.status}". Please investigate.`);
    setRaised(false);
    setDialogError(null);
  }

  async function submitConcern() {
    if (!target) return;
    setSubmitting(true);
    setDialogError(null);
    try {
      await createTicket({
        subject,
        body,
        category: "service_down",
        serviceId: target.id,
        priority: "high",
      });
      setRaised(true);
    } catch (err) {
      setDialogError(err instanceof Error ? err.message : "Failed to raise concern");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AppShell
      title="Service Health"
      subtitle="Live status of your platform services"
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
          <div className="grid grid-cols-2 xl:grid-cols-4 gap-4 mb-6">
            {TILES.map((t) => (
              <div key={t.key} className="rounded-xl border border-slate-200/70 bg-white shadow-sm p-5">
                <div className="flex items-center justify-between">
                  <span className="text-[12px] font-medium text-slate-500">{t.label}</span>
                  <t.icon className={`w-[18px] h-[18px] ${t.tone}`} strokeWidth={2} />
                </div>
                <div
                  className="text-[30px] font-semibold text-slate-900 mt-1"
                  style={{ fontFamily: "var(--font-display)" }}
                >
                  {summary[t.key]}
                </div>
              </div>
            ))}
          </div>

          <div className="rounded-xl border border-slate-200/70 bg-white shadow-sm overflow-hidden">
            <div className="px-5 py-3.5 border-b border-slate-200/70 text-[13px] font-semibold text-slate-700">
              Services
            </div>
            <div className="divide-y divide-slate-100">
              {summary.services.map((svc) => {
                const style = STATUS_STYLE[svc.status];
                const name = names[svc.service] ?? svc.service;
                const actionable = svc.status === "down" || svc.status === "degraded";
                return (
                  <div key={svc.service} className="flex items-center justify-between gap-4 px-5 py-3.5">
                    <div className="min-w-0">
                      <div className="text-[14px] font-medium text-slate-900 truncate">{name}</div>
                      <div className="text-[11.5px] text-slate-400">
                        {svc.latency > 0 ? `${svc.latency} ms · ` : ""}
                        checked {new Date(svc.lastChecked).toLocaleTimeString()}
                      </div>
                    </div>
                    <div className="flex items-center gap-3 shrink-0">
                      <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-semibold ${style.pill}`}>
                        {style.label}
                      </span>
                      {actionable && (
                        <Button variant="outline" size="sm" onClick={() => openConcern(svc)}>
                          Raise concern
                        </Button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </>
      ) : null}

      <Dialog open={!!target} onOpenChange={(o) => !o && setTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Raise a concern</DialogTitle>
            <DialogDescription>
              This opens a support ticket with the Loremind team.
            </DialogDescription>
          </DialogHeader>
          {raised ? (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-[13px] text-emerald-700">
              Concern raised. The Loremind team has been notified.
            </div>
          ) : (
            <div className="space-y-4">
              {dialogError && (
                <div className="rounded-lg border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-[12.5px] text-rose-700">
                  {dialogError}
                </div>
              )}
              <div className="space-y-2">
                <Label htmlFor="subject" className="text-[13px] text-slate-700">Subject</Label>
                <Input id="subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="body" className="text-[13px] text-slate-700">Details</Label>
                <Textarea id="body" rows={4} value={body} onChange={(e) => setBody(e.target.value)} />
              </div>
            </div>
          )}
          <DialogFooter>
            {raised ? (
              <Button onClick={() => setTarget(null)}>Done</Button>
            ) : (
              <>
                <Button variant="outline" onClick={() => setTarget(null)} disabled={submitting}>
                  <X className="w-4 h-4" /> Cancel
                </Button>
                <Button onClick={() => void submitConcern()} disabled={submitting || !subject.trim()}>
                  {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                  Submit
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}

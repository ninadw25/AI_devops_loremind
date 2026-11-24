import { useEffect, useState, useCallback } from "react";
import { Loader2, Check, ArrowUpRight } from "lucide-react";
import AppShell from "@/components/app-shell/AppShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  getSubscription,
  upgradePlan,
  cancelPlan,
  PLAN_CATALOG,
  type SubscriptionView,
} from "@/services/subscriptionApi";
import { createTicket } from "@/services/ticketsApi";

function dollars(cents: number): string {
  return cents === 0 ? "Free" : `$${(cents / 100).toLocaleString()}/mo`;
}
function tokens(n: number): string {
  return `${(n / 1_000_000).toLocaleString()}M tokens/mo`;
}
function seatLabel(n: number): string {
  return n < 0 ? "Unlimited seats" : `${n} seats`;
}

export default function Subscription() {
  const [sub, setSub] = useState<SubscriptionView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [reqOpen, setReqOpen] = useState(false);
  const [reqSubject, setReqSubject] = useState("");
  const [reqBody, setReqBody] = useState("");
  const [reqSubmitting, setReqSubmitting] = useState(false);
  const [reqDone, setReqDone] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSub(await getSubscription());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load subscription");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function switchTo(planKey: string) {
    setBusy(planKey);
    try {
      setSub(await upgradePlan(planKey));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to change plan");
    } finally {
      setBusy(null);
    }
  }

  async function cancel() {
    setBusy("cancel");
    try {
      setSub(await cancelPlan());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to cancel");
    } finally {
      setBusy(null);
    }
  }

  async function submitRequest() {
    setReqSubmitting(true);
    try {
      await createTicket({
        subject: reqSubject || "Subscription change request",
        body: reqBody,
        category: "subscription_change",
      });
      setReqDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit request");
    } finally {
      setReqSubmitting(false);
    }
  }

  return (
    <AppShell
      title="Subscription"
      subtitle="Your plan, limits and change requests"
      actions={
        <Button variant="outline" size="sm" onClick={() => { setReqOpen(true); setReqDone(false); setReqSubject(""); setReqBody(""); }}>
          Request a change
        </Button>
      }
    >
      {loading && !sub ? (
        <div className="flex items-center justify-center py-24 text-slate-400">
          <Loader2 className="w-6 h-6 animate-spin" />
        </div>
      ) : (
        <>
          {error && (
            <div className="mb-5 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-[13px] text-rose-700">
              {error}
            </div>
          )}

          {sub && (
            <div className="rounded-xl border border-slate-200/70 bg-white shadow-sm p-6 mb-6">
              <div className="flex items-start justify-between gap-4 flex-wrap">
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-wider text-blue-700">Current plan</div>
                  <div className="text-[26px] font-semibold text-slate-900 mt-0.5" style={{ fontFamily: "var(--font-display)" }}>
                    {sub.name}
                  </div>
                  <div className="text-[13px] text-slate-500 mt-1">
                    {dollars(sub.limits.monthlyPriceCents)} · {tokens(sub.limits.monthlyTokenLimit)} ·{" "}
                    {seatLabel(Number(sub.limits.features?.seats ?? 0))}
                  </div>
                </div>
                <div className="text-right">
                  <span className="inline-flex items-center rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[11px] font-semibold text-emerald-700 capitalize">
                    {sub.status}
                  </span>
                  {sub.renewalDate && (
                    <div className="text-[11.5px] text-slate-400 mt-1.5">
                      Renews {new Date(sub.renewalDate).toLocaleDateString()}
                    </div>
                  )}
                </div>
              </div>
              {sub.cancelAtPeriodEnd && (
                <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-[12.5px] text-amber-700">
                  Scheduled to cancel at the end of the current period.
                </div>
              )}
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
            {PLAN_CATALOG.map((plan) => {
              const current = sub?.planKey === plan.key;
              return (
                <div
                  key={plan.key}
                  className={`rounded-xl border bg-white shadow-sm p-5 flex flex-col ${current ? "border-blue-300 ring-1 ring-blue-200" : "border-slate-200/70"}`}
                >
                  <div className="flex items-center justify-between">
                    <div className="text-[15px] font-semibold text-slate-900">{plan.name}</div>
                    {current && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-semibold text-blue-700">
                        <Check className="w-3 h-3" /> Current
                      </span>
                    )}
                  </div>
                  <div className="text-[22px] font-semibold text-slate-900 mt-2" style={{ fontFamily: "var(--font-display)" }}>
                    {dollars(plan.priceCents)}
                  </div>
                  <ul className="mt-3 space-y-1.5 text-[12.5px] text-slate-600 flex-1">
                    <li>{tokens(plan.tokenLimit)}</li>
                    <li>{seatLabel(plan.seats)}</li>
                  </ul>
                  <Button
                    className="mt-4 w-full"
                    variant={current ? "outline" : "default"}
                    disabled={current || busy !== null}
                    onClick={() => void switchTo(plan.key)}
                  >
                    {busy === plan.key ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                    {current ? "Current plan" : `Switch to ${plan.name}`}
                  </Button>
                </div>
              );
            })}
          </div>

          {sub && !sub.cancelAtPeriodEnd && sub.planKey !== "free" && (
            <Button variant="ghost" className="text-rose-600 hover:text-rose-700 hover:bg-rose-50" onClick={() => void cancel()} disabled={busy !== null}>
              {busy === "cancel" ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              Cancel at period end
            </Button>
          )}
        </>
      )}

      <Dialog open={reqOpen} onOpenChange={setReqOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Request a subscription change</DialogTitle>
            <DialogDescription>
              Add or remove a service, or change your seat count. This is sent to Loremind as a request.
            </DialogDescription>
          </DialogHeader>
          {reqDone ? (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-[13px] text-emerald-700">
              Request submitted. The Loremind team will follow up.
            </div>
          ) : (
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="rs" className="text-[13px] text-slate-700">Summary</Label>
                <Input id="rs" placeholder="e.g. Increase seats to 50" value={reqSubject} onChange={(e) => setReqSubject(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="rb" className="text-[13px] text-slate-700">Details</Label>
                <Textarea id="rb" rows={4} placeholder="Describe the change you need…" value={reqBody} onChange={(e) => setReqBody(e.target.value)} />
              </div>
            </div>
          )}
          <DialogFooter>
            {reqDone ? (
              <Button onClick={() => setReqOpen(false)}>Done</Button>
            ) : (
              <>
                <Button variant="outline" onClick={() => setReqOpen(false)} disabled={reqSubmitting}>Cancel</Button>
                <Button onClick={() => void submitRequest()} disabled={reqSubmitting || !reqSubject.trim()}>
                  {reqSubmitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowUpRight className="w-4 h-4" />}
                  Submit request
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}

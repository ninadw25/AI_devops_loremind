import { useEffect, useState, useCallback } from "react";
import { Loader2, Plus, Inbox } from "lucide-react";
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
  listTickets,
  createTicket,
  type Ticket,
  type TicketCategory,
  type TicketPriority,
} from "@/services/ticketsApi";

const selectCls =
  "h-9 w-full rounded-md border border-slate-200 bg-white px-2.5 text-[13px] text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-200";

const CATEGORY_STYLE: Record<string, { pill: string; label: string }> = {
  service_down: { pill: "bg-rose-50 text-rose-700 border-rose-200", label: "Service down" },
  subscription_change: { pill: "bg-blue-50 text-blue-700 border-blue-200", label: "Subscription" },
  general: { pill: "bg-slate-100 text-slate-600 border-slate-200", label: "General" },
};

function statusPill(status: string): string {
  if (status === "open") return "bg-amber-50 text-amber-700 border-amber-200";
  if (status === "in_progress") return "bg-blue-50 text-blue-700 border-blue-200";
  return "bg-emerald-50 text-emerald-700 border-emerald-200";
}

export default function Tickets() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ subject: "", category: "general", priority: "normal", body: "" });
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setTickets(await listTickets());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load tickets");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      await createTicket({
        subject: form.subject,
        category: form.category as TicketCategory,
        priority: form.priority as TicketPriority,
        body: form.body,
      });
      setOpen(false);
      setForm({ subject: "", category: "general", priority: "normal", body: "" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create ticket");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AppShell
      title="Support & Requests"
      subtitle="Raise concerns or request subscription changes"
      actions={
        <Button size="sm" onClick={() => setOpen(true)}>
          <Plus className="w-4 h-4" /> Raise a ticket
        </Button>
      }
    >
      {error && (
        <div className="mb-5 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-[13px] text-rose-700">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-24 text-slate-400">
          <Loader2 className="w-6 h-6 animate-spin" />
        </div>
      ) : tickets.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-300 bg-white/50 py-16 flex flex-col items-center text-slate-400">
          <Inbox className="w-8 h-8 mb-3" strokeWidth={1.5} />
          <div className="text-[13px]">No tickets yet. Raise one if a service is down or you need a plan change.</div>
        </div>
      ) : (
        <div className="rounded-xl border border-slate-200/70 bg-white shadow-sm divide-y divide-slate-100 overflow-hidden">
          {tickets.map((t) => {
            const cat = CATEGORY_STYLE[t.category] ?? CATEGORY_STYLE.general;
            return (
              <div key={t.id} className="px-5 py-4 flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="text-[14px] font-medium text-slate-900">{t.subject}</div>
                  {t.body && <div className="text-[12.5px] text-slate-500 mt-0.5 line-clamp-2">{t.body}</div>}
                  <div className="text-[11px] text-slate-400 mt-1.5">
                    {t.createdAt ? new Date(t.createdAt).toLocaleString() : ""} · priority {t.priority}
                  </div>
                </div>
                <div className="flex flex-col items-end gap-1.5 shrink-0">
                  <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-semibold ${cat.pill}`}>
                    {cat.label}
                  </span>
                  <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[10px] font-semibold capitalize ${statusPill(t.status)}`}>
                    {t.status.replace("_", " ")}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Raise a ticket</DialogTitle>
            <DialogDescription>Report a problem or request a change. Loremind will follow up.</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="ts" className="text-[13px] text-slate-700">Subject</Label>
              <Input id="ts" value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label htmlFor="tc" className="text-[13px] text-slate-700">Category</Label>
                <select id="tc" className={selectCls} value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                  <option value="general">General</option>
                  <option value="service_down">Service down</option>
                  <option value="subscription_change">Subscription change</option>
                </select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="tp" className="text-[13px] text-slate-700">Priority</Label>
                <select id="tp" className={selectCls} value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
                  <option value="low">Low</option>
                  <option value="normal">Normal</option>
                  <option value="high">High</option>
                  <option value="urgent">Urgent</option>
                </select>
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="tb" className="text-[13px] text-slate-700">Details</Label>
              <Textarea id="tb" rows={4} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)} disabled={submitting}>Cancel</Button>
            <Button onClick={() => void submit()} disabled={submitting || !form.subject.trim()}>
              {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              Submit
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}

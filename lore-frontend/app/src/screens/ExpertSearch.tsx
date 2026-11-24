import { useState } from 'react';
import { Search, ChevronDown, ChevronRight, FileText } from 'lucide-react';
import AppShell from '@/components/app-shell/AppShell';
import { findExperts, type Expert } from '@/services/continuityApi';

function initials(name?: string | null): string {
  if (!name) return '?';
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? '') + (parts[1]?.[0] ?? '')).toUpperCase() || '?';
}

export default function ExpertSearch() {
  const [topic, setTopic] = useState('');
  const [experts, setExperts] = useState<Expert[] | null>(null);
  const [queried, setQueried] = useState('');
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const run = () => {
    const q = topic.trim();
    if (!q) return;
    setLoading(true); setErr(null);
    findExperts(q, 8)
      .then((r) => { setExperts(r.experts); setQueried(q); })
      .catch((e) => setErr(String(e?.message ?? e)))
      .finally(() => setLoading(false));
  };

  return (
    <AppShell title="Expert Search" subtitle="Who actually knows about X — ranked by real evidence, with receipts">
      <div className="flex gap-2 max-w-xl mb-6">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          <input
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && run()}
            placeholder="e.g. Hiring, Payments, Investor Pitch…"
            className="w-full pl-10 pr-3 py-2.5 rounded-xl border border-slate-200 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-400"
          />
        </div>
        <button onClick={run} disabled={loading}
          className="px-5 py-2.5 rounded-xl bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-60 transition-colors">
          {loading ? 'Searching…' : 'Search'}
        </button>
      </div>

      {err && <div className="bg-rose-50 border border-rose-200 text-rose-700 text-sm rounded-xl p-4 mb-4">{err}</div>}

      {!experts && !loading && (
        <div className="text-center py-16 text-slate-400">
          <Search className="w-8 h-8 mx-auto mb-3 opacity-40" />
          <p className="text-sm">Search a topic to see who holds the knowledge.</p>
        </div>
      )}

      {experts && (
        <>
          <p className="text-xs text-slate-400 mb-3">
            {experts.length} {experts.length === 1 ? 'person' : 'people'} with recorded evidence on <span className="font-medium text-slate-600">"{queried}"</span>
          </p>
          <div className="space-y-3">
            {experts.map((ex, i) => {
              const open = expanded === ex.person_id;
              return (
                <div key={ex.person_id} className="bg-white border border-slate-200 rounded-2xl overflow-hidden">
                  <button
                    onClick={() => setExpanded(open ? null : ex.person_id)}
                    className="w-full flex items-center gap-4 p-4 text-left hover:bg-slate-50/60 transition-colors"
                  >
                    <span className="text-sm font-semibold text-slate-300 w-5">{i + 1}</span>
                    <div className="w-10 h-10 rounded-full bg-blue-50 grid place-items-center text-sm font-semibold text-blue-700">
                      {initials(ex.name)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-slate-800 truncate">{ex.name ?? ex.person_id}</div>
                      {ex.email && <div className="text-xs text-slate-400 truncate">{ex.email}</div>}
                    </div>
                    <div className="text-right">
                      <div className="text-lg font-semibold text-slate-900">{ex.evidence_count}</div>
                      <div className="text-[11px] text-slate-400">evidence</div>
                    </div>
                    {open ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
                  </button>
                  {open && (
                    <div className="border-t border-slate-100 bg-slate-50/40 p-4 space-y-2">
                      {ex.evidence.length === 0 && <p className="text-xs text-slate-400">No sampled receipts.</p>}
                      {ex.evidence.map((ev) => (
                        <div key={ev.id} className="flex items-start gap-2.5 text-sm">
                          <FileText className="w-3.5 h-3.5 text-slate-400 mt-0.5 shrink-0" />
                          <div className="min-w-0">
                            <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 mr-2">{ev.type}</span>
                            <span className="text-slate-700">{ev.description ?? '(no description)'}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
    </AppShell>
  );
}

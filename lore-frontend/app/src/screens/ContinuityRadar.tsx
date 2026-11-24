import { useCallback, useEffect, useState } from 'react';
import { ShieldAlert, AlertTriangle, Layers, RefreshCw, UserRound } from 'lucide-react';
import AppShell from '@/components/app-shell/AppShell';
import { getRadar, type RadarResponse } from '@/services/continuityApi';

function initials(name?: string | null): string {
  if (!name) return '?';
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? '') + (parts[1]?.[0] ?? '')).toUpperCase() || '?';
}

function StatBig({ label, value, sub, tone, icon }: {
  label: string; value: string; sub: string;
  tone: 'danger' | 'warning' | 'neutral'; icon: React.ReactNode;
}) {
  const toneCls = {
    danger: 'text-rose-600 bg-rose-50',
    warning: 'text-amber-600 bg-amber-50',
    neutral: 'text-slate-600 bg-slate-100',
  }[tone];
  return (
    <div className="bg-white border border-slate-200 rounded-2xl p-5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[13px] font-medium text-slate-500">{label}</span>
        <span className={`w-8 h-8 rounded-lg grid place-items-center ${toneCls}`}>{icon}</span>
      </div>
      <div className="text-3xl font-semibold text-slate-900 tracking-tight">{value}</div>
      <div className="text-xs text-slate-400 mt-1.5 leading-relaxed">{sub}</div>
    </div>
  );
}

export default function ContinuityRadar() {
  const [data, setData] = useState<RadarResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [maxHolders, setMaxHolders] = useState(2);

  const load = useCallback(() => {
    setLoading(true); setErr(null);
    getRadar(maxHolders, 40)
      .then(setData)
      .catch((e) => setErr(String(e?.message ?? e)))
      .finally(() => setLoading(false));
  }, [maxHolders]);

  useEffect(() => { load(); }, [load]);

  const s = data?.summary;
  const pct = s && s.total_topics ? Math.round((s.sole_held / s.total_topics) * 100) : 0;

  return (
    <AppShell
      title="Continuity Radar"
      subtitle="Knowledge concentrated in one person — surface it before they leave"
      actions={
        <button onClick={load} className="inline-flex items-center gap-2 text-[13px] font-medium text-slate-600 hover:text-slate-900 px-3 py-2 rounded-lg hover:bg-slate-100 transition-colors">
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </button>
      }
    >
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <StatBig label="Single point of failure" value={loading ? '…' : `${pct}%`}
          sub={s ? `${s.sole_held.toLocaleString()} of ${s.total_topics.toLocaleString()} topics rest on one person` : 'Loading…'}
          tone="danger" icon={<ShieldAlert className="w-4 h-4" />} />
        <StatBig label="Sole-held topics" value={loading ? '…' : (s?.sole_held.toLocaleString() ?? '—')}
          sub="Exactly one person holds any recorded knowledge" tone="warning" icon={<AlertTriangle className="w-4 h-4" />} />
        <StatBig label="At-risk (≤2 holders)" value={loading ? '…' : (s?.at_risk.toLocaleString() ?? '—')}
          sub="Thinly covered — cross-train before it's urgent" tone="neutral" icon={<Layers className="w-4 h-4" />} />
      </div>

      {/* Controls */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-slate-800">At-risk topics</h2>
        <div className="inline-flex bg-slate-100 rounded-lg p-0.5">
          {[1, 2, 3].map((n) => (
            <button key={n} onClick={() => setMaxHolders(n)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${maxHolders === n ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>
              ≤{n} holder{n > 1 ? 's' : ''}
            </button>
          ))}
        </div>
      </div>

      {err && <div className="bg-rose-50 border border-rose-200 text-rose-700 text-sm rounded-xl p-4 mb-4">Couldn't load radar: {err}</div>}

      <div className="bg-white border border-slate-200 rounded-2xl divide-y divide-slate-100 overflow-hidden">
        {loading && Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="p-4 animate-pulse flex items-center gap-4">
            <div className="h-4 bg-slate-100 rounded w-1/3" />
            <div className="h-4 bg-slate-100 rounded w-16 ml-auto" />
          </div>
        ))}

        {!loading && data?.topics.length === 0 && (
          <div className="p-10 text-center text-slate-400 text-sm">No at-risk topics at this threshold.</div>
        )}

        {!loading && data?.topics.map((t) => (
          <div key={t.topic_id} className="p-4 flex items-center gap-4 hover:bg-slate-50/60 transition-colors">
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium text-slate-800 truncate">{t.topic_name ?? t.topic_id}</div>
              <div className="text-xs text-slate-400 mt-0.5">{t.total_evidence} evidence item{t.total_evidence === 1 ? '' : 's'}</div>
            </div>
            <div className="flex -space-x-2">
              {t.holders.slice(0, 4).map((h) => (
                <div key={h.person_id} title={`${h.name ?? h.person_id} · ${h.evidence_count} items`}
                  className="w-8 h-8 rounded-full bg-slate-100 border-2 border-white grid place-items-center text-[11px] font-semibold text-slate-600">
                  {initials(h.name)}
                </div>
              ))}
            </div>
            {t.sole_holder ? (
              <span className="shrink-0 inline-flex items-center gap-1 text-[11px] font-semibold text-rose-600 bg-rose-50 px-2.5 py-1 rounded-full">
                <UserRound className="w-3 h-3" /> Sole holder
              </span>
            ) : (
              <span className="shrink-0 text-[11px] font-semibold text-amber-600 bg-amber-50 px-2.5 py-1 rounded-full">
                {t.holder_count} holders
              </span>
            )}
          </div>
        ))}
      </div>
    </AppShell>
  );
}

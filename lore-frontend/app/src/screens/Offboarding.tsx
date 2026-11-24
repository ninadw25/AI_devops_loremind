import { useState } from 'react';
import {
  UserMinus,
  AlertTriangle,
  FileText,
  CheckSquare,
  CalendarClock,
  Building2,
  Info,
} from 'lucide-react';
import AppShell from '@/components/app-shell/AppShell';
import PersonPicker from '@/components/continuity/PersonPicker';
import {
  getOffboardingReport,
  type OffboardingReport,
  type PersonHit,
  type EvidenceItem,
} from '@/services/continuityApi';

// ── helpers ──────────────────────────────────────────────────────────────────

function initials(name?: string | null): string {
  if (!name) return '?';
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? '') + (parts[1]?.[0] ?? '')).toUpperCase() || '?';
}

type OwnedTabKey = 'decisions' | 'tasks' | 'facts' | 'events';

function typeIcon(type: string) {
  const t = type.toLowerCase();
  if (t === 'task') return <CheckSquare className="w-3.5 h-3.5 text-slate-400 mt-0.5 shrink-0" strokeWidth={2} />;
  if (t === 'event') return <CalendarClock className="w-3.5 h-3.5 text-slate-400 mt-0.5 shrink-0" strokeWidth={2} />;
  return <FileText className="w-3.5 h-3.5 text-slate-400 mt-0.5 shrink-0" strokeWidth={2} />;
}

// ── skeleton ─────────────────────────────────────────────────────────────────

function SkeletonCards() {
  return (
    <div className="space-y-4">
      {/* header skeleton */}
      <div className="bg-white border border-slate-200 rounded-2xl p-5 animate-pulse">
        <div className="flex items-center gap-4">
          <div className="w-12 h-12 rounded-full bg-slate-100" />
          <div className="flex-1 space-y-2">
            <div className="h-4 bg-slate-100 rounded w-1/3" />
            <div className="h-3 bg-slate-100 rounded w-1/4" />
          </div>
        </div>
      </div>
      {/* worklist skeleton */}
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="bg-white border border-slate-200 rounded-2xl p-5 animate-pulse space-y-3">
          <div className="h-4 bg-slate-100 rounded w-1/2" />
          <div className="h-3 bg-slate-100 rounded w-full" />
          <div className="h-3 bg-slate-100 rounded w-4/5" />
        </div>
      ))}
    </div>
  );
}

// ── owned content item row ────────────────────────────────────────────────────

function EvidenceRow({ item }: { item: EvidenceItem }) {
  return (
    <div className="flex items-start gap-2.5 py-2.5 border-b border-slate-100 last:border-0">
      {typeIcon(item.type)}
      <div className="min-w-0 flex-1">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 mr-2">
          {item.type}
        </span>
        <span className="text-sm text-slate-700">
          {item.description ?? '(no description)'}
        </span>
      </div>
      {item.thread_id && (
        <span className="shrink-0 text-[10px] text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full font-mono truncate max-w-[120px]">
          {item.thread_id}
        </span>
      )}
    </div>
  );
}

// ── main component ────────────────────────────────────────────────────────────

export default function Offboarding() {
  const [person, setPerson] = useState<PersonHit | null>(null);
  const [report, setReport] = useState<OffboardingReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ownedTab, setOwnedTab] = useState<OwnedTabKey>('decisions');

  const handleSelect = (p: PersonHit) => {
    setPerson(p);
    setReport(null);
    setErr(null);
    setLoading(true);
    getOffboardingReport(p.person_id)
      .then(setReport)
      .catch((e) => setErr(String(e?.message ?? e)))
      .finally(() => setLoading(false));
  };

  const tabs: { key: OwnedTabKey; label: string }[] = [
    { key: 'decisions', label: 'Decisions' },
    { key: 'tasks',     label: 'Tasks' },
    { key: 'facts',     label: 'Facts' },
    { key: 'events',    label: 'Events' },
  ];

  return (
    <AppShell
      title="Offboarding"
      subtitle="Capture what's walking out the door"
    >
      {/* ── Picker row ── */}
      <div className="mb-6">
        <PersonPicker onSelect={handleSelect} placeholder="Search the person who's leaving…" />
      </div>

      {/* ── Error ── */}
      {err && (
        <div className="bg-rose-50 border border-rose-200 text-rose-700 text-sm rounded-xl p-4 mb-5">
          {err}
        </div>
      )}

      {/* ── Empty state ── */}
      {!person && !loading && (
        <div className="flex flex-col items-center justify-center py-24 text-slate-400 gap-3">
          <UserMinus className="w-10 h-10 opacity-30" strokeWidth={1.5} />
          <p className="text-sm">Pick a departing person to generate their handover report.</p>
        </div>
      )}

      {/* ── Loading skeleton ── */}
      {loading && <SkeletonCards />}

      {/* ── Report ── */}
      {!loading && report && (
        <div className="space-y-6">

          {/* ── Header card ── */}
          <div className="bg-white border border-slate-200 rounded-2xl p-5 flex items-start justify-between gap-4">
            <div className="flex items-center gap-4">
              {/* Avatar */}
              <div className="w-12 h-12 rounded-full bg-blue-50 grid place-items-center text-base font-semibold text-blue-700 shrink-0">
                {initials(report.header.name)}
              </div>
              {/* Name + email */}
              <div>
                <div className="text-base font-semibold text-slate-900">
                  {report.header.name ?? person?.person_id}
                </div>
                {report.header.email && (
                  <div className="text-sm text-slate-500">{report.header.email}</div>
                )}
              </div>
            </div>

            {/* Pills + timestamp */}
            <div className="flex flex-col items-end gap-2 shrink-0">
              <div className="flex flex-wrap justify-end gap-2">
                {report.header.is_internal && (
                  <span className="text-[11px] font-semibold text-blue-600 bg-blue-50 px-2.5 py-0.5 rounded-full">
                    internal
                  </span>
                )}
                {report.header.organization && (
                  <span className="inline-flex items-center gap-1 text-[11px] font-medium text-slate-600 bg-slate-100 px-2.5 py-0.5 rounded-full">
                    <Building2 className="w-3 h-3" />
                    {report.header.organization}
                  </span>
                )}
                {report.header.reports_to && (
                  <span className="text-[11px] font-medium text-slate-500 bg-slate-100 px-2.5 py-0.5 rounded-full">
                    Reports to {report.header.reports_to}
                  </span>
                )}
              </div>
              <span className="text-[11px] text-slate-400">
                Generated {new Date(report.generated_at).toLocaleString()}
              </span>
            </div>
          </div>

          {/* ── At-risk section ── */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <AlertTriangle className="w-4 h-4 text-rose-500" strokeWidth={2} />
              <h2 className="text-sm font-semibold text-slate-800">At risk if they leave</h2>
              <span className="text-[11px] font-medium text-rose-600 bg-rose-50 px-2 py-0.5 rounded-full ml-1">
                {report.worklist.length}
              </span>
            </div>

            {report.worklist.length === 0 ? (
              <div className="bg-green-50 border border-green-200 text-green-700 text-sm rounded-2xl p-5 text-center">
                No single-point-of-failure topics for this person. 🎉
              </div>
            ) : (
              <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden divide-y divide-slate-100">
                {report.worklist.map((item, idx) => (
                  <div key={item.topic_id ?? idx} className="flex items-center gap-3 px-4 py-3 hover:bg-slate-50/60 transition-colors">
                    {/* Risk pill */}
                    <div className="shrink-0 w-20 text-center">
                      {item.sole_holder ? (
                        <span className="text-[10px] font-semibold text-rose-600 bg-rose-50 px-2 py-0.5 rounded-full whitespace-nowrap">
                          Sole holder
                        </span>
                      ) : (
                        <span className="text-[10px] font-semibold text-amber-600 bg-amber-50 px-2 py-0.5 rounded-full whitespace-nowrap">
                          {item.holder_count} holders
                        </span>
                      )}
                    </div>

                    {/* Topic name + evidence count */}
                    <div className="flex-1 min-w-0">
                      <span className="text-sm font-medium text-slate-800 truncate block">
                        {item.topic_name ?? item.topic_id ?? 'Unknown topic'}
                      </span>
                      {item.my_evidence > 0 && (
                        <span className="text-[11px] text-slate-400">
                          {item.my_evidence} item{item.my_evidence === 1 ? '' : 's'}
                        </span>
                      )}
                    </div>

                    {/* Suggested inheritors */}
                    <div className="shrink-0 flex items-center gap-1.5">
                      {item.suggested_inheritors.length > 0 ? (
                        <>
                          <span className="text-[10px] text-slate-400">→</span>
                          <div className="flex -space-x-1.5">
                            {item.suggested_inheritors.slice(0, 3).map((h) => (
                              <div
                                key={h.person_id}
                                title={`${h.name ?? h.person_id} · ${h.evidence_count} items`}
                                className="w-6 h-6 rounded-full bg-slate-200 border-2 border-white grid place-items-center text-[9px] font-bold text-slate-600"
                              >
                                {initials(h.name)}
                              </div>
                            ))}
                          </div>
                        </>
                      ) : (
                        <span className="text-[10px] text-rose-400 font-medium">no cover</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* ── What they handle (owned content) ── */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <Info className="w-4 h-4 text-slate-500" strokeWidth={2} />
              <h2 className="text-sm font-semibold text-slate-800">What they handle</h2>
            </div>

            {/* Segmented control */}
            <div className="inline-flex bg-slate-100 rounded-lg p-0.5 gap-0.5 mb-4">
              {tabs.map(({ key, label }) => {
                const count = report.owned[key].length;
                const active = ownedTab === key;
                return (
                  <button
                    key={key}
                    onClick={() => setOwnedTab(key)}
                    className={`px-3 py-1.5 text-xs font-medium rounded-md transition-all ${
                      active
                        ? 'bg-white text-slate-900 shadow-sm'
                        : 'text-slate-500 hover:text-slate-700'
                    }`}
                  >
                    {label}
                    <span className={`ml-1.5 text-[10px] font-semibold ${active ? 'text-blue-600' : 'text-slate-400'}`}>
                      {count}
                    </span>
                  </button>
                );
              })}
            </div>

            {/* Items list */}
            <div className="bg-white border border-slate-200 rounded-2xl px-4 max-h-[420px] overflow-y-auto">
              {report.owned[ownedTab].length === 0 ? (
                <p className="text-sm text-slate-400 text-center py-8">Nothing recorded.</p>
              ) : (
                report.owned[ownedTab].map((item) => (
                  <EvidenceRow key={item.id} item={item} />
                ))
              )}
            </div>
          </div>

        </div>
      )}
    </AppShell>
  );
}

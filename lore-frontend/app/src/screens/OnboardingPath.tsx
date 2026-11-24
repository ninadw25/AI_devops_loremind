import { useState } from 'react';
import {
  GraduationCap,
  Compass,
  GitBranch,
  Users,
  ClipboardList,
  Quote,
  Building2,
} from 'lucide-react';
import AppShell from '@/components/app-shell/AppShell';
import PersonPicker from '@/components/continuity/PersonPicker';
import {
  getOnboardingPath,
  type LearningPath,
  type PersonHit,
  type EvidenceItem,
} from '@/services/continuityApi';

// ── helpers ──────────────────────────────────────────────────────────────────

function initials(name?: string | null): string {
  if (!name) return '?';
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? '') + (parts[1]?.[0] ?? '')).toUpperCase() || '?';
}

// ── skeleton ─────────────────────────────────────────────────────────────────

function SkeletonCards() {
  return (
    <div className="space-y-5">
      <div className="bg-white border border-slate-200 rounded-2xl p-5 animate-pulse">
        <div className="flex items-center gap-4">
          <div className="w-12 h-12 rounded-full bg-slate-100" />
          <div className="flex-1 space-y-2">
            <div className="h-4 bg-slate-100 rounded w-1/3" />
            <div className="h-3 bg-slate-100 rounded w-1/4" />
          </div>
        </div>
      </div>
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="bg-white border border-slate-200 rounded-2xl p-5 animate-pulse space-y-3">
          <div className="h-3 bg-slate-100 rounded w-1/4" />
          <div className="h-4 bg-slate-100 rounded w-3/4" />
          <div className="h-3 bg-slate-100 rounded w-full" />
          <div className="h-3 bg-slate-100 rounded w-2/3" />
        </div>
      ))}
    </div>
  );
}

// ── inherited work row ────────────────────────────────────────────────────────

function InheritedRow({ item }: { item: EvidenceItem }) {
  return (
    <div className="flex items-start gap-3 py-2.5 border-b border-slate-100 last:border-0">
      <div className="w-4 h-4 mt-0.5 shrink-0 rounded border-2 border-slate-300" />
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

// ── section heading helper ────────────────────────────────────────────────────

function SectionHeading({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <span className="text-slate-500">{icon}</span>
      <h2 className="text-sm font-semibold text-slate-800">{label}</h2>
    </div>
  );
}

// ── main component ────────────────────────────────────────────────────────────

export default function OnboardingPath() {
  const [person, setPerson] = useState<PersonHit | null>(null);
  const [path, setPath] = useState<LearningPath | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleSelect = (p: PersonHit) => {
    setPerson(p);
    setPath(null);
    setErr(null);
    setLoading(true);
    getOnboardingPath(p.person_id)
      .then(setPath)
      .catch((e) => setErr(String(e?.message ?? e)))
      .finally(() => setLoading(false));
  };

  return (
    <AppShell
      title="Onboarding Path"
      subtitle="Learn your predecessor's world"
    >
      {/* ── Picker row ── */}
      <div className="mb-6">
        <PersonPicker onSelect={handleSelect} placeholder="Search the person you're replacing…" />
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
          <GraduationCap className="w-10 h-10 opacity-30" strokeWidth={1.5} />
          <p className="text-sm">Pick a predecessor to build a guided path through their work.</p>
        </div>
      )}

      {/* ── Loading skeleton ── */}
      {loading && <SkeletonCards />}

      {/* ── Path ── */}
      {!loading && path && (
        <div className="space-y-7">

          {/* ── Header card ── */}
          <div className="bg-white border border-slate-200 rounded-2xl p-5 flex items-start justify-between gap-4">
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-full bg-blue-50 grid place-items-center text-base font-semibold text-blue-700 shrink-0">
                {initials(path.header.name)}
              </div>
              <div>
                <div className="text-base font-semibold text-slate-900">
                  {path.header.name ?? person?.person_id}
                </div>
                {path.header.email && (
                  <div className="text-sm text-slate-500">{path.header.email}</div>
                )}
                <div className="text-xs text-slate-400 mt-1">
                  Everything below is drawn from{' '}
                  <span className="font-medium text-slate-600">{path.header.name ?? 'this person'}'s</span>{' '}
                  real history.
                </div>
              </div>
            </div>
            <div className="flex flex-col items-end gap-2 shrink-0">
              <div className="flex flex-wrap justify-end gap-2">
                {path.header.is_internal && (
                  <span className="text-[11px] font-semibold text-blue-600 bg-blue-50 px-2.5 py-0.5 rounded-full">
                    internal
                  </span>
                )}
                {path.header.organization && (
                  <span className="inline-flex items-center gap-1 text-[11px] font-medium text-slate-600 bg-slate-100 px-2.5 py-0.5 rounded-full">
                    <Building2 className="w-3 h-3" />
                    {path.header.organization}
                  </span>
                )}
              </div>
              <span className="text-[11px] text-slate-400">
                Generated {new Date(path.generated_at).toLocaleString()}
              </span>
            </div>
          </div>

          {/* ── Focus areas ── */}
          {path.focus_areas.length > 0 && (
            <div>
              <SectionHeading
                icon={<Compass className="w-4 h-4" strokeWidth={2} />}
                label="Focus areas"
              />
              <div className="flex flex-wrap gap-2">
                {path.focus_areas.map((area) => (
                  <span
                    key={area}
                    className="bg-blue-50 text-blue-700 rounded-full px-3 py-1 text-sm font-medium"
                  >
                    {area}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* ── Key decisions ── */}
          {path.key_decisions.length > 0 && (
            <div>
              <SectionHeading
                icon={<GitBranch className="w-4 h-4" strokeWidth={2} />}
                label={`Key decisions & the why (${path.key_decisions.length})`}
              />
              <div className="space-y-3">
                {path.key_decisions.map((kd, idx) => (
                  <div
                    key={kd.id ?? idx}
                    className="bg-white border border-slate-200 rounded-2xl p-4 space-y-2"
                  >
                    {/* Decision statement */}
                    <p className="text-sm font-medium text-slate-800">
                      {kd.description ?? '(no description recorded)'}
                    </p>

                    {/* Topic tags */}
                    {kd.topics.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {kd.topics.map((t) => (
                          <span
                            key={t}
                            className="text-[10px] font-medium text-slate-500 bg-slate-100 px-2 py-0.5 rounded-full"
                          >
                            {t}
                          </span>
                        ))}
                      </div>
                    )}

                    {/* Rationale block-quotes */}
                    {kd.rationale.length > 0 && (
                      <div className="space-y-1.5 pt-1">
                        <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wide">
                          Why:
                        </span>
                        {kd.rationale.map((r, ri) => (
                          <div
                            key={ri}
                            className="flex items-start gap-2 border-l-2 border-slate-200 pl-3"
                          >
                            <Quote className="w-3 h-3 text-slate-300 mt-0.5 shrink-0" strokeWidth={2} />
                            <p className="text-sm italic text-slate-600">{r}</p>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Thread receipt */}
                    {kd.thread_id && (
                      <span className="inline-block text-[10px] text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full font-mono">
                        {kd.thread_id}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── People you'll work with ── */}
          {path.people.length > 0 && (
            <div>
              <SectionHeading
                icon={<Users className="w-4 h-4" strokeWidth={2} />}
                label={`People you'll work with (${path.people.length})`}
              />
              <div className="bg-white border border-slate-200 rounded-2xl divide-y divide-slate-100 overflow-hidden">
                {path.people.map((p) => (
                  <div key={p.person_id} className="flex items-center gap-3 px-4 py-3">
                    <div className="w-9 h-9 rounded-full bg-blue-50 grid place-items-center text-sm font-semibold text-blue-700 shrink-0">
                      {initials(p.name)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-slate-800 truncate">
                        {p.name ?? p.person_id}
                      </div>
                      <div className="text-xs text-slate-400">
                        {p.weight} interaction{p.weight === 1 ? '' : 's'}
                      </div>
                    </div>
                    {p.is_internal && (
                      <span className="shrink-0 text-[10px] font-semibold text-blue-600 bg-blue-50 px-2 py-0.5 rounded-full">
                        internal
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Work you're inheriting ── */}
          <div>
            <SectionHeading
              icon={<ClipboardList className="w-4 h-4" strokeWidth={2} />}
              label={`Work you're inheriting (${path.inherited_work.length})`}
            />
            <div className="bg-white border border-slate-200 rounded-2xl px-4 max-h-[420px] overflow-y-auto">
              {path.inherited_work.length === 0 ? (
                <p className="text-sm text-slate-400 text-center py-8">No open items to inherit.</p>
              ) : (
                path.inherited_work.map((item) => (
                  <InheritedRow key={item.id} item={item} />
                ))
              )}
            </div>
          </div>

        </div>
      )}
    </AppShell>
  );
}

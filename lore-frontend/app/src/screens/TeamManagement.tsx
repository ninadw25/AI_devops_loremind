import { useState, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Search, Mail, AlertTriangle, UserMinus, Check, ChevronDown, Users, Shield,
} from 'lucide-react';
import AppShell from '@/components/app-shell/AppShell';
import PersonAvatar from '@/components/widgets/PersonAvatar';
import AccessBadge from '@/components/widgets/AccessBadge';
import { usePeople } from '@/data/hooks';
import type { TeamId, Person, AccessLevel } from '@/data/companyData';

const teamFilters: { id: 'all' | TeamId; label: string }[] = [
  { id: 'all',        label: 'All' },
  { id: 'leadership', label: 'Leadership' },
  { id: 'operations', label: 'Operations' },
  { id: 'hr',         label: 'HR' },
  { id: 'finance',    label: 'Finance' },
  { id: 'tech',       label: 'Tech' },
  { id: 'sales',      label: 'Sales' },
  { id: 'marketing',  label: 'Marketing' },
];

const teamChipColor: Record<TeamId, string> = {
  leadership: 'bg-indigo-50 text-indigo-700 border-indigo-100',
  operations: 'bg-blue-50 text-blue-700 border-blue-100',
  hr:         'bg-purple-50 text-purple-700 border-purple-100',
  finance:    'bg-emerald-50 text-emerald-700 border-emerald-100',
  tech:       'bg-cyan-50 text-cyan-700 border-cyan-100',
  sales:      'bg-amber-50 text-amber-700 border-amber-100',
  marketing:  'bg-rose-50 text-rose-700 border-rose-100',
  external:   'bg-slate-100 text-slate-600 border-slate-200',
};

const ACCESS_LEVELS: { id: AccessLevel; label: string; desc: string }[] = [
  { id: 'admin',      label: 'Admin',      desc: 'Full workspace control' },
  { id: 'editor',     label: 'Editor',     desc: 'Can edit, approve, and manage content' },
  { id: 'viewer',     label: 'Viewer',     desc: 'Read-only access to the knowledge base' },
  { id: 'restricted', label: 'Restricted', desc: 'Limited, external access only' },
];

// ─── Inline access level dropdown (used in the Access list view) ────────────
function AccessLevelDropdown({
  person, onChange,
}: {
  person: Person;
  onChange: (level: AccessLevel) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 h-9 pl-3 pr-2.5 rounded-lg bg-white border border-slate-200 hover:border-slate-300 transition-colors min-w-[140px]"
      >
        <AccessBadge level={person.accessLevel} size="xs" />
        <ChevronDown className={`w-3 h-3 text-slate-400 ml-auto transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute top-[calc(100%+6px)] right-0 w-[260px] bg-white border border-slate-200 rounded-xl shadow-[0_16px_40px_-8px_rgba(15,23,42,0.22)] py-2 z-30 animate-in fade-in slide-in-from-top-1 duration-150">
          <div className="px-3 py-2 border-b border-slate-100">
            <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Change access level</div>
          </div>
          <div className="py-1">
            {ACCESS_LEVELS.map(lvl => {
              const active = person.accessLevel === lvl.id;
              return (
                <button
                  key={lvl.id}
                  onClick={() => { onChange(lvl.id); setOpen(false); }}
                  className={`w-full flex items-start gap-2.5 px-3 py-2 text-left hover:bg-slate-50 transition-colors ${
                    active ? 'bg-blue-50/40' : ''
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <AccessBadge level={lvl.id} size="xs" />
                      {active && <Check className="w-3 h-3 text-blue-600" />}
                    </div>
                    <div className="text-[10px] text-slate-500 mt-1 leading-snug">{lvl.desc}</div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Directory: person card (badge is read-only here) ──────────────────────
function PersonCard({ person }: { person: Person }) {
  const navigate = useNavigate();
  const isResigning = person.status === 'resigned';

  return (
    <div
      onClick={() => navigate(`/team/${person.id}`)}
      className="group relative bg-white rounded-2xl border border-slate-200/70 p-5 hover:border-slate-300/80 hover:shadow-[0_12px_32px_-16px_rgba(10,63,181,0.18)] transition-all cursor-pointer"
    >
      <div className="absolute top-3 right-3 flex items-center gap-1.5">
        {isResigning && (
          <span className="px-2 py-0.5 bg-red-600 text-white rounded-md text-[9px] font-semibold uppercase tracking-wider flex items-center gap-1">
            <UserMinus className="w-2.5 h-2.5" />
            Resigning
          </span>
        )}
        <AccessBadge level={person.accessLevel} size="xs" />
      </div>

      <div className="flex items-start gap-4 mb-4 pr-4">
        <PersonAvatar person={person} size="lg" ring />
        <div className="flex-1 min-w-0 pt-0.5">
          <h3 className="text-[14px] font-semibold text-slate-900 leading-tight truncate mt-6" style={{ fontFamily: 'var(--font-display)' }}>
            {person.name}
          </h3>
          <p className="text-[11px] text-slate-600 truncate mt-0.5">{person.role}</p>
          <span
            className={`inline-block mt-1.5 px-2 py-0.5 rounded-md text-[9px] font-semibold uppercase tracking-wider border ${teamChipColor[person.team]}`}
          >
            {person.team}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-1.5 text-[11px] text-slate-500 mb-3 truncate">
        <Mail className="w-3 h-3 shrink-0" />
        <span className="truncate">{person.email}</span>
      </div>

      <div className="grid grid-cols-3 gap-2 pt-3 border-t border-slate-100">
        <div>
          <div className="text-[14px] font-semibold text-slate-900 leading-none" style={{ fontFamily: 'var(--font-display)' }}>
            {person.stats.commitsThisMonth}
          </div>
          <div className="text-[9px] text-slate-500 mt-1 uppercase tracking-wider">Commits</div>
        </div>
        <div>
          <div className="text-[14px] font-semibold text-slate-900 leading-none" style={{ fontFamily: 'var(--font-display)' }}>
            {person.ownsServices.length}
          </div>
          <div className="text-[9px] text-slate-500 mt-1 uppercase tracking-wider">Services</div>
        </div>
        <div>
          <div className="text-[14px] font-semibold text-slate-900 leading-none" style={{ fontFamily: 'var(--font-display)' }}>
            {person.stats.decisionsLogged}
          </div>
          <div className="text-[9px] text-slate-500 mt-1 uppercase tracking-wider">Decisions</div>
        </div>
      </div>

      {person.riskLevel === 'critical' && (
        <div className="mt-3 flex items-start gap-1.5 text-[10px] text-red-700 bg-red-50/80 border border-red-100 rounded-md px-2 py-1.5 leading-tight">
          <AlertTriangle className="w-3 h-3 shrink-0 mt-0.5" />
          <span>{person.riskNote}</span>
        </div>
      )}
    </div>
  );
}

// ─── Access list row ────────────────────────────────────────────────────────
function AccessRow({
  person, onChangeAccess,
}: {
  person: Person;
  onChangeAccess: (level: AccessLevel) => void;
}) {
  return (
    <div className="flex items-center gap-4 px-4 py-3 bg-white border border-slate-200/70 rounded-xl hover:border-slate-300/80 transition-colors">
      <PersonAvatar person={person} size="md" ring />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-semibold text-slate-900" style={{ fontFamily: 'var(--font-display)' }}>
            {person.name}
          </span>
          {person.status === 'resigned' && (
            <span className="px-1.5 py-0.5 bg-red-50 text-red-700 border border-red-100 rounded text-[9px] font-semibold uppercase tracking-wider">
              Resigning
            </span>
          )}
        </div>
        <div className="text-[11px] text-slate-500 truncate">{person.role} · {person.email}</div>
      </div>
      <span
        className={`hidden md:inline-block px-2 py-0.5 rounded-md text-[9px] font-semibold uppercase tracking-wider border ${teamChipColor[person.team]}`}
      >
        {person.team}
      </span>
      <AccessLevelDropdown person={person} onChange={onChangeAccess} />
    </div>
  );
}

// ─── Main screen ────────────────────────────────────────────────────────────
export default function TeamManagement() {
  const sourcePeople = usePeople();
  const [people, setPeople] = useState<Person[]>(sourcePeople);

  const [view, setView] = useState<'directory' | 'access'>('directory');
  const [teamFilter, setTeamFilter] = useState<'all' | TeamId>('all');
  const [query, setQuery] = useState('');

  const internal = people.filter(p => p.status !== 'external');
  const filtered = internal.filter(p => {
    if (teamFilter !== 'all' && p.team !== teamFilter) return false;
    if (query && !p.name.toLowerCase().includes(query.toLowerCase())) return false;
    return true;
  });

  const stats = {
    total: internal.length,
    leaving: internal.filter(p => p.status === 'resigned').length,
  };

  const handleChangeAccess = (personId: string, level: AccessLevel) => {
    setPeople(prev => prev.map(p => p.id === personId ? { ...p, accessLevel: level } : p));
  };

  return (
    <AppShell title="Team" subtitle={`${stats.total} people · ${stats.leaving} leaving`}>
      {/* View toggle - Directory vs Access */}
      <div className="flex items-center gap-1 bg-white p-1 rounded-xl border border-slate-200/70 w-fit mb-6">
        <button
          onClick={() => setView('directory')}
          className={`flex items-center gap-2 px-4 py-1.5 rounded-lg text-[12px] font-semibold transition-all ${
            view === 'directory' ? 'bg-slate-900 text-white' : 'text-slate-600 hover:text-slate-900'
          }`}
        >
          <Users className="w-3.5 h-3.5" />
          Directory
        </button>
        <button
          onClick={() => setView('access')}
          className={`flex items-center gap-2 px-4 py-1.5 rounded-lg text-[12px] font-semibold transition-all ${
            view === 'access' ? 'bg-slate-900 text-white' : 'text-slate-600 hover:text-slate-900'
          }`}
        >
          <Shield className="w-3.5 h-3.5" />
          Access
        </button>
      </div>

      {/* Filter bar + search */}
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <div className="flex items-center gap-1 bg-white p-1 rounded-xl border border-slate-200/70">
          {teamFilters.map(f => (
            <button
              key={f.id}
              onClick={() => setTeamFilter(f.id)}
              className={`px-3 py-1.5 rounded-lg text-[11px] font-semibold transition-all ${
                teamFilter === f.id ? 'bg-slate-900 text-white' : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="relative ml-auto w-full sm:w-[260px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-[14px] h-[14px] text-slate-400" />
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search people…"
            className="w-full h-9 pl-9 pr-3 bg-white border border-slate-200/70 rounded-lg text-[12px] text-slate-800 placeholder:text-slate-400 outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500/40 transition-all"
          />
        </div>
      </div>

      {/* ── Directory view: grid of cards ── */}
      {view === 'directory' && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {filtered.map(p => <PersonCard key={p.id} person={p} />)}
          </div>
          {filtered.length === 0 && (
            <div className="py-16 text-center text-slate-400 text-[13px]">
              No people match your filters.
            </div>
          )}
        </>
      )}

      {/* ── Access view: list with per-row dropdowns ── */}
      {view === 'access' && (
        <div className="space-y-2">
          {/* Column header */}
          <div className="hidden md:flex items-center gap-4 px-4 pb-2 text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
            <div className="w-9" />
            <div className="flex-1">Member</div>
            <div className="w-[60px]">Team</div>
            <div className="w-[140px] text-right pr-6">Access level</div>
          </div>

          {filtered.map(p => (
            <AccessRow
              key={p.id}
              person={p}
              onChangeAccess={(level) => handleChangeAccess(p.id, level)}
            />
          ))}

          {filtered.length === 0 && (
            <div className="py-16 text-center text-slate-400 text-[13px]">
              No people match your filters.
            </div>
          )}
        </div>
      )}
    </AppShell>
  );
}

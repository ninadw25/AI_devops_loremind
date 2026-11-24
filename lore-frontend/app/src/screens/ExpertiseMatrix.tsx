import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { X, ArrowRight, AlertTriangle, Search } from 'lucide-react';
import AppShell from '@/components/app-shell/AppShell';
import ChartCard from '@/components/charts/ChartCard';
import PersonAvatar from '@/components/widgets/PersonAvatar';
import AccessBadge from '@/components/widgets/AccessBadge';
import ExpertiseRadar from '@/components/widgets/ExpertiseRadar';
import { useExpertiseMatrix, useProjectsByPerson } from '@/data/hooks';
import type { Person, TeamId } from '@/data/companyData';

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

const categoryColor: Record<string, string> = {
  backend:  '#0a3fb5',
  frontend: '#7c3aed',
  devops:   '#059669',
  product:  '#d97706',
  sales:    '#ea580c',
  security: '#dc2626',
};

// Score → cell style using the area's category color
function cellStyle(score: number, baseColor: string): { bg: string; text: string } {
  if (score === 0) return { bg: '#f8fafc', text: '#cbd5e1' };
  const a = Math.min(1, 0.18 + score / 130);
  // Convert hex to rgb for opacity
  const r = parseInt(baseColor.slice(1, 3), 16);
  const g = parseInt(baseColor.slice(3, 5), 16);
  const b = parseInt(baseColor.slice(5, 7), 16);
  return {
    bg: `rgba(${r}, ${g}, ${b}, ${a})`,
    text: score > 55 ? '#fff' : '#0f172a',
  };
}

function PersonDrawer({ person, onClose }: { person: Person; onClose: () => void }) {
  const navigate = useNavigate();
  const projects = useProjectsByPerson(person.id);
  const topAreas = Object.entries(person.expertise).sort(([, a], [, b]) => b - a).slice(0, 5);

  return (
    <>
      <div
        className="fixed inset-0 bg-slate-900/20 backdrop-blur-[2px] z-40 animate-in fade-in duration-200"
        onClick={onClose}
      />
      <aside className="fixed top-0 right-0 bottom-0 w-[480px] bg-white border-l border-slate-200 z-50 shadow-2xl overflow-y-auto animate-in slide-in-from-right duration-200">
        <div className="sticky top-0 bg-white/90 backdrop-blur-md border-b border-slate-200 px-6 py-4 flex items-center justify-between z-10">
          <h3 className="text-[13px] font-semibold text-slate-600 uppercase tracking-wider">Profile</h3>
          <button onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-100 flex items-center justify-center text-slate-500">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="px-6 py-6">
          <div className="flex items-start gap-4 mb-5">
            <PersonAvatar person={person} size="xl" ring />
            <div className="flex-1 min-w-0 pt-1">
              <div className="flex items-center gap-2 flex-wrap">
                <h2 className="text-[20px] font-semibold text-slate-900 leading-tight" style={{ fontFamily: 'var(--font-display)' }}>
                  {person.name}
                </h2>
                <AccessBadge level={person.accessLevel} status={person.status} size="xs" />
              </div>
              <div className="text-[12px] text-slate-600 mt-0.5">{person.role}</div>
              <div className="text-[11px] text-slate-400 mt-0.5">{person.email}</div>
              {person.status === 'resigned' && (
                <div className="mt-2 inline-flex items-center gap-1.5 px-2 py-1 bg-red-50 text-red-700 rounded-md text-[10px] font-semibold uppercase tracking-wider">
                  <AlertTriangle className="w-3 h-3" />
                  Resigning · Last day {person.lastDay}
                </div>
              )}
            </div>
          </div>

          {person.riskNote && (
            <div className="mb-5 px-4 py-3 bg-amber-50/80 border border-amber-200/70 rounded-xl text-[12px] text-amber-900 leading-relaxed">
              <div className="font-semibold mb-1 flex items-center gap-1.5 text-amber-800 uppercase tracking-wider text-[10px]">
                <AlertTriangle className="w-3 h-3" /> Risk note
              </div>
              {person.riskNote}
            </div>
          )}

          <div className="grid grid-cols-4 gap-2 mb-5">
            {[
              { label: 'Commits', value: person.stats.commitsThisMonth },
              { label: 'Decisions', value: person.stats.decisionsLogged },
              { label: 'Docs', value: person.stats.docsWritten },
              { label: 'Messages', value: person.stats.messagesSent },
            ].map(s => (
              <div key={s.label} className="rounded-xl bg-slate-50 px-2 py-2.5 text-center">
                <div className="text-[16px] font-semibold text-slate-900" style={{ fontFamily: 'var(--font-display)' }}>
                  {s.value}
                </div>
                <div className="text-[9px] text-slate-500 uppercase tracking-wider mt-0.5">{s.label}</div>
              </div>
            ))}
          </div>

          <div className="mb-5">
            <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-2">Top expertise</div>
            <ExpertiseRadar person={person} color={person.avatarColor} />
            <div className="mt-3 space-y-1.5">
              {topAreas.map(([areaId, score]) => (
                <div key={areaId} className="flex items-center gap-2">
                  <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                    <div className="h-full rounded-full" style={{ width: `${score}%`, backgroundColor: person.avatarColor }} />
                  </div>
                  <span className="text-[11px] font-semibold text-slate-700 tabular-nums w-8 text-right">{score}</span>
                </div>
              ))}
            </div>
          </div>

          {person.ownsServices.length > 0 && (
            <div className="mb-5">
              <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-2">Owned services</div>
              <div className="flex flex-wrap gap-1.5">
                {person.ownsServices.map(s => (
                  <span key={s} className="px-2.5 py-1 bg-blue-50 text-blue-700 rounded-md text-[11px] font-medium capitalize">
                    {s.replace(/-/g, ' ')}
                  </span>
                ))}
              </div>
            </div>
          )}

          {projects.length > 0 && (
            <div className="mb-5">
              <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-2">
                Active projects ({projects.length})
              </div>
              <div className="space-y-1.5">
                {projects.slice(0, 4).map(p => (
                  <div key={p.id} className="flex items-center justify-between px-3 py-2 bg-slate-50 rounded-lg">
                    <span className="text-[12px] font-medium text-slate-800 truncate">{p.name}</span>
                    <span className="text-[10px] text-slate-500 tabular-nums">{p.progress}%</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <button
            onClick={() => navigate(`/team/${person.id}`)}
            className="w-full flex items-center justify-center gap-2 h-10 bg-slate-900 text-white rounded-lg text-[12px] font-semibold hover:bg-slate-800 transition-colors"
          >
            View full profile <ArrowRight className="w-4 h-4" />
          </button>
        </div>
      </aside>
    </>
  );
}

// ─── Heatmap grid (CSS grid, fills full width) ────────────────────────────
interface ExpertiseGridProps {
  cols: ReturnType<typeof useExpertiseMatrix>['cols'];
  people: Person[];
  onSelect: (p: Person) => void;
  hoveredCol: string | null;
  hoveredRow: string | null;
  setHoveredCol: (id: string | null) => void;
  setHoveredRow: (id: string | null) => void;
}

function ExpertiseGrid({
  cols, people, onSelect, hoveredCol, hoveredRow, setHoveredCol, setHoveredRow,
}: ExpertiseGridProps) {
  // Use a CSS grid to fill the full container width:
  // - first column = 220px label
  // - remaining = N equal-width person columns
  const gridTemplate = `220px repeat(${people.length}, minmax(56px, 1fr))`;

  return (
    <div className="overflow-x-auto -mx-1 px-1">
    <div
      className="grid"
      style={{ gridTemplateColumns: gridTemplate, columnGap: '6px', rowGap: '6px', minWidth: `${220 + people.length * 62}px` }}
    >
      {/* Top-left corner cell */}
      <div />

      {/* Person headers */}
      {people.map(person => {
        const isHovered = hoveredCol === person.id;
        return (
          <button
            key={`head-${person.id}`}
            onClick={() => onSelect(person)}
            onMouseEnter={() => setHoveredCol(person.id)}
            onMouseLeave={() => setHoveredCol(null)}
            className="group flex flex-col items-center gap-1.5 pb-3 cursor-pointer transition-colors"
            title={`${person.name} - ${person.role}`}
          >
            <div className="relative">
              <PersonAvatar person={person} size="md" ring />
              {person.riskLevel === 'critical' && (
                <span className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-red-500 ring-2 ring-white animate-pulse" />
              )}
            </div>
            <span
              className={`text-[9px] font-semibold whitespace-nowrap max-w-[80px] truncate transition-colors ${
                isHovered ? 'text-slate-900' : 'text-slate-600'
              }`}
            >
              {person.name.split(' ')[0]}
            </span>
          </button>
        );
      })}

      {/* Body rows: per area */}
      {cols.map(area => {
        const cat = categoryColor[area.category] || '#0a3fb5';
        const isRowHovered = hoveredRow === area.id;
        return (
          <RowGroup
            key={area.id}
            area={area}
            cat={cat}
            people={people}
            onSelect={onSelect}
            isRowHovered={isRowHovered}
            hoveredCol={hoveredCol}
            setHoveredCol={setHoveredCol}
            setHoveredRow={setHoveredRow}
          />
        );
      })}
    </div>
    </div>
  );
}

// One row = one expertise area + 13 cells
interface RowGroupProps {
  area: ReturnType<typeof useExpertiseMatrix>['cols'][number];
  cat: string;
  people: Person[];
  onSelect: (p: Person) => void;
  isRowHovered: boolean;
  hoveredCol: string | null;
  setHoveredCol: (id: string | null) => void;
  setHoveredRow: (id: string | null) => void;
}

function RowGroup({
  area, cat, people, onSelect, isRowHovered, hoveredCol, setHoveredCol, setHoveredRow,
}: RowGroupProps) {
  return (
    <>
      {/* Row label */}
      <div
        onMouseEnter={() => setHoveredRow(area.id)}
        onMouseLeave={() => setHoveredRow(null)}
        className={`flex items-center gap-2.5 pr-4 py-1 rounded-l-lg transition-colors ${
          isRowHovered ? 'bg-slate-50' : ''
        }`}
      >
        <span
          className="w-1 rounded-full shrink-0 self-stretch my-1"
          style={{ backgroundColor: cat }}
        />
        <div className="min-w-0">
          <div className={`text-[12px] font-semibold leading-tight whitespace-nowrap transition-colors ${
            isRowHovered ? 'text-slate-900' : 'text-slate-800'
          }`}>
            {area.name}
          </div>
          <div className="text-[9px] text-slate-400 uppercase tracking-wider mt-0.5">
            {area.category}
          </div>
        </div>
      </div>

      {/* Score cells */}
      {people.map(person => {
        const score = person.expertise[area.id] || 0;
        const style = cellStyle(score, cat);
        const colHovered = hoveredCol === person.id;
        const isHighlighted = colHovered || isRowHovered;
        return (
          <button
            key={`${area.id}-${person.id}`}
            onClick={() => onSelect(person)}
            onMouseEnter={() => { setHoveredCol(person.id); setHoveredRow(area.id); }}
            onMouseLeave={() => { setHoveredCol(null); setHoveredRow(null); }}
            className={`relative h-11 rounded-lg flex items-center justify-center text-[11px] font-bold transition-colors overflow-hidden ${
              score === 0 ? '' : 'shadow-[inset_0_-1px_2px_rgba(255,255,255,0.25),inset_0_1px_1px_rgba(0,0,0,0.05)]'
            } ${isHighlighted && score === 0 ? 'bg-slate-100' : ''}`}
            style={{
              backgroundColor: isHighlighted && score === 0 ? '#f1f5f9' : style.bg,
              color: style.text,
              boxShadow: isHighlighted && score > 0 ? `0 4px 12px -4px ${cat}66` : undefined,
            }}
            title={`${person.name} · ${area.name} · ${score}`}
          >
            {score > 0 ? score : (isHighlighted ? '·' : '')}
          </button>
        );
      })}
    </>
  );
}

export default function ExpertiseMatrix() {
  const { rows, cols } = useExpertiseMatrix();
  const [selected, setSelected] = useState<Person | null>(null);
  const [teamFilter, setTeamFilter] = useState<'all' | TeamId>('all');
  const [query, setQuery] = useState('');
  const [hoveredCol, setHoveredCol] = useState<string | null>(null);
  const [hoveredRow, setHoveredRow] = useState<string | null>(null);

  // Filter people (these become COLUMNS in the new layout)
  const filteredPeople = rows.filter(p => {
    if (teamFilter !== 'all' && p.team !== teamFilter) return false;
    if (query && !p.name.toLowerCase().includes(query.toLowerCase())) return false;
    return true;
  });

  // When a specific team is selected, drop expertise areas where no filtered
  // person has any score - keeps the matrix focused on relevant skills only.
  const filteredCols = teamFilter === 'all'
    ? cols
    : cols.filter(area => filteredPeople.some(p => (p.expertise[area.id] || 0) > 0));

  const criticalGaps = rows.filter(p => p.riskLevel === 'critical').length;

  return (
    <AppShell title="Expertise Matrix" subtitle="Who knows what · click a person to see their profile">
      {/* Filter bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
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

        <div className="relative flex-1 min-w-[220px] max-w-[320px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-[14px] h-[14px] text-slate-400" />
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search people…"
            className="w-full h-9 pl-9 pr-3 bg-white border border-slate-200/70 rounded-lg text-[12px] text-slate-800 placeholder:text-slate-400 outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500/40 transition-all"
          />
        </div>

        {criticalGaps > 0 && (
          <div className="flex items-center gap-1.5 px-3 py-1.5 bg-red-50 text-red-700 rounded-lg text-[11px] font-semibold">
            <AlertTriangle className="w-3 h-3" />
            {criticalGaps} critical knowledge gap{criticalGaps > 1 ? 's' : ''}
          </div>
        )}
      </div>

      {/* Pivoted matrix: rows = expertise areas, columns = people - fills full width */}
      <ChartCard
        title="Skill coverage map"
        subtitle={`${filteredCols.length} expertise areas × ${filteredPeople.length} people`}
        action={
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 justify-end max-w-[420px]">
            {Object.entries(categoryColor).map(([cat, color]) => (
              <div key={cat} className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-sm" style={{ backgroundColor: color }} />
                <span className="text-[10px] text-slate-600 capitalize">{cat}</span>
              </div>
            ))}
          </div>
        }
      >
        <ExpertiseGrid
          cols={filteredCols}
          people={filteredPeople}
          onSelect={setSelected}
          hoveredCol={hoveredCol}
          hoveredRow={hoveredRow}
          setHoveredCol={setHoveredCol}
          setHoveredRow={setHoveredRow}
        />

        {/* Score scale legend - kept at bottom */}
        <div className="flex items-center justify-end gap-2 mt-5 pt-5 border-t border-slate-100">
          <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Score</span>
          <div className="flex items-center gap-0.5">
            {[0.18, 0.35, 0.52, 0.7, 0.9].map((a, i) => (
              <span
                key={i}
                className="w-5 h-2.5 rounded-sm"
                style={{ background: `rgba(10, 63, 181, ${a})` }}
              />
            ))}
          </div>
          <span className="text-[10px] text-slate-500 tabular-nums">0 → 100</span>
        </div>
      </ChartCard>

      {selected && <PersonDrawer person={selected} onClose={() => setSelected(null)} />}
    </AppShell>
  );
}

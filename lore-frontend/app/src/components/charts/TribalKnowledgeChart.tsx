import { AlertTriangle } from 'lucide-react';
import { useKnowledgeConcentration } from '@/data/hooks';

const teamColor: Record<string, string> = {
  Backend:    '#0a3fb5',
  Frontend:   '#7c3aed',
  Devops:     '#059669',
  Sales:      '#ea580c',
  Leadership: '#1d4ed8',
};

function severityFor(c: number): { label: string; bg: string; text: string } {
  if (c >= 75) return { label: 'Critical', bg: 'bg-red-50',     text: 'text-red-700' };
  if (c >= 55) return { label: 'High',     bg: 'bg-amber-50',   text: 'text-amber-700' };
  if (c >= 35) return { label: 'Medium',   bg: 'bg-blue-50',    text: 'text-blue-700' };
  return                { label: 'Healthy', bg: 'bg-emerald-50', text: 'text-emerald-700' };
}

export default function TribalKnowledgeChart() {
  const data = useKnowledgeConcentration().filter(d => d.members > 0);
  const sorted = [...data].sort((a, b) => b.concentration - a.concentration);
  const critical = sorted.filter(d => d.concentration >= 75).length;

  return (
    <div>
      {/* Headline metric */}
      <div className="flex items-end justify-between mb-5 pb-4 border-b border-slate-100">
        <div>
          <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1">
            Teams at single-owner risk
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-[32px] font-semibold text-slate-900 leading-none tabular-nums" style={{ fontFamily: 'var(--font-display)' }}>
              {critical}
            </span>
            <span className="text-[12px] text-slate-500">of {sorted.length}</span>
          </div>
        </div>
        {critical > 0 && (
          <div className="flex items-center gap-1.5 px-2.5 py-1 bg-red-50 text-red-700 rounded-md text-[10px] font-semibold uppercase tracking-wider">
            <AlertTriangle className="w-3 h-3" /> Action needed
          </div>
        )}
      </div>

      {/* Bars */}
      <div className="space-y-4">
        {sorted.map(team => {
          const sev = severityFor(team.concentration);
          const color = teamColor[team.label] || '#0a3fb5';
          // Threshold marker line at 75% (critical zone)
          return (
            <div key={team.team}>
              <div className="flex items-center justify-between mb-1.5">
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
                  <span className="text-[12px] font-semibold text-slate-900" style={{ fontFamily: 'var(--font-display)' }}>
                    {team.label}
                  </span>
                  <span className="text-[10px] text-slate-400">· {team.members} {team.members === 1 ? 'person' : 'people'}</span>
                  {team.leadName && team.concentration >= 55 && (
                    <span className="text-[10px] text-slate-500">
                      · led by {team.leadName.split(' ')[0]}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <span className={`px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase tracking-wider ${sev.bg} ${sev.text}`}>
                    {sev.label}
                  </span>
                  <span className="text-[12px] font-semibold text-slate-900 tabular-nums w-10 text-right" style={{ fontFamily: 'var(--font-display)' }}>
                    {team.concentration}%
                  </span>
                </div>
              </div>

              <div className="relative h-3 bg-slate-100 rounded-full overflow-visible">
                {/* Bar fill */}
                <div
                  className="absolute inset-y-0 left-0 rounded-full transition-all"
                  style={{
                    width: `${team.concentration}%`,
                    background: `linear-gradient(90deg, ${color}cc, ${color})`,
                    boxShadow: `0 1px 4px ${color}55`,
                  }}
                />
                {/* Critical threshold marker at 75% */}
                <div
                  className="absolute top-[-2px] bottom-[-2px] w-px bg-red-400"
                  style={{ left: '75%' }}
                  title="Critical threshold (75%)"
                >
                  <span className="absolute -top-3 -translate-x-1/2 text-[8px] font-semibold text-red-500 uppercase tracking-wider whitespace-nowrap">
                    Risk
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-5 pt-4 border-t border-slate-100 text-[10px] text-slate-500 leading-relaxed">
        <strong className="text-slate-700">Tribal knowledge concentration</strong> - share of total team contribution coming from one person.
        Computed across commits, decisions and message volume from every linked tool. Above 75% means the team is one resignation away from a knowledge gap.
      </div>
    </div>
  );
}

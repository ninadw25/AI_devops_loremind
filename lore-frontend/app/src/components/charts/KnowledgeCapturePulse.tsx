import { CheckCircle2, AlertTriangle, Link2, Sparkles } from 'lucide-react';

// Day-by-day extraction summary - last 7 days
const dailyCaptures = [
  { date: 'Today',     short: 'Wed Jan 22', total: 47, decisions: 5, captures: 32, risks: 4, links: 6 },
  { date: 'Yesterday', short: 'Tue Jan 21', total: 38, decisions: 3, captures: 28, risks: 2, links: 5 },
  { date: 'Mon',       short: 'Mon Jan 20', total: 29, decisions: 4, captures: 21, risks: 1, links: 3 },
  { date: 'Sun',       short: 'Sun Jan 19', total: 12, decisions: 1, captures: 9,  risks: 0, links: 2 },
  { date: 'Sat',       short: 'Sat Jan 18', total: 8,  decisions: 0, captures: 7,  risks: 0, links: 1 },
  { date: 'Fri',       short: 'Fri Jan 17', total: 41, decisions: 6, captures: 28, risks: 1, links: 6 },
  { date: 'Thu',       short: 'Thu Jan 16', total: 35, decisions: 4, captures: 24, risks: 2, links: 5 },
];

const types = [
  { key: 'decisions' as const, icon: CheckCircle2,  color: '#0a3fb5', label: 'Decisions' },
  { key: 'captures'  as const, icon: Sparkles,      color: '#7c3aed', label: 'Captures' },
  { key: 'risks'     as const, icon: AlertTriangle, color: '#dc2626', label: 'Risks' },
  { key: 'links'     as const, icon: Link2,         color: '#059669', label: 'Linked' },
];

export default function KnowledgeCapturePulse() {
  const maxTotal = Math.max(...dailyCaptures.map(d => d.total));
  const weekTotal = dailyCaptures.reduce((a, b) => a + b.total, 0);

  return (
    <div>
      {/* Headline */}
      <div className="flex items-end justify-between mb-4 pb-4 border-b border-slate-100">
        <div>
          <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1">
            Captured this week
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-[28px] font-semibold text-slate-900 leading-none tabular-nums" style={{ fontFamily: 'var(--font-display)' }}>
              {weekTotal}
            </span>
            <span className="text-[12px] text-slate-500">events</span>
          </div>
        </div>
        <div className="flex gap-3 text-[10px]">
          {types.map(t => (
            <div key={t.key} className="flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: t.color }} />
              <span className="text-slate-500">{t.label}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Day rows */}
      <div className="space-y-3">
        {dailyCaptures.map(day => {
          const widthPct = (day.total / maxTotal) * 100;
          return (
            <div key={day.date} className="group">
              <div className="flex items-center justify-between mb-1.5">
                <div className="flex items-center gap-2">
                  <span className={`text-[12px] font-semibold w-20 shrink-0 ${day.date === 'Today' ? 'text-blue-700' : 'text-slate-700'}`}
                    style={{ fontFamily: 'var(--font-display)' }}
                  >
                    {day.date}
                  </span>
                  <span className="text-[10px] text-slate-400 hidden sm:inline">{day.short}</span>
                </div>
                <span className="text-[12px] font-semibold text-slate-900 tabular-nums" style={{ fontFamily: 'var(--font-display)' }}>
                  {day.total}
                </span>
              </div>

              {/* Stacked bar */}
              <div
                className="flex h-2 bg-slate-100 rounded-full overflow-hidden"
                style={{ width: `${widthPct}%` }}
              >
                {types.map(t => {
                  const value = day[t.key];
                  if (value === 0) return null;
                  const segPct = (value / day.total) * 100;
                  return (
                    <div
                      key={t.key}
                      className="h-full"
                      style={{
                        width: `${segPct}%`,
                        backgroundColor: t.color,
                      }}
                      title={`${t.label}: ${value}`}
                    />
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-4 pt-4 border-t border-slate-100 text-[10px] text-slate-500 leading-relaxed">
        Daily extraction count from all linked sources - Slack, GitHub, Gmail, Jira, Fireflies.
      </div>
    </div>
  );
}

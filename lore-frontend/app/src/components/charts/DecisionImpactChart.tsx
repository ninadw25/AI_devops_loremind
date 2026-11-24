import { PieChart, Pie, Cell, ResponsiveContainer } from 'recharts';
import { useDecisions } from '@/data/hooks';

const impactConfig = [
  { key: 'high',   label: 'High impact',   color: '#0a3fb5' },
  { key: 'medium', label: 'Medium impact', color: '#7c3aed' },
  { key: 'low',    label: 'Low impact',    color: '#94a3b8' },
] as const;

export default function DecisionImpactChart() {
  const decisions = useDecisions();
  const data = impactConfig.map(c => ({
    name: c.label,
    value: decisions.filter(d => d.impact === c.key).length,
    color: c.color,
  }));
  const total = data.reduce((a, b) => a + b.value, 0);

  return (
    <div className="flex items-center gap-6">
      <div className="relative w-[140px] h-[140px] shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              innerRadius={46}
              outerRadius={68}
              paddingAngle={3}
              stroke="none"
              startAngle={90}
              endAngle={-270}
            >
              {data.map((d, i) => <Cell key={i} fill={d.color} />)}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          <div className="text-[22px] font-semibold text-slate-900 leading-none" style={{ fontFamily: 'var(--font-display)' }}>
            {total}
          </div>
          <div className="text-[10px] text-slate-400 uppercase tracking-wider mt-1">Total</div>
        </div>
      </div>

      <div className="flex-1 space-y-2.5 min-w-0">
        {data.map(d => {
          const pct = total ? Math.round((d.value / total) * 100) : 0;
          return (
            <div key={d.name}>
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-1.5 min-w-0">
                  <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: d.color }} />
                  <span className="text-[11px] font-medium text-slate-700 truncate">{d.name}</span>
                </div>
                <span className="text-[11px] font-semibold text-slate-900 tabular-nums shrink-0">{d.value}</span>
              </div>
              <div className="h-1 bg-slate-100 rounded-full overflow-hidden">
                <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: d.color }} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

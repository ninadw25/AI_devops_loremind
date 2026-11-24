import { PieChart, Pie, Cell, ResponsiveContainer } from 'recharts';
import { useKnowledgeSources } from '@/data/hooks';

export default function KnowledgeSourcesDonut() {
  const data = useKnowledgeSources();
  const total = data.reduce((a, b) => a + b.value, 0);

  return (
    <div className="flex items-center gap-6">
      <div className="relative w-[160px] h-[160px] shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              innerRadius={52}
              outerRadius={78}
              paddingAngle={2}
              stroke="none"
              startAngle={90}
              endAngle={-270}
            >
              {data.map((d, i) => <Cell key={i} fill={d.color} />)}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          <div className="text-[24px] font-semibold text-slate-900 leading-none" style={{ fontFamily: 'var(--font-display)' }}>
            {total}
          </div>
          <div className="text-[10px] text-slate-400 uppercase tracking-wider mt-1">Sources</div>
        </div>
      </div>

      <div className="flex-1 space-y-2 min-w-0">
        {data.map(d => (
          <div key={d.name} className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: d.color }} />
            <span className="text-[12px] font-medium text-slate-700 flex-1 truncate">{d.name}</span>
            <span className="text-[12px] font-semibold text-slate-900 tabular-nums">{d.value}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

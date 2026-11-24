import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, CartesianGrid } from 'recharts';
import { useServices } from '@/data/hooks';

const buckets = [
  { range: '1',   key: 1, color: '#dc2626', label: 'Critical (bus factor 1)' },
  { range: '2',   key: 2, color: '#d97706', label: 'At risk (bus factor 2)' },
  { range: '3',   key: 3, color: '#0a3fb5', label: 'Healthy (bus factor 3)' },
  { range: '4+',  key: 4, color: '#059669', label: 'Resilient (bus factor 4+)' },
];

export default function BusFactorChart() {
  const services = useServices();
  const data = buckets.map(b => ({
    range: b.range,
    color: b.color,
    label: b.label,
    count: services.filter(s => (b.key === 4 ? s.busFactor >= 4 : s.busFactor === b.key)).length,
  }));

  return (
    <div>
      <div className="h-[160px] -mx-2">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 10, right: 10, left: -20, bottom: 0 }} barCategoryGap="35%">
            <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis
              dataKey="range"
              tick={{ fontSize: 11, fill: '#94a3b8', fontWeight: 600 }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tick={{ fontSize: 11, fill: '#94a3b8' }}
              axisLine={false}
              tickLine={false}
              width={30}
              allowDecimals={false}
            />
            <Tooltip
              contentStyle={{
                background: 'white',
                border: '1px solid #e2e8f0',
                borderRadius: '12px',
                boxShadow: '0 8px 24px rgba(0,0,0,0.08)',
                fontSize: '12px',
              }}
              cursor={{ fill: 'rgba(148,163,184,0.08)' }}
              formatter={(value: number, _name, props) => [`${value} services`, props.payload.label]}
            />
            <Bar dataKey="count" radius={[6, 6, 0, 0]}>
              {data.map((d, i) => <Cell key={i} fill={d.color} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 mt-4 pt-4 border-t border-slate-100">
        {buckets.map(b => (
          <div key={b.range} className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-sm shrink-0" style={{ backgroundColor: b.color }} />
            <span className="text-[10px] text-slate-600 truncate">{b.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

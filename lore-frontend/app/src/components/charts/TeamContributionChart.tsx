import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts';
import { useTeamContribution } from '@/data/hooks';

const teams = [
  { key: 'operations', label: 'Operations', color: '#0a3fb5' },
  { key: 'sales',      label: 'Sales',      color: '#ea580c' },
  { key: 'tech',       label: 'Tech',       color: '#059669' },
];

export default function TeamContributionChart() {
  const data = useTeamContribution();

  return (
    <div>
      <div className="h-[240px] -mx-2">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 10, right: 10, left: -20, bottom: 0 }} barCategoryGap="25%">
            <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis dataKey="week" tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} width={30} />
            <Tooltip
              contentStyle={{
                background: 'white',
                border: '1px solid #e2e8f0',
                borderRadius: '12px',
                boxShadow: '0 8px 24px rgba(0,0,0,0.08)',
                fontSize: '12px',
              }}
              cursor={{ fill: 'rgba(148,163,184,0.08)' }}
            />
            {teams.map((t, i) => (
              <Bar
                key={t.key}
                dataKey={t.key}
                stackId="teams"
                fill={t.color}
                radius={i === teams.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="flex gap-5 mt-4 pt-4 border-t border-slate-100">
        {teams.map(t => (
          <div key={t.key} className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: t.color }} />
            <span className="text-[11px] text-slate-600 font-medium">{t.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

import {
  RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, ResponsiveContainer,
} from 'recharts';
import type { Person } from '@/data/companyData';
import { expertiseAreas } from '@/data/companyData';

interface ExpertiseRadarProps {
  person: Person;
  color?: string;
}

export default function ExpertiseRadar({ person, color = '#0a3fb5' }: ExpertiseRadarProps) {
  // Pick top 8 expertise areas for this person
  const data = expertiseAreas
    .map(a => ({ area: a.name.split(' ')[0], full: a.name, value: person.expertise[a.id] || 0 }))
    .filter(d => d.value > 0)
    .sort((a, b) => b.value - a.value)
    .slice(0, 8);

  if (data.length === 0) {
    return <div className="text-[11px] text-slate-400 italic">No expertise data</div>;
  }

  return (
    <div className="h-[240px]">
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart data={data} margin={{ top: 10, right: 30, left: 30, bottom: 10 }}>
          <PolarGrid stroke="#e2e8f0" />
          <PolarAngleAxis
            dataKey="area"
            tick={{ fontSize: 10, fill: '#64748b' }}
          />
          <PolarRadiusAxis angle={90} domain={[0, 100]} tick={false} axisLine={false} />
          <Radar
            dataKey="value"
            stroke={color}
            fill={color}
            fillOpacity={0.25}
            strokeWidth={2}
          />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
}

import { useTeamVelocityDocs } from '@/data/hooks';

const teamColor: Record<string, string> = {
  Backend:  '#0a3fb5',
  Frontend: '#7c3aed',
  Devops:   '#059669',
};

export default function VelocityDocScatter() {
  const data = useTeamVelocityDocs();
  const maxBar = Math.max(...data.map(d => Math.max(d.velocity, d.docs * 4)));

  return (
    <div className="space-y-5 pt-1">
      {data.map(team => {
        const color = teamColor[team.label] || '#0a3fb5';
        const velocityPct = (team.velocity / maxBar) * 100;
        const docsPct = (team.docs * 4 / maxBar) * 100;

        return (
          <div key={team.team}>
            <div className="flex items-center justify-between mb-2">
              <span className="text-[12px] font-semibold text-slate-900" style={{ fontFamily: 'var(--font-display)' }}>
                {team.label}
              </span>
              <span className="text-[10px] text-slate-400">{team.members} {team.members === 1 ? 'person' : 'people'}</span>
            </div>

            {/* Commits row */}
            <div className="flex items-center gap-3 mb-1.5">
              <span className="text-[10px] text-slate-500 w-16 shrink-0">Commits</span>
              <div className="flex-1 h-2 bg-slate-100 rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${velocityPct}%`,
                    background: color,
                  }}
                />
              </div>
              <span className="text-[12px] font-semibold text-slate-900 tabular-nums w-8 text-right" style={{ fontFamily: 'var(--font-display)' }}>
                {team.velocity}
              </span>
            </div>

            {/* Docs row */}
            <div className="flex items-center gap-3">
              <span className="text-[10px] text-slate-500 w-16 shrink-0">Docs</span>
              <div className="flex-1 h-2 bg-slate-100 rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${docsPct}%`,
                    background: `${color}88`,
                  }}
                />
              </div>
              <span className="text-[12px] font-semibold text-slate-900 tabular-nums w-8 text-right" style={{ fontFamily: 'var(--font-display)' }}>
                {team.docs}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

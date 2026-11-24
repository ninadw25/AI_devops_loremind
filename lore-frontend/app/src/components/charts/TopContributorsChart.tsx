import { useInternalPeople } from '@/data/hooks';
import PersonAvatar from '../widgets/PersonAvatar';

export default function TopContributorsChart() {
  const all = useInternalPeople();
  const top = [...all]
    .filter(p => p.stats.commitsThisMonth > 0)
    .sort((a, b) => b.stats.commitsThisMonth - a.stats.commitsThisMonth)
    .slice(0, 6);

  const max = Math.max(...top.map(p => p.stats.commitsThisMonth));

  return (
    <div className="space-y-3">
      {top.map((p, i) => {
        const pct = (p.stats.commitsThisMonth / max) * 100;
        return (
          <div key={p.id} className="flex items-center gap-3 group">
            <span className="text-[10px] font-semibold text-slate-400 tabular-nums w-3 text-right">
              {i + 1}
            </span>
            <PersonAvatar person={p} size="xs" />
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between mb-1">
                <span className="text-[11px] font-semibold text-slate-800 truncate">
                  {p.name.split(' ')[0]} {p.name.split(' ')[1]?.[0]}.
                </span>
                <span className="text-[11px] font-semibold text-slate-700 tabular-nums">
                  {p.stats.commitsThisMonth}
                </span>
              </div>
              <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${pct}%`,
                    background: `linear-gradient(90deg, ${p.avatarColor}, ${p.avatarColor}dd)`,
                  }}
                />
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

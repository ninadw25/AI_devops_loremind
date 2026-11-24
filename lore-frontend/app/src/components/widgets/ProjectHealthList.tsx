import { AlertTriangle, Calendar } from 'lucide-react';
import { useProjects, usePerson } from '@/data/hooks';
import PersonAvatar from './PersonAvatar';
import type { Project, ProjectHealth } from '@/data/companyData';

const healthConfig: Record<ProjectHealth, { label: string; bg: string; text: string; bar: string }> = {
  'on-track': { label: 'On track',   bg: 'bg-emerald-50', text: 'text-emerald-700', bar: 'bg-emerald-500' },
  'at-risk':  { label: 'At risk',    bg: 'bg-amber-50',   text: 'text-amber-700',   bar: 'bg-amber-500' },
  'blocked':  { label: 'Blocked',    bg: 'bg-red-50',     text: 'text-red-700',     bar: 'bg-red-500' },
};

function daysUntil(dateStr: string, today = '2026-01-22'): number {
  const a = new Date(today).getTime();
  const b = new Date(dateStr).getTime();
  return Math.round((b - a) / (1000 * 60 * 60 * 24));
}

function ProjectRow({ project }: { project: Project }) {
  const owner = usePerson(project.owner);
  const cfg = healthConfig[project.health];
  const days = daysUntil(project.deadline);

  return (
    <div className="group px-4 py-4 hover:bg-slate-50/70 rounded-xl transition-colors cursor-pointer border border-transparent hover:border-slate-200/80">
      <div className="flex items-start justify-between gap-4 mb-2.5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-1">
            <h4 className="text-[13px] font-semibold text-slate-900 truncate" style={{ fontFamily: 'var(--font-display)' }}>
              {project.name}
            </h4>
            <span className={`shrink-0 px-2 py-0.5 text-[10px] font-semibold rounded-md ${cfg.bg} ${cfg.text}`}>
              {cfg.label}
            </span>
          </div>
          <p className="text-[11px] text-slate-500 line-clamp-1">{project.description}</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {owner && <PersonAvatar person={owner} size="sm" ring />}
        </div>
      </div>

      {/* Progress bar */}
      <div className="flex items-center gap-3">
        <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
          <div
            className={`h-full ${cfg.bar} rounded-full transition-all`}
            style={{ width: `${project.progress}%` }}
          />
        </div>
        <span className="text-[11px] font-semibold text-slate-700 tabular-nums w-8 text-right">
          {project.progress}%
        </span>
        <div className="flex items-center gap-1 text-[11px] text-slate-500 tabular-nums w-[68px] justify-end">
          <Calendar className="w-3 h-3" />
          {days > 0 ? `${days}d left` : days === 0 ? 'today' : `${Math.abs(days)}d over`}
        </div>
      </div>

      {/* Risk note */}
      {project.risk && (
        <div className="mt-2.5 flex items-start gap-2 text-[11px] text-amber-800 bg-amber-50/60 border border-amber-100 rounded-md px-2.5 py-1.5">
          <AlertTriangle className="w-3 h-3 shrink-0 mt-0.5" />
          <span className="leading-snug">{project.risk}</span>
        </div>
      )}
    </div>
  );
}

export default function ProjectHealthList() {
  const projects = useProjects();

  return (
    <div className="space-y-1">
      {projects.map(p => <ProjectRow key={p.id} project={p} />)}
    </div>
  );
}

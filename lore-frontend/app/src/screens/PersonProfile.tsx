import { useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft, Mail, Calendar, AlertTriangle, GitCommit, FileText, MessageSquare, Sparkles,
} from 'lucide-react';
import AppShell from '@/components/app-shell/AppShell';
import ChartCard from '@/components/charts/ChartCard';
import PersonAvatar from '@/components/widgets/PersonAvatar';
import AccessBadge from '@/components/widgets/AccessBadge';
import ExpertiseRadar from '@/components/widgets/ExpertiseRadar';
import ActivityFeed from '@/components/widgets/ActivityFeed';
import {
  usePerson, useProjectsByPerson, useDecisionsByPerson, useTeammates, useActivity,
} from '@/data/hooks';

function sinceJoined(joinedAt: string): string {
  const months = Math.round((new Date('2026-01-22').getTime() - new Date(joinedAt).getTime()) / (1000 * 60 * 60 * 24 * 30));
  const years = Math.floor(months / 12);
  const rem = months % 12;
  if (years === 0) return `${months}mo`;
  return rem === 0 ? `${years}y` : `${years}y ${rem}mo`;
}

function StatTile({ icon, label, value }: { icon: React.ReactNode; label: string; value: string | number }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200/70 p-4">
      <div className="flex items-center gap-2 mb-2 text-slate-500">
        {icon}
        <span className="text-[10px] font-semibold uppercase tracking-wider">{label}</span>
      </div>
      <div className="text-[22px] font-semibold text-slate-900 leading-none" style={{ fontFamily: 'var(--font-display)' }}>
        {value}
      </div>
    </div>
  );
}

export default function PersonProfile() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const person = usePerson(id);
  const projects = useProjectsByPerson(id || '');
  const decisions = useDecisionsByPerson(id || '');
  const teammates = useTeammates(id || '');
  const allActivity = useActivity({ limit: 20 });
  const personActivity = allActivity.filter(e => e.people.includes(id || ''));

  if (!person) {
    return (
      <AppShell title="Not found">
        <div className="text-slate-400 text-[13px]">Person not found.</div>
      </AppShell>
    );
  }

  const topExpertise = Object.entries(person.expertise).sort(([, a], [, b]) => b - a).slice(0, 6);

  return (
    <AppShell>
      {/* Back link */}
      <button
        onClick={() => navigate('/team')}
        className="flex items-center gap-1.5 text-[12px] font-medium text-slate-500 hover:text-slate-900 mb-4 transition-colors"
      >
        <ArrowLeft className="w-3.5 h-3.5" /> Back to team
      </button>

      {/* Header card */}
      <div className="bg-white rounded-2xl border border-slate-200/70 p-6 mb-6 relative overflow-hidden">
        <div
          className="absolute -top-20 -right-20 w-[280px] h-[280px] rounded-full opacity-[0.07]"
          style={{ background: `radial-gradient(circle, ${person.avatarColor}, transparent 70%)` }}
        />
        <div className="relative flex items-start gap-6">
          <PersonAvatar person={person} size="xl" ring />
          <div className="flex-1 min-w-0">
            <div className="flex items-start justify-between gap-4 mb-2">
              <div>
                <div className="flex items-center gap-2.5 flex-wrap">
                  <h1 className="text-[26px] font-semibold text-slate-900 leading-tight" style={{ fontFamily: 'var(--font-display)' }}>
                    {person.name}
                  </h1>
                  <AccessBadge level={person.accessLevel} status={person.status} size="md" />
                </div>
                <div className="text-[14px] text-slate-600 mt-0.5">{person.role}</div>
              </div>
              {person.status === 'resigned' && (
                <div className="px-3 py-1.5 bg-red-600 text-white rounded-lg text-[10px] font-semibold uppercase tracking-wider flex items-center gap-1.5">
                  <AlertTriangle className="w-3 h-3" />
                  Resigning · {person.lastDay}
                </div>
              )}
            </div>

            <div className="flex items-center gap-4 mt-3 text-[12px] text-slate-500">
              <span className="flex items-center gap-1.5">
                <Mail className="w-3.5 h-3.5" />
                {person.email}
              </span>
              <span className="flex items-center gap-1.5">
                <Calendar className="w-3.5 h-3.5" />
                Joined {sinceJoined(person.joinedAt)} ago
              </span>
              <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-semibold uppercase tracking-wider bg-slate-100 text-slate-700">
                {person.team}
              </span>
            </div>

            {person.riskNote && (
              <div className="mt-4 flex items-start gap-2 px-4 py-3 bg-amber-50/80 border border-amber-200/70 rounded-xl text-[12px] text-amber-900">
                <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5 text-amber-600" />
                <div>
                  <div className="font-semibold text-amber-800 mb-0.5">Risk note</div>
                  {person.riskNote}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatTile icon={<GitCommit className="w-3.5 h-3.5" />} label="Commits · month" value={person.stats.commitsThisMonth} />
        <StatTile icon={<Sparkles className="w-3.5 h-3.5" />} label="Decisions authored" value={person.stats.decisionsLogged} />
        <StatTile icon={<FileText className="w-3.5 h-3.5" />} label="Docs written" value={person.stats.docsWritten} />
        <StatTile icon={<MessageSquare className="w-3.5 h-3.5" />} label="Messages" value={person.stats.messagesSent} />
      </div>

      {/* Main grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 mb-6">
        {/* Expertise radar */}
        <ChartCard title="Expertise coverage" subtitle="Top skills by proficiency" className="lg:col-span-7">
          <ExpertiseRadar person={person} color={person.avatarColor} />
          <div className="mt-3 space-y-1.5">
            {topExpertise.map(([areaId, score]) => (
              <div key={areaId} className="flex items-center gap-2">
                <span className="text-[11px] text-slate-600 capitalize w-32 truncate">{areaId.replace(/-/g, ' ')}</span>
                <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full"
                    style={{ width: `${score}%`, backgroundColor: person.avatarColor }}
                  />
                </div>
                <span className="text-[11px] font-semibold text-slate-700 tabular-nums w-7 text-right">{score}</span>
              </div>
            ))}
          </div>
        </ChartCard>

        {/* Owned services */}
        <ChartCard title="Owned services" subtitle="Primary ownership responsibilities" className="lg:col-span-5">
          {person.ownsServices.length > 0 ? (
            <div className="space-y-2">
              {person.ownsServices.map(s => (
                <div key={s} className="flex items-center justify-between px-3 py-2.5 bg-slate-50 rounded-lg">
                  <span className="text-[12px] font-semibold text-slate-800 capitalize">{s.replace(/-/g, ' ')}</span>
                  <span className="text-[10px] font-semibold text-blue-700 uppercase tracking-wider">Primary</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-[12px] text-slate-400 italic">No services owned directly.</p>
          )}
        </ChartCard>

      </div>

      {/* Projects + Activity */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 mb-6">
        <ChartCard title="Active projects" subtitle={`${projects.length} ongoing`} className="lg:col-span-7">
          {projects.length === 0 ? (
            <p className="text-[12px] text-slate-400 italic">No active projects.</p>
          ) : (
            <div className="space-y-2">
              {projects.map(p => (
                <div key={p.id} className="px-4 py-3 border border-slate-100 rounded-xl hover:bg-slate-50 transition-colors">
                  <div className="flex items-center justify-between mb-2">
                    <h4 className="text-[12px] font-semibold text-slate-900">{p.name}</h4>
                    <span className="text-[10px] text-slate-500">{p.progress}%</span>
                  </div>
                  <div className="h-1 bg-slate-100 rounded-full overflow-hidden">
                    <div className="h-full rounded-full bg-blue-600" style={{ width: `${p.progress}%` }} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </ChartCard>

        <ChartCard title="Recent activity" subtitle="Events involving this person" className="lg:col-span-5">
          {personActivity.length > 0 ? (
            <ActivityFeed events={personActivity.slice(0, 6)} compact />
          ) : (
            <p className="text-[12px] text-slate-400 italic">No recent activity.</p>
          )}
        </ChartCard>
      </div>

      {/* Decisions + Teammates */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        <ChartCard title="Decisions authored" subtitle={`${decisions.length} decisions`} className="lg:col-span-7">
          {decisions.length === 0 ? (
            <p className="text-[12px] text-slate-400 italic">No decisions authored.</p>
          ) : (
            <div className="space-y-2.5">
              {decisions.slice(0, 5).map(d => (
                <div key={d.id} className="flex items-start gap-3 px-3 py-2.5 bg-slate-50/60 rounded-lg">
                  <div className="w-1 h-10 bg-blue-600 rounded-full shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-[12px] font-semibold text-slate-900 leading-tight">{d.title}</div>
                    <div className="text-[10px] text-slate-500 mt-0.5">{d.date} · {d.category} · {d.impact} impact</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </ChartCard>

        <ChartCard title="Teammates" subtitle={`${teammates.length} in ${person.team}`} className="lg:col-span-5">
          <div className="flex flex-wrap gap-2">
            {teammates.map(t => (
              <div
                key={t.id}
                onClick={() => navigate(`/team/${t.id}`)}
                className="flex items-center gap-2 px-2 py-1.5 bg-slate-50 rounded-lg hover:bg-slate-100 cursor-pointer transition-colors"
              >
                <PersonAvatar person={t} size="xs" />
                <span className="text-[11px] font-medium text-slate-700">{t.name}</span>
              </div>
            ))}
            {teammates.length === 0 && (
              <p className="text-[12px] text-slate-400 italic">No teammates in same team.</p>
            )}
          </div>
        </ChartCard>
      </div>
    </AppShell>
  );
}

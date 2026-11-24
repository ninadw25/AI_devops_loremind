import { useState, useMemo } from 'react';
import {
  Sparkles, Activity,
  Users, AlertTriangle, CheckCircle2, TrendingUp,
  UserCheck, Code2, Lightbulb
} from 'lucide-react';
import AppSidebar from '@/components/AppSidebar';
import InDevelopmentBanner from '@/components/InDevelopmentBanner';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, RadarChart, Radar, PolarGrid, PolarAngleAxis,
  PolarRadiusAxis,
} from 'recharts';
import {
  DEMO_ENTITIES, DEMO_EDGES, DEMO_EXPERTISE, DEMO_DECISIONS, DEMO_STATS,
} from '@/data/demoData';

// ─── Palette ──────────────────────────────────────────────────────────────────
const COLORS = {
  blue:   '#3b82f6',
  indigo: '#6366f1',
  violet: '#8b5cf6',
  rose:   '#f43f5e',
  amber:  '#f59e0b',
  emerald:'#10b981',
  cyan:   '#06b6d4',
  slate:  '#64748b',
};

const PIE_COLORS = ['#3b82f6', '#6366f1', '#10b981', '#f59e0b', '#f43f5e', '#06b6d4', '#8b5cf6', '#64748b'];

const LEVEL_COLORS: Record<string, string> = {
  owner: '#10b981',
  expert: '#3b82f6',
  intermediate: '#f59e0b',
  beginner: '#94a3b8',
};

// ─── Computed data ────────────────────────────────────────────────────────────

function useComputedPulseData() {
  return useMemo(() => {
    const people = DEMO_ENTITIES.filter(e => e.type === 'person' && e.status === 'active');
    const topics = DEMO_ENTITIES.filter(e => e.type === 'topic');
    const projects = DEMO_ENTITIES.filter(e => e.type === 'project');

    // Department distribution
    const deptMap = new Map<string, number>();
    people.forEach(p => {
      const dept = p.department || 'Other';
      deptMap.set(dept, (deptMap.get(dept) || 0) + 1);
    });
    const departmentData = [...deptMap.entries()]
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value);

    // Bus factor: for each topic, count unique people connected
    const topicPeopleMap = new Map<string, Set<string>>();
    topics.forEach(t => topicPeopleMap.set(t.id, new Set()));
    DEMO_EDGES.forEach(edge => {
      if (edge.target.startsWith('topic_') && edge.source.startsWith('emp_')) {
        topicPeopleMap.get(edge.target)?.add(edge.source);
      }
    });
    // Also from expertise data
    DEMO_EXPERTISE.forEach(exp => {
      const topicEntity = topics.find(t => t.name === exp.topic);
      if (topicEntity) topicPeopleMap.get(topicEntity.id)?.add(exp.empId);
    });

    const busFactor = topics.map(t => {
      const peopleCount = topicPeopleMap.get(t.id)?.size || 0;
      const risk = peopleCount <= 1 ? 'critical' : peopleCount <= 2 ? 'warning' : 'healthy';
      return { topic: t.name, people: peopleCount, risk };
    }).sort((a, b) => a.people - b.people);

    // Project bus factor: for each project, count unique people
    const projectPeopleMap = new Map<string, Set<string>>();
    projects.forEach(p => projectPeopleMap.set(p.id, new Set()));
    DEMO_EDGES.forEach(edge => {
      if (edge.target.startsWith('project_') && edge.source.startsWith('emp_')) {
        projectPeopleMap.get(edge.target)?.add(edge.source);
      }
      if (edge.source.startsWith('project_') && edge.target.startsWith('emp_')) {
        projectPeopleMap.get(edge.source)?.add(edge.target);
      }
    });

    const projectRisk = projects.map(p => {
      const count = projectPeopleMap.get(p.id)?.size || 0;
      const risk = count <= 1 ? 'critical' : count <= 2 ? 'warning' : 'healthy';
      return { name: p.name, people: count, risk };
    }).sort((a, b) => a.people - b.people);

    // Expertise by topic (aggregated)
    const topicExpertise = new Map<string, { owners: number; experts: number; intermediates: number; beginners: number }>();
    DEMO_EXPERTISE.forEach(exp => {
      if (!topicExpertise.has(exp.topic)) {
        topicExpertise.set(exp.topic, { owners: 0, experts: 0, intermediates: 0, beginners: 0 });
      }
      const t = topicExpertise.get(exp.topic)!;
      if (exp.level === 'owner') t.owners++;
      else if (exp.level === 'expert') t.experts++;
      else if (exp.level === 'intermediate') t.intermediates++;
      else t.beginners++;
    });
    const expertiseByTopic = [...topicExpertise.entries()].map(([topic, counts]) => ({
      topic,
      ...counts,
      total: counts.owners + counts.experts + counts.intermediates + counts.beginners,
    })).sort((a, b) => b.total - a.total);

    // Top contributors (by expertise evidence count)
    const personEvidence = new Map<string, { name: string; total: number; topics: number }>();
    DEMO_EXPERTISE.forEach(exp => {
      if (!personEvidence.has(exp.empId)) {
        personEvidence.set(exp.empId, { name: exp.empName, total: 0, topics: 0 });
      }
      const p = personEvidence.get(exp.empId)!;
      p.total += exp.evidenceCount;
      p.topics++;
    });
    const topContributors = [...personEvidence.entries()]
      .map(([id, data]) => ({ id, ...data }))
      .sort((a, b) => b.total - a.total)
      .slice(0, 10);

    // Risk summary counts
    const criticalTopics = busFactor.filter(b => b.risk === 'critical').length;
    const warningTopics = busFactor.filter(b => b.risk === 'warning').length;
    const criticalProjects = projectRisk.filter(p => p.risk === 'critical').length;

    // Expertise heatmap data: top 8 people × all unique topics
    const heatmapTopics = [...new Set(DEMO_EXPERTISE.map(e => e.topic))].sort();
    const heatmapPeople = topContributors.slice(0, 8);
    const heatmapData = heatmapPeople.map(person => {
      const row: Record<string, string | number> = { name: person.name };
      heatmapTopics.forEach(topic => {
        const entry = DEMO_EXPERTISE.find(e => e.empId === person.id && e.topic === topic);
        row[topic] = entry ? ({ owner: 4, expert: 3, intermediate: 2, beginner: 1 }[entry.level]) : 0;
      });
      return row;
    });

    return {
      people, topics, projects,
      departmentData, busFactor, projectRisk,
      expertiseByTopic, topContributors,
      criticalTopics, warningTopics, criticalProjects,
      heatmapTopics, heatmapData,
    };
  }, []);
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function StatCard({ icon: Icon, label, value, sub, color }: {
  icon: React.ElementType; label: string; value: string | number; sub: string; color: string;
}) {
  return (
    <div className="bg-white rounded-2xl p-6 border border-slate-200/60 shadow-sm hover:shadow-md transition-all">
      <div className="flex items-center gap-3 mb-3">
        <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${color}`}>
          <Icon className="w-5 h-5" />
        </div>
        <span className="text-sm text-slate-500 font-medium">{label}</span>
      </div>
      <p className="text-3xl font-bold text-slate-900">{value}</p>
      <p className="text-xs text-slate-400 mt-1">{sub}</p>
    </div>
  );
}

function LevelDot({ level }: { level: string }) {
  const color = LEVEL_COLORS[level] || '#94a3b8';
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium" style={{ color }}>
      <span className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
      {level.charAt(0).toUpperCase() + level.slice(1)}
    </span>
  );
}

// Custom Tooltip for charts
function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-white rounded-xl shadow-lg border border-slate-200 px-4 py-3 text-sm">
      <p className="font-semibold text-slate-800 mb-1">{label}</p>
      {payload.map((p: any, i: number) => (
        <p key={i} className="text-slate-600">
          <span className="inline-block w-2 h-2 rounded-full mr-2" style={{ backgroundColor: p.color }} />
          {p.name}: <span className="font-medium text-slate-900">{p.value}</span>
        </p>
      ))}
    </div>
  );
}

// ─── Tabs ─────────────────────────────────────────────────────────────────────
type Tab = 'overview' | 'people' | 'decisions';

// ─── Main Component ───────────────────────────────────────────────────────────

export default function LorePulse() {
  const [activeTab, setActiveTab] = useState<Tab>('overview');
  const data = useComputedPulseData();

  const tabs: { id: Tab; label: string; icon: React.ElementType }[] = [
    { id: 'overview',  label: 'Overview',          icon: TrendingUp },
    { id: 'people',    label: 'People & Expertise', icon: UserCheck },
    { id: 'decisions', label: 'Decisions',          icon: Lightbulb },
  ];

  return (
    <div className="min-h-screen bg-slate-50 flex">
      <AppSidebar activeTab="pulse" />

      <main className="flex-1 ml-64 p-8">
        <InDevelopmentBanner />
        {/* Header */}
        <div className="mb-8">
          <div className="flex items-center gap-3 mb-2">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-500 to-violet-700 flex items-center justify-center">
              <Activity className="w-5 h-5 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-slate-900">Lore Pulse</h1>
              <p className="text-sm text-slate-500">Organizational intelligence dashboard</p>
            </div>
          </div>
        </div>

        {/* Tab Bar */}
        <div className="flex gap-1 bg-white rounded-2xl p-1.5 border border-slate-200/60 shadow-sm mb-8 w-fit">
          {tabs.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium transition-all ${
                activeTab === tab.id
                  ? 'bg-violet-600 text-white shadow-md shadow-violet-200'
                  : 'text-slate-500 hover:text-slate-700 hover:bg-slate-50'
              }`}
            >
              <tab.icon className="w-4 h-4" />
              {tab.label}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        {activeTab === 'overview' && <OverviewTab data={data} />}
        {activeTab === 'people' && <PeopleTab data={data} />}
        {activeTab === 'decisions' && <DecisionsTab />}
      </main>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// TAB: Overview
// ═══════════════════════════════════════════════════════════════════════════════

function OverviewTab({ data }: { data: ReturnType<typeof useComputedPulseData> }) {
  return (
    <div className="space-y-8">
      {/* Stat Cards */}
      <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={Users}         label="Active People"    value={data.people.length}      sub="Across all departments" color="bg-blue-100 text-blue-600" />
        <StatCard icon={Sparkles}      label="Knowledge Units"  value={DEMO_STATS.knowledge_units} sub="Total indexed"      color="bg-violet-100 text-violet-600" />
        <StatCard icon={AlertTriangle} label="At-Risk Topics"   value={data.criticalTopics}     sub="Single point of failure" color="bg-red-100 text-red-600" />
        <StatCard icon={Code2}         label="Active Projects"  value={data.projects.length}    sub="Being tracked"          color="bg-emerald-100 text-emerald-600" />
      </div>

      <div className="grid lg:grid-cols-2 gap-6">
        {/* Department Distribution */}
        <div className="bg-white rounded-2xl border border-slate-200/60 shadow-sm p-6">
          <h3 className="text-lg font-semibold text-slate-900 mb-1">Team by Department</h3>
          <p className="text-sm text-slate-400 mb-6">Active headcount distribution</p>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={data.departmentData}
                  cx="50%" cy="50%"
                  innerRadius={55} outerRadius={95}
                  paddingAngle={3}
                  dataKey="value"
                  stroke="none"
                >
                  {data.departmentData.map((_, i) => (
                    <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip content={<ChartTooltip />} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="flex flex-wrap gap-3 justify-center mt-2">
            {data.departmentData.map((d, i) => (
              <span key={d.name} className="flex items-center gap-1.5 text-xs text-slate-600">
                <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: PIE_COLORS[i % PIE_COLORS.length] }} />
                {d.name} ({d.value})
              </span>
            ))}
          </div>
        </div>

        {/* Expertise Coverage by Topic */}
        <div className="bg-white rounded-2xl border border-slate-200/60 shadow-sm p-6">
          <h3 className="text-lg font-semibold text-slate-900 mb-1">Expertise Coverage</h3>
          <p className="text-sm text-slate-400 mb-6">People per expertise level by topic</p>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.expertiseByTopic.slice(0, 10)} layout="vertical" margin={{ left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                <XAxis type="number" tick={{ fontSize: 12, fill: '#94a3b8' }} />
                <YAxis type="category" dataKey="topic" width={110} tick={{ fontSize: 12, fill: '#475569' }} />
                <Tooltip content={<ChartTooltip />} />
                <Bar dataKey="owners"        name="Owner"        stackId="a" fill={COLORS.emerald} radius={[0, 0, 0, 0]} />
                <Bar dataKey="experts"       name="Expert"       stackId="a" fill={COLORS.blue} />
                <Bar dataKey="intermediates"  name="Intermediate" stackId="a" fill={COLORS.amber} />
                <Bar dataKey="beginners"     name="Beginner"     stackId="a" fill="#cbd5e1" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="flex gap-4 justify-center mt-3">
            {[['Owner', COLORS.emerald], ['Expert', COLORS.blue], ['Intermediate', COLORS.amber], ['Beginner', '#cbd5e1']].map(([label, color]) => (
              <span key={label} className="flex items-center gap-1.5 text-xs text-slate-500">
                <span className="w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: color as string }} />
                {label}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* Top Knowledge Contributors */}
      <div className="bg-white rounded-2xl border border-slate-200/60 shadow-sm p-6">
        <h3 className="text-lg font-semibold text-slate-900 mb-1">Top Knowledge Contributors</h3>
        <p className="text-sm text-slate-400 mb-6">Ranked by evidence count across all topics</p>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data.topContributors} margin={{ bottom: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis dataKey="name" tick={{ fontSize: 11, fill: '#475569' }} angle={-20} textAnchor="end" />
              <YAxis tick={{ fontSize: 12, fill: '#94a3b8' }} />
              <Tooltip content={<ChartTooltip />} />
              <Bar dataKey="total" name="Evidence Count" fill={COLORS.indigo} radius={[6, 6, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// TAB: People & Expertise
// ═══════════════════════════════════════════════════════════════════════════════

function PeopleTab({ data }: { data: ReturnType<typeof useComputedPulseData> }) {
  const [selectedPerson, setSelectedPerson] = useState<string | null>(null);

  const personExpertise = useMemo(() => {
    if (!selectedPerson) return [];
    return DEMO_EXPERTISE.filter(e => e.empId === selectedPerson).sort((a, b) => b.evidenceCount - a.evidenceCount);
  }, [selectedPerson]);

  const radarData = useMemo(() => {
    if (!selectedPerson) return [];
    return personExpertise.map(e => ({
      topic: e.topic,
      level: { owner: 4, expert: 3, intermediate: 2, beginner: 1 }[e.level],
      evidence: e.evidenceCount,
    }));
  }, [selectedPerson, personExpertise]);

  return (
    <div className="space-y-8">
      {/* Expertise Heatmap */}
      <div className="bg-white rounded-2xl border border-slate-200/60 shadow-sm p-6">
        <h3 className="text-lg font-semibold text-slate-900 mb-1">Expertise Matrix</h3>
        <p className="text-sm text-slate-400 mb-6">Who knows what - click a person to see their profile</p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="text-left py-3 px-4 text-slate-500 font-medium sticky left-0 bg-white z-10 min-w-[160px]">Person</th>
                {data.heatmapTopics.map(t => (
                  <th key={t} className="py-3 px-2 text-slate-500 font-medium text-center whitespace-nowrap">
                    <span className="text-xs">{t}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.heatmapData.map((row, i) => {
                const person = data.topContributors[i];
                return (
                  <tr
                    key={person?.id || i}
                    onClick={() => person && setSelectedPerson(person.id === selectedPerson ? null : person.id)}
                    className={`cursor-pointer transition-colors ${
                      selectedPerson === person?.id ? 'bg-violet-50' : 'hover:bg-slate-50'
                    }`}
                  >
                    <td className="py-3 px-4 sticky left-0 bg-inherit z-10">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-violet-400 to-violet-600 flex items-center justify-center text-white text-xs font-bold">
                          {(row.name as string).split(' ').map(n => n[0]).join('')}
                        </div>
                        <span className="font-medium text-slate-800">{row.name as string}</span>
                      </div>
                    </td>
                    {data.heatmapTopics.map(topic => {
                      const val = row[topic] as number;
                      const bg = val === 4 ? 'bg-emerald-500' : val === 3 ? 'bg-blue-500' : val === 2 ? 'bg-amber-400' : val === 1 ? 'bg-slate-300' : 'bg-slate-100';
                      const opacity = val > 0 ? '' : 'opacity-40';
                      return (
                        <td key={topic} className="py-3 px-2 text-center">
                          <div className={`w-8 h-8 rounded-lg mx-auto ${bg} ${opacity} transition-all`} title={`${topic}: ${['', 'Beginner', 'Intermediate', 'Expert', 'Owner'][val] || 'None'}`} />
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="flex gap-4 justify-center mt-4 pt-4 border-t border-slate-100">
          {[['Owner', 'bg-emerald-500'], ['Expert', 'bg-blue-500'], ['Intermediate', 'bg-amber-400'], ['Beginner', 'bg-slate-300'], ['None', 'bg-slate-100']].map(([label, bg]) => (
            <span key={label} className="flex items-center gap-1.5 text-xs text-slate-500">
              <span className={`w-3 h-3 rounded ${bg}`} />
              {label}
            </span>
          ))}
        </div>
      </div>

      {/* Person Detail Card (when selected) */}
      {selectedPerson && (
        <div className="bg-white rounded-2xl border border-violet-200 shadow-sm p-6 animate-in fade-in">
          <div className="flex items-start justify-between mb-6">
            <div className="flex items-center gap-4">
              <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-white text-lg font-bold">
                {personExpertise[0]?.empName.split(' ').map(n => n[0]).join('')}
              </div>
              <div>
                <h3 className="text-xl font-bold text-slate-900">{personExpertise[0]?.empName}</h3>
                <p className="text-sm text-slate-500">
                  {data.people.find(p => p.id === selectedPerson)?.role || 'Team member'} · {data.people.find(p => p.id === selectedPerson)?.department}
                </p>
              </div>
            </div>
            <button onClick={() => setSelectedPerson(null)} className="text-slate-400 hover:text-slate-600 p-1">
              ✕
            </button>
          </div>

          <div className="grid lg:grid-cols-2 gap-6">
            {/* Radar Chart */}
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <RadarChart data={radarData}>
                  <PolarGrid stroke="#e2e8f0" />
                  <PolarAngleAxis dataKey="topic" tick={{ fontSize: 11, fill: '#475569' }} />
                  <PolarRadiusAxis domain={[0, 4]} tick={{ fontSize: 10, fill: '#94a3b8' }} />
                  <Radar name="Level" dataKey="level" stroke={COLORS.violet} fill={COLORS.violet} fillOpacity={0.2} strokeWidth={2} />
                </RadarChart>
              </ResponsiveContainer>
            </div>

            {/* Expertise List */}
            <div className="space-y-3">
              {personExpertise.map(exp => (
                <div key={exp.topic} className="flex items-center justify-between p-3 rounded-xl bg-slate-50 border border-slate-100">
                  <div>
                    <p className="font-medium text-slate-800">{exp.topic}</p>
                    <LevelDot level={exp.level} />
                  </div>
                  <div className="text-right">
                    <p className="text-lg font-bold text-slate-900">{exp.evidenceCount}</p>
                    <p className="text-xs text-slate-400">evidence signals</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Team Directory */}
      <div className="bg-white rounded-2xl border border-slate-200/60 shadow-sm p-6">
        <h3 className="text-lg font-semibold text-slate-900 mb-1">Team Directory</h3>
        <p className="text-sm text-slate-400 mb-6">{data.people.length} active members</p>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {data.people.map(person => {
            const expertise = DEMO_EXPERTISE.filter(e => e.empId === person.id);
            const topSkill = expertise.sort((a, b) => b.evidenceCount - a.evidenceCount)[0];
            return (
              <div
                key={person.id}
                onClick={() => setSelectedPerson(person.id === selectedPerson ? null : person.id)}
                className={`flex items-center gap-3 p-4 rounded-xl border cursor-pointer transition-all ${
                  selectedPerson === person.id
                    ? 'border-violet-300 bg-violet-50 shadow-sm'
                    : 'border-slate-100 hover:border-slate-200 hover:shadow-sm'
                }`}
              >
                <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-400 to-blue-600 flex items-center justify-center text-white text-sm font-bold flex-shrink-0">
                  {person.name.split(' ').map(n => n[0]).join('')}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="font-medium text-slate-900 text-sm truncate">{person.name}</p>
                  <p className="text-xs text-slate-500 truncate">{person.role}</p>
                  {topSkill && (
                    <p className="text-xs text-violet-500 mt-0.5 truncate">Top: {topSkill.topic}</p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// TAB: Decisions
// ═══════════════════════════════════════════════════════════════════════════════

function DecisionsTab() {
  return (
    <div className="space-y-6">
      <div className="bg-white rounded-2xl border border-slate-200/60 shadow-sm p-6">
        <h3 className="text-lg font-semibold text-slate-900 mb-1">Decision Timeline</h3>
        <p className="text-sm text-slate-400 mb-6">Key organizational decisions captured from conversations</p>

        <div className="relative">
          {/* Timeline line */}
          <div className="absolute left-6 top-0 bottom-0 w-px bg-slate-200" />

          <div className="space-y-6">
            {DEMO_DECISIONS.map((decision) => (
              <div key={decision.id} className="relative flex gap-6">
                {/* Timeline dot */}
                <div className="relative z-10 w-12 flex-shrink-0 flex items-start justify-center pt-1">
                  <div className="w-3 h-3 rounded-full bg-violet-500 ring-4 ring-violet-100" />
                </div>

                {/* Card */}
                <div className="flex-1 bg-slate-50 rounded-xl border border-slate-100 p-5 hover:shadow-sm transition-all">
                  <div className="flex items-start justify-between mb-3">
                    <div>
                      <h4 className="font-semibold text-slate-900">{decision.title}</h4>
                      <p className="text-xs text-slate-400 mt-1">{new Date(decision.date).toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' })}</p>
                    </div>
                    <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-600 border border-emerald-100">
                      <CheckCircle2 className="w-3 h-3" />
                      {decision.outcome}
                    </span>
                  </div>

                  <div className="flex flex-wrap gap-2 mb-3">
                    {decision.impactAreas.map(area => (
                      <span key={area} className="px-2.5 py-1 rounded-lg bg-white border border-slate-200 text-xs text-slate-600 font-medium">
                        {area}
                      </span>
                    ))}
                  </div>

                  <div className="flex items-center gap-2">
                    <span className="text-xs text-slate-400">Made by:</span>
                    <div className="flex -space-x-2">
                      {decision.makerNames.map((name, j) => (
                        <div
                          key={j}
                          className="w-7 h-7 rounded-full bg-gradient-to-br from-blue-400 to-indigo-500 flex items-center justify-center text-white text-[10px] font-bold border-2 border-white"
                          title={name}
                        >
                          {name.split(' ').map(n => n[0]).join('')}
                        </div>
                      ))}
                    </div>
                    <span className="text-xs text-slate-500">{decision.makerNames.join(', ')}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

import { Folders, AlertTriangle, Clock3, GraduationCap } from 'lucide-react';
import AppShell from '@/components/app-shell/AppShell';
import StatCard from '@/components/charts/StatCard';
import ChartCard from '@/components/charts/ChartCard';
import VelocityDocScatter from '@/components/charts/VelocityDocScatter';
import CollaborationChord from '@/components/charts/CollaborationChord';
import KnowledgeCapturePulse from '@/components/charts/KnowledgeCapturePulse';
import { useKPIs, useCompany } from '@/data/hooks';

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
}

export default function Dashboard() {
  const { today } = useCompany();
  const kpis = useKPIs();

  return (
    <AppShell title="Dashboard" subtitle={formatDate(today)}>
      {/* Row 1 - KPIs unique to Loremind */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 mb-6">
        <StatCard
          label="Decision → Execution Lag"
          value={kpis.executionLag.value}
          suffix="days"
          delta={kpis.executionLag.delta}
          deltaLabel="median, vs prior month"
          trend={kpis.executionLag.trend}
          tone="success"
          icon={<Clock3 className="w-3.5 h-3.5" />}
        />
        <StatCard
          label="Onboarding Readiness"
          value={kpis.onboardingReadiness.value}
          suffix="%"
          delta={kpis.onboardingReadiness.delta}
          deltaLabel="dropped after Rahul resignation"
          trend={kpis.onboardingReadiness.trend}
          tone={kpis.onboardingReadiness.value < 70 ? 'warning' : 'success'}
          icon={<GraduationCap className="w-3.5 h-3.5" />}
        />
        <StatCard
          label="Knowledge at Risk"
          value={kpis.knowledgeAtRisk.value}
          suffix="critical"
          delta={kpis.knowledgeAtRisk.delta}
          deltaLabel="bus-factor 1 services"
          trend={kpis.knowledgeAtRisk.trend}
          tone="danger"
          icon={<AlertTriangle className="w-3.5 h-3.5" />}
        />
        <StatCard
          label="Active Projects"
          value={kpis.activeProjects.value}
          delta={kpis.activeProjects.delta}
          deltaLabel="vs last week"
          trend={kpis.activeProjects.trend}
          icon={<Folders className="w-3.5 h-3.5" />}
        />
      </div>

      {/* Row 2 - Velocity vs Documentation */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 mb-6">
        <ChartCard
          title="Velocity vs Documentation"
          subtitle="Commits and docs by team this month"
          className="lg:col-span-12"
        >
          <VelocityDocScatter />
        </ChartCard>
      </div>

      {/* Row 3 - Cross-team chord + Live capture pulse */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 mb-6">
        <ChartCard
          title="Cross-Team Collaboration"
          subtitle="Who's actually talking to whom · all channels combined"
          className="lg:col-span-7"
        >
          <CollaborationChord />
        </ChartCard>

        <ChartCard
          title="Knowledge Capture Pulse"
          subtitle="Loremind brain · live extraction feed"
          className="lg:col-span-5"
        >
          <KnowledgeCapturePulse />
        </ChartCard>
      </div>
    </AppShell>
  );
}

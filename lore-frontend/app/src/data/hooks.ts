// ============================================================================
// Data hooks - abstraction layer between screens and data source.
// Currently returns hard-coded demo data. To swap for real APIs later, change
// the hook bodies only; no screen code needs to change.
// ============================================================================

import {
  people, projects, decisions, events, expertiseAreas, services,
  mapNodes, mapEdges, teamContributionWeeks, decisionsTimeline,
  knowledgeSources, kpis, TODAY, COMPANY_NAME,
  onboardingReadiness, teamCollaboration, contradictions, pulseEvents,
  type Person, type Project, type Decision, type ActivityEvent,
  type ExpertiseArea, type Service, type EventType, type TeamId,
} from './companyData';

// ─── Global ─────────────────────────────────────────────────────────────────
export const useCompany = () => ({
  name: COMPANY_NAME,
  today: TODAY,
  totalPeople: people.filter(p => p.status !== 'external').length,
});

// ─── People ─────────────────────────────────────────────────────────────────
export const usePeople = (): Person[] => people;

export const useInternalPeople = (): Person[] =>
  people.filter(p => p.status !== 'external');

export const usePerson = (id: string | undefined): Person | undefined =>
  id ? people.find(p => p.id === id) : undefined;

export const useTeammates = (personId: string): Person[] => {
  const p = people.find(x => x.id === personId);
  if (!p) return [];
  return people.filter(x => x.team === p.team && x.id !== personId);
};

// ─── Projects ───────────────────────────────────────────────────────────────
export const useProjects = (): Project[] => projects;

export const useProject = (id: string | undefined): Project | undefined =>
  id ? projects.find(p => p.id === id) : undefined;

export const useProjectsByPerson = (personId: string): Project[] =>
  projects.filter(p => p.owner === personId || p.contributors.includes(personId));

// ─── Decisions ──────────────────────────────────────────────────────────────
export const useDecisions = (): Decision[] =>
  [...decisions].sort((a, b) => (a.date < b.date ? 1 : -1));

export const useDecisionsByPerson = (personId: string): Decision[] =>
  decisions.filter(d => d.author === personId);

// ─── Activity feed ──────────────────────────────────────────────────────────
export const useActivity = (filters?: { type?: EventType; limit?: number }): ActivityEvent[] => {
  let list = [...events].sort((a, b) => (a.timestamp < b.timestamp ? 1 : -1));
  if (filters?.type) list = list.filter(e => e.type === filters.type);
  if (filters?.limit) list = list.slice(0, filters.limit);
  return list;
};

export const useCriticalEvents = (limit = 5): ActivityEvent[] =>
  [...events]
    .filter(e => e.priority === 'critical' || e.priority === 'warning')
    .sort((a, b) => (a.timestamp < b.timestamp ? 1 : -1))
    .slice(0, limit);

// ─── Expertise ──────────────────────────────────────────────────────────────
export const useExpertiseAreas = (): ExpertiseArea[] => expertiseAreas;

export const useServices = (): Service[] => services;

// ─── Map ────────────────────────────────────────────────────────────────────
export const useKnowledgeMap = () => ({
  nodes: mapNodes,
  edges: mapEdges,
});

// ─── Charts ─────────────────────────────────────────────────────────────────
export const useTeamContribution = () => teamContributionWeeks;
export const useDecisionsTimeline = () => decisionsTimeline;
export const useKnowledgeSources = () => knowledgeSources;
export const useKPIs = () => kpis;

// ─── Derived: risk matrix data ──────────────────────────────────────────────
export const useRiskMatrix = () => {
  // Build people × services ownership matrix
  const rows = people.filter(p => p.team !== 'external' && p.team !== 'sales' && p.team !== 'marketing' && p.team !== 'leadership');
  const cols = services;
  const cells: Array<{ personId: string; serviceId: string; ownership: number; critical: boolean }> = [];

  rows.forEach(person => {
    cols.forEach(service => {
      let ownership = 0;
      if (service.primaryOwner === person.id) {
        // Primary owner
        ownership = service.busFactor === 1 ? 95 : service.busFactor === 2 ? 75 : 60;
      } else {
        // Contributor ownership based on expertise overlap
        const relevantAreas = service.id === 'payment-service' || service.id === 'razorpay-v2'
          ? ['payment-service', 'razorpay', 'go-backend']
          : service.id === 'northwind-connector'
          ? ['kubernetes', 'go-backend', 'terraform']
          : service.id === 'dashboard-v2' || service.id === 'design-system'
          ? ['react', 'tailwind', 'typescript']
          : service.id === 'knowledge-api'
          ? ['go-backend', 'postgres']
          : service.id === 'auth-service' || service.id === 'billing-flow'
          ? ['go-backend', 'postgres']
          : [];
        const scores = relevantAreas.map(a => person.expertise[a] || 0).filter(x => x > 0);
        if (scores.length) {
          ownership = Math.round(scores.reduce((a, b) => a + b, 0) / scores.length / 3);
        }
      }
      const critical = service.primaryOwner === person.id && service.busFactor === 1;
      cells.push({ personId: person.id, serviceId: service.id, ownership, critical });
    });
  });

  return { rows, cols, cells };
};

// ─── Derived: expertise matrix (people × areas) ─────────────────────────────
export const useExpertiseMatrix = () => {
  const rows = people.filter(p => p.status !== 'external');
  const cols = expertiseAreas;
  return { rows, cols };
};

// ─── Decision execution lag ─────────────────────────────────────────────────
export const useExecutionLag = () => {
  const lags = decisions.map(d => d.executionLagDays).filter((x): x is number => typeof x === 'number');
  const sorted = [...lags].sort((a, b) => a - b);
  const median = sorted.length
    ? sorted[Math.floor(sorted.length / 2)]
    : 0;
  // Distribution buckets for histogram
  const buckets = [
    { range: '0-1d',  count: lags.filter(l => l <= 1).length },
    { range: '2-3d',  count: lags.filter(l => l >= 2 && l <= 3).length },
    { range: '4-7d',  count: lags.filter(l => l >= 4 && l <= 7).length },
    { range: '8-14d', count: lags.filter(l => l >= 8 && l <= 14).length },
    { range: '15+',   count: lags.filter(l => l >= 15).length },
  ];
  return { median, distribution: buckets, sample: lags.length };
};

// ─── Onboarding readiness per team ──────────────────────────────────────────
export const useOnboardingReadiness = () => {
  const teamLabels: Record<TeamId, string> = {
    leadership: 'Leadership',
    operations: 'Operations',
    hr: 'HR',
    finance: 'Finance',
    tech: 'Tech',
    sales: 'Sales',
    marketing: 'Marketing',
    external: 'External',
  };
  const items = (Object.entries(onboardingReadiness) as [TeamId, number][])
    .filter(([k]) => k !== 'external')
    .map(([team, score]) => ({ team, label: teamLabels[team], score }));
  const avg = Math.round(items.reduce((a, b) => a + b.score, 0) / items.length);
  return { items, avg };
};

// ─── Cross-team collaboration matrix ────────────────────────────────────────
export const useTeamCollaboration = () => {
  // Sum totals per team
  const teamTotals: Record<string, number> = {};
  teamCollaboration.forEach(({ from, to, volume }) => {
    teamTotals[from] = (teamTotals[from] || 0) + volume;
    teamTotals[to] = (teamTotals[to] || 0) + volume;
  });
  return { edges: teamCollaboration, totals: teamTotals };
};

// ─── Decision contradictions ────────────────────────────────────────────────
export const useContradictions = () => contradictions;

// ─── Knowledge concentration per team ───────────────────────────────────────
// Returns max-share - what % of total team contribution comes from one person.
// 100% = single owner, lower = better distributed.
export const useKnowledgeConcentration = () => {
  const teams: TeamId[] = ['operations', 'hr', 'finance', 'tech', 'sales', 'marketing', 'leadership'];
  return teams.map(team => {
    const members = people.filter(p => p.team === team && p.status !== 'external');
    if (members.length === 0) return { team, label: team, members: 0, concentration: 0, leadName: '' };

    // Score each member by commit + decision activity (proxy for contribution)
    const scores = members.map(p => ({
      name: p.name,
      score: p.stats.commitsThisMonth + p.stats.decisionsLogged * 2,
    }));
    const total = scores.reduce((a, b) => a + b.score, 0);
    if (total === 0) return { team, label: team, members: members.length, concentration: 0, leadName: '' };

    const max = scores.reduce((a, b) => (b.score > a.score ? b : a), scores[0]);
    const concentration = Math.round((max.score / total) * 100);
    return {
      team,
      label: team[0].toUpperCase() + team.slice(1),
      members: members.length,
      concentration,
      leadName: max.name,
    };
  });
};

// ─── Velocity vs documentation per team ─────────────────────────────────────
export const useTeamVelocityDocs = () => {
  const teams: TeamId[] = ['operations', 'tech', 'finance'];
  return teams.map(team => {
    const members = people.filter(p => p.team === team && p.status !== 'external');
    const velocity = members.reduce((a, p) => a + p.stats.commitsThisMonth, 0);
    const docs = members.reduce((a, p) => a + p.stats.docsWritten + p.stats.decisionsLogged, 0);
    // Docs per 10 commits - higher is better
    const docRate = velocity > 0 ? Math.round((docs / velocity) * 100) : 0;
    return {
      team,
      label: team[0].toUpperCase() + team.slice(1),
      velocity,
      docs,
      docRate,
      members: members.length,
    };
  });
};

// ─── Pulse events stream ────────────────────────────────────────────────────
export const usePulseEvents = () => pulseEvents;

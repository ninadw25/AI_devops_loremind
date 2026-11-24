// ============================================================================
// COMPANY DATA - single source of truth for the demo dashboard
// ----------------------------------------------------------------------------
// All screens consume this via hooks.ts. To swap for real APIs later, replace
// the hook bodies - no screen code changes required.
//
// Demo anchor date: 2026-03-10 (Tue) - the snapshot reflects the state of
// EZO Books after the sales ultimatum, Mera Monitor pilot, and Shakir
// termination decisions.
// ============================================================================

export const TODAY = '2026-03-10';
export const COMPANY_NAME = 'EZO Books';

// ─── Types ──────────────────────────────────────────────────────────────────
export type TeamId = 'leadership' | 'operations' | 'hr' | 'finance' | 'tech' | 'sales' | 'marketing' | 'external';
export type RiskLevel = 'low' | 'medium' | 'high' | 'critical';
export type ProjectHealth = 'on-track' | 'at-risk' | 'blocked';
export type DecisionCategory = 'tech' | 'product' | 'ops' | 'cost' | 'personnel';
export type EventType = 'decision' | 'risk' | 'incident' | 'project' | 'knowledge';
export type EventPriority = 'info' | 'warning' | 'critical';
export type PersonStatus = 'active' | 'resigned' | 'external' | 'pending_approval';
export type AccessLevel = 'admin' | 'editor' | 'viewer' | 'restricted';

export interface Person {
  id: string;
  name: string;
  initials: string;
  role: string;
  team: TeamId;
  email: string;
  avatarColor: string;        // hex for gradient start
  status: PersonStatus;
  accessLevel: AccessLevel;   // workspace permission level
  joinedAt: string;
  lastDay?: string;
  expertise: Record<string, number>;   // areaId -> 0-100
  ownsServices: string[];               // service ids
  riskLevel: RiskLevel;
  riskNote?: string;
  stats: {
    commitsThisMonth: number;
    decisionsLogged: number;
    docsWritten: number;
    messagesSent: number;
  };
  // 30-day activity as simple arrays for sparklines
  activityTrend: number[];              // daily commits, 30 points
}

export interface Project {
  id: string;
  name: string;
  description: string;
  owner: string;                 // person id
  contributors: string[];        // person ids
  team: TeamId;
  health: ProjectHealth;
  progress: number;              // 0-100
  deadline: string;
  risk?: string;
  startedAt: string;
}

export interface Decision {
  id: string;
  title: string;
  description: string;
  author: string;                // person id
  date: string;
  impact: 'low' | 'medium' | 'high';
  category: DecisionCategory;
  sources: { platform: string; count: number }[];
  linkedProjectIds: string[];
  // Days from when decision was logged → first execution signal observed
  // (commit referencing it, ticket created, doc updated). undefined = no execution yet.
  executionLagDays?: number;
}

export interface ActivityEvent {
  id: string;
  timestamp: string;             // ISO
  type: EventType;
  title: string;
  description: string;
  people: string[];              // person ids
  priority: EventPriority;
  source?: string;               // platform
}

export interface ExpertiseArea {
  id: string;
  name: string;
  category: 'backend' | 'frontend' | 'devops' | 'product' | 'sales' | 'security';
}

export interface Service {
  id: string;
  name: string;
  primaryOwner: string;          // person id
  busFactor: number;             // 1 = bad, 5+ = good
}

// Knowledge map node (positions are hand-curated for a clean demo layout)
export interface MapNode {
  id: string;
  type: 'person' | 'service' | 'project' | 'decision';
  label: string;
  x: number;                     // 0-1000 coordinate space
  y: number;                     // 0-700 coordinate space
  size: number;                  // base radius
  critical?: boolean;
}

export interface MapEdge {
  from: string;
  to: string;
  weight?: number;               // 0-1, affects opacity
  critical?: boolean;
}

// ─── Expertise areas ─────────────────────────────────────────────────────────
export const expertiseAreas: ExpertiseArea[] = [
  { id: 'operations',          name: 'Operations',              category: 'backend' },
  { id: 'hr-people',           name: 'HR & People',             category: 'frontend' },
  { id: 'finance-payments',    name: 'Finance & Payments',      category: 'devops' },
  { id: 'marketing',           name: 'Marketing',               category: 'sales' },
  { id: 'sales',               name: 'Sales',                   category: 'sales' },
  { id: 'client-relations',    name: 'Client Relations',        category: 'product' },
  { id: 'technology',          name: 'Technology',              category: 'devops' },
  { id: 'legal-compliance',    name: 'Legal & Compliance',      category: 'security' },
  { id: 'banking',             name: 'Banking (EZO Banks)',     category: 'devops' },
];

// ─── Services ───────────────────────────────────────────────────────────────
export const services: Service[] = [
  { id: 'ezobooks',    name: 'EZO Books (Accounting SaaS)',  primaryOwner: 'rishikesh', busFactor: 6 },
  { id: 'ezobanks',    name: 'EZO Banks (Banking Platform)', primaryOwner: 'makarand',  busFactor: 5 },
  { id: 'legaldocs',   name: 'LegalDocs (Legal Document SaaS)', primaryOwner: 'rishikesh', busFactor: 3 },
];

// Helper to generate realistic daily activity arrays (30 points)
const trend = (base: number, variance = 0.4): number[] =>
  Array.from({ length: 30 }, (_, i) => {
    const wave = Math.sin(i * 0.4) * variance;
    const noise = (Math.sin(i * 1.7 + base) + 1) * 0.3;
    return Math.max(0, Math.round(base * (1 + wave + noise)));
  });

// ─── People (31) ────────────────────────────────────────────────────────────
// Expertise scores: normalized per-topic with top contributor = 95-100, scaled
// by evidenceCount ratio. Stats are role-appropriate:
//   Leadership  → 0 commits, high decisions/docs/messages
//   Operations  → 0 commits, moderate decisions, high messages (field coord)
//   HR          → 0 commits, moderate decisions, high messages (recruitment)
//   Sales/Mktg  → 0 commits, low-moderate decisions, very high messages (client calls)
//   Finance     → 0 commits, moderate decisions, moderate messages (invoices/approvals)
//   Technology  → commits, low decisions, moderate docs, moderate messages
//   Legal       → 0 commits, low decisions, high docs, low messages
export const people: Person[] = [
  {
    id: 'gk',
    name: 'Gauravkumar Kate',
    initials: 'GK',
    role: 'CEO / Founder',
    team: 'leadership',
    email: 'gauravkumar@ezobooks.in',
    avatarColor: '#1d4ed8',
    status: 'active',
    accessLevel: 'admin',
    joinedAt: '2021-01-15',
    riskLevel: 'medium',
    riskNote: 'Spans HR, Finance, and Operations oversight - high context load.',
    ownsServices: ['ezobooks', 'ezobanks'],
    expertise: {
      'hr-people': 72, 'finance-payments': 52, 'operations': 42, 'sales': 30, 'client-relations': 25,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 8, docsWritten: 6, messagesSent: 340 },
    activityTrend: trend(5),
  },
  {
    id: 'rishikesh',
    name: 'Rishikesh Sakarkar',
    initials: 'RS',
    role: 'COO / Operations Head',
    team: 'leadership',
    email: 'rishikesh@ezobooks.in',
    avatarColor: '#dc2626',
    status: 'active',
    accessLevel: 'admin',
    joinedAt: '2021-03-01',
    riskLevel: 'high',
    riskNote: 'Owns decisions across Operations, Sales, Finance, Technology - overloaded.',
    ownsServices: ['ezobooks', 'ezobanks', 'legaldocs'],
    expertise: {
      'operations': 95, 'client-relations': 82, 'sales': 75, 'finance-payments': 74, 'hr-people': 40, 'marketing': 28,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 14, docsWritten: 10, messagesSent: 420 },
    activityTrend: trend(7),
  },
  {
    id: 'makarand',
    name: 'Makarand Kate',
    initials: 'MK',
    role: 'Co-founder / Director',
    team: 'leadership',
    email: 'makarand@ezobooks.in',
    avatarColor: '#0a3fb5',
    status: 'active',
    accessLevel: 'admin',
    joinedAt: '2021-01-15',
    riskLevel: 'low',
    ownsServices: ['ezobanks', 'legaldocs'],
    expertise: {
      'hr-people': 55, 'operations': 35, 'finance-payments': 30, 'banking': 45, 'legal-compliance': 28,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 3, docsWritten: 4, messagesSent: 180 },
    activityTrend: trend(3),
  },
  {
    id: 'shri',
    name: 'Shrishail Thote',
    initials: 'ST',
    role: 'Operations Manager',
    team: 'operations',
    email: 'shrishail@ezobooks.in',
    avatarColor: '#7c3aed',
    status: 'active',
    accessLevel: 'editor',
    joinedAt: '2022-02-10',
    riskLevel: 'medium',
    riskNote: 'Heavy cross-domain load: Operations, HR, Finance.',
    ownsServices: ['ezobooks', 'legaldocs'],
    expertise: {
      'operations': 98, 'hr-people': 72, 'finance-payments': 56, 'client-relations': 35, 'sales': 22,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 6, docsWritten: 8, messagesSent: 350 },
    activityTrend: trend(6),
  },
  {
    id: 'mukesh',
    name: 'Mukesh Sethia',
    initials: 'MS',
    role: 'HR Director',
    team: 'hr',
    email: 'mukesh@ezobooks.in',
    avatarColor: '#2563eb',
    status: 'active',
    accessLevel: 'editor',
    joinedAt: '2021-06-01',
    riskLevel: 'medium',
    riskNote: 'Highest evidence count across HR - single point of HR institutional knowledge.',
    ownsServices: ['ezobooks', 'ezobanks'],
    expertise: {
      'hr-people': 100, 'finance-payments': 68, 'operations': 42, 'legal-compliance': 20,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 5, docsWritten: 10, messagesSent: 380 },
    activityTrend: trend(6),
  },
  {
    id: 'shubhamp',
    name: 'Shubham Pardeshi',
    initials: 'SP',
    role: 'Operations Admin',
    team: 'operations',
    email: 'shubhamp@ezobooks.in',
    avatarColor: '#0891b2',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2023-05-15',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'hr-people': 38, 'operations': 22, 'finance-payments': 12,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 0, docsWritten: 2, messagesSent: 95 },
    activityTrend: trend(2),
  },
  {
    id: 'joackin',
    name: 'Joackin Aruputharaj',
    initials: 'JA',
    role: 'Operations Team Lead',
    team: 'operations',
    email: 'joackin@ezobooks.in',
    avatarColor: '#059669',
    status: 'active',
    accessLevel: 'editor',
    joinedAt: '2022-08-01',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'operations': 75, 'sales': 38, 'hr-people': 28, 'client-relations': 22,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 3, docsWritten: 5, messagesSent: 210 },
    activityTrend: trend(4),
  },
  {
    id: 'shubhama',
    name: 'Shubham Augad',
    initials: 'SA',
    role: 'Sales Team Lead',
    team: 'sales',
    email: 'shubham.augad@ezobooks.in',
    avatarColor: '#ea580c',
    status: 'active',
    accessLevel: 'editor',
    joinedAt: '2023-01-10',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'sales': 62, 'operations': 48, 'hr-people': 40, 'client-relations': 30,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 1, docsWritten: 2, messagesSent: 165 },
    activityTrend: trend(3),
  },
  {
    id: 'archita',
    name: 'Archita Bajaj',
    initials: 'AB',
    role: 'HR Manager / Recruiter',
    team: 'hr',
    email: 'archita@ezobooks.in',
    avatarColor: '#d946ef',
    status: 'active',
    accessLevel: 'editor',
    joinedAt: '2022-04-20',
    riskLevel: 'low',
    ownsServices: ['ezobooks'],
    expertise: {
      'hr-people': 92, 'finance-payments': 52, 'operations': 32, 'legal-compliance': 15,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 2, docsWritten: 7, messagesSent: 290 },
    activityTrend: trend(5),
  },
  {
    id: 'divya',
    name: 'Divya Shetty',
    initials: 'DS',
    role: 'Customer Service / Finance',
    team: 'finance',
    email: 'divya@ezobooks.in',
    avatarColor: '#0d9488',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2023-03-05',
    riskLevel: 'low',
    ownsServices: ['ezobooks', 'legaldocs'],
    expertise: {
      'finance-payments': 72, 'client-relations': 60, 'marketing': 28, 'operations': 18,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 4, docsWritten: 4, messagesSent: 170 },
    activityTrend: trend(3),
  },
  {
    id: 'arvind',
    name: 'Arvind',
    initials: 'AR',
    role: 'Customer Engagement Lead',
    team: 'sales',
    email: 'arvind@ezobooks.in',
    avatarColor: '#f59e0b',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2023-07-01',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'client-relations': 65, 'operations': 38, 'sales': 22, 'finance-payments': 15,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 1, docsWritten: 2, messagesSent: 145 },
    activityTrend: trend(2),
  },
  {
    id: 'hamza',
    name: 'Hamza Taibani',
    initials: 'HT',
    role: 'Sales Performance Manager',
    team: 'sales',
    email: 'hamza@ezobooks.in',
    avatarColor: '#6366f1',
    status: 'active',
    accessLevel: 'editor',
    joinedAt: '2023-04-15',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'sales': 85, 'finance-payments': 68, 'operations': 55, 'client-relations': 35, 'hr-people': 20,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 4, docsWritten: 3, messagesSent: 195 },
    activityTrend: trend(3),
  },
  {
    id: 'madhav',
    name: 'Madhav J',
    initials: 'MJ',
    role: 'Procurement / Finance',
    team: 'finance',
    email: 'madhav@ezobooks.in',
    avatarColor: '#84cc16',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2023-09-01',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'finance-payments': 78, 'operations': 38, 'banking': 20,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 2, docsWritten: 3, messagesSent: 120 },
    activityTrend: trend(2),
  },
  {
    id: 'harshit',
    name: 'Harshit Pandey',
    initials: 'HP',
    role: 'Product / Marketing',
    team: 'marketing',
    email: 'harshit@ezobooks.in',
    avatarColor: '#f43f5e',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2023-11-01',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'finance-payments': 52, 'marketing': 72, 'hr-people': 32, 'client-relations': 18,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 2, docsWritten: 3, messagesSent: 140 },
    activityTrend: trend(2),
  },
  {
    id: 'ashish',
    name: 'Ashish Burade',
    initials: 'AB2',
    role: 'Marketing / Sales',
    team: 'marketing',
    email: 'ashish@ezobooks.in',
    avatarColor: '#ec4899',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2024-01-15',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'marketing': 55, 'client-relations': 42, 'sales': 30,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 0, docsWritten: 1, messagesSent: 110 },
    activityTrend: trend(2),
  },
  {
    id: 'mahesh',
    name: 'Mahesh Narale',
    initials: 'MN',
    role: 'Content / Influencer Mktg',
    team: 'marketing',
    email: 'mahesh@ezobooks.in',
    avatarColor: '#a855f7',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2024-02-01',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'marketing': 68, 'hr-people': 45, 'client-relations': 20,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 0, docsWritten: 2, messagesSent: 105 },
    activityTrend: trend(2),
  },
  {
    id: 'amol',
    name: 'Amol Karne',
    initials: 'AK',
    role: 'Tech / Product Lead',
    team: 'tech',
    email: 'amol@ezobooks.in',
    avatarColor: '#14b8a6',
    status: 'active',
    accessLevel: 'editor',
    joinedAt: '2022-10-01',
    riskLevel: 'low',
    ownsServices: ['ezobooks', 'ezobanks'],
    expertise: {
      'technology': 82, 'marketing': 48, 'client-relations': 42, 'operations': 20,
    },
    stats: { commitsThisMonth: 22, decisionsLogged: 2, docsWritten: 4, messagesSent: 130 },
    activityTrend: trend(4),
  },
  {
    id: 'simeen',
    name: 'Simeen A',
    initials: 'SA2',
    role: 'HR Recruiter',
    team: 'hr',
    email: 'simeen@ezobooks.in',
    avatarColor: '#fb923c',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2024-03-01',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'hr-people': 62, 'operations': 42,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 0, docsWritten: 2, messagesSent: 155 },
    activityTrend: trend(2),
  },
  {
    id: 'rahul',
    name: 'Rahul Agrawal',
    initials: 'RA',
    role: 'Operations',
    team: 'operations',
    email: 'rahul@ezobooks.in',
    avatarColor: '#78716c',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2024-05-01',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'operations': 30, 'client-relations': 15, 'finance-payments': 10,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 0, docsWritten: 1, messagesSent: 75 },
    activityTrend: trend(1),
  },
  {
    id: 'shridhar',
    name: 'Shridhar Patil',
    initials: 'SP2',
    role: 'Developer Recruitment',
    team: 'hr',
    email: 'shridhar@ezobooks.in',
    avatarColor: '#8b5cf6',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2024-01-20',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'hr-people': 48, 'technology': 38,
    },
    stats: { commitsThisMonth: 5, decisionsLogged: 0, docsWritten: 2, messagesSent: 95 },
    activityTrend: trend(2),
  },
  {
    id: 'shailesh',
    name: 'Shailesh Moniya',
    initials: 'SM',
    role: 'Customer Engagement',
    team: 'sales',
    email: 'shailesh@ezobooks.in',
    avatarColor: '#22d3ee',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2024-04-01',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'client-relations': 58, 'finance-payments': 32, 'operations': 18,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 0, docsWritten: 1, messagesSent: 125 },
    activityTrend: trend(2),
  },
  {
    id: 'kalpesh',
    name: 'Kalpesh Lambade',
    initials: 'KL',
    role: 'IT / AI Automation',
    team: 'tech',
    email: 'kalpesh@ezobooks.in',
    avatarColor: '#4ade80',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2024-06-01',
    riskLevel: 'low',
    ownsServices: ['ezobooks'],
    expertise: {
      'technology': 92, 'operations': 35,
    },
    stats: { commitsThisMonth: 18, decisionsLogged: 0, docsWritten: 3, messagesSent: 85 },
    activityTrend: trend(3),
  },
  {
    id: 'sumit',
    name: 'Sumit Kadam',
    initials: 'SK',
    role: 'Operations',
    team: 'operations',
    email: 'sumit@ezobooks.in',
    avatarColor: '#94a3b8',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2024-07-01',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'operations': 25, 'client-relations': 12,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 0, docsWritten: 0, messagesSent: 68 },
    activityTrend: trend(1),
  },
  {
    id: 'bhavesh',
    name: 'Bhavesh Shah',
    initials: 'BS',
    role: 'Operations',
    team: 'operations',
    email: 'bhavesh@ezobooks.in',
    avatarColor: '#fbbf24',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2024-08-01',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'operations': 22, 'finance-payments': 10,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 0, docsWritten: 0, messagesSent: 62 },
    activityTrend: trend(1),
  },
  {
    id: 'pratik',
    name: 'Pratik Jadhav',
    initials: 'PJ',
    role: 'Developer',
    team: 'tech',
    email: 'pratik@ezobooks.in',
    avatarColor: '#38bdf8',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2024-09-01',
    riskLevel: 'low',
    ownsServices: ['ezobooks', 'ezobanks'],
    expertise: {
      'technology': 68, 'hr-people': 15,
    },
    stats: { commitsThisMonth: 15, decisionsLogged: 0, docsWritten: 1, messagesSent: 72 },
    activityTrend: trend(3),
  },
  {
    id: 'akash',
    name: 'Akash Shah',
    initials: 'AS',
    role: 'Legal / Compliance',
    team: 'external',
    email: 'akash@ezobooks.in',
    avatarColor: '#64748b',
    status: 'external',
    accessLevel: 'restricted',
    joinedAt: '2023-10-01',
    riskLevel: 'low',
    ownsServices: ['ezobanks', 'legaldocs'],
    expertise: {
      'legal-compliance': 88, 'banking': 65, 'finance-payments': 25,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 1, docsWritten: 6, messagesSent: 78 },
    activityTrend: trend(1),
  },
  {
    id: 'jayesh',
    name: 'Jayesh Saindane',
    initials: 'JS',
    role: 'Operations',
    team: 'operations',
    email: 'jayesh@ezobooks.in',
    avatarColor: '#a78bfa',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2024-10-01',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'operations': 20, 'client-relations': 10,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 0, docsWritten: 0, messagesSent: 58 },
    activityTrend: trend(1),
  },
  {
    id: 'aman',
    name: 'Aman Jha',
    initials: 'AJ',
    role: 'Banking Operations',
    team: 'finance',
    email: 'aman@ezobooks.in',
    avatarColor: '#f472b6',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2024-06-15',
    riskLevel: 'low',
    ownsServices: ['ezobanks'],
    expertise: {
      'banking': 78, 'finance-payments': 55, 'operations': 20,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 0, docsWritten: 2, messagesSent: 92 },
    activityTrend: trend(2),
  },
  {
    id: 'roshan',
    name: 'Roshan Pawar',
    initials: 'RP',
    role: 'Operations',
    team: 'operations',
    email: 'roshan@ezobooks.in',
    avatarColor: '#c084fc',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2025-01-01',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'operations': 18, 'finance-payments': 8,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 0, docsWritten: 0, messagesSent: 55 },
    activityTrend: trend(1),
  },
  {
    id: 'shivnath',
    name: 'Shivnath Jaiswal',
    initials: 'SJ',
    role: 'Operations',
    team: 'operations',
    email: 'shivnath@ezobooks.in',
    avatarColor: '#facc15',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2025-02-01',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'operations': 15, 'client-relations': 8,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 0, docsWritten: 0, messagesSent: 52 },
    activityTrend: trend(1),
  },
  {
    id: 'abhey',
    name: 'Abhey Kaul',
    initials: 'AK2',
    role: 'Banking',
    team: 'finance',
    email: 'abhey@ezobooks.in',
    avatarColor: '#e879f9',
    status: 'active',
    accessLevel: 'viewer',
    joinedAt: '2024-11-01',
    riskLevel: 'low',
    ownsServices: [],
    expertise: {
      'banking': 55, 'finance-payments': 30, 'operations': 12,
    },
    stats: { commitsThisMonth: 0, decisionsLogged: 0, docsWritten: 1, messagesSent: 68 },
    activityTrend: trend(1),
  },
];

// ─── Projects ───────────────────────────────────────────────────────────────
export const projects: Project[] = [
  {
    id: 'ezobooks',
    name: 'EZO Books (Accounting SaaS)',
    description: 'Core accounting SaaS platform - invoicing, GST compliance, ledger management for Indian SMBs.',
    owner: 'rishikesh',
    contributors: ['rishikesh', 'gk', 'mukesh', 'shri', 'archita', 'divya', 'amol', 'kalpesh', 'pratik'],
    team: 'operations',
    health: 'on-track',
    progress: 72,
    deadline: '2026-06-30',
    startedAt: '2021-01-15',
  },
  {
    id: 'ezobanks',
    name: 'EZO Banks (Banking Platform)',
    description: 'Neo-banking platform - account management, UPI, NEFT/RTGS, and compliance modules.',
    owner: 'makarand',
    contributors: ['makarand', 'gk', 'rishikesh', 'mukesh', 'amol', 'pratik', 'akash', 'aman'],
    team: 'tech',
    health: 'at-risk',
    progress: 48,
    deadline: '2026-09-30',
    risk: 'Truecaller SDK integration delayed by Holi reschedule. Compliance review pending with Akash.',
    startedAt: '2022-06-01',
  },
  {
    id: 'legaldocs',
    name: 'LegalDocs (Legal Document SaaS)',
    description: 'Automated legal document generation - contracts, NDAs, compliance certificates.',
    owner: 'rishikesh',
    contributors: ['rishikesh', 'makarand', 'shri', 'divya', 'akash'],
    team: 'external',
    health: 'on-track',
    progress: 35,
    deadline: '2026-12-31',
    startedAt: '2025-06-01',
  },
];

// ─── Decisions (12) ─────────────────────────────────────────────────────────
export const decisions: Decision[] = [
  {
    id: 'd-001',
    title: 'Sales team must hit 50% target by mid-March or face termination',
    description: 'Rishikesh mandated that sales team members not meeting 50% of their DRR targets by mid-March will face termination.',
    author: 'rishikesh',
    date: '2026-03-09',
    impact: 'high',
    category: 'personnel',
    sources: [{ platform: 'slack', count: 6 }, { platform: 'gmail', count: 3 }],
    linkedProjectIds: ['ezobooks'],
    executionLagDays: 1,
  },
  {
    id: 'd-002',
    title: 'Pilot Mera Monitor employee tracking tool',
    description: 'Approved pilot program for Mera Monitor to track employee activity and productivity metrics across the operations team.',
    author: 'rishikesh',
    date: '2026-03-09',
    impact: 'medium',
    category: 'ops',
    sources: [{ platform: 'slack', count: 4 }, { platform: 'gmail', count: 2 }],
    linkedProjectIds: ['ezobooks'],
    executionLagDays: 3,
  },
  {
    id: 'd-003',
    title: 'Terminate Shakir Qureshi for misconduct during suspension',
    description: 'Joackin executed the termination of Shakir Qureshi after misconduct observed during his suspension period.',
    author: 'joackin',
    date: '2026-03-09',
    impact: 'medium',
    category: 'personnel',
    sources: [{ platform: 'slack', count: 3 }, { platform: 'gmail', count: 4 }],
    linkedProjectIds: [],
    executionLagDays: 0,
  },
  {
    id: 'd-004',
    title: 'Accept GMB SEO Optimization Services proposal',
    description: 'Harshit approved the Google My Business SEO optimization services proposal to improve organic search visibility.',
    author: 'harshit',
    date: '2026-02-26',
    impact: 'medium',
    category: 'ops',
    sources: [{ platform: 'gmail', count: 5 }, { platform: 'slack', count: 2 }],
    linkedProjectIds: ['ezobooks'],
    executionLagDays: 5,
  },
  {
    id: 'd-005',
    title: 'Deactivate 3 unused Acefone VoIP licenses',
    description: 'Rishikesh approved deactivation of 3 unused Acefone VoIP licenses to reduce monthly SaaS spend.',
    author: 'rishikesh',
    date: '2026-02-26',
    impact: 'low',
    category: 'cost',
    sources: [{ platform: 'slack', count: 3 }, { platform: 'gmail', count: 1 }],
    linkedProjectIds: [],
    executionLagDays: 2,
  },
  {
    id: 'd-006',
    title: 'Improve customer messaging for payment failure notifications',
    description: 'Divya proposed and got approval for improved customer-facing messages when payment failures occur, reducing support tickets.',
    author: 'divya',
    date: '2026-02-24',
    impact: 'medium',
    category: 'product',
    sources: [{ platform: 'slack', count: 5 }, { platform: 'gmail', count: 3 }],
    linkedProjectIds: ['ezobooks'],
    executionLagDays: 4,
  },
  {
    id: 'd-007',
    title: 'Reschedule Truecaller SDK integration meeting (Holi)',
    description: 'Rishikesh rescheduled the Truecaller SDK integration meeting due to Holi holiday conflict.',
    author: 'rishikesh',
    date: '2026-02-27',
    impact: 'low',
    category: 'tech',
    sources: [{ platform: 'slack', count: 2 }, { platform: 'google-meet', count: 1 }],
    linkedProjectIds: ['ezobanks'],
    executionLagDays: 0,
  },
  {
    id: 'd-008',
    title: 'Transfer Rs 14,400 booking toward 3-year software renewal',
    description: 'Divya approved transferring the Rs 14,400 booking amount as partial payment toward a 3-year software license renewal.',
    author: 'divya',
    date: '2026-02-12',
    impact: 'low',
    category: 'cost',
    sources: [{ platform: 'gmail', count: 3 }, { platform: 'slack', count: 1 }],
    linkedProjectIds: ['ezobooks'],
    executionLagDays: 1,
  },
  {
    id: 'd-009',
    title: 'Map attrition rates to individual Team Leads',
    description: 'Rishikesh approved mapping attrition rates to individual team leads to improve accountability and retention tracking.',
    author: 'rishikesh',
    date: '2025-10-15',
    impact: 'high',
    category: 'personnel',
    sources: [{ platform: 'slack', count: 4 }, { platform: 'google-meet', count: 2 }],
    linkedProjectIds: [],
    executionLagDays: 7,
  },
  {
    id: 'd-010',
    title: 'Redistribute DRR targets after Shivani resignation',
    description: 'Hamza executed redistribution of daily revenue run-rate targets among remaining sales reps after Shivani resigned.',
    author: 'hamza',
    date: '2026-03-09',
    impact: 'medium',
    category: 'ops',
    sources: [{ platform: 'slack', count: 5 }, { platform: 'gmail', count: 2 }],
    linkedProjectIds: ['ezobooks'],
    executionLagDays: 0,
  },
  {
    id: 'd-011',
    title: 'Proceed with thermal printer order for billing machines',
    description: 'Madhav approved the procurement of thermal printers for billing machine upgrades across operations.',
    author: 'madhav',
    date: '2026-02-20',
    impact: 'low',
    category: 'ops',
    sources: [{ platform: 'gmail', count: 4 }],
    linkedProjectIds: ['ezobooks'],
    executionLagDays: 3,
  },
  {
    id: 'd-012',
    title: 'Final warning for PKT activity process non-compliance',
    description: 'Hamza issued a final warning regarding non-compliance with the PKT (Proactive Key Task) activity tracking process.',
    author: 'hamza',
    date: '2026-03-09',
    impact: 'medium',
    category: 'personnel',
    sources: [{ platform: 'slack', count: 4 }, { platform: 'gmail', count: 1 }],
    linkedProjectIds: [],
    executionLagDays: 0,
  },
];

// ─── Activity events (15) ──────────────────────────────────────────────────
export const events: ActivityEvent[] = [
  {
    id: 'e-001',
    timestamp: '2026-03-09T17:30:00+05:30',
    type: 'decision',
    title: 'Sales ultimatum issued - 50% DRR target or termination',
    description: 'Rishikesh mandated mid-March target compliance for all sales reps.',
    people: ['rishikesh', 'hamza', 'shubhama'],
    priority: 'critical',
    source: 'slack',
  },
  {
    id: 'e-002',
    timestamp: '2026-03-09T16:00:00+05:30',
    type: 'decision',
    title: 'Mera Monitor pilot approved for operations team',
    description: 'Employee tracking tool to be piloted across ops team for productivity monitoring.',
    people: ['rishikesh', 'kalpesh'],
    priority: 'warning',
    source: 'slack',
  },
  {
    id: 'e-003',
    timestamp: '2026-03-09T14:45:00+05:30',
    type: 'incident',
    title: 'Shakir Qureshi terminated - misconduct during suspension',
    description: 'HR action executed by Joackin following documented misconduct.',
    people: ['joackin', 'mukesh'],
    priority: 'warning',
    source: 'gmail',
  },
  {
    id: 'e-004',
    timestamp: '2026-03-09T12:20:00+05:30',
    type: 'decision',
    title: 'DRR targets redistributed after Shivani resignation',
    description: 'Hamza redistributed daily revenue targets among remaining sales team.',
    people: ['hamza', 'shubhama'],
    priority: 'warning',
    source: 'slack',
  },
  {
    id: 'e-005',
    timestamp: '2026-03-09T10:00:00+05:30',
    type: 'decision',
    title: 'PKT process non-compliance final warning issued',
    description: 'Hamza issued final warning for PKT activity tracking non-compliance.',
    people: ['hamza'],
    priority: 'warning',
    source: 'slack',
  },
  {
    id: 'e-006',
    timestamp: '2026-02-27T11:30:00+05:30',
    type: 'project',
    title: 'Truecaller SDK meeting rescheduled due to Holi',
    description: 'Integration meeting pushed back - impacts EZO Banks timeline.',
    people: ['rishikesh', 'amol'],
    priority: 'info',
    source: 'google-meet',
  },
  {
    id: 'e-007',
    timestamp: '2026-02-26T15:00:00+05:30',
    type: 'decision',
    title: 'GMB SEO optimization proposal accepted',
    description: 'Harshit approved marketing proposal to boost Google My Business visibility.',
    people: ['harshit', 'ashish'],
    priority: 'info',
    source: 'gmail',
  },
  {
    id: 'e-008',
    timestamp: '2026-02-26T10:15:00+05:30',
    type: 'decision',
    title: '3 Acefone VoIP licenses deactivated',
    description: 'Unused licenses removed to cut monthly SaaS spend.',
    people: ['rishikesh'],
    priority: 'info',
    source: 'slack',
  },
  {
    id: 'e-009',
    timestamp: '2026-02-24T14:00:00+05:30',
    type: 'knowledge',
    title: 'Payment failure notification messaging improved',
    description: 'Divya updated customer-facing payment failure messages in the EZO Books platform.',
    people: ['divya'],
    priority: 'info',
    source: 'slack',
  },
  {
    id: 'e-010',
    timestamp: '2026-02-20T11:45:00+05:30',
    type: 'project',
    title: 'Thermal printer procurement approved for billing',
    description: 'Madhav approved order for thermal printers for billing machine upgrades.',
    people: ['madhav'],
    priority: 'info',
    source: 'gmail',
  },
  {
    id: 'e-011',
    timestamp: '2026-02-12T16:30:00+05:30',
    type: 'decision',
    title: 'Rs 14,400 booking transferred to software renewal',
    description: 'Divya processed partial payment toward 3-year license renewal.',
    people: ['divya'],
    priority: 'info',
    source: 'gmail',
  },
  {
    id: 'e-012',
    timestamp: '2026-03-08T09:00:00+05:30',
    type: 'risk',
    title: 'Sales attrition risk - Shivani resigned',
    description: 'DRR gap created, remaining reps under pressure ahead of mid-March deadline.',
    people: ['hamza', 'rishikesh'],
    priority: 'critical',
    source: 'gmail',
  },
  {
    id: 'e-013',
    timestamp: '2026-03-05T10:00:00+05:30',
    type: 'knowledge',
    title: 'Ops process documentation updated for Q1 review',
    description: 'Shrishail updated standard operating procedures for Q1 operations audit.',
    people: ['shri'],
    priority: 'info',
    source: 'slack',
  },
  {
    id: 'e-014',
    timestamp: '2026-03-03T14:30:00+05:30',
    type: 'project',
    title: 'EZO Banks compliance review scheduled with Akash',
    description: 'Legal compliance check for banking module features before launch.',
    people: ['akash', 'makarand'],
    priority: 'info',
    source: 'google-meet',
  },
  {
    id: 'e-015',
    timestamp: '2026-02-18T09:45:00+05:30',
    type: 'knowledge',
    title: 'AI automation workflow documented by Kalpesh',
    description: 'Kalpesh documented the new AI-driven automation workflows for ops processes.',
    people: ['kalpesh'],
    priority: 'info',
    source: 'slack',
  },
];

// ─── Team contributions (12 weeks of stacked bars) ──────────────────────────
export const teamContributionWeeks: { week: string; operations: number; sales: number; tech: number }[] = [
  { week: 'W01', operations: 48, sales: 22, tech: 18 },
  { week: 'W02', operations: 52, sales: 25, tech: 20 },
  { week: 'W03', operations: 55, sales: 28, tech: 22 },
  { week: 'W04', operations: 50, sales: 24, tech: 19 },
  { week: 'W05', operations: 58, sales: 30, tech: 25 },
  { week: 'W06', operations: 45, sales: 26, tech: 21 },
  { week: 'W07', operations: 53, sales: 27, tech: 23 },
  { week: 'W08', operations: 60, sales: 32, tech: 28 },
  { week: 'W09', operations: 56, sales: 29, tech: 26 },
  { week: 'W10', operations: 62, sales: 34, tech: 30 },
  { week: 'W11', operations: 58, sales: 31, tech: 27 },
  { week: 'W12', operations: 65, sales: 35, tech: 32 },
];

// ─── Decisions timeline (8 weeks stacked by category) ───────────────────────
export const decisionsTimeline: { week: string; tech: number; product: number; ops: number; cost: number; personnel: number }[] = [
  { week: 'W05', tech: 0, product: 0, ops: 2, cost: 0, personnel: 1 },
  { week: 'W06', tech: 0, product: 1, ops: 1, cost: 1, personnel: 0 },
  { week: 'W07', tech: 1, product: 0, ops: 2, cost: 0, personnel: 1 },
  { week: 'W08', tech: 0, product: 1, ops: 3, cost: 1, personnel: 0 },
  { week: 'W09', tech: 1, product: 0, ops: 2, cost: 1, personnel: 2 },
  { week: 'W10', tech: 1, product: 1, ops: 3, cost: 2, personnel: 1 },
  { week: 'W11', tech: 0, product: 0, ops: 4, cost: 1, personnel: 2 },
  { week: 'W12', tech: 1, product: 1, ops: 4, cost: 0, personnel: 4 },
];

// ─── Knowledge sources breakdown (donut) ────────────────────────────────────
export const knowledgeSources = [
  { name: 'Gmail',        value: 45, color: '#ea4335' },
  { name: 'Slack',        value: 30, color: '#7c3aed' },
  { name: 'Google Meet',  value: 15, color: '#10b981' },
  { name: 'Jira',         value: 8,  color: '#2563eb' },
  { name: 'GitHub',       value: 2,  color: '#0a3fb5' },
];

// ─── Knowledge map (curated static layout, 1000x700 viewport) ───────────────
export const mapNodes: MapNode[] = [
  // Key people
  { id: 'gk',         type: 'person', label: 'Gauravkumar Kate',     x: 500, y: 80,  size: 24 },
  { id: 'rishikesh',  type: 'person', label: 'Rishikesh Sakarkar',   x: 500, y: 250, size: 28, critical: true },
  { id: 'makarand',   type: 'person', label: 'Makarand Kate',        x: 780, y: 120, size: 22 },
  { id: 'mukesh',     type: 'person', label: 'Mukesh Sethia',        x: 200, y: 160, size: 24 },
  { id: 'shri',       type: 'person', label: 'Shrishail Thote',      x: 350, y: 380, size: 22 },
  { id: 'archita',    type: 'person', label: 'Archita Bajaj',        x: 120, y: 300, size: 18 },
  { id: 'joackin',    type: 'person', label: 'Joackin Aruputharaj',  x: 620, y: 380, size: 18 },
  { id: 'hamza',      type: 'person', label: 'Hamza Taibani',        x: 870, y: 340, size: 18 },
  { id: 'divya',      type: 'person', label: 'Divya Shetty',         x: 300, y: 520, size: 16 },
  { id: 'amol',       type: 'person', label: 'Amol Karne',           x: 680, y: 200, size: 18 },
  { id: 'akash',      type: 'person', label: 'Akash Shah',           x: 900, y: 500, size: 16 },

  // Projects
  { id: 'ezobooks',   type: 'project', label: 'EZO Books',           x: 420, y: 460, size: 26 },
  { id: 'ezobanks',   type: 'project', label: 'EZO Banks',           x: 750, y: 400, size: 22 },
  { id: 'legaldocs',  type: 'project', label: 'LegalDocs',           x: 580, y: 580, size: 18 },

  // Key decisions
  { id: 'd-001', type: 'decision', label: 'Sales ultimatum',         x: 870, y: 220, size: 14, critical: true },
  { id: 'd-002', type: 'decision', label: 'Mera Monitor pilot',      x: 600, y: 120, size: 14 },
  { id: 'd-009', type: 'decision', label: 'Attrition → TLs',         x: 480, y: 600, size: 14 },
  { id: 'd-010', type: 'decision', label: 'DRR redistribution',      x: 920, y: 420, size: 14 },
];

export const mapEdges: MapEdge[] = [
  // Person → person (collaboration)
  { from: 'rishikesh',  to: 'gk',        weight: 0.90, critical: true },
  { from: 'mukesh',     to: 'gk',        weight: 0.85 },
  { from: 'shri',       to: 'gk',        weight: 0.80 },
  { from: 'makarand',   to: 'gk',        weight: 0.88 },
  { from: 'archita',    to: 'mukesh',     weight: 0.85 },
  { from: 'simeen',     to: 'mukesh',     weight: 0.78 },
  { from: 'joackin',    to: 'rishikesh',  weight: 0.82 },
  { from: 'hamza',      to: 'rishikesh',  weight: 0.78 },
  { from: 'shubhama',   to: 'rishikesh',  weight: 0.75 },
  { from: 'divya',      to: 'rishikesh',  weight: 0.72 },
  { from: 'kalpesh',    to: 'makarand',   weight: 0.75 },
  { from: 'amol',       to: 'rishikesh',  weight: 0.70 },
  { from: 'shubhamp',   to: 'shri',       weight: 0.72 },
  { from: 'arvind',     to: 'rishikesh',  weight: 0.70 },

  // Person → project
  { from: 'gk',         to: 'ezobooks',   weight: 0.90 },
  { from: 'gk',         to: 'ezobanks',   weight: 0.85 },
  { from: 'rishikesh',  to: 'ezobooks',   weight: 0.90, critical: true },
  { from: 'rishikesh',  to: 'ezobanks',   weight: 0.85 },
  { from: 'rishikesh',  to: 'legaldocs',  weight: 0.80 },
  { from: 'makarand',   to: 'ezobanks',   weight: 0.90 },
  { from: 'makarand',   to: 'ezobooks',   weight: 0.80 },
  { from: 'makarand',   to: 'legaldocs',  weight: 0.75 },
  { from: 'mukesh',     to: 'ezobooks',   weight: 0.90 },
  { from: 'shri',       to: 'ezobooks',   weight: 0.85 },
  { from: 'archita',    to: 'ezobooks',   weight: 0.85 },
  { from: 'divya',      to: 'ezobooks',   weight: 0.80 },
  { from: 'amol',       to: 'ezobooks',   weight: 0.80 },
  { from: 'amol',       to: 'ezobanks',   weight: 0.75 },
  { from: 'kalpesh',    to: 'ezobooks',   weight: 0.80 },
  { from: 'pratik',     to: 'ezobooks',   weight: 0.75 },
  { from: 'pratik',     to: 'ezobanks',   weight: 0.70 },
  { from: 'akash',      to: 'ezobanks',   weight: 0.85 },
  { from: 'akash',      to: 'legaldocs',  weight: 0.80 },
  { from: 'aman',       to: 'ezobanks',   weight: 0.85 },

  // Leadership → decisions
  { from: 'rishikesh',  to: 'd-001',      weight: 0.90, critical: true },
  { from: 'rishikesh',  to: 'd-002',      weight: 0.85 },
  { from: 'rishikesh',  to: 'd-009',      weight: 0.80 },
  { from: 'hamza',      to: 'd-010',      weight: 0.85 },
];

// ─── KPI sparklines (small trend arrays for StatCards) ──────────────────────
export const kpis = {
  activeProjects:      { value: 3,   delta: 0,     trend: [2, 2, 3, 3, 3, 3, 3, 3] },
  knowledgeAtRisk:     { value: 0,   delta: 0,     trend: [0, 0, 0, 0, 0, 0, 0, 0] },
  decisionsThisWeek:   { value: 14,  delta: +4,    trend: [6, 8, 7, 10, 9, 11, 12, 14] },
  teamVelocity:        { value: 72,  delta: +2,    trend: [65, 66, 68, 69, 70, 70, 71, 72] },
  // Median days from decision logged → first execution signal
  executionLag:        { value: 3,   delta: -1,    trend: [5, 5, 4, 4, 4, 3, 3, 3] },
  // Avg onboarding readiness across teams (0-100)
  onboardingReadiness: { value: 55,  delta: -3,    trend: [60, 59, 58, 58, 57, 56, 55, 55] },
};

// ─── Onboarding readiness score per team (0-100) ────────────────────────────
export const onboardingReadiness: Record<TeamId, number> = {
  leadership: 85,   // Founders have deep context
  operations: 58,   // Large team but process-heavy, moderate docs
  hr:         72,   // Well-documented policies, Mukesh + Archita knowledge
  finance:    55,   // Scattered across Divya, Madhav, Aman, Abhey
  tech:       48,   // Thin team - Amol, Kalpesh, Pratik, low bus factor
  sales:      60,   // Hamza and Shubham cover, some gaps
  marketing:  50,   // Small team, limited documentation
  external:   0,
};

// ─── Cross-team collaboration (interactions per week, symmetric) ────────────
export const teamCollaboration: Array<{ from: TeamId; to: TeamId; volume: number }> = [
  { from: 'operations', to: 'leadership', volume: 35 },  // Ops ↔ Leadership heavy
  { from: 'operations', to: 'sales',      volume: 18 },  // Ops ↔ Sales
  { from: 'operations', to: 'hr',         volume: 12 },  // Ops ↔ HR
  { from: 'operations', to: 'finance',    volume: 14 },  // Ops ↔ Finance
  { from: 'operations', to: 'tech',       volume: 10 },  // Ops ↔ Tech
  { from: 'hr',         to: 'leadership', volume: 22 },  // HR ↔ Leadership
  { from: 'hr',         to: 'sales',      volume: 8 },   // HR ↔ Sales
  { from: 'finance',    to: 'leadership', volume: 14 },  // Finance ↔ Leadership
  { from: 'tech',       to: 'leadership', volume: 9 },   // Tech ↔ Leadership
  { from: 'sales',      to: 'marketing',  volume: 16 },  // Sales ↔ Marketing
  { from: 'marketing',  to: 'leadership', volume: 10 },  // Marketing ↔ Leadership
  { from: 'leadership', to: 'sales',      volume: 20 },  // Leadership ↔ Sales
];

// ─── Detected decision contradictions ───────────────────────────────────────
export interface Contradiction {
  id: string;
  decisionA: { id: string; title: string; date: string; author: string };
  decisionB: { id: string; title: string; date: string; author: string };
  severity: 'low' | 'medium' | 'high';
  summary: string;
  detectedAt: string;
}

export const contradictions: Contradiction[] = [
  {
    id: 'c-001',
    decisionA: { id: 'd-001', title: 'Sales team must hit 50% target or face termination', date: '2026-03-09', author: 'rishikesh' },
    decisionB: { id: 'd-010', title: 'Redistribute DRR targets after Shivani resignation', date: '2026-03-09', author: 'hamza' },
    severity: 'high',
    summary: 'Termination threat for missing targets, but targets were just redistributed due to attrition - remaining reps face higher quotas and termination risk simultaneously',
    detectedAt: '2026-03-09T18:00:00+05:30',
  },
  {
    id: 'c-002',
    decisionA: { id: 'd-005', title: 'Deactivate 3 unused Acefone VoIP licenses', date: '2026-02-26', author: 'rishikesh' },
    decisionB: { id: 'd-002', title: 'Pilot Mera Monitor employee tracking tool', date: '2026-03-09', author: 'rishikesh' },
    severity: 'medium',
    summary: 'Cost-cutting on VoIP licenses while simultaneously onboarding a new SaaS monitoring tool - conflicting spend signals',
    detectedAt: '2026-03-09T17:00:00+05:30',
  },
];

// ─── Knowledge capture pulse (live-feed events from Loremind itself) ────────
export interface PulseEvent {
  id: string;
  timestamp: string;
  source: 'slack' | 'github' | 'gmail' | 'jira' | 'fireflies';
  type: 'decision' | 'capture' | 'risk' | 'gap' | 'link';
  message: string;
}

export const pulseEvents: PulseEvent[] = [
  { id: 'p-001', timestamp: '11:42', source: 'slack',  type: 'decision', message: 'Decision detected: sales team must hit 50% DRR target by mid-March' },
  { id: 'p-002', timestamp: '11:38', source: 'slack',  type: 'decision', message: 'Decision detected: pilot Mera Monitor for operations team' },
  { id: 'p-003', timestamp: '11:34', source: 'gmail',  type: 'capture',  message: 'Email indexed: "Termination - Shakir Qureshi" · HR action completed' },
  { id: 'p-004', timestamp: '11:30', source: 'slack',  type: 'risk',     message: 'Risk detected: DRR targets redistributed after Shivani resignation' },
  { id: 'p-005', timestamp: '11:22', source: 'gmail',  type: 'capture',  message: 'Email indexed: "GMB SEO Optimization - Proposal Accepted"' },
  { id: 'p-006', timestamp: '11:14', source: 'slack',  type: 'capture',  message: 'New thread captured in #operations · Mera Monitor setup instructions · 12 messages' },
  { id: 'p-007', timestamp: '11:05', source: 'gmail',  type: 'gap',      message: 'Knowledge gap: Truecaller SDK integration owner unclear after reschedule' },
  { id: 'p-008', timestamp: '10:58', source: 'slack',  type: 'decision', message: 'Decision: final warning for PKT activity non-compliance' },
  { id: 'p-009', timestamp: '10:51', source: 'jira',   type: 'link',     message: 'OPS-342 linked to thermal printer procurement decision (d-011)' },
  { id: 'p-010', timestamp: '10:44', source: 'fireflies', type: 'capture', message: 'Meeting summary indexed · "Weekly Ops Review Mar 7" · 6 action items' },
  { id: 'p-011', timestamp: '10:37', source: 'slack',  type: 'link',     message: 'Linked attrition mapping decision (d-009) to Q1 retention report' },
  { id: 'p-012', timestamp: '10:30', source: 'gmail',  type: 'capture',  message: 'Email indexed: "Rs 14,400 booking transfer confirmation" by Divya' },
];

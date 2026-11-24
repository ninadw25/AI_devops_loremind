/**
 * Knowledge-continuity API client.
 *
 * Talks to the lore-engine continuity endpoints. In dev/demo the frontend points
 * VITE_CORE_URL straight at lore-engine (:8000) with QUERY_OPEN_ACCESS=true and
 * INTERNAL_AUTH_SECRET unset, so the seeded Bearer JWT is accepted directly and
 * scopes may be omitted (default []). Same auth pattern as services/loreApi.ts.
 *
 * Endpoints:
 *   POST /api/continuity/radar    → bus-factor summary + at-risk topics
 *   POST /api/experts             → "who knows about X", ranked by evidence
 *   POST /api/offboarding/report  → leaver cockpit
 *   POST /api/onboarding/path     → predecessor learning path
 *   POST /api/people/search       → person picker
 */

// Behind the one-day demo proxy the browser's Authorization header carries the proxy password, so the
// app token travels in X-Lore-Token and the proxy moves it back to Authorization (deploy/oneday/Caddyfile).
const TOKEN_HEADER = import.meta.env.VITE_TOKEN_HEADER || 'Authorization';
const API_URL = import.meta.env.VITE_CORE_URL || 'http://localhost:8000';
const AUTH_STORAGE_KEY = 'lore_auth';

function getToken(): string | null {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    return raw ? (JSON.parse(raw).token as string) : null;
  } catch {
    return null;
  }
}

export class ContinuityApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ContinuityApiError';
    this.status = status;
  }
}

async function post<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const token = getToken();
  const res = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { [TOKEN_HEADER]: `Bearer ${token}` } : {}),
    },
    // Scopes default to [] — the demo engine runs open-access. When behind the
    // gateway these are injected server-side and ignored from the client body.
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ContinuityApiError(res.status, await res.text());
  }
  return res.json();
}

// ─── Types (mirror the engine response models) ─────────────────────────────

export interface Holder {
  person_id: string;
  name: string | null;
  is_internal?: boolean;
  evidence_count: number;
}

export interface TopicRisk {
  topic_id: string;
  topic_name: string | null;
  holder_count: number;
  sole_holder: boolean;
  total_evidence: number;
  holders: Holder[];
}

export interface CoverageSummary {
  total_topics: number;
  sole_held: number;
  at_risk: number;
}

export interface RadarResponse {
  summary: CoverageSummary;
  topics: TopicRisk[];
}

export interface EvidenceItem {
  id: string;
  type: string;
  description: string | null;
  thread_id: string | null;
}

export interface Expert {
  person_id: string;
  name: string | null;
  email: string | null;
  is_internal: boolean;
  evidence_count: number;
  expert_weight: number;
  total_activity: number;
  evidence: EvidenceItem[];
}

export interface ExpertsResponse {
  topic: string;
  experts: Expert[];
}

export interface PersonHeader {
  name: string | null;
  email: string | null;
  is_internal: boolean | null;
  organization: string | null;
  reports_to: string | null;
  direct_reports: string[];
}

export interface OwnedContent {
  decisions: EvidenceItem[];
  tasks: EvidenceItem[];
  facts: EvidenceItem[];
  events: EvidenceItem[];
}

export interface RiskWorkItem {
  topic_id: string | null;
  topic_name: string | null;
  holder_count: number;
  sole_holder: boolean;
  my_evidence: number;
  question: string;
  suggested_inheritors: Holder[];
}

export interface OffboardingReport {
  person_id: string;
  header: PersonHeader;
  owned: OwnedContent;
  worklist: RiskWorkItem[];
  generated_at: string;
}

export interface KeyDecision {
  id: string | null;
  description: string | null;
  thread_id: string | null;
  rationale: string[];
  topics: string[];
}

export interface PathPerson {
  person_id: string;
  name: string | null;
  is_internal: boolean;
  weight: number;
}

export interface LearningPath {
  person_id: string;
  header: PersonHeader;
  focus_areas: string[];
  key_decisions: KeyDecision[];
  people: PathPerson[];
  inherited_work: EvidenceItem[];
  generated_at: string;
}

export interface PersonHit {
  person_id: string;
  name: string | null;
  primary_email: string | null;
  is_internal: boolean | null;
}

// ─── Calls ─────────────────────────────────────────────────────────────────

export const getRadar = (maxHolders = 2, k = 40) =>
  post<RadarResponse>('/api/continuity/radar', { max_holders: maxHolders, k });

export const findExperts = (topic: string, k = 8) =>
  post<ExpertsResponse>('/api/experts', { topic, k });

export const getOffboardingReport = (personId: string, maxHolders = 2) =>
  post<OffboardingReport>('/api/offboarding/report', { person_id: personId, max_holders: maxHolders });

export const getOnboardingPath = (personId: string) =>
  post<LearningPath>('/api/onboarding/path', { person_id: personId });

export const searchPeople = (q: string, k = 8) =>
  post<{ people: PersonHit[] }>('/api/people/search', { q, k });

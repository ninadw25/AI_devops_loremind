/**
 * Lore API client.
 *
 * Calls the knowledge-base query/threads endpoints through the CORE-BACKEND
 * GATEWAY (VITE_CORE_URL, :8001) - NOT lore-engine directly. The gateway
 * verifies the caller's JWT, injects the user's RBAC content scopes, and
 * forwards to lore-engine with the internal service secret. Querying lore-engine
 * (:8000) directly fails with "Authentication required" (its routes require the
 * gateway's credentials), so every request here carries the Bearer JWT.
 */

const API_URL = import.meta.env.VITE_CORE_URL || 'http://localhost:8001';
// Internal service secret — must match lore-engine INTERNAL_AUTH_SECRET.
// In demo mode (direct engine access) this is the dev default. Empty when
// going through the gateway (gateway injects its own header server-side).
// Behind the one-day demo proxy the browser's Authorization header carries the proxy password, so the
// app token travels in X-Lore-Token and the proxy moves it back to Authorization (deploy/oneday/Caddyfile).
const TOKEN_HEADER = import.meta.env.VITE_TOKEN_HEADER || 'Authorization';
const INTERNAL_SECRET = import.meta.env.VITE_INTERNAL_AUTH_SECRET || '';
const AUTH_STORAGE_KEY = 'lore_auth';
// Course demo: no gateway, so the UI asks the engine for unscoped (super_admin) access.
const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === 'true';

/** Read the persisted Better Auth JWT (same store the admin clients use). */
function getToken(): string | null {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    return raw ? (JSON.parse(raw).token as string) : null;
  } catch {
    return null;
  }
}

// ─── Backend types ────────────────────────────────────────────────────────────

export interface BackendSourceRef {
  type:        string;
  description: string;
  thread_id:   string;
  score:       number;
  sensitivity: string;
}

export interface BackendQueryResponse {
  answer:     string;
  confidence: string;
  sources:    BackendSourceRef[];
  stats:      Record<string, unknown>;
}

// ─── Thread / turn types ──────────────────────────────────────────────────────

export interface ThreadSummary {
  thread_id:    string;
  title:        string;
  turn_count:   number;
  total_tokens: number;
  status:       string;
  created_at:   string;
  updated_at:   string;
}

export interface TurnSummary {
  turn_id:    string;
  sequence:   number;
  question:   string;
  answer:     string | null;
  status:     string;
  latency_ms: number | null;
  created_at: string;
}

// ─── SSE stream event shapes ──────────────────────────────────────────────────

export interface ThreadIdEvent { type: 'thread_id'; data: string }
export interface IntentEvent  { type: 'intent';    data: Record<string, unknown> }
export interface SourcesEvent { type: 'sources';   data: BackendSourceRef[] }
export interface TokenEvent   { type: 'token';     data: string }
export interface DoneEvent    { type: 'done';      data: { confidence: string; answer_type?: string } }
export interface ErrorEvent   { type: 'error';     data: string }

export type StreamEvent = ThreadIdEvent | IntentEvent | SourcesEvent | TokenEvent | DoneEvent | ErrorEvent;

// ─── Legacy type (used by EvidenceCard / EvidenceSection) ────────────────────

export interface CitationDetail {
  chunk_id: string;
  content: string;
  type: string;
  confidence: number;
  people_involved: string[];
  topics: string[];
  projects: string[];
  source_platform: string;
  source_channel: string;
  knowledge_date: string | null;
  relevance_score: number;
}

// ─── Error class ─────────────────────────────────────────────────────────────

export class LoreApiError extends Error {
  status: number;
  body: string;
  constructor(status: number, body: string) {
    super(`API error ${status}: ${body}`);
    this.name = 'LoreApiError';
    this.status = status;
    this.body = body;
  }
}

// ─── Thread API ───────────────────────────────────────────────────────────────

async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getToken();
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { [TOKEN_HEADER]: `Bearer ${token}` } : {}),
      ...(INTERNAL_SECRET ? { 'X-Internal-Auth': INTERNAL_SECRET } : {}),
      ...(options.headers as Record<string, string> | undefined),
    },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new LoreApiError(res.status, body);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export async function listThreads(
  limit = 20,
  beforeId?: string,
): Promise<ThreadSummary[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (beforeId) params.set('before_id', beforeId);
  return apiFetch<ThreadSummary[]>(`/api/threads?${params}`);
}

export async function getThreadTurns(
  threadId: string,
  limit = 50,
): Promise<TurnSummary[]> {
  return apiFetch<TurnSummary[]>(`/api/threads/${threadId}/turns?limit=${limit}`);
}

export async function deleteThread(
  threadId: string,
): Promise<void> {
  return apiFetch<void>(`/api/threads/${threadId}`, { method: 'DELETE' });
}

// ─── Streaming query ──────────────────────────────────────────────────────────

export async function* streamQuery(
  query: string,
  k = 8,
  threadId?: string | null,
): AsyncGenerator<StreamEvent> {
  const token = getToken();
  const res = await fetch(`${API_URL}/api/query/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { [TOKEN_HEADER]: `Bearer ${token}` } : {}),
      ...(INTERNAL_SECRET ? { 'X-Internal-Auth': INTERNAL_SECRET } : {}),
    },
    body: JSON.stringify({ query, k, ...(DEMO_MODE ? { super_admin: true } : {}), ...(threadId ? { thread_id: threadId } : {}) }),
  });

  if (!res.ok) {
    const body = await res.text();
    throw new LoreApiError(res.status, body);
  }

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop()!;
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      const json = line.slice(6).trim();
      if (!json) continue;
      try {
        yield JSON.parse(json) as StreamEvent;
      } catch { /* ignore malformed frames */ }
    }
  }
}

// ─── GitLab code-intelligence query ───────────────────────────────────────────
//
// Separate engine surface (/api/gitlab/*) backed by the chronograph store, NOT
// the Gmail/Slack knowledge pipeline. Non-streaming: one JSON response with an
// LLM-synthesised answer over commits + symbols + semantic code search.

export interface GitlabChatResponse {
  answer?: string;
  error?: string;
  source?: string;
  context_length?: number;
  relevant_symbols?: string[];
  usage?: Record<string, unknown>;
}

export async function gitlabChat(question: string): Promise<GitlabChatResponse> {
  return apiFetch<GitlabChatResponse>('/api/gitlab/chat', {
    method: 'POST',
    body: JSON.stringify({ question }),
  });
}

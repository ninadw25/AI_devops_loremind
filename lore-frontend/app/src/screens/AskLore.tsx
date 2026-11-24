import { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Send,
  Sparkles,
  Mail,
  MessageSquare,
  Github,
  ClipboardList,
  Mic,
  ChevronDown,
  PanelLeft,
  X,
  Plus,
  AlertTriangle,
  BarChart3,
  GraduationCap,
  Cpu,
  Search as SearchIcon,
} from 'lucide-react';
import AppShell from '@/components/app-shell/AppShell';
import {
  streamQuery, gitlabChat, LoreApiError, listThreads, getThreadTurns, deleteThread,
  type BackendSourceRef, type ThreadSummary,
} from '@/services/loreApi';

type SourcePlatform = 'gmail' | 'slack' | 'github' | 'jira' | 'fireflies';

interface Source {
  platform: SourcePlatform;
  type: string;
  title: string;
  snippet: string;
  relevance_score: number;
  date: string;
  people?: string[];
  channel?: string;
}

interface Message {
  id: string;
  type: 'user' | 'lore';
  content: string;
  sources?: Source[];
  confidence?: 'high' | 'medium' | 'low';
  intent?: string;
  // If true, only `streamedLength` characters of content are rendered.
  // When streaming completes this flips to false and sources become visible.
  streaming?: boolean;
  streamedLength?: number;
}

// ─── Platform config ─────────────────────────────────────────────────────────

const PLATFORM_CONFIG: Record<SourcePlatform, { label: string; icon: typeof Mail; color: string; bg: string }> = {
  gmail:     { label: 'Gmail',     icon: Mail,          color: 'text-red-600',     bg: 'bg-red-50 border-red-100' },
  slack:     { label: 'Slack',     icon: MessageSquare, color: 'text-purple-600',  bg: 'bg-purple-50 border-purple-100' },
  github:    { label: 'GitHub',    icon: Github,        color: 'text-slate-800',   bg: 'bg-slate-100 border-slate-200' },
  jira:      { label: 'Jira',      icon: ClipboardList, color: 'text-blue-600',    bg: 'bg-blue-50 border-blue-100' },
  fireflies: { label: 'Fireflies', icon: Mic,           color: 'text-amber-600',   bg: 'bg-amber-50 border-amber-100' },
};

// Platform logos for the chat output - replaces the lucide icons in evidence cards
const PLATFORM_LOGOS: Record<SourcePlatform, string> = {
  gmail:     '/images/gmail.png',
  slack:     '/images/slack.png',
  github:    '/images/github.png',
  jira:      '/images/jira.jpg',
  fireflies: '/images/google_meet.png',
};

// ─── Backend source → frontend Source mapping ────────────────────────────────

function inferPlatform(type: string): SourcePlatform {
  const t = type.toLowerCase();
  if (t.includes('slack')) return 'slack';
  if (t.includes('git') || t.includes('commit') || t.includes('pr')) return 'github';
  if (t.includes('jira') || t.includes('ticket')) return 'jira';
  return 'gmail';
}

function sourceRefToSource(src: BackendSourceRef): Source {
  return {
    platform: inferPlatform(src.type),
    type: src.type,
    title: src.description.split('\n')[0].slice(0, 80),
    snippet: src.description,
    relevance_score: src.score,
    date: '',
  };
}

// ─── Suggested questions ─────────────────────────────────────────────────────

// ─── New chat UI constants ──────────────────────────────────────────────────

const MODEL_OPTIONS = [
  // Display only: the engine's model is set server-side (LLM_MODEL). Keep this label matching it.
  { id: 'claude-3-5-sonnet', label: 'Claude 3.5 Sonnet', vendor: 'AWS Bedrock' },
];

type SourceFilter = 'gmail' | 'gitlab' | 'slack' | 'jira';

// Gmail and GitLab are selectable (mutually exclusive); Slack/Jira are greyed
// out until their query paths are wired. GitLab reuses the code logo (no
// dedicated gitlab.png asset yet).
const KNOWLEDGE_SOURCES: { id: SourceFilter; label: string; img?: string; disabled?: boolean }[] = [
  { id: 'gmail',  label: 'Gmail',  img: '/images/gmail.png' },
  { id: 'gitlab', label: 'GitLab', img: '/images/github.png' },
  { id: 'slack',  label: 'Slack',  img: '/images/slack.png', disabled: true },
  { id: 'jira',   label: 'Jira',   img: '/images/jira.jpg',  disabled: true },
];

const SUGGESTED_CARDS: { icon: typeof AlertTriangle; iconColor: string; iconBg: string; title: string; desc: string; query: string }[] = [
  {
    icon: BarChart3,
    iconColor: 'text-emerald-600',
    iconBg: 'bg-emerald-50',
    title: 'Key decisions this month',
    desc: 'Summarise the most important decisions taken across email and code in the last 30 days.',
    query: "What are the most important decisions made in the last 30 days?",
  },
  {
    icon: GraduationCap,
    iconColor: 'text-blue-600',
    iconBg: 'bg-blue-50',
    title: 'Who owns what',
    desc: 'Map out current ownership - who is responsible for which projects or domains.',
    query: "Who owns which projects or domains in the team right now?",
  },
  {
    icon: AlertTriangle,
    iconColor: 'text-red-600',
    iconBg: 'bg-red-50',
    title: 'Open risks and blockers',
    desc: 'Surface unresolved issues, blockers, and risks mentioned across all channels.',
    query: 'What are the current open risks, blockers, or unresolved issues in the team?',
  },
];

function greetingFor(now = new Date()): string {
  const h = now.getHours();
  if (h < 12) return 'Good morning';
  if (h < 17) return 'Good afternoon';
  return 'Good evening';
}

// ─── Component ───────────────────────────────────────────────────────────────

export default function AskLore() {
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [isTyping, setIsTyping] = useState(false);
  const [expandedEvidence, setExpandedEvidence] = useState<Set<string>>(new Set());
  const latestAssistantRef = useRef<HTMLDivElement>(null);

  // receivedRef accumulates the full streamed text so we can finalize on done/error.
  const receivedRef = useRef('');
  const rafRef = useRef<number | null>(null);

  // ── New chat UI state ──
  const [selectedModel, setSelectedModel] = useState(MODEL_OPTIONS[0]);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [modelMenuPos, setModelMenuPos] = useState<{ top: number; left: number } | null>(null);
  // Mutually-exclusive knowledge source: Gmail (default) OR GitLab code
  // intelligence. Gmail → /api/query/stream; GitLab → /api/gitlab/chat.
  const [activeSource, setActiveSource] = useState<'gmail' | 'gitlab'>('gmail');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(false);
  const modelButtonRef = useRef<HTMLButtonElement>(null);
  const modelDropdownRef = useRef<HTMLDivElement>(null);

  const selectSource = (id: SourceFilter) => {
    if (id === 'gmail' || id === 'gitlab') setActiveSource(id);
  };

  // Open model menu - measure trigger, position above it
  const openModelMenu = () => {
    const rect = modelButtonRef.current?.getBoundingClientRect();
    if (!rect) return;
    setModelMenuPos({ top: rect.top - 8, left: rect.left });
    setModelMenuOpen(true);
  };

  // Click-outside for model menu (covers both trigger and dropdown via portal)
  useEffect(() => {
    if (!modelMenuOpen) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        modelButtonRef.current?.contains(target) ||
        modelDropdownRef.current?.contains(target)
      ) return;
      setModelMenuOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [modelMenuOpen]);

  // Close model menu on scroll/resize so it doesn't drift away from its trigger
  useEffect(() => {
    if (!modelMenuOpen) return;
    const close = () => setModelMenuOpen(false);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [modelMenuOpen]);

  const fetchThreads = async () => {
    setThreadsLoading(true);
    try {
      const data = await listThreads();
      setThreads(data);
    } catch {
      // silently ignore - user sees empty list
    } finally {
      setThreadsLoading(false);
    }
  };

  // Load thread list whenever the drawer opens
  useEffect(() => {
    if (historyOpen) fetchThreads();
  }, [historyOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadThread = async (thread: ThreadSummary) => {
    try {
      const turns = await getThreadTurns(thread.thread_id);
      const rebuilt: Message[] = [];
      for (const t of turns) {
        rebuilt.push({ id: `${t.turn_id}-u`, type: 'user', content: t.question });
        if (t.answer) {
          rebuilt.push({ id: `${t.turn_id}-a`, type: 'lore', content: t.answer, streaming: false });
        }
      }
      setMessages(rebuilt);
      setThreadId(thread.thread_id);
      setHistoryOpen(false);
    } catch {
      // ignore - thread may have been deleted
    }
  };

  const handleDeleteThread = async (e: React.MouseEvent, threadId: string) => {
    e.stopPropagation();
    try {
      await deleteThread(threadId);
      setThreads(prev => prev.filter(t => t.thread_id !== threadId));
    } catch {
      // ignore
    }
  };

  const toggleEvidence = (msgId: string) => {
    setExpandedEvidence(prev => {
      const next = new Set(prev);
      if (next.has(msgId)) next.delete(msgId);
      else next.add(msgId);
      return next;
    });
  };


  const handleSend = async () => {
    if (!input.trim()) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      type: 'user',
      content: input,
    };
    setMessages(prev => [...prev, userMessage]);
    const userQuery = input;
    setInput('');
    setIsTyping(true);

    const responseId = (Date.now() + 1).toString();
    let messageAdded = false;
    receivedRef.current = '';
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);

    const addMessage = () => {
      if (messageAdded) return;
      messageAdded = true;
      setIsTyping(false);
      setMessages(prev => [
        ...prev,
        { id: responseId, type: 'lore', content: '', sources: [], streaming: true, streamedLength: 0 },
      ]);
      setExpandedEvidence(prev => new Set(prev).add(responseId));
      requestAnimationFrame(() => {
        latestAssistantRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    };

    // ── GitLab code-intelligence path — separate engine endpoint, non-streaming ──
    if (activeSource === 'gitlab') {
      try {
        const res = await gitlabChat(userQuery);
        const answer = res.answer || res.error || 'No answer returned.';
        setIsTyping(false);
        setMessages(prev => [
          ...prev,
          {
            id: responseId, type: 'lore', content: answer, sources: [],
            streaming: false, streamedLength: answer.length, confidence: 'high',
          },
        ]);
        setExpandedEvidence(prev => new Set(prev).add(responseId));
        requestAnimationFrame(() => {
          latestAssistantRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
      } catch (err) {
        setIsTyping(false);
        const msg = err instanceof LoreApiError && err.status === 401
          ? 'Authentication required. Please log in to query the code intelligence.'
          : 'Could not reach the GitLab code intelligence service.';
        setMessages(prev => [
          ...prev,
          { id: responseId, type: 'lore', content: msg, streaming: false },
        ]);
      }
      return;
    }

    try {
      for await (const event of streamQuery(userQuery, 8, threadId)) {
        if (event.type === 'thread_id') {
          setThreadId(event.data);
        } else if (event.type === 'intent') {
          addMessage();
        } else if (event.type === 'sources') {
          addMessage();
          const mapped = event.data.map(sourceRefToSource);
          setMessages(prev => prev.map(m =>
            m.id === responseId ? { ...m, sources: mapped } : m
          ));
        } else if (event.type === 'token') {
          addMessage();
          receivedRef.current += event.data;
          // Render tokens immediately as they arrive — no typewriter delay
          setMessages(prev => prev.map(m =>
            m.id === responseId
              ? { ...m, content: receivedRef.current, streamedLength: receivedRef.current.length }
              : m
          ));
        } else if (event.type === 'done') {
          const conf = event.data.confidence as 'high' | 'medium' | 'low';
          if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
          setMessages(prev => prev.map(m =>
            m.id === responseId
              ? { ...m, content: receivedRef.current, streamedLength: receivedRef.current.length, streaming: false, confidence: conf }
              : m
          ));
        } else if (event.type === 'error') {
          addMessage();
          if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
          setMessages(prev => prev.map(m =>
            m.id === responseId
              ? { ...m, content: `Error: ${event.data}`, streamedLength: (`Error: ${event.data}`).length, streaming: false }
              : m
          ));
        }
      }
      // Ensure streaming flag is cleared even if 'done' event was missed
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      setMessages(prev => prev.map(m =>
        m.id === responseId
          ? { ...m, content: receivedRef.current, streamedLength: receivedRef.current.length, streaming: false }
          : m
      ));
      // Refresh thread list so new thread / updated title appears in history
      fetchThreads();
    } catch (err) {
      addMessage();
      const msg = err instanceof LoreApiError && err.status === 401
        ? 'Authentication required. Please log in to query the knowledge base.'
        : 'Could not reach the knowledge base. Make sure the backend is running.';
      setMessages(prev => prev.map(m =>
        m.id === responseId ? { ...m, content: msg, streaming: false } : m
      ));
    } finally {
      setIsTyping(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // ── Model selector trigger (dropdown is rendered via portal at the bottom of return) ──
  const modelSelectorTrigger = (
    <button
      ref={modelButtonRef}
      onClick={() => modelMenuOpen ? setModelMenuOpen(false) : openModelMenu()}
      className="flex items-center gap-2 px-3 py-1.5 rounded-full text-[12px] font-medium text-slate-700 hover:bg-slate-100 border border-slate-200 bg-white transition-colors"
    >
      <Cpu className="w-3.5 h-3.5 text-slate-500" />
      <span>{selectedModel.label}</span>
      <ChevronDown className={`w-3 h-3 text-slate-400 transition-transform ${modelMenuOpen ? 'rotate-180' : ''}`} />
    </button>
  );

  // ── Knowledge source pills (multi-select) ──
  const sourcePillsJsx = (
    <div className="flex flex-wrap items-center gap-2">
      {KNOWLEDGE_SOURCES.map(s => {
        const active = s.id === activeSource;
        return (
          <button
            key={s.id}
            onClick={() => !s.disabled && selectSource(s.id)}
            disabled={s.disabled}
            title={s.disabled ? `${s.label} — coming soon` : `Query ${s.label}`}
            className={`flex items-center gap-2 h-9 pl-2 pr-3.5 rounded-full text-[12px] font-medium border transition-all ${
              s.disabled
                ? 'bg-slate-50 text-slate-400 border-slate-200 opacity-50 cursor-not-allowed'
                : active
                ? 'bg-blue-50 text-blue-700 border-blue-200 shadow-sm'
                : 'bg-white text-slate-600 border-slate-200 hover:border-slate-300 hover:bg-slate-50'
            }`}
          >
            {s.img ? (
              <span className="w-6 h-6 rounded-full bg-white border border-slate-200 flex items-center justify-center overflow-hidden">
                <img src={s.img} alt={s.label} className="w-4 h-4 object-contain" />
              </span>
            ) : (
              <span className={`w-6 h-6 rounded-full flex items-center justify-center ${active ? 'bg-blue-100' : 'bg-slate-100'}`}>
                <Sparkles className={`w-3 h-3 ${active ? 'text-blue-600' : 'text-slate-500'}`} />
              </span>
            )}
            {s.label}
          </button>
        );
      })}
    </div>
  );

  // ── Big input box (used in both empty + active states) ──
  const inputBoxJsx = (
    <div className="relative w-full bg-white border border-slate-200/80 rounded-2xl shadow-[0_8px_32px_-12px_rgba(15,23,42,0.10)] p-3 transition-all focus-within:border-blue-300 focus-within:shadow-[0_8px_32px_-12px_rgba(10,63,181,0.20)]">
      <input
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Ask Loremind anything about your team…"
        className="w-full px-3 pt-2 pb-3 bg-transparent text-[14px] text-slate-800 placeholder:text-slate-400 outline-none"
      />
      <div className="flex items-center justify-between gap-2 px-1">
        <div className="flex items-center gap-2">
          <button
            className="w-9 h-9 rounded-full hover:bg-slate-100 flex items-center justify-center text-slate-500 transition-colors"
            title="Add context"
          >
            <Plus className="w-4 h-4" />
          </button>
          {modelSelectorTrigger}
        </div>
        <button
          onClick={handleSend}
          disabled={!input.trim() || isTyping}
          className={`w-10 h-10 rounded-full flex items-center justify-center transition-all ${
            input.trim() && !isTyping
              ? 'bg-blue-600 text-white hover:bg-blue-700 shadow-sm'
              : 'bg-slate-200 text-slate-400 cursor-not-allowed'
          }`}
          title="Send"
        >
          <Send className="w-4 h-4" />
        </button>
      </div>
    </div>
  );

  // ── Chat history drawer (overlay, slides from left) ──
  const historyDrawerJsx = (
    <>
      {historyOpen && (
        <div
          className="absolute inset-0 z-30 bg-slate-900/10 backdrop-blur-[2px] animate-in fade-in duration-150"
          onClick={() => setHistoryOpen(false)}
        />
      )}
      <aside
        className={`absolute top-0 bottom-0 left-0 w-[300px] bg-white border-r border-slate-200 z-40 transition-transform duration-200 flex flex-col ${
          historyOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <PanelLeft className="w-4 h-4 text-slate-500" />
            <h3 className="text-[13px] font-semibold text-slate-900" style={{ fontFamily: 'var(--font-display)' }}>
              History
            </h3>
          </div>
          <button
            onClick={() => setHistoryOpen(false)}
            className="w-7 h-7 rounded-lg hover:bg-slate-100 flex items-center justify-center text-slate-400"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
        <div className="px-3 py-3">
          <button
            onClick={() => { setMessages([]); setThreadId(null); setHistoryOpen(false); }}
            className="w-full flex items-center gap-2 px-3 py-2 bg-slate-900 text-white rounded-xl text-[12px] font-semibold hover:bg-slate-800 transition-colors"
          >
            <Plus className="w-3.5 h-3.5" />
            New chat
          </button>
        </div>
        <div className="px-3 pb-3">
          <div className="relative">
            <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
            <input
              type="text"
              placeholder="Search history…"
              className="w-full h-9 pl-9 pr-3 bg-slate-50 border border-slate-200 rounded-lg text-[12px] placeholder:text-slate-400 outline-none focus:ring-2 focus:ring-blue-500/20 focus:bg-white"
            />
          </div>
        </div>
        <div className="flex-1 overflow-y-auto px-3 pb-4">
          {threadsLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="flex gap-1">
                <span className="w-1.5 h-1.5 bg-slate-300 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                <span className="w-1.5 h-1.5 bg-slate-300 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                <span className="w-1.5 h-1.5 bg-slate-300 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
            </div>
          ) : threads.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-center px-4">
              <div className="w-10 h-10 rounded-full bg-slate-100 flex items-center justify-center mb-3">
                <PanelLeft className="w-4 h-4 text-slate-400" />
              </div>
              <p className="text-[12px] text-slate-500 leading-relaxed">
                Your conversations will appear here. Start a new chat to begin.
              </p>
            </div>
          ) : (
            <div className="space-y-0.5">
              {threads.map(t => (
                <div
                  key={t.thread_id}
                  onClick={() => loadThread(t)}
                  className={`group flex items-start justify-between gap-2 px-3 py-2.5 rounded-lg cursor-pointer transition-colors hover:bg-slate-100 ${
                    t.thread_id === threadId ? 'bg-blue-50 hover:bg-blue-100' : ''
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-[12px] text-slate-800 line-clamp-1 leading-snug">{t.title}</p>
                    <p className="text-[10px] text-slate-400 mt-0.5">{t.turn_count} turn{t.turn_count !== 1 ? 's' : ''}</p>
                  </div>
                  <button
                    onClick={(e) => handleDeleteThread(e, t.thread_id)}
                    className="shrink-0 w-5 h-5 rounded flex items-center justify-center text-slate-300 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-all"
                    title="Delete thread"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </aside>
    </>
  );

  // ── Model dropdown rendered in a portal so it escapes overflow:hidden parents ──
  const modelDropdownPortal = modelMenuOpen && modelMenuPos && createPortal(
    <div
      ref={modelDropdownRef}
      style={{
        position: 'fixed',
        top: modelMenuPos.top,
        left: modelMenuPos.left,
        transform: 'translateY(-100%)',
        zIndex: 1000,
      }}
      className="w-[220px] bg-white border border-slate-200 rounded-xl shadow-[0_16px_40px_-8px_rgba(15,23,42,0.22)] py-1.5 animate-in fade-in slide-in-from-bottom-2 duration-150"
    >
      {MODEL_OPTIONS.map(m => (
        <button
          key={m.id}
          onClick={() => { setSelectedModel(m); setModelMenuOpen(false); }}
          className={`w-full flex items-center justify-between px-3 py-2 text-left hover:bg-slate-50 transition-colors ${
            m.id === selectedModel.id ? 'bg-blue-50/50' : ''
          }`}
        >
          <div>
            <div className="text-[12px] font-semibold text-slate-900">{m.label}</div>
            <div className="text-[10px] text-slate-500">{m.vendor}</div>
          </div>
          {m.id === selectedModel.id && <span className="w-1.5 h-1.5 rounded-full bg-blue-600" />}
        </button>
      ))}
    </div>,
    document.body
  );

  return (
    <AppShell title="Ask Lore" bleed>
      <div className="flex flex-col flex-1 min-h-0 relative">
        {/* History toggle button (top-left of chat area) */}
        <button
          onClick={() => setHistoryOpen(o => !o)}
          className="absolute top-4 left-4 z-20 w-9 h-9 rounded-lg bg-white border border-slate-200/80 hover:bg-slate-50 flex items-center justify-center text-slate-600 shadow-sm transition-colors"
          title="Open chat history"
        >
          <PanelLeft className="w-4 h-4" />
        </button>

        {historyDrawerJsx}

        {/* Messages */}
        <div className="flex-1 overflow-y-auto">
          <div className={messages.length === 0 ? 'h-full px-6 py-8 flex' : 'max-w-[860px] mx-auto px-6 py-8 space-y-7'}>
          {messages.length === 0 ? (
            /* Empty State - ChatFusion-style, vertically centered */
            <div className="m-auto w-full max-w-[720px] flex flex-col items-center">
              {/* Greeting */}
              <div className="text-center mb-10">
                <h1 className="text-[32px] font-semibold text-slate-900 leading-tight" style={{ fontFamily: 'var(--font-display)' }}>
                  {greetingFor()} <span className="inline-block">👋</span>
                </h1>
                <p className="text-[15px] text-slate-500 mt-2">
                  What would you like to know about your team today?
                </p>
              </div>

              {/* Big input box */}
              <div className="w-full max-w-[680px] mb-6">
                {inputBoxJsx}
              </div>

              {/* Knowledge source pills */}
              <div className="mb-14 flex justify-center">
                {sourcePillsJsx}
              </div>

              {/* Suggested for you */}
              <div className="w-full">
                <h3 className="text-[13px] font-semibold text-slate-700 mb-3" style={{ fontFamily: 'var(--font-display)' }}>
                  Suggested for you
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  {SUGGESTED_CARDS.map(card => {
                    const Icon = card.icon;
                    return (
                      <button
                        key={card.title}
                        onClick={() => setInput(card.query)}
                        className="text-left p-4 bg-white border border-slate-200/70 rounded-2xl hover:border-blue-200 hover:shadow-[0_8px_24px_-12px_rgba(15,23,42,0.12)] transition-all group"
                      >
                        <div className={`w-9 h-9 rounded-xl ${card.iconBg} ${card.iconColor} flex items-center justify-center mb-3`}>
                          <Icon className="w-4 h-4" />
                        </div>
                        <h4 className="text-[13px] font-semibold text-slate-900 mb-1 leading-tight" style={{ fontFamily: 'var(--font-display)' }}>
                          {card.title}
                        </h4>
                        <p className="text-[11px] text-slate-500 leading-snug line-clamp-2">
                          {card.desc}
                        </p>
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          ) : (
            /* Messages List */
            <>
              {messages.map((message) => {
                if (message.type === 'user') {
                  // ── User message: right-aligned soft-blue pill ──
                  return (
                    <div key={message.id} className="flex justify-end pl-12">
                      <div className="max-w-[78%] bg-blue-50/70 border border-blue-100 rounded-2xl px-5 py-3">
                        <p className="text-[14px] text-slate-800 whitespace-pre-line leading-[1.55]">
                          {message.content}
                        </p>
                      </div>
                    </div>
                  );
                }

                // ── Assistant message: no avatar, no bubble - left-aligned, narrower ──
                const uniquePlatforms = message.sources
                  ? Array.from(new Set(message.sources.map(s => s.platform)))
                  : [];
                // Streaming: slice content to the streamed length; otherwise show full content.
                const displayedContent = message.streaming
                  ? message.content.slice(0, message.streamedLength ?? 0)
                  : message.content;
                const isLatestAssistant = message.id === [...messages].reverse().find(m => m.type === 'lore')?.id;

                return (
                  <div
                    key={message.id}
                    ref={isLatestAssistant ? latestAssistantRef : undefined}
                    className="text-slate-800 max-w-[88%] mr-auto pr-4 scroll-mt-6"
                  >
                    <div className="text-[14px] leading-[1.7]">
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          p:      ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
                          strong: ({ children }) => <strong className="font-semibold text-slate-900">{children}</strong>,
                          em:     ({ children }) => <em className="italic text-slate-600">{children}</em>,
                          ul:     ({ children }) => <ul className="list-disc pl-5 mb-3 space-y-1">{children}</ul>,
                          ol:     ({ children }) => <ol className="list-decimal pl-5 mb-3 space-y-1">{children}</ol>,
                          li:     ({ children }) => <li className="text-slate-700">{children}</li>,
                          h1:     ({ children }) => <h1 className="text-[16px] font-bold text-slate-900 mt-5 mb-2">{children}</h1>,
                          h2:     ({ children }) => <h2 className="text-[15px] font-semibold text-slate-900 mt-5 mb-2">{children}</h2>,
                          h3:     ({ children }) => <h3 className="text-[14px] font-semibold text-slate-900 mt-4 mb-1.5">{children}</h3>,
                          code:   ({ children, ...props }) => {
                            const isInline = !(props as { className?: string }).className;
                            return isInline
                              ? <code className="text-[12px] font-mono bg-slate-100 text-slate-800 px-1.5 py-0.5 rounded">{children}</code>
                              : <code className="block bg-slate-900 text-slate-100 p-3 rounded-lg text-[12px] font-mono overflow-x-auto">{children}</code>;
                          },
                          pre:    ({ children }) => <pre className="bg-slate-900 text-slate-100 p-3 rounded-lg overflow-x-auto my-3 text-[12px]">{children}</pre>,
                          a:      ({ children, href }) => <a href={href} className="text-blue-600 hover:underline" target="_blank" rel="noreferrer">{children}</a>,
                          hr:     () => <hr className="my-4 border-slate-200" />,
                          blockquote: ({ children }) => <blockquote className="border-l-2 border-slate-300 pl-3 italic text-slate-600 my-3">{children}</blockquote>,
                          table:  ({ children }) => <div className="overflow-x-auto my-3"><table className="w-full text-[12px] border-collapse">{children}</table></div>,
                          thead:  ({ children }) => <thead className="bg-slate-50">{children}</thead>,
                          th:     ({ children }) => <th className="border border-slate-200 px-2 py-1.5 text-left font-semibold text-slate-700">{children}</th>,
                          td:     ({ children }) => <td className="border border-slate-200 px-2 py-1.5 text-slate-600">{children}</td>,
                        }}
                      >
                        {displayedContent}
                      </ReactMarkdown>
                      {message.streaming && (
                        <span className="inline-block w-[2px] h-[14px] bg-blue-500 align-middle animate-pulse ml-0.5" />
                      )}
                    </div>

                    {/* Sources - only show after streaming completes */}
                    {!message.streaming && message.sources && message.sources.length > 0 && (
                      <div className="mt-6 pt-5 border-t-2 border-slate-200">
                        <button
                          onClick={() => toggleEvidence(message.id)}
                          className="w-full flex items-center gap-2 group text-left"
                        >
                          <ChevronDown
                            className={`w-3.5 h-3.5 text-slate-400 transition-transform ${
                              expandedEvidence.has(message.id) ? 'rotate-0' : '-rotate-90'
                            }`}
                          />
                          <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
                            Sources · {message.sources.length}
                          </span>
                          <div className="flex items-center gap-1 ml-auto">
                            {uniquePlatforms.map(p => (
                              <span
                                key={p}
                                className="w-5 h-5 rounded-full bg-white border border-slate-200 flex items-center justify-center overflow-hidden"
                                title={PLATFORM_CONFIG[p].label}
                              >
                                <img src={PLATFORM_LOGOS[p]} alt={PLATFORM_CONFIG[p].label} className="w-3 h-3 object-contain" />
                              </span>
                            ))}
                          </div>
                        </button>

                        {expandedEvidence.has(message.id) && (
                          <div className="mt-3 space-y-2">
                            {message.sources.map((source, idx) => (
                              <div
                                key={idx}
                                className="flex items-start gap-3 px-4 py-3 bg-white hover:bg-slate-50 rounded-xl border border-slate-200 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.06)] transition-colors"
                              >
                                <span className="w-7 h-7 rounded-full bg-white border border-slate-200 flex items-center justify-center overflow-hidden shrink-0 mt-0.5 shadow-sm">
                                  <img
                                    src={PLATFORM_LOGOS[source.platform]}
                                    alt={PLATFORM_CONFIG[source.platform].label}
                                    className="w-4 h-4 object-contain"
                                  />
                                </span>
                                <div className="flex-1 min-w-0">
                                  <div className="text-[12.5px] font-semibold text-slate-900 leading-tight">
                                    {source.title}
                                  </div>
                                  <p className="text-[11.5px] text-slate-600 italic mt-1 leading-snug line-clamp-2">
                                    "{source.snippet}"
                                  </p>
                                  <div className="flex items-center flex-wrap gap-x-3 gap-y-1 mt-2">
                                    {source.channel && (
                                      <span className="text-[10px] text-slate-600 font-semibold">{source.channel}</span>
                                    )}
                                    {source.people?.map(person => (
                                      <span key={person} className="text-[10px] text-slate-600">
                                        @{person.split(' ')[0].toLowerCase()}
                                      </span>
                                    ))}
                                    <span className="text-[10px] text-slate-500 ml-auto tabular-nums font-medium">
                                      {source.date}
                                    </span>
                                  </div>
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}

              {/* Typing Indicator - minimal, no avatar */}
              {isTyping && (
                <div className="flex items-center gap-2.5 text-slate-500">
                  <div className="flex gap-1">
                    <span className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                    <span className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                    <span className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                  </div>
                  <span className="text-[12px]">Searching your knowledge base…</span>
                </div>
              )}
            </>
          )}
          </div>
        </div>

        {/* Bottom Input Area - only shown during active chat */}
        {messages.length > 0 && (
          <div className="bg-white border-t border-slate-200 px-6 py-4">
            <div className="max-w-3xl mx-auto">
              <div className="flex justify-center mb-3">
                {sourcePillsJsx}
              </div>
              {inputBoxJsx}
            </div>
          </div>
        )}
      </div>
      {modelDropdownPortal}
    </AppShell>
  );
}

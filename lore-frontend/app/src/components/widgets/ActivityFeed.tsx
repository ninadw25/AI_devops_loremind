import {
  AlertCircle, AlertTriangle, Info, CheckCircle2, Zap, GitBranch, MessageSquare,
} from 'lucide-react';
import type { ActivityEvent, EventType, EventPriority } from '@/data/companyData';

const typeIcons: Record<EventType, React.ComponentType<{ className?: string }>> = {
  decision:  CheckCircle2,
  risk:      AlertTriangle,
  incident:  Zap,
  project:   GitBranch,
  knowledge: MessageSquare,
};

const priorityStyles: Record<EventPriority, { bg: string; text: string; ring: string }> = {
  critical: { bg: 'bg-red-50',     text: 'text-red-600',     ring: 'ring-red-100' },
  warning:  { bg: 'bg-amber-50',   text: 'text-amber-600',   ring: 'ring-amber-100' },
  info:     { bg: 'bg-slate-100',  text: 'text-slate-600',   ring: 'ring-slate-200' },
};

function timeAgo(iso: string, nowIso = '2026-01-22T11:00:00+05:30'): string {
  const diff = (new Date(nowIso).getTime() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

interface ActivityFeedProps {
  events: ActivityEvent[];
  compact?: boolean;
}

export default function ActivityFeed({ events, compact = false }: ActivityFeedProps) {
  return (
    <div className="space-y-1">
      {events.map(e => {
        const Icon = typeIcons[e.type] || Info;
        const s = priorityStyles[e.priority];
        return (
          <div
            key={e.id}
            className="group flex items-start gap-3 px-3 py-3 rounded-xl hover:bg-slate-50/70 border border-transparent hover:border-slate-200/80 transition-colors cursor-pointer"
          >
            <div className={`w-8 h-8 rounded-lg ${s.bg} ${s.text} flex items-center justify-center shrink-0 ring-1 ${s.ring}`}>
              <Icon className="w-4 h-4" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-start justify-between gap-3 mb-0.5">
                <h5 className="text-[12px] font-semibold text-slate-900 leading-tight line-clamp-2" style={{ fontFamily: 'var(--font-display)' }}>
                  {e.title}
                </h5>
                <span className="text-[10px] text-slate-400 whitespace-nowrap tabular-nums shrink-0">
                  {timeAgo(e.timestamp)}
                </span>
              </div>
              {!compact && (
                <p className="text-[11px] text-slate-500 leading-snug line-clamp-2">{e.description}</p>
              )}
              {e.source && (
                <div className="flex items-center gap-1.5 mt-1.5">
                  <span className="text-[9px] uppercase tracking-wider text-slate-400 font-semibold">{e.source}</span>
                  {e.priority === 'critical' && (
                    <span className="inline-flex items-center gap-1 text-[9px] font-semibold text-red-600 uppercase tracking-wider">
                      <AlertCircle className="w-2.5 h-2.5" /> critical
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

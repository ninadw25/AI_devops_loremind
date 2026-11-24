import { useState, useRef, useEffect } from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { useContradictions } from '@/data/hooks';
import type { Contradiction } from '@/data/companyData';

const severityStyle: Record<Contradiction['severity'], { bg: string; border: string; text: string; dot: string }> = {
  high:   { bg: 'bg-red-50',     border: 'border-red-200',     text: 'text-red-700',     dot: 'bg-red-500' },
  medium: { bg: 'bg-amber-50',   border: 'border-amber-200',   text: 'text-amber-700',   dot: 'bg-amber-500' },
  low:    { bg: 'bg-slate-50',   border: 'border-slate-200',   text: 'text-slate-700',   dot: 'bg-slate-400' },
};

export default function ContradictionBadge() {
  const items = useContradictions();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Click outside to close
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  if (items.length === 0) return null;

  return (
    <div ref={wrapRef} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 h-9 px-3 bg-red-50 hover:bg-red-100 border border-red-200/70 rounded-full transition-colors group"
      >
        <span className="relative flex items-center">
          <AlertTriangle className="w-3.5 h-3.5 text-red-600" />
        </span>
        <span className="text-[11px] font-semibold text-red-700">
          {items.length} contradiction{items.length > 1 ? 's' : ''}
        </span>
        <span className="relative flex w-1.5 h-1.5">
          <span className="absolute inset-0 rounded-full bg-red-500 animate-ping opacity-70" />
          <span className="relative w-1.5 h-1.5 rounded-full bg-red-500" />
        </span>
      </button>

      {open && (
        <div className="absolute top-[calc(100%+8px)] right-0 w-[440px] bg-white border border-slate-200 rounded-2xl shadow-[0_24px_48px_-12px_rgba(0,0,0,0.12)] z-50 animate-in fade-in slide-in-from-top-2 duration-200 overflow-hidden">
          <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
            <div>
              <h3 className="text-[13px] font-semibold text-slate-900" style={{ fontFamily: 'var(--font-display)' }}>
                Detected contradictions
              </h3>
              <p className="text-[11px] text-slate-500 mt-0.5">
                Cross-tool semantic conflicts found in your knowledge graph
              </p>
            </div>
            <button
              onClick={() => setOpen(false)}
              className="w-7 h-7 rounded-lg hover:bg-slate-100 flex items-center justify-center text-slate-400"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>

          <div className="max-h-[420px] overflow-y-auto">
            {items.map(c => {
              const sev = severityStyle[c.severity];
              return (
                <div key={c.id} className="px-5 py-4 border-b border-slate-100 last:border-b-0 hover:bg-slate-50/50">
                  <div className="flex items-center gap-2 mb-2">
                    <span className={`w-1.5 h-1.5 rounded-full ${sev.dot}`} />
                    <span className={`text-[9px] font-bold uppercase tracking-wider ${sev.text}`}>
                      {c.severity} severity
                    </span>
                    <span className="text-[9px] text-slate-400 ml-auto">{c.detectedAt.split('T')[0]}</span>
                  </div>
                  <p className="text-[12px] text-slate-700 font-medium leading-snug mb-3">
                    {c.summary}
                  </p>

                  <div className="space-y-1.5">
                    <div className={`flex items-start gap-2 px-2.5 py-1.5 rounded-lg ${sev.bg} border ${sev.border}`}>
                      <span className="text-[9px] font-bold text-slate-500 mt-0.5 tabular-nums">{c.decisionA.id}</span>
                      <span className="text-[10px] text-slate-700 flex-1 leading-snug">{c.decisionA.title}</span>
                      <span className="text-[9px] text-slate-400 tabular-nums">{c.decisionA.date.slice(5)}</span>
                    </div>
                    <div className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider text-center">vs</div>
                    <div className={`flex items-start gap-2 px-2.5 py-1.5 rounded-lg ${sev.bg} border ${sev.border}`}>
                      <span className="text-[9px] font-bold text-slate-500 mt-0.5 tabular-nums">{c.decisionB.id}</span>
                      <span className="text-[10px] text-slate-700 flex-1 leading-snug">{c.decisionB.title}</span>
                      <span className="text-[9px] text-slate-400 tabular-nums">{c.decisionB.date.slice(5)}</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

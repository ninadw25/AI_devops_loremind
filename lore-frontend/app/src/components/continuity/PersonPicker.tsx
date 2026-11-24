import { useEffect, useRef, useState } from 'react';
import { Search, Loader2 } from 'lucide-react';
import { searchPeople, type PersonHit } from '@/services/continuityApi';

interface Props {
  onSelect: (p: PersonHit) => void;
  placeholder?: string;
}

/** Debounced name search with a dropdown of Person hits. */
export default function PersonPicker({ onSelect, placeholder }: Props) {
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<PersonHit[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); return; }
    let active = true;
    setLoading(true);
    const t = setTimeout(() => {
      searchPeople(q.trim(), 8)
        .then((r) => { if (active) { setHits(r.people); setOpen(true); } })
        .catch(() => { if (active) setHits([]); })
        .finally(() => { if (active) setLoading(false); });
    }, 250);
    return () => { active = false; clearTimeout(t); };
  }, [q]);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  return (
    <div className="relative max-w-md" ref={boxRef}>
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
        {loading && (
          <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 animate-spin" />
        )}
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => hits.length > 0 && setOpen(true)}
          placeholder={placeholder ?? 'Search a person by name…'}
          className="w-full pl-10 pr-9 py-2.5 rounded-xl border border-slate-200 bg-white text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-400"
        />
      </div>
      {open && hits.length > 0 && (
        <div className="absolute z-20 mt-1 w-full bg-white border border-slate-200 rounded-xl shadow-lg overflow-hidden max-h-72 overflow-y-auto">
          {hits.map((p) => (
            <button
              key={p.person_id}
              onClick={() => { onSelect(p); setOpen(false); setQ(p.name ?? ''); }}
              className="w-full text-left px-4 py-2.5 hover:bg-slate-50 flex items-center justify-between gap-3"
            >
              <div className="min-w-0">
                <div className="text-sm font-medium text-slate-800 truncate">{p.name ?? p.person_id}</div>
                {p.primary_email && <div className="text-xs text-slate-400 truncate">{p.primary_email}</div>}
              </div>
              {p.is_internal && (
                <span className="shrink-0 text-[10px] font-semibold text-blue-600 bg-blue-50 px-2 py-0.5 rounded-full">
                  internal
                </span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

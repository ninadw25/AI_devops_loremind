import { AlertTriangle } from 'lucide-react';
import { useRiskMatrix } from '@/data/hooks';

// Map ownership value → color shade
function cellColor(ownership: number, critical: boolean): { bg: string; text: string } {
  if (ownership === 0) return { bg: '#f8fafc', text: '#cbd5e1' };
  if (critical) {
    // Red scale for critical bus-factor-1
    const a = Math.min(1, 0.4 + ownership / 150);
    return { bg: `rgba(220, 38, 38, ${a})`, text: ownership > 50 ? '#fff' : '#7f1d1d' };
  }
  // Blue scale for normal ownership
  const a = Math.min(1, 0.15 + ownership / 130);
  return { bg: `rgba(10, 63, 181, ${a})`, text: ownership > 55 ? '#fff' : '#1e3a8a' };
}

export default function KnowledgeRiskHeatmap() {
  const { rows, cols, cells } = useRiskMatrix();

  const getCell = (personId: string, serviceId: string) =>
    cells.find(c => c.personId === personId && c.serviceId === serviceId);

  const criticalCount = cells.filter(c => c.critical).length;

  return (
    <div>
      {/* Legend + critical count */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2 text-[11px] text-slate-500">
          <span>Ownership →</span>
          <div className="flex items-center gap-0.5">
            {[0.15, 0.35, 0.55, 0.75, 0.95].map((a, i) => (
              <span
                key={i}
                className="w-4 h-2.5 rounded-sm"
                style={{ background: `rgba(10, 63, 181, ${a})` }}
              />
            ))}
          </div>
          <span className="tabular-nums">0</span>
          <span className="mx-6">→</span>
          <span className="tabular-nums">100%</span>
        </div>
        {criticalCount > 0 && (
          <div className="flex items-center gap-1.5 px-2.5 py-1 bg-red-50 text-red-700 rounded-md text-[11px] font-semibold">
            <AlertTriangle className="w-3 h-3" />
            {criticalCount} critical
          </div>
        )}
      </div>

      {/* Heatmap grid */}
      <div className="overflow-x-auto -mx-2 px-2">
        <table className="w-full border-separate border-spacing-1">
          <thead>
            <tr>
              <th className="sticky left-0 bg-white z-10"></th>
              {cols.map(col => (
                <th
                  key={col.id}
                  className="text-left align-bottom pb-2 min-w-[88px]"
                >
                  <div
                    className="text-[10px] font-semibold text-slate-600 whitespace-nowrap"
                    style={{
                      writingMode: 'vertical-rl',
                      transform: 'rotate(180deg)',
                      fontFamily: 'var(--font-body)',
                    }}
                  >
                    {col.name}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(person => (
              <tr key={person.id}>
                <td className="sticky left-0 bg-white z-10 pr-3 text-right">
                  <div className="flex items-center justify-end gap-2">
                    <div className="leading-tight">
                      <div className="text-[12px] font-semibold text-slate-900 whitespace-nowrap">{person.name.split(' ')[0]}</div>
                      <div className="text-[10px] text-slate-500 whitespace-nowrap">{person.role}</div>
                    </div>
                  </div>
                </td>
                {cols.map(col => {
                  const cell = getCell(person.id, col.id);
                  const o = cell?.ownership ?? 0;
                  const critical = cell?.critical ?? false;
                  const style = cellColor(o, critical);
                  return (
                    <td key={col.id}>
                      <div
                        className={`h-9 rounded-md flex items-center justify-center text-[10px] font-semibold transition-transform hover:scale-[1.08] cursor-default relative ${
                          critical ? 'ring-1 ring-red-500/40' : ''
                        }`}
                        style={{ backgroundColor: style.bg, color: style.text }}
                        title={`${person.name} · ${col.name} · ${o}%${critical ? ' · BUS FACTOR 1' : ''}`}
                      >
                        {o > 0 ? `${o}%` : ''}
                        {critical && (
                          <span className="absolute -top-1 -right-1 w-2 h-2 rounded-full bg-red-500 ring-2 ring-white animate-pulse" />
                        )}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

import { TrendingUp, TrendingDown } from 'lucide-react';
import { LineChart, Line, ResponsiveContainer, YAxis } from 'recharts';

interface StatCardProps {
  label: string;
  value: string | number;
  suffix?: string;
  delta?: number;              // positive / negative change
  deltaLabel?: string;
  trend?: number[];            // sparkline values
  tone?: 'neutral' | 'success' | 'warning' | 'danger';
  icon?: React.ReactNode;
}

const toneStyles = {
  neutral: { bar: '#0a3fb5', bg: 'bg-blue-50/60', text: 'text-blue-700', ring: 'ring-blue-100' },
  success: { bar: '#059669', bg: 'bg-emerald-50/60', text: 'text-emerald-700', ring: 'ring-emerald-100' },
  warning: { bar: '#d97706', bg: 'bg-amber-50/60', text: 'text-amber-700', ring: 'ring-amber-100' },
  danger:  { bar: '#dc2626', bg: 'bg-red-50/60', text: 'text-red-700', ring: 'ring-red-100' },
};

export default function StatCard({
  label, value, suffix, delta, deltaLabel, trend, tone = 'neutral', icon,
}: StatCardProps) {
  const s = toneStyles[tone];
  const deltaPositive = (delta ?? 0) >= 0;
  const data = trend ? trend.map((v, i) => ({ i, v })) : [];

  return (
    <div className="group relative bg-white rounded-2xl border border-slate-200/70 p-5 hover:border-slate-300/80 hover:shadow-[0_8px_24px_-12px_rgba(10,63,181,0.15)] transition-all">
      {/* Header */}
      <div className="flex items-start justify-between mb-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-1">
            {icon && (
              <div className={`w-6 h-6 rounded-md ${s.bg} flex items-center justify-center ${s.text} ring-1 ${s.ring}`}>
                {icon}
              </div>
            )}
            <span className="text-[11px] font-medium text-slate-500 uppercase tracking-[0.06em]">
              {label}
            </span>
          </div>
        </div>

        {delta !== undefined && (
          <div
            className={`flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold ${
              deltaPositive
                ? 'bg-emerald-50 text-emerald-700'
                : 'bg-red-50 text-red-700'
            }`}
          >
            {deltaPositive ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
            {deltaPositive ? '+' : ''}{delta}
          </div>
        )}
      </div>

      {/* Value */}
      <div className="flex items-baseline gap-1 mb-2">
        <span
          className="text-[32px] font-semibold text-slate-900 leading-none tracking-[-0.02em]"
          style={{ fontFamily: 'var(--font-display)' }}
        >
          {value}
        </span>
        {suffix && <span className="text-[14px] font-medium text-slate-400">{suffix}</span>}
      </div>

      {deltaLabel && (
        <div className="text-[11px] text-slate-500">{deltaLabel}</div>
      )}

      {/* Sparkline */}
      {trend && trend.length > 0 && (
        <div className="h-[32px] -mx-1 mt-3">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data}>
              <YAxis hide domain={['dataMin', 'dataMax']} />
              <Line
                type="monotone"
                dataKey="v"
                stroke={s.bar}
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

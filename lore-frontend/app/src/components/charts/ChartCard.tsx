interface ChartCardProps {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

export default function ChartCard({ title, subtitle, action, children, className = '' }: ChartCardProps) {
  return (
    <div className={`bg-white rounded-2xl border border-slate-200/70 p-6 ${className}`}>
      <div className="flex items-start justify-between mb-5">
        <div>
          <h3
            className="text-[15px] font-semibold text-slate-900 leading-tight tracking-[-0.005em]"
            style={{ fontFamily: 'var(--font-display)' }}
          >
            {title}
          </h3>
          {subtitle && (
            <p className="text-[12px] text-slate-500 mt-1 leading-tight">{subtitle}</p>
          )}
        </div>
        {action}
      </div>
      {children}
    </div>
  );
}

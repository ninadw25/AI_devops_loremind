import { Shield, Pencil, Eye, Lock, Clock } from 'lucide-react';
import type { AccessLevel, PersonStatus } from '@/data/companyData';

interface AccessBadgeProps {
  level: AccessLevel;
  status?: PersonStatus;         // pass to show "Pending" instead of the level when applicable
  size?: 'xs' | 'sm' | 'md';
  showIcon?: boolean;
  showLabel?: boolean;
}

const config: Record<AccessLevel, { label: string; icon: React.ComponentType<{ className?: string }>; bg: string; text: string; border: string }> = {
  admin: {
    label: 'Admin',
    icon: Shield,
    bg: 'bg-indigo-50',
    text: 'text-indigo-700',
    border: 'border-indigo-100',
  },
  editor: {
    label: 'Editor',
    icon: Pencil,
    bg: 'bg-emerald-50',
    text: 'text-emerald-700',
    border: 'border-emerald-100',
  },
  viewer: {
    label: 'Viewer',
    icon: Eye,
    bg: 'bg-slate-100',
    text: 'text-slate-600',
    border: 'border-slate-200',
  },
  restricted: {
    label: 'Restricted',
    icon: Lock,
    bg: 'bg-red-50',
    text: 'text-red-700',
    border: 'border-red-100',
  },
};

const pendingConfig = {
  label: 'Pending',
  icon: Clock,
  bg: 'bg-amber-50',
  text: 'text-amber-700',
  border: 'border-amber-200',
};

const sizeMap = {
  xs: { wrap: 'px-1.5 py-0.5 text-[9px] gap-0.5',  iconCls: 'w-2.5 h-2.5' },
  sm: { wrap: 'px-2 py-0.5 text-[10px] gap-1',     iconCls: 'w-3 h-3' },
  md: { wrap: 'px-2.5 py-1 text-[11px] gap-1.5',   iconCls: 'w-3.5 h-3.5' },
};

export default function AccessBadge({
  level, status, size = 'sm', showIcon = true, showLabel = true,
}: AccessBadgeProps) {
  const cfg = status === 'pending_approval' ? pendingConfig : config[level];
  const Icon = cfg.icon;
  const s = sizeMap[size];

  return (
    <span
      className={`inline-flex items-center ${s.wrap} rounded-md font-semibold border ${cfg.bg} ${cfg.text} ${cfg.border} uppercase tracking-wider whitespace-nowrap`}
      title={cfg.label}
    >
      {showIcon && <Icon className={s.iconCls} />}
      {showLabel && cfg.label}
    </span>
  );
}

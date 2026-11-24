import { useState, useEffect } from 'react';
import { Bell } from 'lucide-react';
import { useAuth } from '@/auth/AuthContext';

/** Derive up-to-2-char initials from a name or email. */
function initialsFrom(name?: string, email?: string | null): string {
  const source = (name && name.trim()) || (email ? email.split('@')[0] : '');
  if (!source) return 'U';
  const parts = source.replace(/[._-]+/g, ' ').trim().split(/\s+/);
  const letters = parts.length >= 2 ? parts[0][0] + parts[1][0] : source.slice(0, 2);
  return letters.toUpperCase();
}

/** Human label for a platform/org role. */
function roleLabel(platformRole: string | null, orgRole: string | null): string {
  if (platformRole === 'lore_superadmin') return 'Lore Super Admin';
  if (platformRole === 'client_superadmin') return 'Org Admin';
  if (orgRole) return orgRole.charAt(0).toUpperCase() + orgRole.slice(1);
  return 'Member';
}

interface TopBarProps {
  title?: string;
  subtitle?: string;
  actions?: React.ReactNode;
}

const CONNECTOR_MAP: Record<string, { name: string; img: string }> = {
  gmail: { name: 'Gmail', img: '/images/gmail.png' },
  slack: { name: 'Slack', img: '/images/slack.png' },
  jira: { name: 'Jira', img: '/images/jira.jpg' },
  github: { name: 'GitHub', img: '/images/github.png' },
  gitlab: { name: 'GitLab', img: '/images/github.png' },
  outlook: { name: 'Outlook', img: '/images/outlook.png' },
  meet: { name: 'Google Meet', img: '/images/google_meet.png' },
};

function ConnectedSources() {
  const [connected, setConnected] = useState<Array<{ name: string; img: string }>>([]);

  const loadConnectedSources = () => {
    // 1. Primary Source: check loremind_setup_draft
    const draftRaw = localStorage.getItem('loremind_setup_draft');
    if (draftRaw) {
      try {
        const draft = JSON.parse(draftRaw);
        const active: Array<{ name: string; img: string }> = [];
        if (draft.gmail?.enabled && CONNECTOR_MAP.gmail) active.push(CONNECTOR_MAP.gmail);
        if (draft.slack?.enabled && CONNECTOR_MAP.slack) active.push(CONNECTOR_MAP.slack);
        if (draft.jira?.enabled && CONNECTOR_MAP.jira) active.push(CONNECTOR_MAP.jira);

        if (active.length > 0) {
          setConnected(active);
          return;
        }
      } catch (e) {
        console.error('Failed to parse loremind_setup_draft in TopBar', e);
      }
    }

    // 2. Fallback Source: loremind_connected_sources
    const fallbackRaw = localStorage.getItem('loremind_connected_sources');
    if (fallbackRaw) {
      try {
        const fallback = JSON.parse(fallbackRaw) as string[];
        const active = fallback
          .map((id) => CONNECTOR_MAP[id.toLowerCase()])
          .filter(Boolean) as Array<{ name: string; img: string }>;
        setConnected(active);
        return;
      } catch (e) {
        console.error('Failed to parse loremind_connected_sources in TopBar', e);
      }
    }

    // If absolutely nothing is connected
    setConnected([]);
  };

  useEffect(() => {
    loadConnectedSources();

    // Listen to local storage changes (real-time cross-component/cross-tab synchronization)
    const handleStorageChange = () => {
      loadConnectedSources();
    };

    window.addEventListener('storage', handleStorageChange);
    return () => {
      window.removeEventListener('storage', handleStorageChange);
    };
  }, []);

  if (connected.length === 0) {
    return (
      <div className="flex items-center gap-2 pl-3 pr-3.5 h-9 bg-slate-50 border border-slate-200/60 rounded-full">
        <span className="w-1.5 h-1.5 rounded-full bg-slate-300" />
        <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
          No Sources Connected
        </span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2 pl-3 pr-3.5 h-9 bg-white border border-slate-200/80 rounded-full">
      <span className="relative flex items-center">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
        <span className="absolute w-1.5 h-1.5 rounded-full bg-emerald-500 animate-ping opacity-70" />
      </span>
      <span className="text-[10px] font-semibold text-slate-600 uppercase tracking-wider mr-1">
        Connected
      </span>
      <div className="flex items-center -space-x-1.5">
        {connected.map((c) => (
          <div
            key={c.name}
            title={c.name}
            className="w-6 h-6 rounded-full bg-white border border-slate-200/70 flex items-center justify-center overflow-hidden ring-1 ring-white"
          >
            <img src={c.img} alt={c.name} className="w-3.5 h-3.5 object-contain" />
          </div>
        ))}
      </div>
    </div>
  );
}

export default function TopBar({ title, subtitle, actions }: TopBarProps) {
  const { user, identity } = useAuth();
  const displayName = user?.name?.trim() || (user?.email ? user.email.split('@')[0] : 'Account');
  const initials = initialsFrom(user?.name, user?.email ?? identity?.email);
  const subRole = roleLabel(identity?.platformRole ?? null, identity?.orgRole ?? null);

  return (
    <header className="h-[72px] bg-white/80 backdrop-blur-md border-b border-slate-200/80 sticky top-0 z-30 flex items-center justify-between px-8 gap-6">
      {/* Left - title + subtitle */}
      <div className="flex items-center gap-4 min-w-0 flex-1">
        {title && (
          <div className="min-w-0">
            <h1 className="text-[18px] font-semibold text-slate-900 leading-tight truncate" style={{ fontFamily: 'var(--font-display)' }}>
              {title}
            </h1>
            {subtitle && (
              <p className="text-[12px] text-slate-500 leading-tight truncate">{subtitle}</p>
            )}
          </div>
        )}
      </div>

      {/* Right - connector pill + actions + bell + avatar */}
      <div className="flex items-center gap-3 shrink-0">
        <ConnectedSources />
        {actions}
        <button className="relative w-9 h-9 rounded-lg hover:bg-slate-100 flex items-center justify-center text-slate-600 transition-colors">
          <Bell className="w-[17px] h-[17px]" strokeWidth={2} />
          <span className="absolute top-1.5 right-1.5 w-2 h-2 bg-red-500 rounded-full ring-2 ring-white" />
        </button>
        <div className="flex items-center gap-2.5 pl-3 border-l border-slate-200">
          <div className="w-9 h-9 rounded-full bg-gradient-to-br from-[#0a3fb5] to-[#3259ce] flex items-center justify-center text-white text-[12px] font-semibold ring-2 ring-white shadow-sm">
            {initials}
          </div>
          <div className="leading-tight hidden lg:block">
            <div className="text-[12px] font-semibold text-slate-900">{displayName}</div>
            <div className="text-[10px] text-slate-500">{subRole}</div>
          </div>
        </div>
      </div>
    </header>
  );
}

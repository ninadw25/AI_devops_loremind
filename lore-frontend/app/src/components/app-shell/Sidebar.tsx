import { NavLink, useNavigate } from 'react-router-dom';
import type { LucideIcon } from 'lucide-react';
import {
  Network, Users, MessageSquare, Settings, LogOut,
  Activity, UserCog, Ticket, ShieldCheck, ShieldAlert, BarChart3, Database,
  Search, UserMinus, GraduationCap,
} from 'lucide-react';
import { useAuth } from '@/auth/AuthContext';

interface NavItem {
  to: string;
  icon: LucideIcon;
  label: string;
}

const navItems: NavItem[] = [
  { to: '/map',       icon: Network,         label: 'Knowledge Map' },
  // Expertise tab hidden per request - restore by re-adding this line and the
  // `Grid3x3` import: { to: '/expertise', icon: Grid3x3, label: 'Expertise' },
  { to: '/team',      icon: Users,           label: 'Team' },
  { to: '/ask',       icon: MessageSquare,   label: 'Ask Lore' },
];

/** Org-admin (client_superadmin) dashboard sections - gated on identity.isClientAdmin. */
const clientAdminItems: NavItem[] = [
  { to: '/insights',     icon: BarChart3,   label: 'Insights' },
  { to: '/sources',      icon: Database,    label: 'Data Sources' },
  { to: '/security',     icon: ShieldAlert, label: 'Security & Access' },
  { to: '/health',       icon: Activity,    label: 'Service Health' },
  { to: '/org-users',    icon: UserCog,     label: 'Team & Access' },
  { to: '/tickets',      icon: Ticket,      label: 'Support Tickets' },
];

/** Loremind platform-admin (lore_superadmin) sections - gated on identity.isLoreAdmin. */
const loreAdminItems: NavItem[] = [
  { to: '/admin', icon: ShieldCheck, label: 'Admin Console' },
];

/** Knowledge Continuity tools — available to all authenticated users. */
const continuityItems: NavItem[] = [
  { to: '/continuity',    icon: ShieldAlert,   label: 'Continuity Radar' },
  { to: '/experts',       icon: Search,        label: 'Expert Search' },
  { to: '/offboarding',   icon: UserMinus,     label: 'Offboarding' },
  { to: '/onboarding-path', icon: GraduationCap, label: 'Onboarding Path' },
];

/** A single nav row (active indicator + icon + label), shared by every group. */
function NavRow({ item }: { item: NavItem }) {
  const Icon = item.icon;
  return (
    <NavLink
      to={item.to}
      className={({ isActive }) =>
        `group flex items-center gap-3 px-3 py-2.5 rounded-lg text-[13px] font-medium transition-all relative ${
          isActive
            ? 'text-blue-700 bg-blue-50/60'
            : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
        }`
      }
    >
      {({ isActive }) => (
        <>
          {isActive && (
            <span className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 bg-blue-700 rounded-r-full" />
          )}
          <Icon
            className={`w-[17px] h-[17px] ${
              isActive ? 'text-blue-700' : 'text-slate-500 group-hover:text-slate-700'
            }`}
            strokeWidth={2}
          />
          {item.label}
        </>
      )}
    </NavLink>
  );
}

/** Small uppercase group heading. */
function NavGroupLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-[0.12em] px-3 mb-2">
      {children}
    </div>
  );
}

export default function Sidebar() {
  const { logout, identity } = useAuth();
  const navigate = useNavigate();

  const handleSignOut = () => {
    logout();
    navigate('/login', { replace: true });
  };

  return (
    <aside className="fixed inset-y-0 left-0 w-[240px] bg-white border-r border-slate-200/80 flex flex-col z-40">
      {/* Brand */}
      <div className="h-[72px] flex items-center justify-center px-6 border-b border-slate-200/80">
        <img src="/images/logo.png" alt="Loremind" className="h-9 w-auto object-contain" />
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-5 space-y-0.5 overflow-y-auto">
        <NavGroupLabel>Analytics</NavGroupLabel>
        {navItems.map((item) => (
          <NavRow key={item.to} item={item} />
        ))}

        {/* Knowledge Continuity — visible to all authenticated users */}
        <div className="pt-5">
          <NavGroupLabel>Knowledge Continuity</NavGroupLabel>
          {continuityItems.map((item) => (
            <NavRow key={item.to} item={item} />
          ))}
        </div>

        {/* Client (org) admin controls - only for organization super-admins. */}
        {identity?.isClientAdmin && (
          <div className="pt-5">
            <NavGroupLabel>Administration</NavGroupLabel>
            {clientAdminItems.map((item) => (
              <NavRow key={item.to} item={item} />
            ))}
          </div>
        )}

        {/* Loremind platform admin console - only for lore super-admins. */}
        {identity?.isLoreAdmin && (
          <div className="pt-5">
            <NavGroupLabel>Platform</NavGroupLabel>
            {loreAdminItems.map((item) => (
              <NavRow key={item.to} item={item} />
            ))}
          </div>
        )}
      </nav>

      {/* Footer */}
      <div className="border-t border-slate-200/80 p-3 space-y-0.5">
        <NavLink
          to="/setup"
          className={({ isActive }) =>
            `group flex items-center gap-3 px-3 py-2.5 rounded-lg text-[13px] font-medium transition-all relative ${
              isActive
                ? 'text-blue-700 bg-blue-50/60'
                : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
            }`
          }
        >
          {({ isActive }) => (
            <>
              {isActive && (
                <span className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 bg-blue-700 rounded-r-full" />
              )}
              <Settings
                className={`w-[17px] h-[17px] ${
                  isActive ? 'text-blue-700' : 'text-slate-500 group-hover:text-slate-700'
                }`}
                strokeWidth={2}
              />
              Settings
            </>
          )}
        </NavLink>
        <button
          onClick={handleSignOut}
          className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-[13px] font-medium text-slate-600 hover:bg-slate-50 hover:text-slate-900 transition-all"
        >
          <LogOut className="w-[17px] h-[17px] text-slate-500" strokeWidth={2} />
          Sign out
        </button>
      </div>
    </aside>
  );
}

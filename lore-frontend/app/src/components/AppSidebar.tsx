import { Link, useNavigate } from 'react-router-dom';
import {
  Search, Map, LayoutDashboard, Activity, Users, Home,
} from 'lucide-react';

export type SidebarTab = 'ask-lore' | 'map' | 'dashboard' | 'pulse' | 'team';

const navItems: { id: SidebarTab; label: string; path: string; icon: typeof Search }[] = [
  { id: 'ask-lore', label: 'Ask Lore', path: '/ask', icon: Search },
  { id: 'dashboard', label: 'Dashboard', path: '/dashboard', icon: LayoutDashboard },
  { id: 'map', label: 'Knowledge Map', path: '/map', icon: Map },
  { id: 'pulse', label: 'Lore Pulse', path: '/pulse', icon: Activity },
  { id: 'team', label: 'Team Management', path: '/team', icon: Users },
];

interface AppSidebarProps {
  activeTab: SidebarTab;
  children?: React.ReactNode;
}

export default function AppSidebar({ activeTab, children }: AppSidebarProps) {
  const navigate = useNavigate();

  return (
    <aside className="w-64 bg-white border-r border-slate-200 flex flex-col fixed h-full z-10">
      {/* Logo */}
      <div className="p-6 border-b border-slate-100">
        <Link to="/ask" className="flex items-center gap-3">
          <img src="/images/logo.png" alt="Lore" className="h-9 w-auto" />
          <span className="text-xl font-semibold text-slate-900">LORE</span>
        </Link>
      </div>

      {/* Navigation */}
      <nav className="p-4 space-y-1">
        {navItems.map((item) => {
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => navigate(item.path)}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-200 ${
                isActive ? 'bg-blue-50 text-blue-700' : 'text-slate-600 hover:bg-slate-50'
              }`}
            >
              <item.icon className="w-5 h-5" />
              <span className="font-medium">{item.label}</span>
            </button>
          );
        })}
      </nav>

      {/* Extra sidebar content from pages */}
      {children && (
        <div className="flex-1 overflow-y-auto border-t border-slate-100">
          {children}
        </div>
      )}

      {/* Spacer when no children */}
      {!children && <div className="flex-1" />}

      {/* Back to home */}
      <div className="p-4 border-t border-slate-100">
        <button
          onClick={() => navigate('/')}
          className="w-full flex items-center gap-3 px-4 py-3 rounded-xl text-slate-600 hover:bg-slate-50 transition-colors"
        >
          <Home className="w-5 h-5" />
          <span className="font-medium">Back to Home</span>
        </button>
      </div>
    </aside>
  );
}

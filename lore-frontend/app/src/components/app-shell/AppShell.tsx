import { useEffect, useState } from 'react';
import Sidebar from './Sidebar';
import TopBar from './TopBar';
import PatSetupModal from '@/components/PatSetupModal';
import { useAuth } from '@/auth/AuthContext';
import { listEnvVars } from '@/services/envVarsApi';

interface AppShellProps {
  title?: string;
  subtitle?: string;
  actions?: React.ReactNode;
  /** Skip dashboard padding + max-width - for full-bleed canvases like the graph or chat. */
  bleed?: boolean;
  children: React.ReactNode;
}

const PAT_CHECK_FLAG = 'lore_pat_checked';

export default function AppShell({ title, subtitle, actions, bleed, children }: AppShellProps) {
  const { identity } = useAuth();
  const [showPat, setShowPat] = useState(false);

  // First-login prompt: an org admin with no stored keys is asked to connect a
  // service (PAT) so ingestion can start. Checked once per session.
  useEffect(() => {
    if (!identity?.isClientAdmin) return;
    if (sessionStorage.getItem(PAT_CHECK_FLAG)) return;
    sessionStorage.setItem(PAT_CHECK_FLAG, '1');
    listEnvVars()
      .then((keys) => {
        if (keys.length === 0) setShowPat(true);
      })
      .catch(() => {
        /* non-fatal - don't block the dashboard if the check fails */
      });
  }, [identity]);

  return (
    <div className={`bg-[#f8f9fb] app-shell ${bleed ? 'h-screen overflow-hidden' : 'min-h-screen'}`}>
      <Sidebar />
      <div className={`pl-[240px] flex flex-col ${bleed ? 'h-screen' : 'min-h-screen'}`}>
        <TopBar title={title} subtitle={subtitle} actions={actions} />
        <main className={bleed ? 'flex-1 flex flex-col min-h-0 overflow-hidden' : 'px-8 py-8 max-w-[1600px]'}>
          {children}
        </main>
      </div>
      <PatSetupModal open={showPat} onClose={() => setShowPat(false)} onComplete={() => setShowPat(false)} />
    </div>
  );
}

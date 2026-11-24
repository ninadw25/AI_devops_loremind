import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, useLocation } from 'react-router-dom';
import { ErrorBoundary } from './components/ErrorBoundary';
import RequireAuth from './auth/RequireAuth';
import RequireRole from './auth/RequireRole';
import NewLandingPage from './screens/NewLandingPage';
import Login from './screens/Login';
import Onboarding from './screens/Onboarding';
import Setup from './screens/Setup';
import Learning from './screens/Learning';
import AskLore from './screens/AskLore';
import Dashboard from './screens/Dashboard';
import ExpertiseMatrix from './screens/ExpertiseMatrix';
import KnowledgeMap from './screens/KnowledgeMap';
import TeamManagement from './screens/TeamManagement';
import PersonProfile from './screens/PersonProfile';
import Health from './screens/Health';
import OrgUsers from './screens/OrgUsers';
import Tickets from './screens/Tickets';
import Insights from './screens/Insights';
import Sources from './screens/Sources';
import Security from './screens/Security';
import LoreAdminHome from './screens/admin/LoreAdminHome';
import ContinuityRadar from './screens/ContinuityRadar';
import ExpertSearch from './screens/ExpertSearch';
import Offboarding from './screens/Offboarding';
import OnboardingPath from './screens/OnboardingPath';

function ScrollToTop() {
  const { pathname } = useLocation();

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  return null;
}

function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <ScrollToTop />
        <Routes>
          {/* Public */}
          <Route path="/" element={<NewLandingPage />} />
          <Route path="/contact" element={<NewLandingPage />} />
          <Route path="/login" element={<Login />} />

          {/* Authenticated app (client + regular users) */}
          <Route element={<RequireAuth />}>
            <Route path="/onboarding" element={<Onboarding />} />
            <Route path="/setup" element={<Setup />} />
            <Route path="/learning" element={<Learning />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/expertise" element={<ExpertiseMatrix />} />
            <Route path="/ask" element={<AskLore />} />
            <Route path="/map" element={<KnowledgeMap />} />
            <Route path="/team" element={<TeamManagement />} />
            <Route path="/team/:id" element={<PersonProfile />} />

            {/* Client (org) admin dashboard - client_superadmin only */}
            <Route element={<RequireRole require="client_superadmin" />}>
              <Route path="/insights" element={<Insights />} />
              <Route path="/sources" element={<Sources />} />
              <Route path="/security" element={<Security />} />
              <Route path="/health" element={<Health />} />
              <Route path="/org-users" element={<OrgUsers />} />
              <Route path="/tickets" element={<Tickets />} />
            </Route>

            {/* Loremind platform admin area */}
            <Route element={<RequireRole require="lore_superadmin" />}>
              <Route path="/admin" element={<LoreAdminHome />} />
            </Route>

            {/* Knowledge Continuity */}
            <Route path="/continuity" element={<ContinuityRadar />} />
            <Route path="/experts" element={<ExpertSearch />} />
            <Route path="/offboarding" element={<Offboarding />} />
            <Route path="/onboarding-path" element={<OnboardingPath />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  );
}

export default App;

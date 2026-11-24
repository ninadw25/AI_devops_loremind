import { useMemo, useState } from "react";
import {
  ShieldCheck, Users, Eye, KeyRound, Check, Plus, Layers, Building2,
} from "lucide-react";
import AppShell from "@/components/app-shell/AppShell";

/**
 * Security & Access - RBAC management.
 *
 * People are grouped by team (group). Each person shows their visibility scope
 * (which teams' knowledge they can read) and the roles assigned to them. Roles
 * bundle policies (authorities) and can be assigned to a person or to a whole
 * team (group), following the IAM policy/role model.
 */

// ── Policy catalogue (authorities) ──────────────────────────────────────────
const POLICY_LABEL: Record<string, string> = {
  "*.*": "All permissions (wildcard)",
  "content.read_all": "Read all knowledge (bypass content ACL)",
  "query.ask": "Ask questions",
  "query.cross_source": "Cross-source queries",
  "map.view": "View knowledge map",
  "citation.view_source": "View source citations",
  "user.manage": "Manage team members",
  "billing.manage": "Manage billing",
  "rbac.manage": "Manage roles & access",
  "audit.view": "View audit logs",
  "ingestion.manage": "Manage data sources",
};

// ── Roles (bundles of policies) ─────────────────────────────────────────────
interface Role {
  key: string;
  name: string;
  tone: string; // pill classes
  policies: string[];
}
const ROLES: Role[] = [
  { key: "company-admin", name: "Company Admin", tone: "bg-violet-50 text-violet-700 border-violet-200", policies: ["*.*"] },
  { key: "content-admin", name: "Content Admin", tone: "bg-amber-50 text-amber-700 border-amber-200", policies: ["content.read_all", "query.ask", "query.cross_source", "map.view", "citation.view_source"] },
  { key: "security-admin", name: "Security Admin", tone: "bg-rose-50 text-rose-700 border-rose-200", policies: ["rbac.manage", "audit.view", "user.manage"] },
  { key: "billing-admin", name: "Billing Admin", tone: "bg-emerald-50 text-emerald-700 border-emerald-200", policies: ["billing.manage"] },
  { key: "data-admin", name: "Data Admin", tone: "bg-sky-50 text-sky-700 border-sky-200", policies: ["ingestion.manage", "map.view"] },
  { key: "team-lead", name: "Team Lead", tone: "bg-blue-50 text-blue-700 border-blue-200", policies: ["query.ask", "query.cross_source", "map.view", "user.manage"] },
  { key: "analyst", name: "Analyst", tone: "bg-indigo-50 text-indigo-700 border-indigo-200", policies: ["query.ask", "query.cross_source", "map.view"] },
  { key: "member", name: "Member", tone: "bg-slate-100 text-slate-600 border-slate-200", policies: ["query.ask", "map.view"] },
  { key: "viewer", name: "Viewer", tone: "bg-slate-100 text-slate-500 border-slate-200", policies: ["map.view"] },
];
const ROLE_BY_KEY = Object.fromEntries(ROLES.map((r) => [r.key, r]));

// ── Teams (groups) + their default visibility scope ─────────────────────────
interface Team {
  id: string;
  name: string;
  scope: string[]; // team ids this team can see
  groupRoles: string[]; // roles applied to the whole team
}
const TEAMS: Team[] = [
  { id: "leadership", name: "Leadership", scope: ["leadership", "marketing", "ai-services", "software-services", "hr", "finance"], groupRoles: [] },
  { id: "marketing", name: "Marketing", scope: ["marketing"], groupRoles: ["member"] },
  { id: "ai-services", name: "AI Services", scope: ["ai-services", "software-services"], groupRoles: ["member"] },
  { id: "software-services", name: "Software Services", scope: ["software-services"], groupRoles: ["member"] },
  { id: "hr", name: "HR", scope: ["hr"], groupRoles: ["member"] },
  { id: "finance", name: "Finance", scope: ["finance"], groupRoles: ["member"] },
];
const TEAM_BY_ID = Object.fromEntries(TEAMS.map((t) => [t.id, t]));

// ── People (Ezobooks employees) ─────────────────────────────────────────────
interface Person {
  id: string;
  name: string;
  title: string;
  teamId: string;
  roles: string[]; // per-person roles (on top of the team's group roles)
}
const PEOPLE: Person[] = [
  { id: "p-gaurav", name: "Gaurav Kumar Kate", title: "CEO", teamId: "leadership", roles: ["company-admin"] },
  { id: "p-makarand", name: "Makarand", title: "CTO", teamId: "leadership", roles: ["content-admin", "data-admin"] },
  { id: "p-rishikesh", name: "Rishikesh Sarkar", title: "COO", teamId: "leadership", roles: ["content-admin", "security-admin"] },
  { id: "p-rohan", name: "Rohan Patil", title: "Marketing Lead", teamId: "marketing", roles: ["team-lead"] },
  { id: "p-priya", name: "Priya Sharma", title: "Content Strategist", teamId: "marketing", roles: ["analyst"] },
  { id: "p-neha", name: "Neha Verma", title: "Campaign Manager", teamId: "marketing", roles: [] },
  { id: "p-amol", name: "Amol Kale", title: "SEO Specialist", teamId: "marketing", roles: [] },
  { id: "p-aditi", name: "Aditi Kelkar", title: "AI Services Lead", teamId: "ai-services", roles: ["team-lead"] },
  { id: "p-omkar", name: "Omkar Gokhale", title: "ML Engineer", teamId: "ai-services", roles: ["analyst"] },
  { id: "p-rutuja", name: "Rutuja Bhosale", title: "Data Scientist", teamId: "ai-services", roles: [] },
  { id: "p-vishal", name: "Vishal Sawant", title: "Software Lead", teamId: "software-services", roles: ["team-lead"] },
  { id: "p-ajinkya", name: "Ajinkya Pawar", title: "Senior Engineer", teamId: "software-services", roles: ["analyst"] },
  { id: "p-prasad", name: "Prasad Kulkarni", title: "Backend Engineer", teamId: "software-services", roles: [] },
  { id: "p-sagar", name: "Sagar Jadhav", title: "Frontend Engineer", teamId: "software-services", roles: [] },
  { id: "p-vaishnavi", name: "Vaishnavi Kadam", title: "HR Manager", teamId: "hr", roles: ["team-lead", "billing-admin"] },
  { id: "p-pooja", name: "Pooja Deshmukh", title: "HR Analyst", teamId: "hr", roles: [] },
  { id: "p-sameer", name: "Sameer Gaikwad", title: "Finance Lead", teamId: "finance", roles: ["team-lead"] },
  { id: "p-ketki", name: "Ketki Ranade", title: "Accountant", teamId: "finance", roles: [] },
];

function initials(name: string): string {
  return name.split(" ").slice(0, 2).map((w) => w[0]).join("").toUpperCase();
}

export default function Security() {
  // Local, editable assignment state.
  const [personRoles, setPersonRoles] = useState<Record<string, string[]>>(
    Object.fromEntries(PEOPLE.map((p) => [p.id, p.roles])),
  );
  const [teamRoles, setTeamRoles] = useState<Record<string, string[]>>(
    Object.fromEntries(TEAMS.map((t) => [t.id, t.groupRoles])),
  );
  const [selectedId, setSelectedId] = useState<string>("p-gaurav");

  const selected = PEOPLE.find((p) => p.id === selectedId)!;
  const selectedTeam = TEAM_BY_ID[selected.teamId];

  // Effective roles = person roles + the roles applied to their team (group).
  const effectiveRoleKeys = useMemo(() => {
    const set = new Set<string>([...(personRoles[selected.id] ?? []), ...(teamRoles[selected.teamId] ?? [])]);
    return [...set];
  }, [personRoles, teamRoles, selected]);

  // Effective policies = union of all effective roles' policies.
  const effectivePolicies = useMemo(() => {
    const set = new Set<string>();
    for (const rk of effectiveRoleKeys) for (const p of ROLE_BY_KEY[rk]?.policies ?? []) set.add(p);
    return [...set];
  }, [effectiveRoleKeys]);

  const isSuperReader = effectivePolicies.includes("*.*") || effectivePolicies.includes("content.read_all");
  const effectiveScope = isSuperReader
    ? TEAMS.map((t) => t.name)
    : selectedTeam.scope.map((id) => TEAM_BY_ID[id]?.name ?? id);

  function togglePersonRole(roleKey: string) {
    setPersonRoles((prev) => {
      const cur = prev[selected.id] ?? [];
      return { ...prev, [selected.id]: cur.includes(roleKey) ? cur.filter((r) => r !== roleKey) : [...cur, roleKey] };
    });
  }
  function toggleTeamRole(teamId: string, roleKey: string) {
    setTeamRoles((prev) => {
      const cur = prev[teamId] ?? [];
      return { ...prev, [teamId]: cur.includes(roleKey) ? cur.filter((r) => r !== roleKey) : [...cur, roleKey] };
    });
  }

  return (
    <AppShell
      title="Security & Access"
      subtitle="Teams, visibility scope, and role and policy assignment"
    >
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* ── Teams + members ── */}
        <div className="lg:col-span-2 space-y-5">
          {TEAMS.map((team) => {
            const members = PEOPLE.filter((p) => p.teamId === team.id);
            return (
              <div key={team.id} className="rounded-xl border border-slate-200/70 bg-white shadow-sm overflow-hidden">
                <div className="px-5 py-3.5 border-b border-slate-200/70 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <Users className="w-4 h-4 text-slate-400" strokeWidth={2} />
                    <span className="text-[13.5px] font-semibold text-slate-800">{team.name}</span>
                    <span className="text-[11px] text-slate-400">{members.length} members</span>
                  </div>
                  <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
                    <Eye className="w-3.5 h-3.5" />
                    sees: {team.scope.map((id) => TEAM_BY_ID[id]?.name ?? id).join(", ")}
                  </div>
                </div>
                <div className="divide-y divide-slate-100">
                  {members.map((p) => {
                    const roles = [...(personRoles[p.id] ?? []), ...(teamRoles[p.teamId] ?? [])];
                    const uniqueRoles = [...new Set(roles)];
                    const active = p.id === selectedId;
                    return (
                      <button
                        key={p.id}
                        onClick={() => setSelectedId(p.id)}
                        className={`w-full flex items-center justify-between gap-4 px-5 py-3 text-left transition-colors ${active ? "bg-blue-50/50" : "hover:bg-slate-50"}`}
                      >
                        <div className="flex items-center gap-3 min-w-0">
                          <div className="w-8 h-8 rounded-full bg-slate-100 border border-slate-200 flex items-center justify-center text-[11px] font-semibold text-slate-600 shrink-0">
                            {initials(p.name)}
                          </div>
                          <div className="min-w-0">
                            <div className="text-[13.5px] font-medium text-slate-900 truncate">{p.name}</div>
                            <div className="text-[11.5px] text-slate-400 truncate">{p.title}</div>
                          </div>
                        </div>
                        <div className="flex items-center gap-1.5 flex-wrap justify-end shrink-0 max-w-[52%]">
                          {uniqueRoles.length === 0 ? (
                            <span className="text-[11px] text-slate-300">no roles</span>
                          ) : (
                            uniqueRoles.map((rk) => (
                              <span key={rk} className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold ${ROLE_BY_KEY[rk]?.tone ?? "bg-slate-100 text-slate-600 border-slate-200"}`}>
                                {ROLE_BY_KEY[rk]?.name ?? rk}
                              </span>
                            ))
                          )}
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>

        {/* ── Assignment panel ── */}
        <div className="lg:col-span-1">
          <div className="lg:sticky lg:top-6 space-y-4">
            {/* Selected identity */}
            <div className="rounded-xl border border-slate-200/70 bg-white shadow-sm p-5">
              <div className="flex items-center gap-3">
                <div className="w-11 h-11 rounded-full bg-slate-100 border border-slate-200 flex items-center justify-center text-[13px] font-semibold text-slate-600">
                  {initials(selected.name)}
                </div>
                <div className="min-w-0">
                  <div className="text-[15px] font-semibold text-slate-900 truncate">{selected.name}</div>
                  <div className="text-[12px] text-slate-400">{selected.title} · {selectedTeam.name}</div>
                </div>
              </div>

              {/* Effective scope */}
              <div className="mt-4">
                <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-1.5">
                  <Eye className="w-3.5 h-3.5" /> Visibility scope
                </div>
                {isSuperReader ? (
                  <div className="inline-flex items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[12px] font-medium text-amber-800">
                    <KeyRound className="w-3.5 h-3.5" /> All teams (content.read_all bypass)
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {effectiveScope.map((name) => (
                      <span key={name} className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-medium text-slate-600">
                        <Building2 className="w-3 h-3" /> {name}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* Effective policies */}
              <div className="mt-4">
                <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-1.5">
                  <ShieldCheck className="w-3.5 h-3.5" /> Effective policies
                </div>
                <div className="space-y-1">
                  {effectivePolicies.map((pol) => (
                    <div key={pol} className="flex items-center gap-2 text-[12px] text-slate-700">
                      <Check className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                      <span className="font-mono text-[11.5px] text-slate-500">{pol}</span>
                      <span className="text-slate-400 truncate">· {POLICY_LABEL[pol] ?? ""}</span>
                    </div>
                  ))}
                  {effectivePolicies.length === 0 && (
                    <div className="text-[12px] text-slate-400">No policies - assign a role below.</div>
                  )}
                </div>
              </div>
            </div>

            {/* Assign roles to the person */}
            <div className="rounded-xl border border-slate-200/70 bg-white shadow-sm p-5">
              <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-3">
                <Plus className="w-3.5 h-3.5" /> Roles for {selected.name.split(" ")[0]}
              </div>
              <div className="flex flex-wrap gap-2">
                {ROLES.map((r) => {
                  const on = (personRoles[selected.id] ?? []).includes(r.key);
                  const fromTeam = (teamRoles[selected.teamId] ?? []).includes(r.key);
                  return (
                    <button
                      key={r.key}
                      onClick={() => togglePersonRole(r.key)}
                      disabled={fromTeam}
                      title={fromTeam ? "Inherited from the team - manage below" : undefined}
                      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-semibold transition-colors ${
                        on || fromTeam ? r.tone : "bg-white text-slate-500 border-slate-200 hover:border-slate-300"
                      } ${fromTeam ? "opacity-60 cursor-not-allowed" : ""}`}
                    >
                      {on || fromTeam ? <Check className="w-3 h-3" /> : <Plus className="w-3 h-3" />}
                      {r.name}
                      {fromTeam ? " (team)" : ""}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Assign roles to the whole team (group) */}
            <div className="rounded-xl border border-slate-200/70 bg-white shadow-sm p-5">
              <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-3">
                <Layers className="w-3.5 h-3.5" /> Group roles for {selectedTeam.name}
              </div>
              <div className="flex flex-wrap gap-2">
                {ROLES.map((r) => {
                  const on = (teamRoles[selectedTeam.id] ?? []).includes(r.key);
                  return (
                    <button
                      key={r.key}
                      onClick={() => toggleTeamRole(selectedTeam.id, r.key)}
                      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-semibold transition-colors ${
                        on ? r.tone : "bg-white text-slate-500 border-slate-200 hover:border-slate-300"
                      }`}
                    >
                      {on ? <Check className="w-3 h-3" /> : <Plus className="w-3 h-3" />}
                      {r.name}
                    </button>
                  );
                })}
              </div>
              <p className="text-[11px] text-slate-400 mt-2.5">
                Group roles apply to every member of {selectedTeam.name}.
              </p>
            </div>

            {/* Roles & policies legend */}
            <div className="rounded-xl border border-slate-200/70 bg-white shadow-sm p-5">
              <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-3">Roles & policies</div>
              <div className="space-y-2.5">
                {ROLES.map((r) => (
                  <div key={r.key}>
                    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold ${r.tone}`}>{r.name}</span>
                    <span className="text-[11px] text-slate-400 ml-2">{r.policies.join(", ")}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

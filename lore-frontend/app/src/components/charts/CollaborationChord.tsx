import { useState } from 'react';
import { useTeamCollaboration } from '@/data/hooks';
import type { TeamId } from '@/data/companyData';

const teamColor: Record<string, string> = {
  leadership: '#1d4ed8',
  operations: '#0a3fb5',
  hr:         '#7c3aed',
  finance:    '#059669',
  tech:       '#0891b2',
  sales:      '#ea580c',
  marketing:  '#e11d48',
};

const teamLabel: Record<string, string> = {
  leadership: 'Leadership',
  operations: 'Operations',
  hr:         'HR',
  finance:    'Finance',
  tech:       'Tech',
  sales:      'Sales',
  marketing:  'Marketing',
};

const TEAMS: TeamId[] = ['leadership', 'operations', 'hr', 'finance', 'tech', 'sales', 'marketing'];

// SVG canvas
const W = 480;
const H = 380;
const CX = W / 2;
const CY = H / 2 + 6;
const R = 120;

export default function CollaborationChord() {
  const { edges, totals } = useTeamCollaboration();
  const [hoveredPair, setHoveredPair] = useState<string | null>(null);
  const [hoveredTeam, setHoveredTeam] = useState<TeamId | null>(null);

  // Position teams around the circle
  const positions: Record<string, { x: number; y: number; angle: number }> = {};
  TEAMS.forEach((t, i) => {
    const angle = (i / TEAMS.length) * 2 * Math.PI - Math.PI / 2;
    positions[t] = {
      x: CX + R * Math.cos(angle),
      y: CY + R * Math.sin(angle),
      angle,
    };
  });

  const maxVolume = Math.max(...edges.map(e => e.volume));
  const totalVolume = edges.reduce((a, e) => a + e.volume, 0);

  // For each edge, compute curved path through center
  // Quadratic Bezier with control point near center, slightly offset
  const curvePath = (a: { x: number; y: number }, b: { x: number; y: number }) => {
    // Control point near center but pulled toward midpoint slightly
    const mx = (a.x + b.x) / 2;
    const my = (a.y + b.y) / 2;
    const cx = CX + (mx - CX) * 0.15;
    const cy = CY + (my - CY) * 0.15;
    return `M ${a.x} ${a.y} Q ${cx} ${cy} ${b.x} ${b.y}`;
  };

  // Edge thickness based on volume
  const strokeWidth = (vol: number) => 1 + (vol / maxVolume) * 9;

  const isEdgeActive = (from: string, to: string) => {
    if (hoveredPair) return hoveredPair === `${from}-${to}` || hoveredPair === `${to}-${from}`;
    if (hoveredTeam) return from === hoveredTeam || to === hoveredTeam;
    return false;
  };

  const anyHover = hoveredPair || hoveredTeam;

  return (
    <div>
      <div className="flex items-end justify-between mb-3">
        <div>
          <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1">
            Cross-team interactions · weekly
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-[32px] font-semibold text-slate-900 leading-none tabular-nums" style={{ fontFamily: 'var(--font-display)' }}>
              {totalVolume}
            </span>
            <span className="text-[12px] text-slate-500">touchpoints</span>
          </div>
        </div>
      </div>

      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" style={{ aspectRatio: `${W} / ${H}` }}>
          <defs>
            {edges.map(e => (
              <linearGradient
                key={`grad-${e.from}-${e.to}`}
                id={`grad-${e.from}-${e.to}`}
                x1={positions[e.from].x}
                y1={positions[e.from].y}
                x2={positions[e.to].x}
                y2={positions[e.to].y}
                gradientUnits="userSpaceOnUse"
              >
                <stop offset="0%"  stopColor={teamColor[e.from]} />
                <stop offset="100%" stopColor={teamColor[e.to]} />
              </linearGradient>
            ))}
          </defs>

          {/* Edges (chords) */}
          <g>
            {edges.map(e => {
              const a = positions[e.from];
              const b = positions[e.to];
              const active = isEdgeActive(e.from, e.to);
              const dimmed = anyHover && !active;
              return (
                <path
                  key={`${e.from}-${e.to}`}
                  d={curvePath(a, b)}
                  fill="none"
                  stroke={`url(#grad-${e.from}-${e.to})`}
                  strokeWidth={strokeWidth(e.volume)}
                  strokeLinecap="round"
                  opacity={dimmed ? 0.08 : (active ? 0.95 : 0.55)}
                  className="transition-opacity duration-200 cursor-pointer"
                  onMouseEnter={() => setHoveredPair(`${e.from}-${e.to}`)}
                  onMouseLeave={() => setHoveredPair(null)}
                />
              );
            })}
          </g>

          {/* Team nodes */}
          <g>
            {TEAMS.map(team => {
              const p = positions[team];
              const total = totals[team] || 0;
              const isHovered = hoveredTeam === team || (hoveredPair && hoveredPair.includes(team));
              const dimmed = anyHover && !isHovered;
              const radius = 22 + (total / Math.max(...Object.values(totals))) * 8;

              // Label position - push outward from circle
              const labelDist = R + 38;
              const lx = CX + labelDist * Math.cos(p.angle);
              const ly = CY + labelDist * Math.sin(p.angle);

              return (
                <g
                  key={team}
                  onMouseEnter={() => setHoveredTeam(team)}
                  onMouseLeave={() => setHoveredTeam(null)}
                  className="cursor-pointer"
                  opacity={dimmed ? 0.35 : 1}
                  style={{ transition: 'opacity 0.2s' }}
                >
                  {/* Outer halo when active */}
                  {isHovered && (
                    <circle
                      cx={p.x}
                      cy={p.y}
                      r={radius + 8}
                      fill={teamColor[team]}
                      opacity={0.15}
                    />
                  )}
                  {/* Outer ring */}
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r={radius + 3}
                    fill="white"
                    stroke={teamColor[team]}
                    strokeWidth={1.5}
                  />
                  {/* Body */}
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r={radius}
                    fill={teamColor[team]}
                  />
                  {/* Volume label inside */}
                  <text
                    x={p.x}
                    y={p.y}
                    textAnchor="middle"
                    dy="0.35em"
                    fontSize={radius * 0.6}
                    fontWeight={700}
                    fill="white"
                    fontFamily="var(--font-display)"
                  >
                    {total}
                  </text>
                  {/* Team name label outside */}
                  <text
                    x={lx}
                    y={ly}
                    textAnchor="middle"
                    dy="0.35em"
                    fontSize={11}
                    fontWeight={600}
                    fill="#334155"
                    fontFamily="var(--font-body)"
                  >
                    {teamLabel[team]}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      <div className="mt-3 pt-4 border-t border-slate-100 text-[10px] text-slate-500 leading-relaxed">
        Numbers inside each node = total weekly touchpoints across messages, threads, mentions, and meetings.
        Chord thickness = pairwise volume. Hover any chord or team to isolate its connections.
      </div>
    </div>
  );
}

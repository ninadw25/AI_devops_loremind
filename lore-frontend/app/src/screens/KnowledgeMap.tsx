import { useState, useEffect, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Users, Tag, Server, X, ChevronRight, MessageSquare,
  Building2
} from 'lucide-react';
import {
  people as cpyPeople,
  services as cpyServices,
  projects as cpyProjects,
  expertiseAreas as cpyExpertiseAreas,
} from '@/data/companyData';
import AppShell from '@/components/app-shell/AppShell';
import AccessBadge from '@/components/widgets/AccessBadge';

// ──────────────── Adapter - build entities + edges from companyData ─────────
interface EntityItem {
  id: string;
  name: string;
  type: 'person' | 'project' | 'topic' | 'client';
  role?: string;
  department?: string;
  status?: string;
}

interface EdgeItem {
  source: string;
  target: string;
  weight: number;
}

function buildFromCompanyData(): { entities: EntityItem[]; edges: EdgeItem[] } {
  const entities: EntityItem[] = [];
  const edges: EdgeItem[] = [];

  // People (internal only - exclude external customers)
  cpyPeople.filter(p => p.status !== 'external').forEach(p => {
    entities.push({
      id: `p-${p.id}`,
      name: p.name,
      type: 'person',
      role: p.role,
      department: p.team,
      status: p.status,
    });
  });

  // Services (rendered visually as 'client' type → labelled "Services")
  cpyServices.forEach(s => {
    entities.push({
      id: `s-${s.id}`,
      name: s.name,
      type: 'client',
      status: s.busFactor === 1 ? 'critical' : 'active',
    });
  });

  // Projects
  cpyProjects.forEach(pr => {
    entities.push({
      id: `pr-${pr.id}`,
      name: pr.name,
      type: 'project',
      status: pr.health,
    });
  });

  // Topics (only expertise areas with notable usage - score >= 70 by someone)
  const usedTopics = new Set<string>();
  cpyPeople.forEach(p => {
    Object.entries(p.expertise).forEach(([areaId, score]) => {
      if (score >= 65) usedTopics.add(areaId);
    });
  });
  cpyExpertiseAreas.filter(a => usedTopics.has(a.id)).forEach(a => {
    entities.push({
      id: `t-${a.id}`,
      name: a.name,
      type: 'topic',
    });
  });

  // ── Edges ──
  // person → service (ownership)
  cpyServices.forEach(s => {
    edges.push({ source: `p-${s.primaryOwner}`, target: `s-${s.id}`, weight: 0.95 });
  });

  // person → topic (expertise)
  cpyPeople.filter(p => p.status !== 'external').forEach(p => {
    Object.entries(p.expertise).forEach(([areaId, score]) => {
      if (score >= 65 && usedTopics.has(areaId)) {
        edges.push({ source: `p-${p.id}`, target: `t-${areaId}`, weight: score / 100 });
      }
    });
  });

  // project → person (owner + contributors)
  cpyProjects.forEach(pr => {
    if (cpyPeople.find(p => p.id === pr.owner && p.status !== 'external')) {
      edges.push({ source: `pr-${pr.id}`, target: `p-${pr.owner}`, weight: 0.85 });
    }
    pr.contributors.forEach(cId => {
      if (cId === pr.owner) return;
      if (cpyPeople.find(p => p.id === cId && p.status !== 'external')) {
        edges.push({ source: `pr-${pr.id}`, target: `p-${cId}`, weight: 0.45 });
      }
    });
  });

  return { entities, edges };
}

// ──────────────── Types ────────────────
type NodeType = 'person' | 'topic' | 'project' | 'client';

interface Node {
  id: string;
  type: NodeType;
  name: string;
  x: number;
  y: number;
  connections: string[];
  connectionCount: number;
  details?: {
    role?: string;
    department?: string;
    description?: string;
    status?: string;
  };
}

interface GraphEdge {
  source: string;
  target: string;
  weight: number;
}

// ──────────────── Layout ────────────────

/**
 * Force-directed layout (Fruchterman-Reingold).
 * All node types are mixed together; connected nodes are pulled close,
 * unconnected nodes are pushed apart → even, organic distribution.
 */
function layoutNodes(
  entities: EntityItem[],
  edges: EdgeItem[],
): { nodes: Node[]; graphEdges: GraphEdge[] } {
  // Build connection map + weighted degree
  const connMap = new Map<string, Set<string>>();
  const connCount = new Map<string, number>();
  for (const e of entities) { connMap.set(e.id, new Set()); connCount.set(e.id, 0); }
  for (const edge of edges) {
    connMap.get(edge.source)?.add(edge.target);
    connMap.get(edge.target)?.add(edge.source);
    connCount.set(edge.source, (connCount.get(edge.source) || 0) + edge.weight);
    connCount.set(edge.target, (connCount.get(edge.target) || 0) + edge.weight);
  }

  const CX = 100, CY = 100;          // centre of 200×200 viewbox
  const PADDING = 14;                  // keep nodes off the edges
  const MIN = PADDING, MAX = 200 - PADDING;

  // ── Seed positions: evenly spaced on a circle so no two nodes start on top of each other
  type Pos = { x: number; y: number; fx: number; fy: number };
  const pos = new Map<string, Pos>();
  entities.forEach((e, i) => {
    const angle = (i / entities.length) * 2 * Math.PI;
    const r = 55 + (i % 3) * 10;     // stagger radius slightly to avoid exact overlap
    pos.set(e.id, {
      x: CX + Math.cos(angle) * r,
      y: CY + Math.sin(angle) * r,
      fx: 0, fy: 0,
    });
  });

  // ── Deduplicate edges (our dataset has repeated source→target pairs)
  const edgeSet = new Map<string, number>();
  for (const edge of edges) {
    const key = [edge.source, edge.target].sort().join('|');
    edgeSet.set(key, Math.max(edgeSet.get(key) ?? 0, edge.weight));
  }
  const uniqueEdges = [...edgeSet.entries()].map(([key, w]) => {
    const [s, t] = key.split('|');
    return { source: s, target: t, weight: w };
  });

  // ── Force parameters
  const REPULSION   = 900;    // pushes every pair apart
  const IDEAL_DIST  = 22;     // desired edge length
  const K_SPRING    = 0.06;   // edge attraction
  const GRAVITY     = 0.012;  // pull toward centre (prevents drift)
  const DAMPING     = 0.82;
  const ITERATIONS  = 280;

  for (let iter = 0; iter < ITERATIONS; iter++) {
    const cool = 1 - iter / ITERATIONS;  // cooling schedule

    // Reset forces
    for (const p of pos.values()) { p.fx = 0; p.fy = 0; }

    const ids = [...pos.keys()];

    // Repulsion - all pairs
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = pos.get(ids[i])!;
        const b = pos.get(ids[j])!;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist2 = dx * dx + dy * dy + 1;
        const dist  = Math.sqrt(dist2);
        const f = REPULSION / dist2;
        const fx = (dx / dist) * f;
        const fy = (dy / dist) * f;
        a.fx -= fx; a.fy -= fy;
        b.fx += fx; b.fy += fy;
      }
    }

    // Attraction - connected nodes (spring)
    for (const { source, target } of uniqueEdges) {
      const a = pos.get(source);
      const b = pos.get(target);
      if (!a || !b) continue;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) + 0.01;
      const f = K_SPRING * (dist - IDEAL_DIST);
      const fx = (dx / dist) * f;
      const fy = (dy / dist) * f;
      a.fx += fx; a.fy += fy;
      b.fx -= fx; b.fy -= fy;
    }

    // Gravity toward centre
    for (const [, p] of pos) {
      p.fx += (CX - p.x) * GRAVITY;
      p.fy += (CY - p.y) * GRAVITY;
    }

    // Integrate
    for (const [, p] of pos) {
      p.x = Math.max(MIN, Math.min(MAX, p.x + p.fx * DAMPING * cool));
      p.y = Math.max(MIN, Math.min(MAX, p.y + p.fy * DAMPING * cool));
    }
  }

  // ── Build final Node objects
  const nodes: Node[] = entities.map(entity => {
    const p = pos.get(entity.id)!;
    return {
      id: entity.id,
      type: entity.type,
      name: entity.name,
      x: p.x,
      y: p.y,
      connections: Array.from(connMap.get(entity.id) ?? []),
      connectionCount: connCount.get(entity.id) ?? 0,
      details: { role: entity.role, department: entity.department, status: entity.status },
    };
  });

  return { nodes, graphEdges: uniqueEdges };
}

// ──────────────── Component ────────────────
export default function KnowledgeMap() {
  const navigate = useNavigate();
  const svgRef = useRef<SVGSVGElement>(null);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [graphEdges, setGraphEdges] = useState<GraphEdge[]>([]);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [pulseNodes, setPulseNodes] = useState<Set<string>>(new Set());
  const [activeFilters, setActiveFilters] = useState<Set<NodeType>>(
    new Set(['person', 'topic', 'project', 'client'])
  );
  const [loading, setLoading] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });

  // Build entities/edges from companyData (synchronous, no API)
  useEffect(() => {
    const data = buildFromCompanyData();
    const { nodes: layoutNodes_, graphEdges: edges_ } = layoutNodes(data.entities, data.edges);
    setNodes(layoutNodes_);
    setGraphEdges(edges_);
    setLoading(false);
  }, []);

  // Non-passive wheel listener so we can preventDefault and zoom instead of scroll.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault();
      const delta = e.deltaY > 0 ? 0.9 : 1.1;
      setZoom(prev => Math.max(0.3, Math.min(5, prev * delta)));
    };
    svg.addEventListener('wheel', handler, { passive: false });
    return () => svg.removeEventListener('wheel', handler);
  }, [loading]);

  // Node lookup by ID
  const nodeMap = useMemo(() => {
    const m = new Map<string, Node>();
    for (const n of nodes) m.set(n.id, n);
    return m;
  }, [nodes]);

  // Visible nodes based on active filters
  const visibleNodes = useMemo(
    () => nodes.filter(n => activeFilters.has(n.type)),
    [nodes, activeFilters],
  );
  const visibleIds = useMemo(
    () => new Set(visibleNodes.map(n => n.id)),
    [visibleNodes],
  );

  // Visible edges
  const visibleEdges = useMemo(
    () => graphEdges.filter(e => visibleIds.has(e.source) && visibleIds.has(e.target)),
    [graphEdges, visibleIds],
  );

  // Pulse animation
  useEffect(() => {
    const interval = setInterval(() => {
      const ids = visibleNodes.map(n => n.id);
      const rand = new Set<string>();
      for (let i = 0; i < Math.min(4, ids.length); i++) rand.add(ids[Math.floor(Math.random() * ids.length)]);
      setPulseNodes(rand);
    }, 2500);
    return () => clearInterval(interval);
  }, [visibleNodes]);

  // Brand-aligned palette
  const getNodeColor = (type: NodeType) => {
    switch (type) {
      case 'person':  return '#0a3fb5'; // brand blue
      case 'topic':   return '#7c3aed'; // violet
      case 'project': return '#f59e0b'; // amber
      case 'client':  return '#10b981'; // emerald - used for Services
      default: return '#64748b';
    }
  };

  // Slightly darker variant for gradient end + ring
  const getNodeColorDark = (type: NodeType) => {
    switch (type) {
      case 'person':  return '#062d80';
      case 'topic':   return '#5b21b6';
      case 'project': return '#b45309';
      case 'client':  return '#047857';
      default: return '#475569';
    }
  };

  const getNodeIcon = (type: NodeType) => {
    switch (type) {
      case 'person':  return Users;
      case 'topic':   return Tag;
      case 'project': return Server;
      case 'client':  return Building2; // used for Services
      default: return Tag;
    }
  };

  const getNodeLabel = (node: Node) => {
    if (node.type === 'person') return node.name.split(' ')[0];
    if (node.name.length > 14) return node.name.substring(0, 11) + '...';
    return node.name;
  };

  const getRelationship = (from: Node, to: Node) => {
    if (from.type === 'person' && to.type === 'person') return 'Collaborates with';
    if (from.type === 'person' && to.type === 'topic') return 'Knows about';
    if (from.type === 'topic' && to.type === 'person') return 'Known by';
    if (from.type === 'person' && to.type === 'project') return 'Works on';
    if (from.type === 'project' && to.type === 'person') return 'Involves';
    if (from.type === 'topic' && to.type === 'topic') return 'Related to';
    if (from.type === 'topic' && to.type === 'project') return 'Used in';
    if (from.type === 'project' && to.type === 'topic') return 'Uses';
    return 'Connected to';
  };

  const toggleFilter = (type: NodeType) => {
    setActiveFilters(prev => {
      const next = new Set(prev);
      if (next.has(type)) { if (next.size > 1) next.delete(type); }
      else next.add(type);
      return next;
    });
  };

  return (
    <AppShell
      title="Knowledge Map"
      subtitle={`${visibleNodes.length} entities · ${visibleEdges.length} connections`}
      bleed
    >
      <div className="flex-1 relative flex flex-col min-h-0">
        {/* Filter chips - overlay on top-left of canvas */}
        <div className="absolute top-4 left-4 z-20 flex items-center gap-1.5 bg-white/95 backdrop-blur-md border border-slate-200/70 rounded-full p-1.5 shadow-[0_12px_32px_-12px_rgba(15,23,42,0.12)]">
          {([
            { type: 'person' as NodeType,  label: 'People',   color: 'bg-blue-700' },
            { type: 'topic' as NodeType,   label: 'Topics',   color: 'bg-violet-500' },
            { type: 'project' as NodeType, label: 'Projects', color: 'bg-amber-500' },
            { type: 'client' as NodeType,  label: 'Services', color: 'bg-emerald-500' },
          ]).map(item => (
            <button
              key={item.type}
              onClick={() => toggleFilter(item.type)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[11px] font-medium transition-all ${
                activeFilters.has(item.type)
                  ? 'bg-white text-slate-800 shadow-sm ring-1 ring-slate-200'
                  : 'text-slate-400 hover:text-slate-600'
              }`}
            >
              <div className={`w-2 h-2 rounded-full ${item.color} ${!activeFilters.has(item.type) ? 'opacity-30' : ''}`} />
              {item.label}
            </button>
          ))}
        </div>

        {/* Graph Canvas */}
        <div className="flex-1 min-h-0 bg-gradient-to-br from-slate-50 to-blue-50/30">
          {loading ? (
            <div className="h-full flex items-center justify-center">
              <p className="text-slate-400">Loading knowledge map...</p>
            </div>
          ) : (
            <>
              <svg
                ref={svgRef}
                className="w-full h-full cursor-grab active:cursor-grabbing block"
                viewBox="0 0 200 200"
                preserveAspectRatio="xMidYMid meet"
                onMouseDown={(e) => {
                  setIsDragging(true);
                  setDragStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
                }}
                onMouseMove={(e) => {
                  if (!isDragging) return;
                  setPan({ x: e.clientX - dragStart.x, y: e.clientY - dragStart.y });
                }}
                onMouseUp={() => setIsDragging(false)}
                onMouseLeave={() => setIsDragging(false)}
              >
                <defs>
                  {(['person', 'topic', 'project', 'client'] as NodeType[]).map(t => (
                    <radialGradient key={t} id={`km-grad-${t}`} cx="0.35" cy="0.35" r="0.75">
                      <stop offset="0%"  stopColor={getNodeColor(t)} stopOpacity="1" />
                      <stop offset="100%" stopColor={getNodeColorDark(t)} stopOpacity="1" />
                    </radialGradient>
                  ))}
                  <filter id="km-soft-glow" x="-50%" y="-50%" width="200%" height="200%">
                    <feGaussianBlur stdDeviation="0.8" />
                  </filter>
                </defs>
                <g transform={`translate(${pan.x / 5}, ${pan.y / 5}) scale(${zoom})`}>
                {/* Edges */}
                {visibleEdges.map(edge => {
                  const sourceNode = nodeMap.get(edge.source);
                  const targetNode = nodeMap.get(edge.target);
                  if (!sourceNode || !targetNode) return null;

                  const isHighlighted =
                    hoveredNode === edge.source || hoveredNode === edge.target ||
                    (selectedNode && (selectedNode.id === edge.source || selectedNode.id === edge.target));

                  return (
                    <line
                      key={`${edge.source}-${edge.target}`}
                      x1={sourceNode.x} y1={sourceNode.y}
                      x2={targetNode.x} y2={targetNode.y}
                      stroke={isHighlighted ? '#3b82f6' : '#94a3b8'}
                      strokeWidth={isHighlighted ? 0.25 : 0.12}
                      opacity={isHighlighted ? 0.8 : 0.18}
                      className="transition-all duration-300"
                    />
                  );
                })}

                {/* Nodes */}
                {visibleNodes.map(node => {
                  const isHovered = hoveredNode === node.id;
                  const isPulsing = pulseNodes.has(node.id);
                  const isSelected = selectedNode?.id === node.id;
                  const isConnectedToHover = hoveredNode !== null && node.connections.includes(hoveredNode);
                  const isConnectedToSelected = selectedNode !== null && node.connections.includes(selectedNode.id);
                  const highlight = isHovered || isSelected || isConnectedToHover || isConnectedToSelected;

                  // Scale node size based on connections
                  const baseR = Math.min(1.5 + node.connectionCount * 0.06, 3.5);
                  const r = highlight ? baseR + 1 : baseR;

                  return (
                    <g
                      key={node.id}
                      transform={`translate(${node.x}, ${node.y})`}
                      className="cursor-pointer"
                      onMouseEnter={() => setHoveredNode(node.id)}
                      onMouseLeave={() => setHoveredNode(null)}
                      onClick={() => setSelectedNode(node)}
                    >
                      {isPulsing && (
                        <circle r={r + 2} fill={getNodeColor(node.type)} opacity={0.18} className="animate-ping" />
                      )}
                      {/* Soft drop shadow */}
                      <circle r={r + 0.4} fill={getNodeColorDark(node.type)} opacity={0.18} filter="url(#km-soft-glow)" />
                      {/* Outer ring */}
                      <circle r={r + 0.5} fill="white" />
                      {/* Body - gradient fill */}
                      <circle
                        r={r}
                        fill={`url(#km-grad-${node.type})`}
                        stroke={isSelected ? getNodeColorDark(node.type) : 'rgba(255,255,255,0.9)'}
                        strokeWidth={isSelected ? 0.4 : 0.2}
                        opacity={highlight ? 1 : 0.92}
                        className="transition-all duration-300"
                      />
                      <text
                        y={-(r + 1.5)}
                        textAnchor="middle"
                        className="pointer-events-none select-none"
                        fill={highlight ? '#1e293b' : '#64748b'}
                        fontWeight={highlight ? 600 : 400}
                        style={{ fontSize: highlight ? '2.2px' : '1.7px' }}
                      >
                        {getNodeLabel(node)}
                      </text>
                    </g>
                  );
                })}
                </g>
              </svg>

              {/* Zoom Controls */}
              <div className="absolute bottom-8 right-8 flex flex-col gap-2">
                <button
                  onClick={() => setZoom(prev => Math.min(5, prev * 1.3))}
                  className="w-10 h-10 bg-white/90 backdrop-blur-sm rounded-lg shadow-lg border border-slate-200 flex items-center justify-center hover:bg-white transition-colors"
                  title="Zoom In"
                >
                  <span className="text-xl font-semibold text-slate-700">+</span>
                </button>
                <button
                  onClick={() => setZoom(prev => Math.max(0.3, prev * 0.77))}
                  className="w-10 h-10 bg-white/90 backdrop-blur-sm rounded-lg shadow-lg border border-slate-200 flex items-center justify-center hover:bg-white transition-colors"
                  title="Zoom Out"
                >
                  <span className="text-xl font-semibold text-slate-700">−</span>
                </button>
                <button
                  onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}
                  className="w-10 h-10 bg-white/90 backdrop-blur-sm rounded-lg shadow-lg border border-slate-200 flex items-center justify-center hover:bg-white transition-colors text-xs font-medium text-slate-600"
                  title="Reset View"
                >
                  ⟲
                </button>
              </div>

              {/* Hint */}
              <div className="absolute bottom-8 left-1/2 -translate-x-1/2 bg-white/90 backdrop-blur-sm rounded-full px-6 py-3 shadow-lg border border-slate-200">
                <p className="text-sm text-slate-600">Scroll to zoom &middot; Drag to pan &middot; Click nodes to explore</p>
              </div>
            </>
          )}
        </div>

        {/* ── Node Details Panel ── */}
        {selectedNode && (
          <div className="absolute top-0 right-0 bottom-0 w-[360px] overflow-y-auto bg-white shadow-[-12px_0_32px_-12px_rgba(15,23,42,0.10)] border-l border-slate-200 p-6 animate-in slide-in-from-right duration-300 z-20">
            <div className="flex items-start justify-between mb-4">
              <div className="flex items-center gap-3">
                <div className="w-12 h-12 rounded-xl flex items-center justify-center" style={{ backgroundColor: `${getNodeColor(selectedNode.type)}20` }}>
                  {(() => { const Icon = getNodeIcon(selectedNode.type); return <Icon className="w-6 h-6" style={{ color: getNodeColor(selectedNode.type) }} />; })()}
                </div>
                <div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="font-semibold text-slate-900">{selectedNode.name}</h3>
                    {selectedNode.type === 'person' && (() => {
                      // Node id is prefixed with `p-`; strip it to look up the Person
                      const pid = selectedNode.id.startsWith('p-') ? selectedNode.id.slice(2) : selectedNode.id;
                      const cpyPerson = cpyPeople.find(x => x.id === pid);
                      return cpyPerson ? (
                        <AccessBadge level={cpyPerson.accessLevel} status={cpyPerson.status} size="xs" />
                      ) : null;
                    })()}
                  </div>
                  <p className="text-sm text-slate-500 capitalize">{selectedNode.type}</p>
                </div>
              </div>
              <button onClick={() => setSelectedNode(null)} className="p-2 hover:bg-slate-100 rounded-lg transition-colors">
                <X className="w-5 h-5 text-slate-400" />
              </button>
            </div>

            <div className="space-y-4">
              {selectedNode.details?.role && (
                <div>
                  <p className="text-xs text-slate-400 uppercase tracking-wider mb-1">Role</p>
                  <p className="text-sm text-slate-700">{selectedNode.details.role}</p>
                </div>
              )}
              {selectedNode.details?.department && (
                <div>
                  <p className="text-xs text-slate-400 uppercase tracking-wider mb-1">Department</p>
                  <span className="inline-block px-2.5 py-0.5 bg-blue-50 text-blue-700 rounded-md text-xs font-medium">{selectedNode.details.department}</span>
                </div>
              )}
              {selectedNode.details?.status && (
                <div>
                  <p className="text-xs text-slate-400 uppercase tracking-wider mb-1">Status</p>
                  <span className={`inline-block px-2.5 py-0.5 rounded-md text-xs font-medium ${selectedNode.details.status === 'active' ? 'bg-green-50 text-green-700' : 'bg-slate-100 text-slate-700'}`}>
                    {selectedNode.details.status}
                  </span>
                </div>
              )}

              {/* Connected nodes */}
              {selectedNode.connections.length > 0 && (
                <div>
                  <p className="text-xs text-slate-400 uppercase tracking-wider mb-2">
                    Connected to ({selectedNode.connections.length})
                  </p>
                  <div className="space-y-1 max-h-64 overflow-y-auto">
                    {selectedNode.connections.map(connId => {
                      const conn = nodeMap.get(connId);
                      if (!conn) return null;
                      return (
                        <div
                          key={connId}
                          className="flex items-center gap-2 p-2 rounded-lg hover:bg-slate-50 cursor-pointer transition-colors"
                          onClick={() => setSelectedNode(conn)}
                        >
                          <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: getNodeColor(conn.type) }} />
                          <div className="flex-1 min-w-0">
                            <span className="text-sm text-slate-700 truncate block">{conn.name}</span>
                            <span className="text-[10px] text-slate-400">{getRelationship(selectedNode, conn)}</span>
                          </div>
                          <ChevronRight className="w-4 h-4 text-slate-400 flex-shrink-0" />
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              <button
                onClick={() => navigate('/ask')}
                className="w-full mt-2 flex items-center justify-center gap-2 px-4 py-3 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-colors"
              >
                <MessageSquare className="w-4 h-4" />
                Ask Lore about this
              </button>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}

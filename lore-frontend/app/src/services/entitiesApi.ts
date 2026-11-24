import { DEMO_ENTITIES, DEMO_EDGES, DEMO_STATS } from '@/data/demoData';

export interface EntityItem {
  id: string;
  name: string;
  type: 'person' | 'project' | 'topic' | 'client';
  aliases: string[];
  role?: string;
  department?: string;
  status?: string;
}

export interface EdgeItem {
  source: string;
  target: string;
  weight: number;
}

export interface Stats {
  knowledge_units: number;
  people: number;
  projects: number;
  topics: number;
  clients: number;
}

export interface EntitiesResponse {
  entities: EntityItem[];
  edges: EdgeItem[];
  stats: Stats;
}

/** Returns the full demo dataset - no network call required. */
export async function fetchEntities(): Promise<EntitiesResponse> {
  // Simulate a brief network-like delay so the UI loading states are visible
  await new Promise(r => setTimeout(r, 400));
  return {
    entities: DEMO_ENTITIES,
    edges: DEMO_EDGES,
    stats: DEMO_STATS,
  };
}

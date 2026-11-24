/**
 * EZO Books organizational data extracted from the Lore knowledge pipeline.
 * Sources: person_registry, comm_graph, knowledge_graph_v2, topic_taxonomy.
 */

import type { EntityItem, EdgeItem, Stats } from '@/services/entitiesApi';

// ─── Stats ────────────────────────────────────────────────────────────────────
export const DEMO_STATS: Stats = {
  knowledge_units: 33014,
  people: 31,
  projects: 3,
  topics: 9,
  clients: 0,
};

// ─── Entities ─────────────────────────────────────────────────────────────────
export const DEMO_ENTITIES: EntityItem[] = [
  // ── People (internal, non-functional, sorted by activity) ──
  { id: 'emp_gk',          name: 'Gauravkumar Kate',    type: 'person', aliases: ['GK'],            role: 'CEO / Founder',              department: 'Leadership',  status: 'active' },
  { id: 'emp_rishikesh',   name: 'Rishikesh Sakarkar',  type: 'person', aliases: ['Rishikesh'],     role: 'COO / Operations Head',      department: 'Leadership',  status: 'active' },
  { id: 'emp_shri',        name: 'Shrishail Thote',     type: 'person', aliases: ['Shri'],          role: 'Operations Manager',         department: 'Operations',  status: 'active' },
  { id: 'emp_mukesh',      name: 'Mukesh Sethia',       type: 'person', aliases: ['Mukesh'],        role: 'HR Director',                department: 'HR',          status: 'active' },
  { id: 'emp_makarand',    name: 'Makarand Kate',       type: 'person', aliases: ['Makarand'],      role: 'Co-founder / Director',      department: 'Leadership',  status: 'active' },
  { id: 'emp_shubhamp',    name: 'Shubham Pardeshi',    type: 'person', aliases: ['Shubham P'],     role: 'Operations Admin',           department: 'Operations',  status: 'active' },
  { id: 'emp_joackin',     name: 'Joackin Aruputharaj', type: 'person', aliases: ['Joackin'],       role: 'Operations Team Lead',       department: 'Operations',  status: 'active' },
  { id: 'emp_shubhama',    name: 'Shubham Augad',       type: 'person', aliases: ['Shubham A'],     role: 'Sales Team Lead',            department: 'Sales',       status: 'active' },
  { id: 'emp_archita',     name: 'Archita Bajaj',       type: 'person', aliases: ['Archita'],       role: 'HR Manager / Recruiter',     department: 'HR',          status: 'active' },
  { id: 'emp_divya',       name: 'Divya Shetty',        type: 'person', aliases: ['Divya'],         role: 'Customer Service / Finance', department: 'Finance',     status: 'active' },
  { id: 'emp_arvind',      name: 'Arvind',              type: 'person', aliases: [],                role: 'Customer Engagement Lead',   department: 'Operations',  status: 'active' },
  { id: 'emp_hamza',       name: 'Hamza Taibani',       type: 'person', aliases: ['Hamza'],         role: 'Sales Performance Manager',  department: 'Sales',       status: 'active' },
  { id: 'emp_madhav',      name: 'Madhav J',            type: 'person', aliases: ['Madhav'],        role: 'Procurement / Finance',      department: 'Finance',     status: 'active' },
  { id: 'emp_harshit',     name: 'Harshit Pandey',      type: 'person', aliases: ['Harshit'],       role: 'Product / Marketing',        department: 'Marketing',   status: 'active' },
  { id: 'emp_ashish',      name: 'Ashish Burade',       type: 'person', aliases: ['Ashish'],        role: 'Marketing / Sales',          department: 'Marketing',   status: 'active' },
  { id: 'emp_mahesh',      name: 'Mahesh Narale',       type: 'person', aliases: ['Mahesh'],        role: 'Content / Influencer Mktg',  department: 'Marketing',   status: 'active' },
  { id: 'emp_amol',        name: 'Amol Karne',          type: 'person', aliases: ['Amol'],          role: 'Tech / Product Lead',        department: 'Technology',  status: 'active' },
  { id: 'emp_simeen',      name: 'Simeen A',            type: 'person', aliases: ['Simeen'],        role: 'HR Recruiter',               department: 'HR',          status: 'active' },
  { id: 'emp_rahul',       name: 'Rahul Agrawal',       type: 'person', aliases: ['Rahul'],         role: 'Operations',                 department: 'Operations',  status: 'active' },
  { id: 'emp_shridhar',    name: 'Shridhar Patil',      type: 'person', aliases: ['Shridhar'],      role: 'Developer Recruitment',      department: 'Technology',  status: 'active' },
  { id: 'emp_shailesh',    name: 'Shailesh Moniya',     type: 'person', aliases: ['Shailesh'],      role: 'Customer Engagement',        department: 'Operations',  status: 'active' },
  { id: 'emp_kalpesh',     name: 'Kalpesh Lambade',     type: 'person', aliases: ['Kalpesh'],       role: 'IT / AI Automation',         department: 'Technology',  status: 'active' },
  { id: 'emp_sumit',       name: 'Sumit Kadam',         type: 'person', aliases: ['Sumit'],         role: 'Operations',                 department: 'Operations',  status: 'active' },
  { id: 'emp_bhavesh',     name: 'Bhavesh Shah',        type: 'person', aliases: ['Bhavesh'],       role: 'Operations',                 department: 'Operations',  status: 'active' },
  { id: 'emp_pratik',      name: 'Pratik Jadhav',       type: 'person', aliases: ['Pratik'],        role: 'Developer',                  department: 'Technology',  status: 'active' },
  { id: 'emp_akash',       name: 'Akash Shah',          type: 'person', aliases: ['Akash'],         role: 'Legal / Compliance',         department: 'Legal',       status: 'active' },
  { id: 'emp_jayesh',      name: 'Jayesh Saindane',     type: 'person', aliases: ['Jayesh'],        role: 'Operations',                 department: 'Operations',  status: 'active' },
  { id: 'emp_aman',        name: 'Aman Jha',            type: 'person', aliases: ['Aman'],          role: 'Banking Operations',         department: 'Finance',     status: 'active' },
  { id: 'emp_roshan',      name: 'Roshan Pawar',        type: 'person', aliases: ['Roshan'],        role: 'Operations',                 department: 'Operations',  status: 'active' },
  { id: 'emp_shivnath',    name: 'Shivnath Jaiswal',    type: 'person', aliases: ['Shivnath'],      role: 'Operations',                 department: 'Operations',  status: 'active' },
  { id: 'emp_abhey',       name: 'Abhey Kaul',          type: 'person', aliases: ['Abhey'],         role: 'Banking',                    department: 'Finance',     status: 'active' },

  // ── Topics (from taxonomy parent categories + key sub-categories) ──
  { id: 'topic_operations',    name: 'Operations',          type: 'topic', aliases: ['ops', 'audits'],       role: 'Business',  department: 'Operations',  status: 'active' },
  { id: 'topic_hr',            name: 'HR & People',         type: 'topic', aliases: ['hiring', 'onboarding'],role: 'Business',  department: 'HR',          status: 'active' },
  { id: 'topic_finance',       name: 'Finance & Payments',  type: 'topic', aliases: ['payments', 'billing'], role: 'Business',  department: 'Finance',     status: 'active' },
  { id: 'topic_marketing',     name: 'Marketing',           type: 'topic', aliases: ['branding', 'content'], role: 'Business',  department: 'Marketing',   status: 'active' },
  { id: 'topic_sales',         name: 'Sales',               type: 'topic', aliases: ['revenue', 'targets'],  role: 'Business',  department: 'Sales',       status: 'active' },
  { id: 'topic_client',        name: 'Client Relations',    type: 'topic', aliases: ['customer', 'support'], role: 'Business',  department: 'Operations',  status: 'active' },
  { id: 'topic_tech',          name: 'Technology',          type: 'topic', aliases: ['AI', 'automation'],    role: 'Technical', department: 'Technology',  status: 'active' },
  { id: 'topic_legal',         name: 'Legal & Compliance',  type: 'topic', aliases: ['legal', 'statutory'],  role: 'Business',  department: 'Legal',       status: 'active' },
  { id: 'topic_banking',       name: 'Banking (EZO Banks)', type: 'topic', aliases: ['banking', 'fintech'],  role: 'Business',  department: 'Finance',     status: 'active' },

  // ── Projects (EZO group companies) ──
  { id: 'project_ezobooks',    name: 'EZO Books',      type: 'project', aliases: ['ezobooks'],    role: 'Accounting SaaS',   department: 'Product', status: 'active' },
  { id: 'project_ezobanks',    name: 'EZO Banks',      type: 'project', aliases: ['ezobanks'],    role: 'Banking Platform',  department: 'Product', status: 'active' },
  { id: 'project_legaldocs',   name: 'LegalDocs',      type: 'project', aliases: ['legaldocs'],   role: 'Legal Document SaaS', department: 'Product', status: 'active' },
];

// ─── Edges ─────────────────────────────────────────────────────────────────────
export const DEMO_EDGES: EdgeItem[] = [
  // person → topic (expertise based on top categories from knowledge graph)
  { source: 'emp_gk',          target: 'topic_hr',         weight: 0.90 },
  { source: 'emp_gk',          target: 'topic_finance',    weight: 0.85 },
  { source: 'emp_gk',          target: 'topic_operations', weight: 0.80 },
  { source: 'emp_rishikesh',   target: 'topic_operations', weight: 0.95 },
  { source: 'emp_rishikesh',   target: 'topic_client',     weight: 0.88 },
  { source: 'emp_rishikesh',   target: 'topic_sales',      weight: 0.85 },
  { source: 'emp_shri',        target: 'topic_operations', weight: 0.92 },
  { source: 'emp_shri',        target: 'topic_hr',         weight: 0.85 },
  { source: 'emp_mukesh',      target: 'topic_hr',         weight: 0.95 },
  { source: 'emp_mukesh',      target: 'topic_finance',    weight: 0.70 },
  { source: 'emp_archita',     target: 'topic_hr',         weight: 0.92 },
  { source: 'emp_joackin',     target: 'topic_operations', weight: 0.90 },
  { source: 'emp_joackin',     target: 'topic_sales',      weight: 0.75 },
  { source: 'emp_hamza',       target: 'topic_sales',      weight: 0.90 },
  { source: 'emp_hamza',       target: 'topic_operations', weight: 0.72 },
  { source: 'emp_shubhama',    target: 'topic_sales',      weight: 0.85 },
  { source: 'emp_shubhama',    target: 'topic_operations', weight: 0.70 },
  { source: 'emp_divya',       target: 'topic_finance',    weight: 0.88 },
  { source: 'emp_divya',       target: 'topic_client',     weight: 0.80 },
  { source: 'emp_madhav',      target: 'topic_finance',    weight: 0.90 },
  { source: 'emp_madhav',      target: 'topic_operations', weight: 0.70 },
  { source: 'emp_harshit',     target: 'topic_marketing',  weight: 0.82 },
  { source: 'emp_harshit',     target: 'topic_finance',    weight: 0.75 },
  { source: 'emp_ashish',      target: 'topic_marketing',  weight: 0.80 },
  { source: 'emp_ashish',      target: 'topic_client',     weight: 0.72 },
  { source: 'emp_mahesh',      target: 'topic_marketing',  weight: 0.85 },
  { source: 'emp_amol',        target: 'topic_tech',       weight: 0.88 },
  { source: 'emp_amol',        target: 'topic_marketing',  weight: 0.72 },
  { source: 'emp_simeen',      target: 'topic_hr',         weight: 0.85 },
  { source: 'emp_kalpesh',     target: 'topic_tech',       weight: 0.90 },
  { source: 'emp_pratik',      target: 'topic_tech',       weight: 0.85 },
  { source: 'emp_shridhar',    target: 'topic_tech',       weight: 0.78 },
  { source: 'emp_shridhar',    target: 'topic_hr',         weight: 0.70 },
  { source: 'emp_akash',       target: 'topic_legal',      weight: 0.85 },
  { source: 'emp_akash',       target: 'topic_banking',    weight: 0.80 },
  { source: 'emp_aman',        target: 'topic_banking',    weight: 0.85 },
  { source: 'emp_aman',        target: 'topic_finance',    weight: 0.75 },
  { source: 'emp_makarand',    target: 'topic_operations', weight: 0.75 },
  { source: 'emp_makarand',    target: 'topic_finance',    weight: 0.70 },
  { source: 'emp_arvind',      target: 'topic_client',     weight: 0.85 },
  { source: 'emp_arvind',      target: 'topic_operations', weight: 0.72 },
  { source: 'emp_shailesh',    target: 'topic_client',     weight: 0.82 },
  { source: 'emp_shailesh',    target: 'topic_finance',    weight: 0.70 },
  { source: 'emp_shubhamp',    target: 'topic_operations', weight: 0.78 },
  { source: 'emp_shubhamp',    target: 'topic_hr',         weight: 0.65 },

  // person → project (works on - based on email domains)
  { source: 'emp_gk',          target: 'project_ezobooks',  weight: 0.90 },
  { source: 'emp_gk',          target: 'project_ezobanks',  weight: 0.85 },
  { source: 'emp_rishikesh',   target: 'project_ezobooks',  weight: 0.90 },
  { source: 'emp_rishikesh',   target: 'project_ezobanks',  weight: 0.85 },
  { source: 'emp_rishikesh',   target: 'project_legaldocs', weight: 0.80 },
  { source: 'emp_makarand',    target: 'project_ezobanks',  weight: 0.90 },
  { source: 'emp_makarand',    target: 'project_ezobooks',  weight: 0.80 },
  { source: 'emp_makarand',    target: 'project_legaldocs', weight: 0.75 },
  { source: 'emp_mukesh',      target: 'project_ezobooks',  weight: 0.90 },
  { source: 'emp_mukesh',      target: 'project_ezobanks',  weight: 0.75 },
  { source: 'emp_shri',        target: 'project_ezobooks',  weight: 0.85 },
  { source: 'emp_shri',        target: 'project_legaldocs', weight: 0.70 },
  { source: 'emp_archita',     target: 'project_ezobooks',  weight: 0.85 },
  { source: 'emp_divya',       target: 'project_ezobooks',  weight: 0.80 },
  { source: 'emp_divya',       target: 'project_legaldocs', weight: 0.70 },
  { source: 'emp_amol',        target: 'project_ezobooks',  weight: 0.80 },
  { source: 'emp_amol',        target: 'project_ezobanks',  weight: 0.75 },
  { source: 'emp_kalpesh',     target: 'project_ezobooks',  weight: 0.80 },
  { source: 'emp_pratik',      target: 'project_ezobooks',  weight: 0.75 },
  { source: 'emp_pratik',      target: 'project_ezobanks',  weight: 0.70 },
  { source: 'emp_akash',       target: 'project_ezobanks',  weight: 0.85 },
  { source: 'emp_akash',       target: 'project_legaldocs', weight: 0.80 },
  { source: 'emp_aman',        target: 'project_ezobanks',  weight: 0.85 },

  // project → topic
  { source: 'project_ezobooks',  target: 'topic_finance',    weight: 0.90 },
  { source: 'project_ezobooks',  target: 'topic_operations', weight: 0.85 },
  { source: 'project_ezobooks',  target: 'topic_sales',      weight: 0.80 },
  { source: 'project_ezobooks',  target: 'topic_tech',       weight: 0.75 },
  { source: 'project_ezobanks',  target: 'topic_banking',    weight: 0.95 },
  { source: 'project_ezobanks',  target: 'topic_finance',    weight: 0.85 },
  { source: 'project_ezobanks',  target: 'topic_legal',      weight: 0.80 },
  { source: 'project_legaldocs', target: 'topic_legal',      weight: 0.92 },
  { source: 'project_legaldocs', target: 'topic_hr',         weight: 0.75 },

  // person → person (key reporting / collaboration from comm graph)
  { source: 'emp_rishikesh',   target: 'emp_gk',          weight: 0.90 },
  { source: 'emp_mukesh',      target: 'emp_gk',          weight: 0.85 },
  { source: 'emp_shri',        target: 'emp_gk',          weight: 0.80 },
  { source: 'emp_makarand',    target: 'emp_gk',          weight: 0.88 },
  { source: 'emp_archita',     target: 'emp_mukesh',      weight: 0.85 },
  { source: 'emp_simeen',      target: 'emp_mukesh',      weight: 0.78 },
  { source: 'emp_joackin',     target: 'emp_rishikesh',   weight: 0.82 },
  { source: 'emp_hamza',       target: 'emp_rishikesh',   weight: 0.78 },
  { source: 'emp_shubhama',    target: 'emp_rishikesh',   weight: 0.75 },
  { source: 'emp_divya',       target: 'emp_rishikesh',   weight: 0.72 },
  { source: 'emp_kalpesh',     target: 'emp_makarand',    weight: 0.75 },
  { source: 'emp_amol',        target: 'emp_rishikesh',   weight: 0.70 },
  { source: 'emp_shubhamp',    target: 'emp_shri',        weight: 0.72 },
  { source: 'emp_arvind',      target: 'emp_rishikesh',   weight: 0.70 },
];

// ─── Pulse Data (Lore Pulse dashboard) ──────────────────────────────────────

export interface ExpertiseEntry {
  empId: string;
  empName: string;
  topic: string;
  level: 'owner' | 'expert' | 'intermediate' | 'beginner';
  evidenceCount: number;
}

export interface DecisionEntry {
  id: string;
  title: string;
  date: string;
  makers: string[];
  makerNames: string[];
  outcome: string;
  impactAreas: string[];
}

export const DEMO_EXPERTISE: ExpertiseEntry[] = [
  // Rishikesh Sakarkar - 7,626 total extractions
  { empId: 'emp_rishikesh',  empName: 'Rishikesh Sakarkar',  topic: 'Operations',          level: 'owner',        evidenceCount: 571 },
  { empId: 'emp_rishikesh',  empName: 'Rishikesh Sakarkar',  topic: 'Client Relations',    level: 'owner',        evidenceCount: 492 },
  { empId: 'emp_rishikesh',  empName: 'Rishikesh Sakarkar',  topic: 'Sales',               level: 'expert',       evidenceCount: 448 },
  { empId: 'emp_rishikesh',  empName: 'Rishikesh Sakarkar',  topic: 'Finance & Payments',  level: 'expert',       evidenceCount: 443 },

  // Gauravkumar Kate - 7,577 total extractions
  { empId: 'emp_gk',         empName: 'Gauravkumar Kate',    topic: 'HR & People',         level: 'owner',        evidenceCount: 554 },
  { empId: 'emp_gk',         empName: 'Gauravkumar Kate',    topic: 'Finance & Payments',  level: 'expert',       evidenceCount: 373 },
  { empId: 'emp_gk',         empName: 'Gauravkumar Kate',    topic: 'Operations',          level: 'expert',       evidenceCount: 307 },

  // Mukesh Sethia - 6,825 total extractions
  { empId: 'emp_mukesh',     empName: 'Mukesh Sethia',       topic: 'HR & People',         level: 'owner',        evidenceCount: 913 },
  { empId: 'emp_mukesh',     empName: 'Mukesh Sethia',       topic: 'Finance & Payments',  level: 'expert',       evidenceCount: 525 },
  { empId: 'emp_mukesh',     empName: 'Mukesh Sethia',       topic: 'Operations',          level: 'intermediate', evidenceCount: 314 },

  // Shrishail Thote - 6,152 total extractions
  { empId: 'emp_shri',       empName: 'Shrishail Thote',     topic: 'Operations',          level: 'owner',        evidenceCount: 706 },
  { empId: 'emp_shri',       empName: 'Shrishail Thote',     topic: 'HR & People',         level: 'expert',       evidenceCount: 526 },
  { empId: 'emp_shri',       empName: 'Shrishail Thote',     topic: 'Finance & Payments',  level: 'intermediate', evidenceCount: 408 },

  // Archita Bajaj - 5,006 total extractions
  { empId: 'emp_archita',    empName: 'Archita Bajaj',       topic: 'HR & People',         level: 'owner',        evidenceCount: 767 },
  { empId: 'emp_archita',    empName: 'Archita Bajaj',       topic: 'Finance & Payments',  level: 'intermediate', evidenceCount: 379 },
  { empId: 'emp_archita',    empName: 'Archita Bajaj',       topic: 'Operations',          level: 'intermediate', evidenceCount: 223 },

  // Joackin Aruputharaj - 2,544 total extractions
  { empId: 'emp_joackin',    empName: 'Joackin Aruputharaj', topic: 'Operations',          level: 'owner',        evidenceCount: 442 },
  { empId: 'emp_joackin',    empName: 'Joackin Aruputharaj', topic: 'Sales',               level: 'expert',       evidenceCount: 216 },
  { empId: 'emp_joackin',    empName: 'Joackin Aruputharaj', topic: 'HR & People',         level: 'intermediate', evidenceCount: 161 },

  // Hamza Taibani - 1,784 total extractions
  { empId: 'emp_hamza',      empName: 'Hamza Taibani',       topic: 'Sales',               level: 'owner',        evidenceCount: 165 },
  { empId: 'emp_hamza',      empName: 'Hamza Taibani',       topic: 'Finance & Payments',  level: 'expert',       evidenceCount: 158 },
  { empId: 'emp_hamza',      empName: 'Hamza Taibani',       topic: 'Operations',          level: 'intermediate', evidenceCount: 135 },

  // Shubham Augad - 1,444 total extractions
  { empId: 'emp_shubhama',   empName: 'Shubham Augad',       topic: 'Sales',               level: 'expert',       evidenceCount: 112 },
  { empId: 'emp_shubhama',   empName: 'Shubham Augad',       topic: 'Operations',          level: 'intermediate', evidenceCount: 99 },
  { empId: 'emp_shubhama',   empName: 'Shubham Augad',       topic: 'HR & People',         level: 'intermediate', evidenceCount: 83 },

  // Divya Shetty - 1,173 total extractions
  { empId: 'emp_divya',      empName: 'Divya Shetty',        topic: 'Finance & Payments',  level: 'owner',        evidenceCount: 140 },
  { empId: 'emp_divya',      empName: 'Divya Shetty',        topic: 'Client Relations',    level: 'expert',       evidenceCount: 117 },
  { empId: 'emp_divya',      empName: 'Divya Shetty',        topic: 'Marketing',           level: 'intermediate', evidenceCount: 52 },

  // Harshit Pandey - 871 total extractions
  { empId: 'emp_harshit',    empName: 'Harshit Pandey',      topic: 'Finance & Payments',  level: 'expert',       evidenceCount: 92 },
  { empId: 'emp_harshit',    empName: 'Harshit Pandey',      topic: 'Marketing',           level: 'expert',       evidenceCount: 78 },
  { empId: 'emp_harshit',    empName: 'Harshit Pandey',      topic: 'HR & People',         level: 'intermediate', evidenceCount: 54 },

  // Simeen A - 853 total extractions
  { empId: 'emp_simeen',     empName: 'Simeen A',            topic: 'HR & People',         level: 'expert',       evidenceCount: 110 },
  { empId: 'emp_simeen',     empName: 'Simeen A',            topic: 'Operations',          level: 'intermediate', evidenceCount: 74 },

  // Mahesh Narale - 806 total extractions
  { empId: 'emp_mahesh',     empName: 'Mahesh Narale',       topic: 'Marketing',           level: 'expert',       evidenceCount: 69 },
  { empId: 'emp_mahesh',     empName: 'Mahesh Narale',       topic: 'HR & People',         level: 'intermediate', evidenceCount: 83 },

  // Makarand Kate - 625 total extractions
  { empId: 'emp_makarand',   empName: 'Makarand Kate',       topic: 'HR & People',         level: 'expert',       evidenceCount: 98 },
  { empId: 'emp_makarand',   empName: 'Makarand Kate',       topic: 'Operations',          level: 'intermediate', evidenceCount: 44 },
  { empId: 'emp_makarand',   empName: 'Makarand Kate',       topic: 'Finance & Payments',  level: 'intermediate', evidenceCount: 33 },

  // Madhav J - 563 total extractions
  { empId: 'emp_madhav',     empName: 'Madhav J',            topic: 'Finance & Payments',  level: 'owner',        evidenceCount: 135 },
  { empId: 'emp_madhav',     empName: 'Madhav J',            topic: 'Operations',          level: 'intermediate', evidenceCount: 55 },

  // Amol Karne - 561 total extractions
  { empId: 'emp_amol',       empName: 'Amol Karne',          topic: 'Technology',          level: 'expert',       evidenceCount: 37 },
  { empId: 'emp_amol',       empName: 'Amol Karne',          topic: 'Marketing',           level: 'intermediate', evidenceCount: 29 },
  { empId: 'emp_amol',       empName: 'Amol Karne',          topic: 'Client Relations',    level: 'intermediate', evidenceCount: 26 },

  // Arvind - 517 total extractions
  { empId: 'emp_arvind',     empName: 'Arvind',              topic: 'Client Relations',    level: 'expert',       evidenceCount: 50 },
  { empId: 'emp_arvind',     empName: 'Arvind',              topic: 'Operations',          level: 'intermediate', evidenceCount: 27 },

  // Kalpesh Lambade - 97 total extractions
  { empId: 'emp_kalpesh',    empName: 'Kalpesh Lambade',     topic: 'Technology',          level: 'owner',        evidenceCount: 15 },
  { empId: 'emp_kalpesh',    empName: 'Kalpesh Lambade',     topic: 'Operations',          level: 'intermediate', evidenceCount: 14 },

  // Pratik Jadhav - 130 total extractions
  { empId: 'emp_pratik',     empName: 'Pratik Jadhav',       topic: 'Technology',          level: 'expert',       evidenceCount: 12 },
  { empId: 'emp_pratik',     empName: 'Pratik Jadhav',       topic: 'HR & People',         level: 'beginner',     evidenceCount: 8 },

  // Shailesh Moniya - 373 total extractions
  { empId: 'emp_shailesh',   empName: 'Shailesh Moniya',     topic: 'Client Relations',    level: 'expert',       evidenceCount: 42 },
  { empId: 'emp_shailesh',   empName: 'Shailesh Moniya',     topic: 'Finance & Payments',  level: 'intermediate', evidenceCount: 24 },

  // Ashish Burade - 257 total extractions
  { empId: 'emp_ashish',     empName: 'Ashish Burade',       topic: 'Marketing',           level: 'intermediate', evidenceCount: 28 },
  { empId: 'emp_ashish',     empName: 'Ashish Burade',       topic: 'Client Relations',    level: 'intermediate', evidenceCount: 21 },

  // Shridhar Patil - 290 total extractions
  { empId: 'emp_shridhar',   empName: 'Shridhar Patil',      topic: 'HR & People',         level: 'intermediate', evidenceCount: 34 },
  { empId: 'emp_shridhar',   empName: 'Shridhar Patil',      topic: 'Technology',          level: 'intermediate', evidenceCount: 21 },

  // Shubham Pardeshi - 248 total extractions
  { empId: 'emp_shubhamp',   empName: 'Shubham Pardeshi',    topic: 'HR & People',         level: 'intermediate', evidenceCount: 56 },
  { empId: 'emp_shubhamp',   empName: 'Shubham Pardeshi',    topic: 'Operations',          level: 'intermediate', evidenceCount: 19 },
];

export const DEMO_DECISIONS: DecisionEntry[] = [
  {
    id: 'd1',
    title: 'Sales team must hit 50% target by mid-March or face termination',
    date: '2026-03-09',
    makers: ['emp_rishikesh'],
    makerNames: ['Sonal (Team Lead)'],
    outcome: 'Enforced',
    impactAreas: ['Sales', 'HR'],
  },
  {
    id: 'd2',
    title: 'Pilot program for Mera Monitor employee tracking tool',
    date: '2026-03-09',
    makers: ['emp_rishikesh'],
    makerNames: ['Rishikesh Sakarkar'],
    outcome: 'Approved',
    impactAreas: ['Operations', 'Technology'],
  },
  {
    id: 'd3',
    title: 'Terminate Shakir Qureshi for misconduct during suspension',
    date: '2026-03-09',
    makers: ['emp_joackin'],
    makerNames: ['Joackin Aruputharaj'],
    outcome: 'Executed',
    impactAreas: ['HR', 'Operations'],
  },
  {
    id: 'd4',
    title: 'Accept GMB SEO Optimization Services proposal for clients',
    date: '2026-02-26',
    makers: ['emp_harshit'],
    makerNames: ['Harshit Pandey'],
    outcome: 'Approved',
    impactAreas: ['Marketing', 'Sales'],
  },
  {
    id: 'd5',
    title: 'Deactivate 3 unused Acefone VoIP licenses to cut costs',
    date: '2026-02-26',
    makers: ['emp_rishikesh'],
    makerNames: ['Rishikesh Sakarkar'],
    outcome: 'Approved',
    impactAreas: ['Technology', 'Finance'],
  },
  {
    id: 'd6',
    title: 'Improve customer messaging for payment failure notifications',
    date: '2026-02-24',
    makers: ['emp_divya'],
    makerNames: ['Omkar Kadam'],
    outcome: 'Approved',
    impactAreas: ['Finance', 'Client Relations'],
  },
  {
    id: 'd7',
    title: 'Reschedule Truecaller SDK integration meeting (Holi conflict)',
    date: '2026-02-27',
    makers: ['emp_rishikesh'],
    makerNames: ['Rishikesh Sakarkar'],
    outcome: 'Rescheduled',
    impactAreas: ['Technology', 'Operations'],
  },
  {
    id: 'd8',
    title: 'Transfer Rs 14,400 booking amount toward 3-year software renewal',
    date: '2026-02-12',
    makers: ['emp_divya'],
    makerNames: ['Yash Pawar'],
    outcome: 'Approved',
    impactAreas: ['Finance', 'Sales'],
  },
  {
    id: 'd9',
    title: 'Map attrition rates to individual Team Leads for accountability',
    date: '2025-10-15',
    makers: ['emp_rishikesh'],
    makerNames: ['Rishikesh Sakarkar'],
    outcome: 'Approved',
    impactAreas: ['HR', 'Operations'],
  },
  {
    id: 'd10',
    title: 'Redistribute DRR targets after Shivani resignation',
    date: '2026-03-09',
    makers: ['emp_hamza'],
    makerNames: ['Nikhar Hansora'],
    outcome: 'Executed',
    impactAreas: ['Sales', 'Operations'],
  },
  {
    id: 'd11',
    title: 'Proceed with thermal printer order for billing machines',
    date: '2026-02-20',
    makers: ['emp_madhav'],
    makerNames: ['Sunil Mallah'],
    outcome: 'Approved',
    impactAreas: ['Operations', 'Finance'],
  },
  {
    id: 'd12',
    title: 'Final warning issued for PKT activity process non-compliance',
    date: '2026-03-09',
    makers: ['emp_hamza'],
    makerNames: ['Hamza Taibani'],
    outcome: 'Enforced',
    impactAreas: ['Operations', 'HR'],
  },
];

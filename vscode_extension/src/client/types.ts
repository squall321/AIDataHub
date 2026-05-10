/**
 * Wire types — must mirror api_server's response payloads exactly.
 * Source of truth: api_server/src/api/routes/{meta,system,convert,bundle,search,records,discover}.py
 */

export interface AgentOption {
  agent_type: string;
  name: string;
  description: string;
  data_types: string[];
}

export interface MetaOptions {
  version: string;
  teams: string[];
  groups: Record<string, string[]>;
  agents: AgentOption[];
  classifications: string[];
  statuses: string[];
  derivations: string[];
  languages: string[];
  data_types: string[];
  supported_extensions: string[];
  max_upload_mb: number;
  allow_custom: {
    team: boolean;
    group: boolean;
    domain: boolean;
  };
}

export interface SystemHealth {
  status: string;
  version: string;
  auth_required: boolean;
  build: string;
}

export interface VerifyKeyResponse {
  ok: boolean;
  key_name?: string;
  agent_scopes?: string[];
}

export interface IngestResponseRecord {
  id: string;
  data_type: string;
  title: string;
  summary: string;
  tags: string[];
  agents: string[];
  team: string;
  group: string;
  year: number;
  seq: number;
  source_file: string | null;
  content_hash: string | null;
}

export interface IngestResponse {
  record_id: string;
  status: 'inserted' | 'updated' | 'skipped';
  sections_written: number;
  assigned_seq?: number;
  attachments_persisted?: number;
  record: IngestResponseRecord;
}

export interface UploadFormValues {
  // Identification (required)
  team: string;
  group: string;
  year: number;
  seq: number;
  // Classification (Migration 0006 extended)
  classification: string;
  status: string;
  domain: string;
  language: string;
  // Discovery
  tags: string[];
  agents: string[];        // wire field name kept as `agents` for server compat
  subject_keywords: string[];
  // Override (optional)
  title_override: string;
  summary_override: string;
  // Quality (optional)
  derivation: string;
  quality_score: number | null;
  valid_from: string;
  valid_until: string;
  // Migration 0006 — extended provenance / lifecycle
  source_system: string;
  parent_record_id: string;
}

// ---------------------------------------------------------------------------
// Bundle upload — POST /api/ingest/bundle
// ---------------------------------------------------------------------------
export interface BundleUploadResponse {
  id: string;
  data_type: string;
  title: string;
  figures_copied: number;
  attachments_copied: number;
  warnings: {
    missing_resources: string[];
    extra_resources: string[];
  };
}

// ---------------------------------------------------------------------------
// Search — GET /api/search and POST /api/search/faceted
// ---------------------------------------------------------------------------
export interface SearchItem {
  record_id: string;
  title?: string;
  section_id?: string | null;
  section_title?: string;
  snippet?: string;
  score?: number;
  data_type?: string;
  tags?: string[];
  // RecordOut shape (tag-mode)
  id?: string;
  summary?: string;
  domain?: string;
  classification?: string;
  status?: string;
  agents?: string[];
  // misc
  [k: string]: unknown;
}

export interface SearchResponse {
  mode: 'tag' | 'fts' | 'semantic';
  q?: string;
  tags?: string[];
  items: SearchItem[];
  total: number;
  limit?: number;
  offset?: number;
}

export interface FacetedSearchFilters {
  q?: string;
  mode?: 'fts' | 'semantic';
  data_type?: string;        // CSV
  tags?: string;             // CSV (AND)
  agent?: string;
  domain?: string;
  classification?: string;
  status?: string;
  year_from?: number;
  year_to?: number;
  min_quality?: number;
  limit?: number;
  offset?: number;
}

export interface FacetedSearchResponse {
  q: string | null;
  mode: 'fts' | 'semantic' | null;
  filters: Record<string, unknown>;
  total: number;
  items: SearchItem[];
  facets: {
    data_type: Record<string, number>;
    tags: Record<string, number>;
    domain: Record<string, number>;
    agent: Record<string, number>;
    status: Record<string, number>;
    classification: Record<string, number>;
    year: Record<string, number>;
  };
  limit: number;
  offset: number;
}

// ---------------------------------------------------------------------------
// Record detail — GET /api/records/{id}
// ---------------------------------------------------------------------------
export interface RecordSection {
  section_id: string;
  level: number;
  title: string;
  content_text?: string;
  figure_refs?: string[];
  table_refs?: string[];
}

export interface FullRecord {
  id: string;
  data_type: string;
  team: string;
  group: string;
  year: number;
  seq: number;
  title: string;
  summary?: string;
  tags?: string[];
  agents?: string[];
  classification?: string;
  status?: string;
  domain?: string;
  language?: string;
  derivation?: string;
  quality_score?: number | null;
  valid_from?: string | null;
  valid_until?: string | null;
  agent_hints?: string;
  query_examples?: string[];
  access_pattern?: string;
  source_system?: string;
  parent_record_id?: string;
  schema_version?: string;
  content?: { sections?: RecordSection[]; [k: string]: unknown };
  content_hash?: string | null;
  source_file?: string | null;
  author?: string;
  department?: string;
  project?: string | null;
  version?: string;
  created_at?: string | null;
  updated_at?: string | null;
  [k: string]: unknown;
}

// ---------------------------------------------------------------------------
// Discover — GET /api/discover
// ---------------------------------------------------------------------------
export interface DiscoverAgentEntry {
  agent_type: string;
  name: string;
  description: string;
  record_count: number;
  common_tags: string[];
  data_types: string[];
  sample_query?: string;
}

export interface DiscoverResponse {
  version: string;
  title: string;
  description: string;
  total_records: number;
  by_data_type: Record<string, number>;
  by_team?: Record<string, number>;
  by_classification?: Record<string, number>;
  agents: DiscoverAgentEntry[];
  data_types_explained?: Record<string, string>;
  starting_points?: string[];
  // top tags surfaced for the UI; not all servers return this — we compute
  // client-side as fallback if missing.
  top_tags?: Array<{ tag: string; count: number }>;
  [k: string]: unknown;
}

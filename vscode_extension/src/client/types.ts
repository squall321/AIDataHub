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
// Agents CRUD — /api/agents
// Mirrors api_server's AgentOut / AgentIn / AgentPatch pydantic schemas.
// ---------------------------------------------------------------------------
// v0.13.0 — RAG recipe (Migration 0014).
// agent 을 단순 라우팅 태그가 아니라 검색·응답 레시피로 격상한다.
// 키 누락 시 서버는 빈 dict / null 로 받아 generic 폴백을 적용한다.
export interface AgentRetrievalConfigT {
  top_k?: number;
  score_threshold?: number;       // 0.0~1.0
  data_type_filter?: string[];    // subset of DOC|DATA|SIM|CAD|LOG|FORM|OTHER
  tag_boost?: Record<string, number>;
}

export interface AgentResponseConfigT {
  max_tokens?: number;
  citation_required?: boolean;
  refusal_message?: string;
  refuse_below_score?: number;    // 0.0~1.0; below → refuse with refusal_message
}

export interface AgentOutT {
  agent_type: string;             // PK
  name: string;
  description: string;
  common_tags: string[];
  data_types: string[];           // subset of DOC|DATA|SIM|CAD|LOG|FORM|OTHER
  // v0.7.0 — expected-schema fields (Migration 0008)
  required_doc_type: string | null;
  required_tags: string[];
  excluded_tags: string[];
  // v0.13.0 — RAG recipe fields (Migration 0014)
  retrieval_config: AgentRetrievalConfigT;
  system_prompt: string | null;
  response_config: AgentResponseConfigT;
  sample_queries: string[];
  // v0.13.0 — Sample-embedding routing index status (Migration 0016)
  samples_indexed_count?: number;
  samples_stale?: boolean;
  created_at: string | null;      // ISO datetime
}

export interface AgentInT {
  agent_type: string;             // required
  name: string;                   // required
  description?: string;
  common_tags?: string[];
  data_types?: string[];
  required_doc_type?: string | null;
  required_tags?: string[];
  excluded_tags?: string[];
  retrieval_config?: AgentRetrievalConfigT;
  system_prompt?: string | null;
  response_config?: AgentResponseConfigT;
  sample_queries?: string[];
}

export interface AgentPatchT {
  name?: string;
  description?: string;
  common_tags?: string[];
  data_types?: string[];
  required_doc_type?: string | null;
  required_tags?: string[];
  excluded_tags?: string[];
  retrieval_config?: AgentRetrievalConfigT | null;
  system_prompt?: string | null;
  response_config?: AgentResponseConfigT | null;
  sample_queries?: string[] | null;
}

// v0.13.0 — Agent history (Migration 0015). append-only audit log.
export interface AgentHistoryOutT {
  id: number;
  agent_type: string;
  operation: 'create' | 'update' | 'delete';
  snapshot: Record<string, unknown>;
  changed_by: string | null;
  changed_at: string | null;
}

// v0.13.0 — Agent preview (Migration 0014). 저장 전 dry-run.
export interface AgentPreviewInT {
  query: string;
  agent_type?: string | null;
  retrieval_config?: AgentRetrievalConfigT;
  system_prompt?: string | null;
  response_config?: AgentResponseConfigT;
}

export interface AgentPreviewHitT {
  record_id: string;
  section_id: string;
  section_title: string;
  snippet: string;
  score: number;
}

export interface AgentPreviewOutT {
  query: string;
  hits: AgentPreviewHitT[];
  hits_above_threshold: number;
  threshold: number | null;
  refused: boolean;
  refusal_message: string | null;
  answer: string | null;
  llm_used: boolean;
  llm_note: string | null;
}

// v0.13.0 — Agent sample embeddings resync (Migration 0016).
export interface AgentSamplesResyncOutT {
  agent_type: string;
  indexed_count: number;
  sample_queries: string[];
}

// ---------------------------------------------------------------------------
// Doc-types taxonomy — /api/doc-types (v0.7.0)
// Mirrors api_server's DocTypeOut / DocTypeIn / DocTypePatch pydantic schemas.
// ---------------------------------------------------------------------------
export interface DocTypeOutT {
  code: string;                   // PK, e.g. "manual"
  name: string;                   // human-friendly label
  description: string;
  expected_sections: string[];    // suggested top-level section titles
  created_at: string | null;
}

export interface DocTypeInT {
  code: string;                   // required
  name: string;                   // required
  description?: string;
  expected_sections?: string[];
}

export interface DocTypePatchT {
  name?: string;
  description?: string;
  expected_sections?: string[];
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

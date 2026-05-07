/**
 * Wire types — must mirror api_server's response payloads exactly.
 * Source of truth: api_server/src/api/routes/{meta,system,convert}.py
 */

export interface AgentOption {
  agent_type: string;
  name: string;
  description: string;
  data_types: string[];
}

export interface MetaOptions {
  version: string;
  divisions: string[];
  teams: Record<string, string[]>;
  agents: AgentOption[];
  classifications: string[];
  statuses: string[];
  derivations: string[];
  languages: string[];
  data_types: string[];
  supported_extensions: string[];
  max_upload_mb: number;
  allow_custom: {
    division: boolean;
    team: boolean;
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
  division: string;
  team: string;
  year: number;
  seq: number;
  source_file: string | null;
  content_hash: string | null;
}

export interface IngestResponse {
  record_id: string;
  status: 'inserted' | 'updated' | 'skipped';
  sections_written: number;
  record: IngestResponseRecord;
}

export interface UploadFormValues {
  // Identification (required)
  division: string;
  team: string;
  year: number;
  seq: number;
  // Classification
  classification: string;
  status: string;
  domain: string;
  language: string;
  // Discovery
  tags: string[];
  agents: string[];
  subject_keywords: string[];
  // Override (optional)
  title_override: string;
  summary_override: string;
  // Quality (optional)
  derivation: string;
  quality_score: number | null;
  valid_from: string;
  valid_until: string;
}

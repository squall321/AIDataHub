/**
 * Strongly-typed messages between Extension Host and Webview.
 * Keep this file dependency-free so it can be imported by both sides.
 */

import type {
  AgentInT,
  AgentOutT,
  AgentPatchT,
  DiscoverResponse,
  DocTypeInT,
  DocTypeOutT,
  FacetedSearchFilters,
  FacetedSearchResponse,
  FullRecord,
  IngestResponse,
  MetaOptions,
  SearchItem,
  SearchResponse,
  SystemHealth,
} from '../client/types';

// Webview → Host
export type WebviewToHost =
  | { type: 'ready' }
  | { type: 'getConfig' }
  | { type: 'testConnection'; baseUrl: string; apiKey: string }
  | { type: 'saveConfig'; baseUrl: string; apiKey: string }
  | { type: 'reset' }
  | { type: 'fetchOptions' }
  | { type: 'requestUploadCredentials' }   // webview asks host for { baseUrl, apiKey } once
  | {
      type: 'uploadResult';
      ok: boolean;
      recordId?: string;
      status?: string;       // inserted | updated | skipped
      requestId?: string;    // backend error envelope's request_id for support
      httpStatus?: number;   // 401, 413, etc — host uses 401 to re-prompt for API key
      error?: string;
    }
  | { type: 'promptApiKey' }
  // ---- New v0.4.0 messages ----
  | { type: 'searchRequest'; reqId: number; q: string; mode: 'semantic' | 'fts' | 'tag'; limit?: number }
  | { type: 'searchFacetedRequest'; reqId: number; filters: FacetedSearchFilters }
  | { type: 'getRecordRequest'; reqId: number; id: string }
  | { type: 'discoverRequest'; reqId: number }
  // ---- v0.6.0 Agents CRUD ----
  | { type: 'listAgentsRequest'; reqId: number }
  | { type: 'getAgentRecordsRequest'; reqId: number; agentType: string }
  | { type: 'createAgentRequest'; reqId: number; payload: AgentInT }
  | { type: 'updateAgentRequest'; reqId: number; agentType: string; patch: AgentPatchT }
  | { type: 'deleteAgentRequest'; reqId: number; agentType: string }
  // ---- v0.7.0 Doc-types ----
  | { type: 'listDocTypesRequest'; reqId: number }
  | { type: 'createDocTypeRequest'; reqId: number; payload: DocTypeInT }
  // ---- File picker / drop fallback ----
  /** Webview asks host to open OS file picker when drag-drop yields no File. */
  | { type: 'openFilePicker'; reqId: number; target: 'upload' | 'bundle' }
  /** Webview hands off a file path (from text/uri-list) to host for fs read. */
  | { type: 'loadDroppedPath'; reqId: number; target: 'upload' | 'bundle'; path: string };

// Host → Webview
export type HostToWebview =
  | { type: 'config'; baseUrl: string; hasApiKey: boolean; connected: boolean }
  | {
      type: 'connection';
      ok: boolean;
      error?: string;
      health?: SystemHealth;
      /** Server URL that actually responded (may differ from input if fallback succeeded). */
      effectiveUrl?: string;
      /** True when the original URL failed and a fallback (localhost) was used. */
      fellBack?: boolean;
    }
  | { type: 'options'; ok: boolean; payload?: MetaOptions; error?: string }
  | {
      type: 'uploadCredentials';
      ok: boolean;
      baseUrl?: string;
      apiKey?: string;     // delivered once, webview should drop after use
      error?: string;
    }
  // ---- New v0.4.0 messages ----
  | { type: 'searchResponse'; reqId: number; ok: boolean; payload?: SearchResponse; error?: string }
  | { type: 'searchFacetedResponse'; reqId: number; ok: boolean; payload?: FacetedSearchResponse; error?: string }
  | { type: 'getRecordResponse'; reqId: number; ok: boolean; payload?: FullRecord; error?: string }
  | { type: 'discoverResponse'; reqId: number; ok: boolean; payload?: DiscoverResponse; error?: string }
  // ---- v0.6.0 Agents CRUD responses ----
  | { type: 'listAgentsResponse'; reqId: number; ok: boolean; payload?: AgentOutT[]; error?: string }
  | { type: 'getAgentRecordsResponse'; reqId: number; ok: boolean; payload?: SearchItem[]; error?: string }
  | { type: 'createAgentResponse'; reqId: number; ok: boolean; payload?: AgentOutT; error?: string; httpStatus?: number }
  | { type: 'updateAgentResponse'; reqId: number; ok: boolean; payload?: AgentOutT; error?: string; httpStatus?: number }
  | { type: 'deleteAgentResponse'; reqId: number; ok: boolean; agentType?: string; error?: string; httpStatus?: number }
  // ---- v0.7.0 Doc-types responses ----
  | { type: 'listDocTypesResponse'; reqId: number; ok: boolean; payload?: DocTypeOutT[]; error?: string }
  | { type: 'createDocTypeResponse'; reqId: number; ok: boolean; payload?: DocTypeOutT; error?: string; httpStatus?: number }
  // ---- v0.6.0 cache invalidation hint ----
  | { type: 'optionsInvalidated' }
  // ---- File loaded from host ----
  | {
      type: 'fileLoaded';
      reqId: number;
      target: 'upload' | 'bundle';
      ok: boolean;
      /** base64 of file bytes (utf-8 safe across postMessage). */
      contentBase64?: string;
      filename?: string;
      size?: number;
      mimeType?: string;
      error?: string;
    };

export type IngestResponseT = IngestResponse;

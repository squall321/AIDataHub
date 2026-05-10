/**
 * Strongly-typed messages between Extension Host and Webview.
 * Keep this file dependency-free so it can be imported by both sides.
 */

import type {
  DiscoverResponse,
  FacetedSearchFilters,
  FacetedSearchResponse,
  FullRecord,
  IngestResponse,
  MetaOptions,
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
  | { type: 'discoverRequest'; reqId: number };

// Host → Webview
export type HostToWebview =
  | { type: 'config'; baseUrl: string; hasApiKey: boolean; connected: boolean }
  | { type: 'connection'; ok: boolean; error?: string; health?: SystemHealth }
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
  | { type: 'discoverResponse'; reqId: number; ok: boolean; payload?: DiscoverResponse; error?: string };

export type IngestResponseT = IngestResponse;

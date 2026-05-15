/**
 * Strongly-typed messages between Extension Host and Webview.
 * Keep this file dependency-free so it can be imported by both sides.
 */

import type {
  AgentHistoryOutT,
  AgentInT,
  AgentOutT,
  AgentPatchT,
  AgentPreviewInT,
  AgentPreviewOutT,
  AgentSamplesResyncOutT,
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
  // ---- v0.13.0 Agent history + preview + sample resync ----
  | { type: 'listAgentHistoryRequest'; reqId: number; agentType: string; limit?: number }
  | { type: 'previewAgentRecipeRequest'; reqId: number; payload: AgentPreviewInT }
  | { type: 'resyncAgentSamplesRequest'; reqId: number; agentType: string }
  // ---- v0.14.0 LLM-assisted agent draft + auto-bind ----
  | { type: 'draftAgentRequest'; reqId: number; payload: { record_ids?: string[]; filter_tags?: string[]; filter_data_types?: string[]; hint?: string | null } }
  | { type: 'bindMatchingRequest'; reqId: number; agentType: string; limit?: number }
  // ---- v0.8.0 Agent Word template download ----
  | { type: 'downloadAgentTemplateRequest'; reqId: number; agentType: string }
  // ---- v0.9.0 Console tab (agent-discovery-console) ----
  | { type: 'recommendAgentsRequest'; reqId: number; q: string; topK?: number }
  | { type: 'getContextBundleRequest'; reqId: number; agentType: string; format: 'markdown' | 'json' }
  | { type: 'getSystemPromptRequest'; reqId: number; agentType: string; baseUrlOverride?: string }
  | { type: 'copyToClipboardRequest'; reqId: number; text: string; label?: string }
  | {
      type: 'installMcpConfigRequest';
      reqId: number;
      client: 'cline' | 'claude_desktop' | 'claude_code' | 'cursor' | 'copilot' | 'gemini' | 'codex';
      baseUrl: string;  // 'http://host:port' (no trailing slash, /mcp/ 추가는 host측)
      // v0.13.0 — admin system_prompt 도 같이 자동 주입 (가능한 클라이언트 한정).
      systemPrompt?: string | null;
      agentType?: string | null;  // 식별자 (marker block 에 사용)
    }
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
  // ---- v0.13.0 Agent history + preview + sample resync responses ----
  | { type: 'listAgentHistoryResponse'; reqId: number; ok: boolean; payload?: AgentHistoryOutT[]; error?: string }
  | { type: 'previewAgentRecipeResponse'; reqId: number; ok: boolean; payload?: AgentPreviewOutT; error?: string }
  | { type: 'resyncAgentSamplesResponse'; reqId: number; ok: boolean; payload?: AgentSamplesResyncOutT; error?: string }
  // ---- v0.14.0 LLM-assisted agent draft + auto-bind responses ----
  | { type: 'draftAgentResponse'; reqId: number; ok: boolean; payload?: Record<string, unknown>; error?: string }
  | { type: 'bindMatchingResponse'; reqId: number; ok: boolean; payload?: Record<string, unknown>; error?: string }
  // ---- v0.8.0 Agent Word template download ----
  | { type: 'downloadAgentTemplateResponse'; reqId: number; ok: boolean; savedPath?: string; error?: string }
  // ---- v0.9.0 Console tab responses ----
  | {
      type: 'recommendAgentsResponse';
      reqId: number;
      ok: boolean;
      payload?: {
        query: string;
        candidate_sections: number;
        agents: Array<{
          agent_type: string;
          name: string;
          description: string;
          common_tags: string[];
          data_types: string[];
          score: number;
          matched_records: number;
          matched_sections: number;
          matched_samples?: number;  // v0.13.0 — sample_queries routing hint count
          why: string;
        }>;
      };
      error?: string;
    }
  | { type: 'getContextBundleResponse'; reqId: number; ok: boolean; text?: string; error?: string }
  | { type: 'getSystemPromptResponse'; reqId: number; ok: boolean; text?: string; error?: string }
  | { type: 'copyToClipboardResponse'; reqId: number; ok: boolean; error?: string }
  | {
      type: 'installMcpConfigResponse';
      reqId: number;
      ok: boolean;
      client?: string;
      configPath?: string;   // 갱신된 파일 경로 (MCP 서버 등록 대상)
      action?: 'created' | 'updated' | 'shell';  // 새 파일 / 기존 머지 / CLI 명령 실행
      shellCommand?: string; // shell 모드에서 실제 실행한 명령
      error?: string;
      hint?: string;         // 후속 안내 (재시작 등)
      // v0.13.0 — system_prompt 자동 주입 결과 (Option B). 클라이언트별로 가능 여부 다름.
      promptAction?: 'created' | 'updated' | 'manual' | 'skipped';
      promptPath?: string;   // 주입 위치 (manual 이면 안내 텍스트)
      promptError?: string;  // 주입 실패 사유 (있을 때만)
    }
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

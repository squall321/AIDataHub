import type {
  AgentHistoryOutT,
  AgentInT,
  AgentOutT,
  AgentPatchT,
  AgentPreviewInT,
  AgentPreviewOutT,
  AgentSamplesResyncOutT,
  BundleUploadResponse,
  DiscoverResponse,
  DocTypeInT,
  DocTypeOutT,
  DocTypePatchT,
  FacetedSearchFilters,
  FacetedSearchResponse,
  FullRecord,
  IngestResponse,
  MetaOptions,
  SearchResponse,
  SearchItem,
  SystemHealth,
  VerifyKeyResponse,
} from './types';

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

function joinUrl(baseUrl: string, path: string): string {
  const trimmed = baseUrl.replace(/\/+$/, '');
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${trimmed}${suffix}`;
}

async function parseError(res: Response): Promise<ApiError> {
  const text = await res.text();
  let code = `HTTP_${res.status}`;
  let message = text || res.statusText || `HTTP ${res.status}`;
  try {
    const body = JSON.parse(text);
    if (body?.error?.code) {
      code = body.error.code;
      message = body.error.message ?? message;
    } else if (body?.detail) {
      message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    }
  } catch {
    /* not JSON */
  }
  return new ApiError(code, message, res.status);
}

export class ApiClient {
  constructor(
    private readonly baseUrl: string,
    private readonly apiKey?: string,
    private readonly userId?: string,
  ) {}

  private headers(extra?: Record<string, string>): Record<string, string> {
    const h: Record<string, string> = { Accept: 'application/json', ...extra };
    if (this.apiKey) h['X-API-Key'] = this.apiKey;
    // v0.13.0 — agents_history.changed_by 채움. 인증 미연동 단계의 임시 식별.
    if (this.userId) h['X-User-Id'] = this.userId;
    return h;
  }

  /** Newer richer health (`/api/system/health`) with fallback to legacy `/health`. */
  async health(): Promise<SystemHealth> {
    const rich = await fetch(joinUrl(this.baseUrl, '/api/system/health'), {
      method: 'GET',
      headers: this.headers(),
    });
    if (rich.ok) {
      return (await rich.json()) as SystemHealth;
    }
    if (rich.status !== 404) throw await parseError(rich);

    // fallback — older deployments only expose minimal /health
    const legacy = await fetch(joinUrl(this.baseUrl, '/health'), {
      method: 'GET',
      headers: this.headers(),
    });
    if (!legacy.ok) throw await parseError(legacy);
    const body = (await legacy.json()) as { status?: string };
    return {
      status: body.status ?? 'ok',
      version: 'unknown',
      auth_required: false,
      build: 'legacy',
    };
  }

  async verifyKey(): Promise<VerifyKeyResponse> {
    const res = await fetch(joinUrl(this.baseUrl, '/api/auth/keys/verify'), {
      method: 'POST',
      headers: this.headers(),
    });
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as VerifyKeyResponse;
  }

  async getOptions(): Promise<MetaOptions> {
    const res = await fetch(joinUrl(this.baseUrl, '/api/meta/options'), {
      method: 'GET',
      headers: this.headers(),
    });
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as MetaOptions;
  }

  /**
   * `/api/convert/ingest` — multipart upload.
   * Builds the form locally; the actual upload (with progress) happens in the
   * webview's `uploader.ts` so we don't shuttle file bytes across the postMessage
   * bridge. This method exists for host-side smoke tests / future Electron work.
   */
  async ingest(file: Blob, filename: string, fields: Record<string, string>): Promise<IngestResponse> {
    const fd = new FormData();
    fd.append('file', file, filename);
    for (const [k, v] of Object.entries(fields)) {
      if (v !== undefined && v !== null && v !== '') fd.append(k, v);
    }
    const res = await fetch(joinUrl(this.baseUrl, '/api/convert/ingest'), {
      method: 'POST',
      headers: this.headers(),
      body: fd,
    });
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as IngestResponse;
  }

  /**
   * `/api/convert/` — DRY-RUN. Same payload as ingest, but the backend only
   * runs the converter and returns the resulting JSON without touching the DB.
   * Useful for previewing the parsed metadata/title/summary.
   */
  async convertOnly(file: Blob, filename: string, fields: Record<string, string>): Promise<unknown> {
    const fd = new FormData();
    fd.append('file', file, filename);
    for (const [k, v] of Object.entries(fields)) {
      if (v !== undefined && v !== null && v !== '') fd.append(k, v);
    }
    const res = await fetch(joinUrl(this.baseUrl, '/api/convert/'), {
      method: 'POST',
      headers: this.headers(),
      body: fd,
    });
    if (!res.ok) throw await parseError(res);
    return await res.json();
  }

  // ---------------------------------------------------------------------------
  // Bundle — POST /api/ingest/bundle
  // ---------------------------------------------------------------------------
  /**
   * Upload a pre-converted JSON+resources zip bundle.
   * `zipBlob` is the raw .zip bytes; `filename` ends in `.zip`.
   */
  async uploadBundle(zipBlob: Blob, filename: string): Promise<BundleUploadResponse> {
    const fd = new FormData();
    fd.append('file', zipBlob, filename);
    const res = await fetch(joinUrl(this.baseUrl, '/api/ingest/bundle'), {
      method: 'POST',
      headers: this.headers(),
      body: fd,
    });
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as BundleUploadResponse;
  }

  // ---------------------------------------------------------------------------
  // Search — /api/search and /api/search/faceted
  // ---------------------------------------------------------------------------
  /**
   * Single-mode search.
   *  - mode=`fts` / `semantic` → uses `q`
   *  - mode=`tag` → `q` is treated as comma-separated tag list
   */
  async search(
    q: string,
    mode: 'semantic' | 'fts' | 'tag' = 'fts',
    limit = 20,
  ): Promise<SearchResponse> {
    const params = new URLSearchParams();
    params.set('mode', mode);
    params.set('limit', String(limit));
    if (mode === 'tag') {
      // tag mode requires repeated `tags=` query parameters
      const tags = q
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
      for (const t of tags) params.append('tags', t);
    } else {
      params.set('q', q);
    }
    const res = await fetch(
      joinUrl(this.baseUrl, `/api/search?${params.toString()}`),
      { method: 'GET', headers: this.headers() },
    );
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as SearchResponse;
  }

  /**
   * Faceted search (multi-axis filters + facet counts).
   * Despite the original spec mentioning POST, the backend exposes this as
   * `GET /api/search/faceted` with all filters as query parameters. We accept
   * a single object and serialize it.
   */
  async searchFaceted(filters: FacetedSearchFilters): Promise<FacetedSearchResponse> {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(filters)) {
      if (v === undefined || v === null || v === '') continue;
      params.set(k, String(v));
    }
    const res = await fetch(
      joinUrl(this.baseUrl, `/api/search/faceted?${params.toString()}`),
      { method: 'GET', headers: this.headers() },
    );
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as FacetedSearchResponse;
  }

  /** GET /api/records/{id}/suggest-parent — format-similar campaign candidates. */
  async suggestParent(id: string, topK = 5): Promise<Record<string, unknown>> {
    const u = new URL(joinUrl(this.baseUrl, `/api/records/${encodeURIComponent(id)}/suggest-parent`));
    u.searchParams.set('top_k', String(topK));
    const res = await fetch(u.toString(), { method: 'GET', headers: this.headers() });
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as Record<string, unknown>;
  }

  /** PATCH /api/records/{id} — partial update (e.g. set parent_record_id). */
  async patchRecord(id: string, patch: Record<string, unknown>): Promise<Record<string, unknown>> {
    const res = await fetch(
      joinUrl(this.baseUrl, `/api/records/${encodeURIComponent(id)}`),
      {
        method: 'PATCH',
        headers: this.headers({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(patch),
      },
    );
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as Record<string, unknown>;
  }

  /** GET /api/records/{id} — full record detail. */
  async getRecord(id: string): Promise<FullRecord> {
    const res = await fetch(
      joinUrl(this.baseUrl, `/api/records/${encodeURIComponent(id)}`),
      { method: 'GET', headers: this.headers() },
    );
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as FullRecord;
  }

  // ---------------------------------------------------------------------------
  // Agents — /api/agents CRUD
  // ---------------------------------------------------------------------------
  /** GET /api/agents — list all agents. */
  async listAgents(): Promise<AgentOutT[]> {
    const res = await fetch(joinUrl(this.baseUrl, '/api/agents'), {
      method: 'GET',
      headers: this.headers(),
    });
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as AgentOutT[];
  }

  /** GET /api/agents/{agent_type} — single agent (404 if missing). */
  async getAgent(agentType: string): Promise<AgentOutT> {
    const res = await fetch(
      joinUrl(this.baseUrl, `/api/agents/${encodeURIComponent(agentType)}`),
      { method: 'GET', headers: this.headers() },
    );
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as AgentOutT;
  }

  /** GET /api/agents/{agent_type}/records — records consumed by this agent. */
  async getAgentRecords(agentType: string): Promise<SearchItem[]> {
    const res = await fetch(
      joinUrl(this.baseUrl, `/api/agents/${encodeURIComponent(agentType)}/records`),
      { method: 'GET', headers: this.headers() },
    );
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as SearchItem[];
  }

  /**
   * GET /api/agents/{agent_type}/tools — Wave-7 P3.
   * 매니페스트 정책 (restrict/require/exclude) 평가 후 호환 도구 반환.
   */
  async getAgentTools(agentType: string): Promise<{
    agent_type: string;
    agent_common_tags: string[];
    tools: Array<{
      name: string;
      title: string;
      description: string;
      version: number;
      policy: { restrict_agents: string[]; require_agent_tag: string[]; exclude_agent_tag: string[] };
    }>;
    tool_count: number;
  }> {
    const res = await fetch(
      joinUrl(this.baseUrl, `/api/agents/${encodeURIComponent(agentType)}/tools`),
      { method: 'GET', headers: this.headers() },
    );
    if (!res.ok) throw await parseError(res);
    return await res.json();
  }

  /**
   * GET /api/agents/{agent_type}/template — Word (.docx) template for this agent.
   *
   * Server returns `application/vnd.openxmlformats-officedocument.wordprocessingml.document`
   * with a `Content-Disposition: attachment; filename=agent_<type>_template.docx`
   * header. We parse the filename out of that header so the host can offer it
   * as the default `Save As` name; falls back to `agent_<type>_template.docx`.
   */
  async getAgentTemplate(agentType: string): Promise<{ bytes: ArrayBuffer; filename: string }> {
    // The endpoint returns binary; do NOT advertise Accept: application/json
    // (the server may 406 in strict configs). Pass only the API key header.
    const headers: Record<string, string> = {};
    if (this.apiKey) headers['X-API-Key'] = this.apiKey;
    const res = await fetch(
      joinUrl(this.baseUrl, `/api/agents/${encodeURIComponent(agentType)}/template`),
      { method: 'GET', headers },
    );
    if (!res.ok) throw await parseError(res);
    const bytes = await res.arrayBuffer();
    const fallback = `agent_${agentType}_template.docx`;
    const cd = res.headers.get('content-disposition') || res.headers.get('Content-Disposition') || '';
    let filename = fallback;
    if (cd) {
      // RFC 5987: filename*=UTF-8''foo.docx — prefer that if present.
      const star = /filename\*\s*=\s*[^']*''([^;]+)/i.exec(cd);
      if (star && star[1]) {
        try { filename = decodeURIComponent(star[1].trim().replace(/^"|"$/g, '')); }
        catch { /* keep fallback */ }
      } else {
        const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(cd);
        if (plain && plain[1]) filename = plain[1].trim();
      }
    }
    return { bytes, filename };
  }

  /**
   * POST /api/recommend/agents — natural language → ranked agents
   * (agent-discovery-console).
   */
  async recommendAgents(q: string, topK = 5): Promise<{
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
      why: string;
    }>;
  }> {
    const res = await fetch(joinUrl(this.baseUrl, '/api/recommend/agents'), {
      method: 'POST',
      headers: this.headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ q, top_k: topK }),
    });
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as ReturnType<typeof Object>;
  }

  /**
   * GET /api/agents/{agent_type}/context-bundle
   * Accept header switches between markdown (default) and JSON.
   */
  async getContextBundle(
    agentType: string,
    format: 'markdown' | 'json' = 'markdown',
  ): Promise<string> {
    const accept = format === 'json' ? 'application/json' : 'text/markdown';
    const headers: Record<string, string> = { Accept: accept };
    if (this.apiKey) headers['X-API-Key'] = this.apiKey;
    const res = await fetch(
      joinUrl(this.baseUrl, `/api/agents/${encodeURIComponent(agentType)}/context-bundle`),
      { method: 'GET', headers },
    );
    if (!res.ok) throw await parseError(res);
    return await res.text();
  }

  /** GET /api/agents/{agent_type}/system-prompt — text/plain. */
  async getSystemPrompt(agentType: string, baseUrlOverride?: string): Promise<string> {
    const headers: Record<string, string> = {};
    if (this.apiKey) headers['X-API-Key'] = this.apiKey;
    const url = new URL(joinUrl(this.baseUrl, `/api/agents/${encodeURIComponent(agentType)}/system-prompt`));
    if (baseUrlOverride) url.searchParams.set('base_url', baseUrlOverride);
    const res = await fetch(url.toString(), { method: 'GET', headers });
    if (!res.ok) throw await parseError(res);
    return await res.text();
  }

  /** POST /api/agents — create (201, or 409 on conflict). */
  async createAgent(body: AgentInT): Promise<AgentOutT> {
    const res = await fetch(joinUrl(this.baseUrl, '/api/agents'), {
      method: 'POST',
      headers: this.headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    });
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as AgentOutT;
  }

  /** PATCH /api/agents/{agent_type} — partial update (404 if missing). */
  async patchAgent(agentType: string, patch: AgentPatchT): Promise<AgentOutT> {
    const res = await fetch(
      joinUrl(this.baseUrl, `/api/agents/${encodeURIComponent(agentType)}`),
      {
        method: 'PATCH',
        headers: this.headers({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(patch),
      },
    );
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as AgentOutT;
  }

  /** DELETE /api/agents/{agent_type} — 204 on success, 404 if missing. */
  async deleteAgent(agentType: string): Promise<void> {
    const res = await fetch(
      joinUrl(this.baseUrl, `/api/agents/${encodeURIComponent(agentType)}`),
      { method: 'DELETE', headers: this.headers() },
    );
    if (!res.ok && res.status !== 204) throw await parseError(res);
  }

  /** GET /api/agents/{agent_type}/history — append-only audit log (Migration 0015). */
  async listAgentHistory(agentType: string, limit = 50): Promise<AgentHistoryOutT[]> {
    const u = new URL(
      joinUrl(this.baseUrl, `/api/agents/${encodeURIComponent(agentType)}/history`),
    );
    u.searchParams.set('limit', String(limit));
    const res = await fetch(u.toString(), { method: 'GET', headers: this.headers() });
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as AgentHistoryOutT[];
  }

  /** POST /api/agents/preview — dry-run RAG recipe + (optional) LLM answer. */
  async previewAgentRecipe(body: AgentPreviewInT): Promise<AgentPreviewOutT> {
    const res = await fetch(joinUrl(this.baseUrl, '/api/agents/preview'), {
      method: 'POST',
      headers: this.headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    });
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as AgentPreviewOutT;
  }

  /** POST /api/agents/{agent_type}/resync-samples — re-embed sample_queries. */
  async resyncAgentSamples(agentType: string): Promise<AgentSamplesResyncOutT> {
    const res = await fetch(
      joinUrl(this.baseUrl, `/api/agents/${encodeURIComponent(agentType)}/resync-samples`),
      { method: 'POST', headers: this.headers() },
    );
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as AgentSamplesResyncOutT;
  }

  /** POST /api/agents/draft — LLM/heuristic agent definition draft (not saved). */
  async draftAgent(body: { record_ids?: string[]; filter_tags?: string[]; filter_data_types?: string[]; hint?: string | null }): Promise<Record<string, unknown>> {
    const res = await fetch(joinUrl(this.baseUrl, '/api/agents/draft'), {
      method: 'POST',
      headers: this.headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    });
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as Record<string, unknown>;
  }

  /** POST /api/agents/{agent_type}/bind-matching — auto-bind matching records. */
  async bindMatchingRecords(agentType: string, limit = 500): Promise<Record<string, unknown>> {
    const res = await fetch(
      joinUrl(this.baseUrl, `/api/agents/${encodeURIComponent(agentType)}/bind-matching`),
      {
        method: 'POST',
        headers: this.headers({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ limit }),
      },
    );
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as Record<string, unknown>;
  }

  /** GET /api/discover — system-wide catalog. */
  async discover(): Promise<DiscoverResponse> {
    const res = await fetch(joinUrl(this.baseUrl, '/api/discover'), {
      method: 'GET',
      headers: this.headers(),
    });
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as DiscoverResponse;
  }

  // ---------------------------------------------------------------------------
  // Doc-types taxonomy — /api/doc-types CRUD (v0.7.0)
  // ---------------------------------------------------------------------------
  /** GET /api/doc-types — list all doc_type definitions. */
  async listDocTypes(): Promise<DocTypeOutT[]> {
    const res = await fetch(joinUrl(this.baseUrl, '/api/doc-types'), {
      method: 'GET',
      headers: this.headers(),
    });
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as DocTypeOutT[];
  }

  /** GET /api/doc-types/{code} — single doc_type (404 if missing). */
  async getDocType(code: string): Promise<DocTypeOutT> {
    const res = await fetch(
      joinUrl(this.baseUrl, `/api/doc-types/${encodeURIComponent(code)}`),
      { method: 'GET', headers: this.headers() },
    );
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as DocTypeOutT;
  }

  /** POST /api/doc-types — create (201, or 409 on conflict). */
  async createDocType(body: DocTypeInT): Promise<DocTypeOutT> {
    const res = await fetch(joinUrl(this.baseUrl, '/api/doc-types'), {
      method: 'POST',
      headers: this.headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    });
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as DocTypeOutT;
  }

  /** PATCH /api/doc-types/{code} — partial update (404 if missing). */
  async patchDocType(code: string, patch: DocTypePatchT): Promise<DocTypeOutT> {
    const res = await fetch(
      joinUrl(this.baseUrl, `/api/doc-types/${encodeURIComponent(code)}`),
      {
        method: 'PATCH',
        headers: this.headers({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(patch),
      },
    );
    if (!res.ok) throw await parseError(res);
    return (await res.json()) as DocTypeOutT;
  }

  /** DELETE /api/doc-types/{code} — 204 on success, 404 if missing. */
  async deleteDocType(code: string): Promise<void> {
    const res = await fetch(
      joinUrl(this.baseUrl, `/api/doc-types/${encodeURIComponent(code)}`),
      { method: 'DELETE', headers: this.headers() },
    );
    if (!res.ok && res.status !== 204) throw await parseError(res);
  }

  // ---------------------------------------------------------------------------
  // v0.9.0 — LLM-assisted ingest
  // ---------------------------------------------------------------------------
  /**
   * GET /api/schema/ingest-guide — LLM 시스템 프롬프트로 그대로 쓸 수 있는 가이드.
   * format=markdown(기본) 이면 text/markdown 본문, json 이면 구조화 dict.
   */
  async getIngestGuide(
    agentType?: string | null,
    format: 'markdown' | 'json' = 'markdown',
  ): Promise<{ text: string; payload?: unknown }> {
    const params = new URLSearchParams();
    params.set('format', format);
    if (agentType) params.set('agent_type', agentType);
    const res = await fetch(
      joinUrl(this.baseUrl, `/api/schema/ingest-guide?${params.toString()}`),
      { method: 'GET', headers: this.headers() },
    );
    if (!res.ok) throw await parseError(res);
    if (format === 'json') {
      const payload = await res.json();
      return { text: JSON.stringify(payload, null, 2), payload };
    }
    return { text: await res.text() };
  }

  /**
   * GET /api/schema/ingest-kit.zip — self-contained 검증 키트 (zip bytes).
   * agent_type 을 지정하면 해당 agent 의 expected schema 가 validate.py 에 박힘.
   */
  async getIngestKitZip(
    agentType?: string | null,
  ): Promise<{ bytes: ArrayBuffer; filename: string }> {
    const params = new URLSearchParams();
    if (agentType) params.set('agent_type', agentType);
    const qs = params.toString();
    const res = await fetch(
      joinUrl(this.baseUrl, `/api/schema/ingest-kit.zip${qs ? `?${qs}` : ''}`),
      { method: 'GET', headers: this.headers() },
    );
    if (!res.ok) throw await parseError(res);
    // 파일명 파싱 (Content-Disposition: attachment; filename="...").
    const cd = res.headers.get('content-disposition') || '';
    const m = /filename="?([^"]+)"?/i.exec(cd);
    const filename = (m && m[1]) || (agentType ? `ingest-kit-${agentType}.zip` : 'ingest-kit.zip');
    const bytes = await res.arrayBuffer();
    return { bytes, filename };
  }

  /**
   * POST /api/records/import — JSON 일괄 임포트 (auto_seq + UPSERT + dry_run).
   * `body` 는 record dict, list, 또는 {records:[...]} 형식 모두 허용.
   */
  async importRecords(
    body: unknown,
    opts: { autoSeq?: boolean; dryRun?: boolean } = {},
  ): Promise<{
    count: number;
    ok: number;
    failed: number;
    warnings: number;
    auto_seq: boolean;
    dry_run: boolean;
    results: Array<Record<string, unknown>>;
  }> {
    const params = new URLSearchParams();
    if (opts.autoSeq) params.set('auto_seq', 'true');
    if (opts.dryRun) params.set('dry_run', 'true');
    const qs = params.toString();
    const url = joinUrl(
      this.baseUrl,
      `/api/records/import${qs ? `?${qs}` : ''}`,
    );
    const res = await fetch(url, {
      method: 'POST',
      headers: this.headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    });
    if (!res.ok) throw await parseError(res);
    return await res.json();
  }
}

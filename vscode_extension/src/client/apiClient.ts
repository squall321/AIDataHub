import type {
  AgentInT,
  AgentOutT,
  AgentPatchT,
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
  ) {}

  private headers(extra?: Record<string, string>): Record<string, string> {
    const h: Record<string, string> = { Accept: 'application/json', ...extra };
    if (this.apiKey) h['X-API-Key'] = this.apiKey;
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
}

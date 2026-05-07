import type {
  IngestResponse,
  MetaOptions,
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
}

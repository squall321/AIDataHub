/**
 * AI Data Hub — Minimal TypeScript client (fetch-based, browser/Node 모두 동작).
 *
 * 사용법:
 *   import { client } from "./ts_client";
 *   const items = await client.search("KooRemapper", "semantic", 5);
 *   const rec   = await client.getRecord(items[0].record_id);
 */

// === API URL ===============================================================
// 우선순위:
//   1) process.env.AIDH_API_URL (Node) 또는 globalThis.AIDH_API_URL (browser)
//   2) 아래 하드코딩 값 (../update_url.py 가 일괄 갱신)
// ===========================================================================
const ENV_URL: string | undefined =
  (typeof process !== "undefined" && process.env?.AIDH_API_URL) ||
  (globalThis as { AIDH_API_URL?: string }).AIDH_API_URL;
const BASE = (ENV_URL ?? "http://110.15.177.125:8000").replace(/\/$/, "");
const ENV_KEY: string | undefined =
  (typeof process !== "undefined" && process.env?.AIDH_API_KEY) ||
  (globalThis as { AIDH_API_KEY?: string }).AIDH_API_KEY;
const API_KEY: string | null = ENV_KEY ?? null;

interface SearchItem {
  record_id?: string;
  id?: string;
  title: string;
  data_type?: string;
  snippet?: string;
  summary?: string;
  score?: number;
  section_id?: string;
  section_title?: string;
  tags?: string[];
}

interface SearchResponse {
  mode: string;
  items: SearchItem[];
  total: number;
}

interface DiscoverResponse {
  total_records: number;
  by_data_type: Record<string, number>;
  agents?: unknown;
  top_tags?: Array<{ tag: string; count: number }>;
}

interface AutoGroupsResponse {
  query: string;
  total_records: number;
  groups: Array<{
    label: string;
    size: number;
    common_domain?: string;
    common_tags?: string[];
    records: Array<{ id: string; title: string; score: number }>;
  }>;
}

class AIDataHubClient {
  constructor(
    private readonly base: string = BASE,
    private readonly apiKey: string | null = API_KEY,
  ) {}

  private async request<T>(
    path: string,
    init: RequestInit = {},
  ): Promise<T> {
    const headers: Record<string, string> = {
      Accept: "application/json",
      ...((init.headers as Record<string, string>) ?? {}),
    };
    if (this.apiKey) headers["X-API-Key"] = this.apiKey;
    if (init.body && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    const resp = await fetch(this.base + path, { ...init, headers });
    const ct = resp.headers.get("content-type") ?? "";
    const body = ct.includes("application/json")
      ? await resp.json()
      : await resp.text();
    if (!resp.ok) {
      const message =
        typeof body === "object" && body !== null && "error" in body
          ? (body as { error: { message: string } }).error.message
          : String(body);
      throw new Error(`HTTP ${resp.status}: ${message}`);
    }
    return body as T;
  }

  health(): Promise<{ status: string; version: string; auth_required: boolean }> {
    return this.request("/api/system/health");
  }

  discover(): Promise<DiscoverResponse> {
    return this.request("/api/discover");
  }

  async search(
    q: string,
    mode: "semantic" | "fts" | "tag" = "semantic",
    limit = 20,
    tags?: string[],
  ): Promise<SearchItem[]> {
    const params = new URLSearchParams({ mode, limit: String(limit) });
    if (mode === "tag") {
      if (!tags || tags.length === 0)
        throw new Error("mode=tag requires tags");
      tags.forEach((t) => params.append("tags", t));
    } else {
      params.set("q", q);
    }
    const r = await this.request<SearchResponse>(
      `/api/search?${params.toString()}`,
    );
    return r.items ?? [];
  }

  searchFaceted(
    q: string | null,
    filters: Record<string, string | number | undefined>,
  ): Promise<unknown> {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    Object.entries(filters).forEach(([k, v]) => {
      if (v != null) params.set(k, String(v));
    });
    return this.request(`/api/search/faceted?${params.toString()}`);
  }

  getRecord(recordId: string): Promise<unknown> {
    return this.request(`/api/records/${encodeURIComponent(recordId)}`);
  }

  listRecords(
    filters: Record<string, string | number | undefined> = {},
  ): Promise<{ items: unknown[]; total: number }> {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([k, v]) => {
      if (v != null) params.set(k, String(v));
    });
    return this.request(
      `/api/records${params.toString() ? "?" + params.toString() : ""}`,
    );
  }

  autoGroups(
    q: string,
    n_groups = 3,
    top_k = 50,
  ): Promise<AutoGroupsResponse> {
    return this.request("/api/groups/auto", {
      method: "POST",
      body: JSON.stringify({ q, n_groups, top_k }),
    });
  }

  ask(q: string): Promise<unknown> {
    return this.request("/api/ask", {
      method: "POST",
      body: JSON.stringify({ q }),
    });
  }
}

export const client = new AIDataHubClient();
export { AIDataHubClient, BASE };

// 자가 진단 (Node 에서 직접 실행 시):
//   tsc ts_client.ts && node ts_client.js
if (typeof require !== "undefined" && require.main === module) {
  (async () => {
    const h = await client.health();
    console.log(`[OK] health: ${h.status} v${h.version}`);
    const d = await client.discover();
    console.log(`[OK] discover: ${d.total_records} records`);
    const items = await client.search("KooRemapper", "semantic", 3);
    console.log(`[OK] search: ${items.length} items`);
    items.forEach((it) =>
      console.log(`  - ${it.record_id ?? it.id} ${it.title?.slice(0, 50)}`),
    );
  })().catch((e) => {
    console.error("FAIL:", e.message);
    process.exit(1);
  });
}

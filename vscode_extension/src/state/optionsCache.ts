import type { MetaOptions } from '../client/types';

interface CacheEntry {
  fetchedAt: number;
  baseUrl: string;
  options: MetaOptions;
}

const TTL_MS = 5 * 60 * 1000; // 5 minutes — matches backend Cache-Control: max-age=300

export class OptionsCache {
  private entry?: CacheEntry;

  get(baseUrl: string): MetaOptions | undefined {
    if (!this.entry) return undefined;
    if (this.entry.baseUrl !== baseUrl) return undefined;
    if (Date.now() - this.entry.fetchedAt > TTL_MS) return undefined;
    return this.entry.options;
  }

  set(baseUrl: string, options: MetaOptions): void {
    this.entry = { fetchedAt: Date.now(), baseUrl, options };
  }

  clear(): void {
    this.entry = undefined;
  }
}

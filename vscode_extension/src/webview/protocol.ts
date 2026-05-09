/**
 * Strongly-typed messages between Extension Host and Webview.
 * Keep this file dependency-free so it can be imported by both sides.
 */

import type { IngestResponse, MetaOptions, SystemHealth } from '../client/types';

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
  | { type: 'promptApiKey' };    // user clicked "re-enter API key" after 401

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
    };

export type IngestResponseT = IngestResponse;

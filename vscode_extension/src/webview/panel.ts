import * as vscode from 'vscode';
import { ConfigStore } from '../state/configStore';
import { OptionsCache } from '../state/optionsCache';
import { ApiClient, ApiError } from '../client/apiClient';
import type { HostToWebview, WebviewToHost } from './protocol';
import { renderHtml } from './html';

const VIEW_TYPE = 'aidh.uploader';

export class UploaderPanel {
  private static current: UploaderPanel | undefined;

  static show(context: vscode.ExtensionContext, store: ConfigStore): void {
    const column = vscode.window.activeTextEditor?.viewColumn ?? vscode.ViewColumn.One;
    if (UploaderPanel.current) {
      UploaderPanel.current.panel.reveal(column);
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      VIEW_TYPE,
      'Mobile eXperience AI Data Hub',
      column,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, 'media')],
      },
    );
    UploaderPanel.current = new UploaderPanel(panel, store);
  }

  private readonly disposables: vscode.Disposable[] = [];
  private readonly options = new OptionsCache();

  private constructor(
    private readonly panel: vscode.WebviewPanel,
    private readonly store: ConfigStore,
  ) {
    this.panel.webview.html = renderHtml();

    this.panel.onDidDispose(() => this.dispose(), null, this.disposables);
    this.panel.webview.onDidReceiveMessage(
      (msg: WebviewToHost) => this.onMessage(msg),
      null,
      this.disposables,
    );
  }

  private dispose(): void {
    UploaderPanel.current = undefined;
    while (this.disposables.length) {
      const d = this.disposables.pop();
      d?.dispose();
    }
  }

  private post(msg: HostToWebview): void {
    void this.panel.webview.postMessage(msg);
  }

  private async getClient(): Promise<ApiClient | null> {
    const baseUrl = this.store.getBaseUrl();
    if (!baseUrl) return null;
    const apiKey = await this.store.getApiKey();
    return new ApiClient(baseUrl, apiKey || undefined);
  }

  private async onMessage(msg: WebviewToHost): Promise<void> {
    switch (msg.type) {
      case 'ready':
      case 'getConfig': {
        const snap = await this.store.snapshot();
        this.post({ type: 'config', ...snap });
        return;
      }
      case 'testConnection': {
        await this.testConnection(msg.baseUrl, msg.apiKey);
        return;
      }
      case 'saveConfig': {
        await this.testConnection(msg.baseUrl, msg.apiKey, /*persist*/ true);
        return;
      }
      case 'reset': {
        await this.store.reset();
        this.options.clear();
        const snap = await this.store.snapshot();
        this.post({ type: 'config', ...snap });
        return;
      }
      case 'fetchOptions': {
        await this.fetchOptions();
        return;
      }
      case 'requestUploadCredentials': {
        const baseUrl = this.store.getBaseUrl();
        const apiKey = await this.store.getApiKey();
        if (!baseUrl) {
          this.post({ type: 'uploadCredentials', ok: false, error: 'Not connected' });
          return;
        }
        this.post({
          type: 'uploadCredentials',
          ok: true,
          baseUrl,
          apiKey: apiKey ?? '',
        });
        return;
      }
      case 'uploadResult': {
        // Surface final outcome as a VS Code toast so it's visible even if
        // the user navigates away from the panel.
        if (msg.ok) {
          const status = msg.status ? ` (${msg.status})` : '';
          void vscode.window.showInformationMessage(
            `Mobile eXperience AI Data Hub: uploaded ${msg.recordId ?? 'record'}${status}`,
          );
        } else if (msg.httpStatus === 401) {
          // Auth failed → offer re-entry of API key right from the toast.
          const choice = await vscode.window.showErrorMessage(
            'Mobile eXperience AI Data Hub: API key invalid (401). Re-enter your key?',
            'Re-enter API key',
            'Cancel',
          );
          if (choice === 'Re-enter API key') {
            await this.promptForApiKey();
          }
        } else {
          const detail = msg.requestId ? ` [request_id=${msg.requestId}]` : '';
          void vscode.window.showErrorMessage(
            `Mobile eXperience AI Data Hub upload failed: ${msg.error ?? 'unknown error'}${detail}`,
          );
        }
        return;
      }
      case 'promptApiKey': {
        await this.promptForApiKey();
        return;
      }
      // ---- New v0.4.0 routes ----
      case 'searchRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'searchResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const payload = await client.search(msg.q, msg.mode, msg.limit ?? 20);
          this.post({ type: 'searchResponse', reqId: msg.reqId, ok: true, payload });
        } catch (err) {
          this.post({ type: 'searchResponse', reqId: msg.reqId, ok: false, error: formatError(err) });
        }
        return;
      }
      case 'searchFacetedRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'searchFacetedResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const payload = await client.searchFaceted(msg.filters);
          this.post({ type: 'searchFacetedResponse', reqId: msg.reqId, ok: true, payload });
        } catch (err) {
          this.post({ type: 'searchFacetedResponse', reqId: msg.reqId, ok: false, error: formatError(err) });
        }
        return;
      }
      case 'getRecordRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'getRecordResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const payload = await client.getRecord(msg.id);
          this.post({ type: 'getRecordResponse', reqId: msg.reqId, ok: true, payload });
        } catch (err) {
          this.post({ type: 'getRecordResponse', reqId: msg.reqId, ok: false, error: formatError(err) });
        }
        return;
      }
      case 'discoverRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'discoverResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const payload = await client.discover();
          this.post({ type: 'discoverResponse', reqId: msg.reqId, ok: true, payload });
        } catch (err) {
          this.post({ type: 'discoverResponse', reqId: msg.reqId, ok: false, error: formatError(err) });
        }
        return;
      }
      // ---- v0.6.0 Agents CRUD ----
      case 'listAgentsRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'listAgentsResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const payload = await client.listAgents();
          this.post({ type: 'listAgentsResponse', reqId: msg.reqId, ok: true, payload });
        } catch (err) {
          this.post({ type: 'listAgentsResponse', reqId: msg.reqId, ok: false, error: formatError(err) });
        }
        return;
      }
      case 'getAgentRecordsRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'getAgentRecordsResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const payload = await client.getAgentRecords(msg.agentType);
          this.post({ type: 'getAgentRecordsResponse', reqId: msg.reqId, ok: true, payload });
        } catch (err) {
          this.post({ type: 'getAgentRecordsResponse', reqId: msg.reqId, ok: false, error: formatError(err) });
        }
        return;
      }
      case 'createAgentRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'createAgentResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const payload = await client.createAgent(msg.payload);
          // Agent list changed → invalidate meta/options cache so Upload tab's
          // agent dropdown picks up the new entry on next fetchOptions.
          this.options.clear();
          this.post({ type: 'createAgentResponse', reqId: msg.reqId, ok: true, payload });
          this.post({ type: 'optionsInvalidated' });
        } catch (err) {
          const status = err instanceof ApiError ? err.status : undefined;
          this.post({ type: 'createAgentResponse', reqId: msg.reqId, ok: false, error: formatError(err), httpStatus: status });
        }
        return;
      }
      case 'updateAgentRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'updateAgentResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const payload = await client.patchAgent(msg.agentType, msg.patch);
          this.options.clear();
          this.post({ type: 'updateAgentResponse', reqId: msg.reqId, ok: true, payload });
          this.post({ type: 'optionsInvalidated' });
        } catch (err) {
          const status = err instanceof ApiError ? err.status : undefined;
          this.post({ type: 'updateAgentResponse', reqId: msg.reqId, ok: false, error: formatError(err), httpStatus: status });
        }
        return;
      }
      case 'deleteAgentRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'deleteAgentResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          await client.deleteAgent(msg.agentType);
          this.options.clear();
          this.post({ type: 'deleteAgentResponse', reqId: msg.reqId, ok: true, agentType: msg.agentType });
          this.post({ type: 'optionsInvalidated' });
        } catch (err) {
          const status = err instanceof ApiError ? err.status : undefined;
          this.post({ type: 'deleteAgentResponse', reqId: msg.reqId, ok: false, error: formatError(err), httpStatus: status });
        }
        return;
      }
      // ---- v0.8.0 Word template download for an agent ----
      case 'downloadAgentTemplateRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'downloadAgentTemplateResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const { bytes, filename } = await client.getAgentTemplate(msg.agentType);
          // Default Save dialog into the workspace folder if any, otherwise
          // VS Code falls back to user home; defaultUri must include the file
          // name to populate the dialog.
          const wsRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
          const defaultUri = wsRoot
            ? vscode.Uri.joinPath(wsRoot, filename)
            : vscode.Uri.file(filename);
          const target = await vscode.window.showSaveDialog({
            defaultUri,
            filters: { Word: ['docx'] },
            saveLabel: 'Save template',
          });
          if (!target) {
            this.post({ type: 'downloadAgentTemplateResponse', reqId: msg.reqId, ok: false, error: 'cancelled' });
            return;
          }
          await vscode.workspace.fs.writeFile(target, new Uint8Array(bytes));
          this.post({
            type: 'downloadAgentTemplateResponse',
            reqId: msg.reqId,
            ok: true,
            savedPath: target.fsPath,
          });
        } catch (err) {
          this.post({
            type: 'downloadAgentTemplateResponse',
            reqId: msg.reqId,
            ok: false,
            error: formatError(err),
          });
        }
        return;
      }
      // ---- v0.7.0 Doc-types ----
      case 'listDocTypesRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'listDocTypesResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const payload = await client.listDocTypes();
          this.post({ type: 'listDocTypesResponse', reqId: msg.reqId, ok: true, payload });
        } catch (err) {
          this.post({ type: 'listDocTypesResponse', reqId: msg.reqId, ok: false, error: formatError(err) });
        }
        return;
      }
      case 'createDocTypeRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'createDocTypeResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const payload = await client.createDocType(msg.payload);
          // doc_type taxonomy changed — invalidate meta cache in case the
          // server-side options bundle later starts exposing them.
          this.options.clear();
          this.post({ type: 'createDocTypeResponse', reqId: msg.reqId, ok: true, payload });
          this.post({ type: 'optionsInvalidated' });
        } catch (err) {
          const status = err instanceof ApiError ? err.status : undefined;
          this.post({ type: 'createDocTypeResponse', reqId: msg.reqId, ok: false, error: formatError(err), httpStatus: status });
        }
        return;
      }
      case 'openFilePicker': {
        // 드래그-드롭이 webview 안에서 빈 dataTransfer 를 반환할 때 호출됨.
        // VS Code OS 네이티브 파일 다이얼로그를 띄워 사용자가 파일 선택 → fs 로 읽어 webview 로 전달.
        const filters: { [name: string]: string[] } = msg.target === 'bundle'
          ? { 'ZIP bundle': ['zip'] }
          : { 'Documents': ['docx', 'pdf', 'pptx', 'md', 'markdown', 'xlsx', 'html', 'htm'] };
        try {
          const uris = await vscode.window.showOpenDialog({
            canSelectFiles: true, canSelectFolders: false, canSelectMany: false,
            filters,
            openLabel: msg.target === 'bundle' ? 'Upload bundle' : 'Upload file',
          });
          if (!uris || uris.length === 0) {
            this.post({ type: 'fileLoaded', reqId: msg.reqId, target: msg.target, ok: false, error: 'cancelled' });
            return;
          }
          await this.streamFileToWebview(uris[0], msg.reqId, msg.target);
        } catch (err) {
          this.post({ type: 'fileLoaded', reqId: msg.reqId, target: msg.target, ok: false, error: formatError(err) });
        }
        return;
      }
      case 'loadDroppedPath': {
        // text/uri-list 또는 file:/// 경로를 webview 가 추출한 후 호출.
        try {
          let raw = msg.path;
          if (/^file:\/\//i.test(raw)) {
            // file:///C:/... → 경로만 추출 (vscode.Uri.parse 가 처리)
            const uri = vscode.Uri.parse(raw);
            await this.streamFileToWebview(uri, msg.reqId, msg.target);
          } else {
            const uri = vscode.Uri.file(raw);
            await this.streamFileToWebview(uri, msg.reqId, msg.target);
          }
        } catch (err) {
          this.post({ type: 'fileLoaded', reqId: msg.reqId, target: msg.target, ok: false, error: formatError(err) });
        }
        return;
      }
    }
  }

  /** 파일을 디스크에서 읽어 base64 로 인코딩한 후 webview 로 postMessage. */
  private async streamFileToWebview(
    uri: vscode.Uri,
    reqId: number,
    target: 'upload' | 'bundle',
  ): Promise<void> {
    const data = await vscode.workspace.fs.readFile(uri);
    const filename = uri.path.split('/').pop() || 'file';
    // Buffer 로 변환해 base64 인코딩 (Node only — 확장 호스트에서 안전)
    const b64 = Buffer.from(data).toString('base64');
    this.post({
      type: 'fileLoaded',
      reqId,
      target,
      ok: true,
      contentBase64: b64,
      filename,
      size: data.byteLength,
      mimeType: this.guessMime(filename),
    });
  }

  private guessMime(filename: string): string {
    const lc = filename.toLowerCase();
    if (lc.endsWith('.docx')) return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
    if (lc.endsWith('.pptx')) return 'application/vnd.openxmlformats-officedocument.presentationml.presentation';
    if (lc.endsWith('.xlsx')) return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
    if (lc.endsWith('.pdf'))  return 'application/pdf';
    if (lc.endsWith('.md') || lc.endsWith('.markdown')) return 'text/markdown';
    if (lc.endsWith('.html') || lc.endsWith('.htm')) return 'text/html';
    if (lc.endsWith('.zip'))  return 'application/zip';
    return 'application/octet-stream';
  }

  /**
   * Prompt the user to re-enter their API key (used after 401 errors).
   * Stores the new key in SecretStorage and notifies the webview.
   */
  private async promptForApiKey(): Promise<void> {
    const newKey = await vscode.window.showInputBox({
      title: 'Mobile eXperience AI Data Hub — API Key',
      prompt: 'Enter your API key. It will be stored in VS Code SecretStorage.',
      password: true,
      ignoreFocusOut: true,
    });
    if (newKey === undefined) return; // user cancelled
    if (newKey.trim().length === 0) {
      await this.store.clearApiKey();
    } else {
      await this.store.setApiKey(newKey.trim());
    }
    const snap = await this.store.snapshot();
    this.post({ type: 'config', ...snap });
  }

  private async testConnection(
    baseUrl: string,
    apiKey: string,
    persist = false,
  ): Promise<void> {
    const normalizedUrl = baseUrl.trim();
    if (!normalizedUrl) {
      this.post({ type: 'connection', ok: false, error: 'Server URL is empty.' });
      return;
    }

    // 동적 폴백: 입력된 URL → 실패 시 localhost:8000 자동 시도.
    // NAT loopback 환경 (자기 외부 IP 가 자기 PC 에서 안 닿음) 등에서
    // 사용자가 일일이 URL 을 바꾸지 않아도 동작하도록.
    const FALLBACK_URL = 'http://localhost:8000';
    const candidates: string[] = [normalizedUrl];
    if (
      !/^https?:\/\/(localhost|127\.0\.0\.1)(:|\/|$)/i.test(normalizedUrl)
      && normalizedUrl !== FALLBACK_URL
    ) {
      candidates.push(FALLBACK_URL);
    }

    let lastError: unknown = null;
    for (let i = 0; i < candidates.length; i++) {
      const url = candidates[i];
      const fellBack = i > 0;
      const client = new ApiClient(url, apiKey || undefined);
      try {
        const health = await client.health();
        if (health.auth_required && !apiKey) {
          this.post({
            type: 'connection',
            ok: false,
            error: 'This server requires an API key (auth_required=true).',
            health,
            effectiveUrl: url,
            fellBack,
          });
          return;
        }
        if (apiKey) {
          await client.verifyKey();
        }
        if (persist) {
          await this.store.setBaseUrl(url);
          if (apiKey) await this.store.setApiKey(apiKey);
          await this.store.setConnected(true);
          const snap = await this.store.snapshot();
          this.post({ type: 'config', ...snap });
        }
        this.post({
          type: 'connection',
          ok: true,
          health,
          effectiveUrl: url,
          fellBack,
        });
        return;
      } catch (err) {
        lastError = err;
        // 다음 후보로 계속.
      }
    }
    this.post({
      type: 'connection',
      ok: false,
      error: formatError(lastError),
    });
  }

  private async fetchOptions(): Promise<void> {
    const baseUrl = this.store.getBaseUrl();
    if (!baseUrl) {
      this.post({ type: 'options', ok: false, error: 'Not connected' });
      return;
    }
    const cached = this.options.get(baseUrl);
    if (cached) {
      this.post({ type: 'options', ok: true, payload: cached });
      return;
    }
    const apiKey = await this.store.getApiKey();
    const client = new ApiClient(baseUrl, apiKey || undefined);
    try {
      const opts = await client.getOptions();
      this.options.set(baseUrl, opts);
      this.post({ type: 'options', ok: true, payload: opts });
    } catch (err) {
      this.post({ type: 'options', ok: false, error: formatError(err) });
    }
  }
}

function formatError(err: unknown): string {
  if (err instanceof ApiError) return `[${err.code}] ${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

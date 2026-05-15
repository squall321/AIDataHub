import * as os from 'node:os';
import * as vscode from 'vscode';
import { ConfigStore } from '../state/configStore';
import { OptionsCache } from '../state/optionsCache';
import { ApiClient, ApiError } from '../client/apiClient';
import type { HostToWebview, WebviewToHost } from './protocol';
import { renderHtml } from './html';

// v0.13.0 — agents_history.changed_by 채울 임시 식별자. 인증 미연동 단계라
// OS 사용자명을 그대로 사용. 형식: "vscode:<username>" — 향후 SSO 토큰으로 교체.
function _currentUserId(): string | undefined {
  try {
    const u = os.userInfo().username;
    return u ? `vscode:${u}` : undefined;
  } catch {
    return undefined;
  }
}

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
    return new ApiClient(baseUrl, apiKey || undefined, _currentUserId());
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
      // ---- v0.13.0 — Agent history (append-only audit log) ----
      case 'listAgentHistoryRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'listAgentHistoryResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const payload = await client.listAgentHistory(msg.agentType, msg.limit ?? 50);
          this.post({ type: 'listAgentHistoryResponse', reqId: msg.reqId, ok: true, payload });
        } catch (err) {
          this.post({ type: 'listAgentHistoryResponse', reqId: msg.reqId, ok: false, error: formatError(err) });
        }
        return;
      }
      // ---- v0.13.0 — Agent preview (save-time dry run) ----
      case 'previewAgentRecipeRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'previewAgentRecipeResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const payload = await client.previewAgentRecipe(msg.payload);
          this.post({ type: 'previewAgentRecipeResponse', reqId: msg.reqId, ok: true, payload });
        } catch (err) {
          this.post({ type: 'previewAgentRecipeResponse', reqId: msg.reqId, ok: false, error: formatError(err) });
        }
        return;
      }
      // ---- v0.14.0 — LLM-assisted agent draft + auto-bind ----
      case 'draftAgentRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'draftAgentResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const payload = await client.draftAgent(msg.payload);
          this.post({ type: 'draftAgentResponse', reqId: msg.reqId, ok: true, payload });
        } catch (err) {
          this.post({ type: 'draftAgentResponse', reqId: msg.reqId, ok: false, error: formatError(err) });
        }
        return;
      }
      case 'bindMatchingRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'bindMatchingResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const payload = await client.bindMatchingRecords(msg.agentType, msg.limit ?? 500);
          this.post({ type: 'bindMatchingResponse', reqId: msg.reqId, ok: true, payload });
        } catch (err) {
          this.post({ type: 'bindMatchingResponse', reqId: msg.reqId, ok: false, error: formatError(err) });
        }
        return;
      }
      // ---- v0.13.0 — Resync agent sample embeddings (Migration 0016) ----
      case 'resyncAgentSamplesRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'resyncAgentSamplesResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const payload = await client.resyncAgentSamples(msg.agentType);
          this.post({ type: 'resyncAgentSamplesResponse', reqId: msg.reqId, ok: true, payload });
        } catch (err) {
          this.post({ type: 'resyncAgentSamplesResponse', reqId: msg.reqId, ok: false, error: formatError(err) });
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
      // ---- v0.9.0 Console tab (agent-discovery-console) ----
      case 'recommendAgentsRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'recommendAgentsResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const payload = await client.recommendAgents(msg.q, msg.topK ?? 5);
          this.post({ type: 'recommendAgentsResponse', reqId: msg.reqId, ok: true, payload: payload as any });
        } catch (err) {
          this.post({ type: 'recommendAgentsResponse', reqId: msg.reqId, ok: false, error: formatError(err) });
        }
        return;
      }
      case 'getContextBundleRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'getContextBundleResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const text = await client.getContextBundle(msg.agentType, msg.format);
          this.post({ type: 'getContextBundleResponse', reqId: msg.reqId, ok: true, text });
        } catch (err) {
          this.post({ type: 'getContextBundleResponse', reqId: msg.reqId, ok: false, error: formatError(err) });
        }
        return;
      }
      case 'getSystemPromptRequest': {
        const client = await this.getClient();
        if (!client) {
          this.post({ type: 'getSystemPromptResponse', reqId: msg.reqId, ok: false, error: 'Not connected' });
          return;
        }
        try {
          const text = await client.getSystemPrompt(msg.agentType, msg.baseUrlOverride);
          this.post({ type: 'getSystemPromptResponse', reqId: msg.reqId, ok: true, text });
        } catch (err) {
          this.post({ type: 'getSystemPromptResponse', reqId: msg.reqId, ok: false, error: formatError(err) });
        }
        return;
      }
      case 'copyToClipboardRequest': {
        try {
          await vscode.env.clipboard.writeText(msg.text);
          if (msg.label) {
            vscode.window.setStatusBarMessage(`Copied: ${msg.label}`, 3000);
          }
          this.post({ type: 'copyToClipboardResponse', reqId: msg.reqId, ok: true });
        } catch (err) {
          this.post({ type: 'copyToClipboardResponse', reqId: msg.reqId, ok: false, error: formatError(err) });
        }
        return;
      }
      case 'installMcpConfigRequest': {
        // 진단 — 호스트 도달 즉시 가시 피드백
        vscode.window.setStatusBarMessage(`MCP install start: ${msg.client}`, 4000);
        try {
          const result = await installMcpConfig(
            msg.client,
            msg.baseUrl,
            msg.systemPrompt ?? null,
            msg.agentType ?? null,
          );
          const where = result.configPath || result.shellCommand || '(no path)';
          const promptMsg = result.promptAction === 'created' || result.promptAction === 'updated'
            ? `  · prompt ${result.promptAction} → ${result.promptPath}`
            : result.promptAction === 'manual'
            ? `  · prompt: 수동 복사 필요 (${result.promptPath || ''})`
            : '';
          vscode.window.showInformationMessage(
            `MCP ${msg.client}: ${result.action} → ${where}${promptMsg}`,
          );
          this.post({ type: 'installMcpConfigResponse', reqId: msg.reqId, ok: true, ...result });
        } catch (err) {
          vscode.window.showErrorMessage(`MCP ${msg.client} 설치 실패: ${formatError(err)}`);
          this.post({ type: 'installMcpConfigResponse', reqId: msg.reqId, ok: false, error: formatError(err) });
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
      const client = new ApiClient(url, apiKey || undefined, _currentUserId());
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
    const client = new ApiClient(baseUrl, apiKey || undefined, _currentUserId());
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


// ===========================================================================
// MCP config auto-install (agent-discovery-console / mcp-http-server cycles)
// ===========================================================================
type McpInstallResult = {
  client: string;
  configPath?: string;
  action: 'created' | 'updated' | 'shell';
  shellCommand?: string;
  hint?: string;
  // v0.13.0 — system_prompt 자동 주입 결과.
  promptAction?: 'created' | 'updated' | 'manual' | 'skipped';
  promptPath?: string;
  promptError?: string;
};

async function installMcpConfig(
  client: string,
  baseUrl: string,
  systemPrompt?: string | null,
  agentType?: string | null,
): Promise<McpInstallResult> {
  const fs = await import('node:fs/promises');
  const path = await import('node:path');
  const os = await import('node:os');

  const mcpUrl = baseUrl.replace(/\/+$/, '') + '/mcp/';
  const serverEntry = { url: mcpUrl };
  const prompt = (systemPrompt || '').trim();

  // 결과에 promptAction/promptPath 를 채워줄 헬퍼. systemPrompt 가 비어있으면 skipped.
  const attachPromptResult = async (
    base: McpInstallResult,
    fn: () => Promise<{ action: 'created' | 'updated' | 'manual' | 'skipped'; path?: string; error?: string }>,
  ): Promise<McpInstallResult> => {
    if (!prompt) {
      return { ...base, promptAction: 'skipped' };
    }
    try {
      const r = await fn();
      return { ...base, promptAction: r.action, promptPath: r.path, promptError: r.error };
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      return { ...base, promptAction: 'manual', promptError: msg };
    }
  };

  switch (client) {
    case 'cline': {
      const home = os.homedir();
      const candidates = [
        path.join(home, '.config', 'Code', 'User', 'globalStorage', 'saoudrizwan.claude-dev', 'settings', 'cline_mcp_settings.json'),
        path.join(home, 'Library', 'Application Support', 'Code', 'User', 'globalStorage', 'saoudrizwan.claude-dev', 'settings', 'cline_mcp_settings.json'),
        path.join(home, 'AppData', 'Roaming', 'Code', 'User', 'globalStorage', 'saoudrizwan.claude-dev', 'settings', 'cline_mcp_settings.json'),
      ];
      const target = await pickExistingOrFirst(candidates, fs);
      await fs.mkdir(path.dirname(target), { recursive: true });
      const action = await mergeMcpJson(target, 'aidatahub', serverEntry, fs);
      const base: McpInstallResult = { client, configPath: target, action, hint: 'Cline 의 MCP Servers 에 자동 반영. Cline 패널을 한 번 열어 새 도구 일람을 확인하세요.' };
      return attachPromptResult(base, async () => {
        // Cline 은 VSCode setting ``cline.customInstructions`` (string) 사용.
        const cfg = vscode.workspace.getConfiguration('cline');
        const cur = String(cfg.get('customInstructions') || '');
        const merged = mergePromptBlock(cur, prompt, agentType || 'aidatahub');
        await cfg.update('customInstructions', merged.text, vscode.ConfigurationTarget.Global);
        return { action: merged.action, path: 'VSCode setting: cline.customInstructions (User)' };
      });
    }
    case 'copilot': {
      const ws = vscode.workspace.workspaceFolders?.[0];
      let target: string;
      if (ws) {
        target = path.join(ws.uri.fsPath, '.vscode', 'settings.json');
      } else {
        const home = os.homedir();
        target = path.join(home, '.config', 'Code', 'User', 'settings.json');
      }
      await fs.mkdir(path.dirname(target), { recursive: true });
      const action = await mergeJsonAt(
        target,
        ['chat.mcp.servers', 'aidatahub'],
        serverEntry,
        fs,
      );
      const base: McpInstallResult = { client, configPath: target, action, hint: 'VSCode 를 재시작하거나 Copilot Chat 의 도구 패널을 새로고침하세요.' };
      return attachPromptResult(base, async () => {
        // Copilot Chat 은 array setting ``github.copilot.chat.codeGeneration.instructions``.
        // 각 entry: { text: string } | { file: string }. ``[aidatahub:<agent>]`` 접두로 식별.
        const cfg = vscode.workspace.getConfiguration('github.copilot.chat.codeGeneration');
        const cur = (cfg.get<any[]>('instructions') as any[] | undefined) ?? [];
        const tagged = `[aidatahub:${agentType || 'agent'}]\n` + prompt;
        const next: any[] = [];
        let replaced = false;
        for (const it of cur) {
          if (it && typeof it.text === 'string' && it.text.startsWith('[aidatahub')) {
            if (!replaced) { next.push({ text: tagged }); replaced = true; }
            continue;
          }
          next.push(it);
        }
        if (!replaced) next.push({ text: tagged });
        const target_ = ws ? vscode.ConfigurationTarget.Workspace : vscode.ConfigurationTarget.Global;
        await cfg.update('instructions', next, target_);
        return {
          action: replaced ? 'updated' : 'created',
          path: 'VSCode setting: github.copilot.chat.codeGeneration.instructions (' + (ws ? 'Workspace' : 'User') + ')',
        };
      });
    }
    case 'claude_desktop': {
      const home = os.homedir();
      const candidates = [
        path.join(home, 'Library', 'Application Support', 'Claude', 'claude_desktop_config.json'),
        path.join(home, '.config', 'Claude', 'claude_desktop_config.json'),
        path.join(home, 'AppData', 'Roaming', 'Claude', 'claude_desktop_config.json'),
      ];
      const target = await pickExistingOrFirst(candidates, fs);
      await fs.mkdir(path.dirname(target), { recursive: true });
      const action = await mergeMcpJson(target, 'aidatahub', serverEntry, fs);
      const base: McpInstallResult = { client, configPath: target, action, hint: 'Claude Desktop 을 완전히 종료 후 재시작하세요.' };
      // Claude Desktop 은 prompt 저장 위치가 config 파일에 없음 (Project Instructions UI 만 존재).
      return {
        ...base,
        promptAction: prompt ? 'manual' : 'skipped',
        promptPath: prompt ? 'Claude Desktop → 프로젝트 → Instructions 에 복사 붙여넣기 (자동 주입 경로 없음)' : undefined,
      };
    }
    case 'cursor': {
      const home = os.homedir();
      const target = path.join(home, '.cursor', 'mcp.json');
      await fs.mkdir(path.dirname(target), { recursive: true });
      const action = await mergeMcpJson(target, 'aidatahub', serverEntry, fs);
      const base: McpInstallResult = { client, configPath: target, action, hint: 'Cursor 를 재시작하거나 Settings → MCP 패널을 새로고침하세요.' };
      return attachPromptResult(base, async () => {
        // Cursor: workspace .cursorrules (없으면 user home 폴백).
        const ws = vscode.workspace.workspaceFolders?.[0];
        const rulesPath = ws
          ? path.join(ws.uri.fsPath, '.cursorrules')
          : path.join(os.homedir(), '.cursorrules');
        let cur = '';
        try { cur = await fs.readFile(rulesPath, 'utf-8'); } catch { /* new file */ }
        const merged = mergePromptBlock(cur, prompt, agentType || 'aidatahub');
        await fs.writeFile(rulesPath, merged.text, 'utf-8');
        return { action: merged.action, path: rulesPath };
      });
    }
    case 'claude_code': {
      const cp = await import('node:child_process');
      const cmd = `claude mcp add aidatahub --transport http ${mcpUrl}`;
      await new Promise<void>((resolve, reject) => {
        cp.exec(cmd, { timeout: 10000 }, (err, _stdout, stderr) => {
          if (err) {
            reject(new Error((stderr || err.message).trim()));
            return;
          }
          resolve();
        });
      });
      const base: McpInstallResult = { client, action: 'shell', shellCommand: cmd, hint: 'Claude Code 의 새 세션부터 도구가 인식됩니다.' };
      return attachPromptResult(base, async () => {
        // Claude Code: workspace CLAUDE.md (없으면 ~/.claude/CLAUDE.md).
        const ws = vscode.workspace.workspaceFolders?.[0];
        const mdPath = ws
          ? path.join(ws.uri.fsPath, 'CLAUDE.md')
          : path.join(os.homedir(), '.claude', 'CLAUDE.md');
        await fs.mkdir(path.dirname(mdPath), { recursive: true });
        let cur = '';
        try { cur = await fs.readFile(mdPath, 'utf-8'); } catch { /* new file */ }
        const merged = mergePromptBlock(cur, prompt, agentType || 'aidatahub');
        await fs.writeFile(mdPath, merged.text, 'utf-8');
        return { action: merged.action, path: mdPath };
      });
    }
    case 'gemini': {
      const home = os.homedir();
      const target = path.join(home, '.gemini', 'mcp.json');
      await fs.mkdir(path.dirname(target), { recursive: true });
      const action = await mergeMcpJson(target, 'aidatahub', serverEntry, fs);
      const base: McpInstallResult = { client, configPath: target, action, hint: 'Gemini CLI 를 재시작하세요.' };
      // Gemini CLI 는 표준 prompt 파일 컨벤션 부재 — 수동 안내.
      return {
        ...base,
        promptAction: prompt ? 'manual' : 'skipped',
        promptPath: prompt ? 'Gemini CLI: 표준 system_prompt 저장 위치가 없습니다 — 세션 시작 시 수동 복사' : undefined,
      };
    }
    case 'codex': {
      // OpenAI Codex CLI: ~/.codex/config.toml 의 [mcp_servers.aidatahub] 블록.
      // streamable HTTP 는 url 키로 지정. prompt 는 Codex 가 읽는 AGENTS.md 로.
      const home = os.homedir();
      const target = path.join(home, '.codex', 'config.toml');
      await fs.mkdir(path.dirname(target), { recursive: true });
      const action = await mergeCodexToml(target, mcpUrl, fs);
      const base: McpInstallResult = { client, configPath: target, action, hint: 'Codex CLI 를 재시작하세요 (새 세션부터 도구 인식).' };
      return attachPromptResult(base, async () => {
        // Codex: workspace AGENTS.md (없으면 ~/.codex/AGENTS.md).
        const ws = vscode.workspace.workspaceFolders?.[0];
        const mdPath = ws
          ? path.join(ws.uri.fsPath, 'AGENTS.md')
          : path.join(os.homedir(), '.codex', 'AGENTS.md');
        await fs.mkdir(path.dirname(mdPath), { recursive: true });
        let cur = '';
        try { cur = await fs.readFile(mdPath, 'utf-8'); } catch { /* new file */ }
        const merged = mergePromptBlock(cur, prompt, agentType || 'aidatahub');
        await fs.writeFile(mdPath, merged.text, 'utf-8');
        return { action: merged.action, path: mdPath };
      });
    }
  }
  throw new Error(`unknown client: ${client}`);
}

// v0.13.0 — marker block 기반 prompt 머지. text 또는 cline.customInstructions
// 같은 string blob 어디에든 같은 패턴으로 동작. 기존 block 이 있으면 교체, 없으면 append.
function mergePromptBlock(
  existing: string,
  prompt: string,
  agentType: string,
): { text: string; action: 'created' | 'updated' } {
  const begin = `<!-- aidatahub:system-prompt:${agentType}:begin -->`;
  const end = `<!-- aidatahub:system-prompt:${agentType}:end -->`;
  // 동일 agent 의 block 우선 검색. 없으면 generic ``aidatahub:system-prompt:*:`` 블록도 매치하지 않고,
  // append (여러 agent prompt 공존 가능).
  const block = `${begin}\n${prompt.trim()}\n${end}`;
  const re = new RegExp(
    begin.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') +
      '[\\s\\S]*?' +
      end.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'),
  );
  if (re.test(existing)) {
    return { text: existing.replace(re, block), action: 'updated' };
  }
  const sep = existing.length && !existing.endsWith('\n') ? '\n\n' : (existing.length ? '\n' : '');
  return { text: existing + sep + block + '\n', action: 'created' };
}

async function pickExistingOrFirst(candidates: string[], fs: typeof import('node:fs/promises')): Promise<string> {
  for (const p of candidates) {
    try { await fs.stat(p); return p; } catch { /* not found */ }
  }
  // None exists — pick by platform so first-install writes to the correct OS path.
  const plat = process.platform;
  const preferred =
    plat === 'win32'  ? candidates.find(c => c.includes('AppData')) :
    plat === 'darwin' ? candidates.find(c => c.includes('Library')) :
                        candidates.find(c => c.includes('.config'));
  return preferred ?? candidates[0];
}

/**
 * Codex CLI ``~/.codex/config.toml`` 의 ``[mcp_servers.aidatahub]`` 블록을
 * 머지한다. TOML 파서 없이 해당 테이블 블록만 정규식으로 교체/추가한다.
 * - 기존 블록 있으면 통째로 교체, 없으면 파일 끝에 append.
 * - 다른 ``[mcp_servers.*]`` / 일반 설정은 보존.
 */
async function mergeCodexToml(
  filePath: string,
  mcpUrl: string,
  fs: typeof import('node:fs/promises'),
): Promise<'created' | 'updated'> {
  let existing = '';
  let isNew = false;
  try {
    existing = await fs.readFile(filePath, 'utf-8');
  } catch {
    isNew = true;
  }
  const block =
    `[mcp_servers.aidatahub]\n` +
    `url = "${mcpUrl}"\n`;
  // 기존 [mcp_servers.aidatahub] 테이블 (다음 [ 헤더 또는 EOF 까지) 매치.
  const re = /\[mcp_servers\.aidatahub\][^\[]*/;
  let next: string;
  if (re.test(existing)) {
    next = existing.replace(re, block);
  } else {
    const sep = existing.length && !existing.endsWith('\n') ? '\n\n' : (existing.length ? '\n' : '');
    next = existing + sep + block;
  }
  await fs.writeFile(filePath, next, 'utf-8');
  return isNew ? 'created' : 'updated';
}

/**
 * 최상위 ``mcpServers.<name>`` 에 entry 를 머지한다 (Cline / Claude Desktop / Cursor / Gemini 공통).
 * - 파일 없으면 새로 생성.
 * - 기존 mcpServers 보존.
 */
async function mergeMcpJson(
  filePath: string,
  name: string,
  entry: Record<string, unknown>,
  fs: typeof import('node:fs/promises'),
): Promise<'created' | 'updated'> {
  let existing: any = {};
  let isNew = false;
  try {
    const buf = await fs.readFile(filePath, 'utf-8');
    existing = JSON.parse(buf || '{}');
    if (typeof existing !== 'object' || existing === null) existing = {};
  } catch {
    isNew = true;
  }
  if (!existing.mcpServers || typeof existing.mcpServers !== 'object') {
    existing.mcpServers = {};
  }
  existing.mcpServers[name] = entry;
  await fs.writeFile(filePath, JSON.stringify(existing, null, 2) + '\n', 'utf-8');
  return isNew ? 'created' : 'updated';
}

/**
 * 임의 path (예: ``chat.mcp.servers.aidatahub``) 에 entry 를 머지한다.
 * VSCode settings.json 같이 점-키 경로가 있는 경우.
 */
async function mergeJsonAt(
  filePath: string,
  keyPath: string[],
  entry: Record<string, unknown>,
  fs: typeof import('node:fs/promises'),
): Promise<'created' | 'updated'> {
  let existing: any = {};
  let isNew = false;
  try {
    const buf = await fs.readFile(filePath, 'utf-8');
    existing = JSON.parse(buf || '{}');
    if (typeof existing !== 'object' || existing === null) existing = {};
  } catch {
    isNew = true;
  }
  let node = existing;
  for (let i = 0; i < keyPath.length - 1; i++) {
    const k = keyPath[i];
    if (!node[k] || typeof node[k] !== 'object') node[k] = {};
    node = node[k];
  }
  node[keyPath[keyPath.length - 1]] = entry;
  await fs.writeFile(filePath, JSON.stringify(existing, null, 2) + '\n', 'utf-8');
  return isNew ? 'created' : 'updated';
}

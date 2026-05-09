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
      'AI Data Hub',
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
            `AI Data Hub: uploaded ${msg.recordId ?? 'record'}${status}`,
          );
        } else if (msg.httpStatus === 401) {
          // Auth failed → offer re-entry of API key right from the toast.
          const choice = await vscode.window.showErrorMessage(
            'AI Data Hub: API key invalid (401). Re-enter your key?',
            'Re-enter API key',
            'Cancel',
          );
          if (choice === 'Re-enter API key') {
            await this.promptForApiKey();
          }
        } else {
          const detail = msg.requestId ? ` [request_id=${msg.requestId}]` : '';
          void vscode.window.showErrorMessage(
            `AI Data Hub upload failed: ${msg.error ?? 'unknown error'}${detail}`,
          );
        }
        return;
      }
      case 'promptApiKey': {
        await this.promptForApiKey();
        return;
      }
    }
  }

  /**
   * Prompt the user to re-enter their API key (used after 401 errors).
   * Stores the new key in SecretStorage and notifies the webview.
   */
  private async promptForApiKey(): Promise<void> {
    const newKey = await vscode.window.showInputBox({
      title: 'AI Data Hub — API Key',
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
    const client = new ApiClient(normalizedUrl, apiKey || undefined);
    try {
      const health = await client.health();
      if (health.auth_required && !apiKey) {
        this.post({
          type: 'connection',
          ok: false,
          error: 'This server requires an API key (auth_required=true).',
          health,
        });
        return;
      }
      if (apiKey) {
        await client.verifyKey();
      }
      if (persist) {
        await this.store.setBaseUrl(normalizedUrl);
        if (apiKey) await this.store.setApiKey(apiKey);
        await this.store.setConnected(true);
        const snap = await this.store.snapshot();
        this.post({ type: 'config', ...snap });
      }
      this.post({ type: 'connection', ok: true, health });
    } catch (err) {
      this.post({ type: 'connection', ok: false, error: formatError(err) });
    }
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

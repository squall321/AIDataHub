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
          void vscode.window.showInformationMessage(
            `AI Data Hub: uploaded ${msg.recordId ?? 'record'}`,
          );
        } else {
          void vscode.window.showErrorMessage(
            `AI Data Hub upload failed: ${msg.error ?? 'unknown error'}`,
          );
        }
        return;
      }
    }
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

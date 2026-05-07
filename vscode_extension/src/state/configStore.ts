import * as vscode from 'vscode';

const KEY_BASE_URL = 'aidh.baseUrl';
const KEY_CONNECTED = 'aidh.connected';
const SECRET_API_KEY = 'aidh.apiKey';

export interface ConnectionConfig {
  baseUrl: string;
  hasApiKey: boolean;
  connected: boolean;
}

export class ConfigStore {
  constructor(private readonly context: vscode.ExtensionContext) {}

  getBaseUrl(): string {
    return this.context.globalState.get<string>(KEY_BASE_URL, '');
  }

  async setBaseUrl(value: string): Promise<void> {
    await this.context.globalState.update(KEY_BASE_URL, value);
  }

  isConnected(): boolean {
    return this.context.globalState.get<boolean>(KEY_CONNECTED, false);
  }

  async setConnected(value: boolean): Promise<void> {
    await this.context.globalState.update(KEY_CONNECTED, value);
  }

  async getApiKey(): Promise<string | undefined> {
    return this.context.secrets.get(SECRET_API_KEY);
  }

  async setApiKey(value: string): Promise<void> {
    await this.context.secrets.store(SECRET_API_KEY, value);
  }

  async clearApiKey(): Promise<void> {
    await this.context.secrets.delete(SECRET_API_KEY);
  }

  async snapshot(): Promise<ConnectionConfig> {
    const apiKey = await this.getApiKey();
    return {
      baseUrl: this.getBaseUrl(),
      hasApiKey: Boolean(apiKey && apiKey.length > 0),
      connected: this.isConnected(),
    };
  }

  async reset(): Promise<void> {
    await this.context.globalState.update(KEY_BASE_URL, undefined);
    await this.context.globalState.update(KEY_CONNECTED, undefined);
    await this.clearApiKey();
  }
}

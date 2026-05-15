import * as vscode from 'vscode';

const KEY_BASE_URL = 'aidh.baseUrl';
const KEY_CONNECTED = 'aidh.connected';
const SECRET_API_KEY = 'aidh.apiKey';

/**
 * 첫 사용 시 보이는 기본 URL — 운영 서버의 외부 IP.
 * 사용자가 한 번 입력해 saveConfig 하면 globalState 에 저장되고
 * 그 이후에는 저장된 값이 우선한다.
 */
// 빈 문자열 — 사용자가 자기 서버 URL 을 직접 입력해야 한다 (welcome 화면).
// 과거 사내 개발 IP 가 하드코딩돼 있어 새 환경에서 연결이 잘못 잡혔다.
export const DEFAULT_BASE_URL = '';

export interface ConnectionConfig {
  baseUrl: string;
  hasApiKey: boolean;
  connected: boolean;
}

export class ConfigStore {
  constructor(private readonly context: vscode.ExtensionContext) {}

  getBaseUrl(): string {
    return this.context.globalState.get<string>(KEY_BASE_URL, DEFAULT_BASE_URL);
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

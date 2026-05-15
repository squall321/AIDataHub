import * as vscode from 'vscode';
import { BUILD_DEFAULT_BASE_URL } from './buildDefaults';

const KEY_BASE_URL = 'aidh.baseUrl';
const KEY_CONNECTED = 'aidh.connected';
const SECRET_API_KEY = 'aidh.apiKey';

/**
 * 첫 사용 시 보이는 기본 URL.
 * setup.sh 가 빌드한 vsix 면 그 서버 URL 이 buildDefaults 에 주입돼 있어
 * 대시보드에서 받아 설치하면 바로 연결된다. 수동 빌드면 빈 문자열 →
 * welcome 화면에서 사용자가 입력. saveConfig 후에는 globalState 우선.
 */
export const DEFAULT_BASE_URL = BUILD_DEFAULT_BASE_URL;

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

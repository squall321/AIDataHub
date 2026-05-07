import * as vscode from 'vscode';
import { ConfigStore } from './state/configStore';
import { UploaderPanel } from './webview/panel';

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const store = new ConfigStore(context);

  context.subscriptions.push(
    vscode.commands.registerCommand('aidh.openUploader', () => {
      UploaderPanel.show(context, store);
    }),
    vscode.commands.registerCommand('aidh.openSettings', () => {
      UploaderPanel.show(context, store);
    }),
    vscode.commands.registerCommand('aidh.resetConnection', async () => {
      await store.reset();
      UploaderPanel.show(context, store);
    }),
  );

  if (!store.isConnected()) {
    UploaderPanel.show(context, store);
  }
}

export function deactivate(): void {
  // no-op
}

/**
 * Webview HTML/CSS/JS shell.
 *
 * v0.6.0 — four-tab layout:
 *   - Upload  : original-file ingest (state machine: drop → form → sending → result)
 *   - Bundle  : pre-converted JSON+resources zip ingest
 *   - Search  : semantic / fts / tag search + record detail viewer + discover panel
 *   - Agents  : full agent CRUD (list / view / create / edit / delete)
 *
 * Each tab is a self-contained mini state machine. The "Settings" gear in the
 * header still opens the welcome (connection) screen.
 *
 * Vanilla JS — no bundler, no framework. Webview ↔ Host messages live in
 * `protocol.ts`. We re-encode the message type strings here at runtime since
 * we cannot import TS types into a string-embedded script.
 */

export function renderHtml(): string {
  const nonce = randomNonce();
  const csp =
    `default-src 'none'; ` +
    `style-src 'unsafe-inline'; ` +
    `script-src 'nonce-${nonce}'; ` +
    `connect-src http: https:; ` +
    `img-src data: https: vscode-resource:;`;

  return /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="${csp}" />
  <title>AI Data Hub</title>
  <style>${styles()}</style>
</head>
<body>
  <header>
    <div class="title">AI Data Hub Uploader</div>
    <nav id="tabnav" class="tabnav" style="display:none">
      <button class="tab" data-tab="upload">Upload</button>
      <button class="tab" data-tab="bundle">Bundle</button>
      <button class="tab" data-tab="search">Search</button>
      <button class="tab" data-tab="agents">Agents</button>
    </nav>
    <div class="actions">
      <button id="btn-settings" class="ghost" title="Settings">⚙</button>
    </div>
  </header>

  <main id="root"></main>

  <script nonce="${nonce}">${clientScript()}</script>
</body>
</html>`;
}

function styles(): string {
  return `
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: var(--vscode-font-family);
  color: var(--vscode-foreground);
  background: var(--vscode-editor-background);
  font-size: 13px;
}
header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 18px;
  border-bottom: 1px solid var(--vscode-panel-border, rgba(128,128,128,0.25));
  gap: 16px;
}
header .title { font-weight: 600; font-size: 14px; }
.tabnav { display: flex; gap: 2px; flex: 1; }
.tabnav .tab {
  padding: 6px 14px;
  background: transparent;
  color: var(--vscode-foreground);
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  cursor: pointer;
  font-size: 13px;
  opacity: 0.7;
}
.tabnav .tab:hover { background: rgba(128,128,128,0.08); opacity: 0.95; }
.tabnav .tab.active {
  border-bottom-color: var(--vscode-focusBorder);
  opacity: 1;
  font-weight: 600;
}
main { padding: 24px; max-width: 860px; margin: 0 auto; }
h1 { font-size: 18px; margin: 0 0 8px; }
h2 { font-size: 14px; margin: 18px 0 8px; opacity: 0.85; font-weight: 600; }
h3 { font-size: 13px; margin: 12px 0 6px; opacity: 0.85; font-weight: 600; }
p.subtle { color: var(--vscode-descriptionForeground); margin-top: 0; }

label { display: block; margin: 12px 0 4px; font-size: 12px; opacity: 0.85; }
input, select, textarea {
  width: 100%;
  padding: 6px 8px;
  background: var(--vscode-input-background);
  color: var(--vscode-input-foreground);
  border: 1px solid var(--vscode-input-border, transparent);
  border-radius: 2px;
  font-family: inherit;
  font-size: 13px;
}
textarea { resize: vertical; min-height: 60px; }
input:focus, select:focus, textarea:focus { outline: 1px solid var(--vscode-focusBorder); }

button {
  padding: 6px 14px;
  background: var(--vscode-button-background);
  color: var(--vscode-button-foreground);
  border: none;
  border-radius: 2px;
  cursor: pointer;
  font-size: 13px;
}
button:hover { background: var(--vscode-button-hoverBackground); }
button:disabled { opacity: 0.5; cursor: not-allowed; }
button.secondary {
  background: var(--vscode-button-secondaryBackground);
  color: var(--vscode-button-secondaryForeground);
}
button.ghost { background: transparent; color: var(--vscode-foreground); padding: 4px 8px; }
button.tiny { padding: 2px 8px; font-size: 11px; }

.row { display: flex; gap: 8px; align-items: stretch; }
.row > * { flex: 1; }
.row.tight { gap: 6px; }

.toolbar { display: flex; gap: 8px; margin-top: 16px; flex-wrap: wrap; }

.status { margin-top: 16px; padding: 8px 12px; border-radius: 2px; }
.status.ok  { background: rgba(0,160,0,0.15); color: var(--vscode-testing-iconPassed, #4caf50); }
.status.err { background: rgba(200,0,0,0.15); color: var(--vscode-errorForeground, #f44336); }
.status.warn{ background: rgba(220,150,0,0.15); color: var(--vscode-editorWarning-foreground, #ffb84d); }
.status.info{ background: rgba(50,130,255,0.12); color: var(--vscode-foreground); }

.dropzone {
  margin: 24px 0;
  padding: 48px 24px;
  border: 2px dashed var(--vscode-panel-border, #555);
  border-radius: 6px;
  text-align: center;
  color: var(--vscode-descriptionForeground);
  cursor: pointer;
  transition: all 120ms ease-out;
}
.dropzone.over { border-color: var(--vscode-focusBorder); background: rgba(50,130,255,0.06); }
.dropzone.bad  { border-color: var(--vscode-errorForeground); background: rgba(200,0,0,0.06); }
.dropzone .big { font-size: 32px; margin-bottom: 6px; }

.file-card {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 14px;
  background: rgba(128,128,128,0.06);
  border-radius: 4px;
  margin-bottom: 12px;
}
.file-card .meta { flex: 1; }
.file-card .name { font-weight: 600; }
.file-card .sub  { font-size: 11px; color: var(--vscode-descriptionForeground); margin-top: 2px; }

.chips { display: flex; flex-wrap: wrap; gap: 4px; padding: 4px; min-height: 30px;
         background: var(--vscode-input-background); border: 1px solid var(--vscode-input-border, transparent); border-radius: 2px; }
.chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 2px 8px;
  background: var(--vscode-badge-background, rgba(128,128,128,0.25));
  color: var(--vscode-badge-foreground, inherit);
  border-radius: 10px;
  font-size: 12px;
}
.chip .x { cursor: pointer; opacity: 0.7; }
.chip .x:hover { opacity: 1; }
.chips input { flex: 1; min-width: 80px; border: none; background: transparent; padding: 2px 4px; outline: none; }

.progress {
  width: 100%;
  height: 8px;
  background: rgba(128,128,128,0.18);
  border-radius: 4px;
  overflow: hidden;
}
.progress > div {
  height: 100%;
  width: 0%;
  background: var(--vscode-progressBar-background, var(--vscode-button-background));
  transition: width 120ms ease-out;
}

.field-error { color: var(--vscode-errorForeground); font-size: 11px; margin-top: 2px; min-height: 14px; }
.hint { font-size: 12px; color: var(--vscode-descriptionForeground); margin-top: 12px; }
.muted { color: var(--vscode-descriptionForeground); }
.kv { display: grid; grid-template-columns: 130px 1fr; gap: 4px 12px; font-size: 12px; }
.kv .k { opacity: 0.7; }

details.advanced {
  margin-top: 12px;
  padding: 8px 12px;
  border: 1px solid var(--vscode-panel-border, rgba(128,128,128,0.25));
  border-radius: 3px;
  background: rgba(128,128,128,0.04);
}
details.advanced summary {
  cursor: pointer;
  font-weight: 600;
  font-size: 12px;
  opacity: 0.85;
  padding: 2px 0;
}
details.advanced[open] summary { margin-bottom: 6px; }

/* search results */
.results { margin-top: 12px; }
.result-row {
  padding: 8px 10px;
  border-bottom: 1px solid var(--vscode-panel-border, rgba(128,128,128,0.18));
  cursor: pointer;
}
.result-row:hover { background: rgba(128,128,128,0.06); }
.result-row .top { display: flex; gap: 8px; align-items: baseline; }
.result-row .rid {
  font-family: var(--vscode-editor-font-family, monospace);
  font-size: 11px;
  color: var(--vscode-textLink-foreground);
}
.result-row .score {
  margin-left: auto;
  font-size: 11px;
  color: var(--vscode-descriptionForeground);
}
.result-row .title { font-weight: 600; }
.result-row .snippet { font-size: 12px; color: var(--vscode-descriptionForeground); margin-top: 4px; }
.result-row .tags { margin-top: 4px; }
.result-row .tags .chip { font-size: 10px; padding: 1px 6px; }

.facet-bar { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.facet-pill {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  background: rgba(128,128,128,0.15);
  cursor: pointer;
}
.facet-pill:hover { background: rgba(128,128,128,0.28); }
.facet-pill.active { background: var(--vscode-focusBorder); color: var(--vscode-button-foreground); }

pre.json {
  max-height: 360px; overflow: auto;
  padding: 10px;
  background: rgba(128,128,128,0.08);
  border-radius: 4px;
  font-size: 11px;
  font-family: var(--vscode-editor-font-family, monospace);
  white-space: pre-wrap;
  word-break: break-word;
}

.warn-box {
  padding: 8px 12px;
  border-radius: 3px;
  margin-top: 8px;
  font-size: 12px;
}
.warn-box.miss { background: rgba(200,0,0,0.10); color: var(--vscode-errorForeground); }
.warn-box.extra { background: rgba(220,150,0,0.12); color: var(--vscode-editorWarning-foreground, #ffb84d); }
.warn-box ul { margin: 4px 0 0 16px; padding: 0; }
.warn-box li { font-family: var(--vscode-editor-font-family, monospace); font-size: 11px; }

.discover-card {
  border: 1px solid var(--vscode-panel-border, rgba(128,128,128,0.25));
  border-radius: 4px;
  padding: 12px 14px;
  margin-bottom: 12px;
}
.discover-card .big-num { font-size: 22px; font-weight: 600; }
.discover-card .label { font-size: 11px; opacity: 0.7; }

/* Agents tab table */
.agents-table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 12px;
  font-size: 12px;
}
.agents-table thead th {
  text-align: left;
  font-weight: 600;
  font-size: 11px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--vscode-panel-border, rgba(128,128,128,0.25));
  opacity: 0.75;
}
.agents-table tbody tr {
  border-bottom: 1px solid var(--vscode-panel-border, rgba(128,128,128,0.15));
  cursor: pointer;
}
.agents-table tbody tr:hover { background: rgba(128,128,128,0.06); }
.agents-table tbody tr.expanded { background: rgba(128,128,128,0.08); }
.agents-table td { padding: 8px; vertical-align: top; }
.agents-table td.mono {
  font-family: var(--vscode-editor-font-family, monospace);
  font-size: 11px;
  color: var(--vscode-textLink-foreground);
  white-space: nowrap;
}
.agents-table td.desc {
  max-width: 320px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: var(--vscode-descriptionForeground);
}
.agents-table td .chip { font-size: 10px; padding: 1px 6px; }

.agent-detail {
  padding: 12px 16px;
  background: rgba(128,128,128,0.05);
  border-left: 3px solid var(--vscode-focusBorder);
  margin: 0 0 12px;
}
.agent-detail h3 { margin-top: 0; }

.agent-form {
  border: 1px solid var(--vscode-panel-border, rgba(128,128,128,0.25));
  border-radius: 4px;
  padding: 14px 16px;
  margin-top: 12px;
}
.checkbox-row { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 4px; }
.checkbox-row label { display: inline-flex; align-items: center; gap: 4px; margin: 0; font-size: 12px; opacity: 1; }
.checkbox-row input[type="checkbox"] { width: auto; margin: 0; }

.agents-banner {
  padding: 8px 12px;
  border-radius: 3px;
  margin: 8px 0;
  font-size: 12px;
}
.agents-banner.ok { background: rgba(50,180,90,0.18); color: var(--vscode-foreground); }
.agents-banner.err{ background: rgba(220,60,60,0.18); color: var(--vscode-errorForeground); }
`;
}

function clientScript(): string {
  return `
(function(){
  const vscode = acquireVsCodeApi();
  const root = document.getElementById('root');
  const headerSettingsBtn = document.getElementById('btn-settings');
  const tabnav = document.getElementById('tabnav');

  // ------------------------------------------------------------ Global drag/drop guard
  //
  // VS Code 의 webview 는 기본 상태에서 drag 이벤트를 자체 처리해 파일을
  // 에디터에서 열어버린다. dropzone 밖 영역에서도 drag 가 활성화되지 않으면
  // 사용자가 정확히 dropzone 안으로 드롭하지 않는 한 파일이 가로채진다.
  //
  // 해결: document 전체에 dragenter/dragover/drop 을 잡아 preventDefault.
  //   - dragover: 'copy' effect 를 강제해 dropzone 위에서는 드롭 허용 표시.
  //     dropzone 밖이면 'none' 으로 두지만 어쨌든 preventDefault 해서 VS Code
  //     가 이벤트를 받지 않게 한다.
  //   - drop: dropzone 밖에 떨어지면 silent — VS Code 도, dropzone 도 받지
  //     않음. dropzone 안에 떨어지면 dz 자체의 drop 핸들러가 처리한다
  //     (이벤트 버블링은 dz 의 stopPropagation 으로 차단되지 않으므로
  //     document 핸들러도 함께 호출되지만 dz 가 이미 처리한 후라 무해).
  function _isInsideDropzone(target) {
    let n = target;
    while (n && n !== document) {
      if (n.classList && n.classList.contains('dropzone')) return true;
      n = n.parentNode;
    }
    return false;
  }
  window.addEventListener('dragenter', function(e){ e.preventDefault(); }, false);
  window.addEventListener('dragover', function(e){
    e.preventDefault();
    if (e.dataTransfer) {
      e.dataTransfer.dropEffect = _isInsideDropzone(e.target) ? 'copy' : 'none';
    }
  }, false);
  window.addEventListener('drop', function(e){
    // dropzone 밖에 떨어진 파일은 VS Code 가 받지 못하게만 막고 그 외 무동작.
    if (!_isInsideDropzone(e.target)) {
      e.preventDefault();
    }
    // dropzone 안이면 dz 의 자체 drop 핸들러가 이미 preventDefault 한 상태.
  }, false);

  // ------------------------------------------------------------ State
  const state = {
    tab: 'upload',              // upload | bundle | search | agents
    showWelcome: true,          // forced when not connected
    config: { baseUrl: 'http://110.15.177.125:8000', hasApiKey: false, connected: false },
    options: null,              // MetaOptions
    optionsError: null,
    // Upload tab
    upload: {
      screen: 'drop',           // drop | form | sending | result
      file: null,               // { file: File, dataType: string }
      progress: 0,
      error: null,
      response: null,
      dryRun: false,
      pendingValues: null,
      autoFilled: null,         // 0007 fields fetched after upload
    },
    // Bundle tab
    bundle: {
      screen: 'drop',           // drop | sending | result
      file: null,               // File (.zip)
      progress: 0,
      error: null,
      response: null,
    },
    // Search tab
    search: {
      mode: 'semantic',
      q: '',
      results: null,            // SearchResponse | null
      facets: null,             // FacetedSearchResponse.facets
      filters: {},              // applied facet filters
      error: null,
      loading: false,
      detail: null,             // FullRecord | null
      detailError: null,
      detailLoading: false,
      discover: null,           // DiscoverResponse | null
      discoverError: null,
      discoverLoading: false,
    },
    // Agents tab
    agents: {
      screen: 'list',           // list | form
      editing: null,            // agent_type being edited (null = create-new in form mode)
      expanded: null,           // agent_type expanded inline in list mode
      list: null,               // AgentOutT[] | null
      loading: false,
      error: null,              // string | null — list-level error
      banner: null,             // { kind: 'ok'|'err', text: string } | null
      formValues: {
        agent_type: '',
        name: '',
        description: '',
        common_tags: [],
        data_types: [],
      },
      formError: null,          // form-level error message (string|null)
      saving: false,
      recordsByAgent: {},       // agent_type -> { loading, items, error }
    },
  };

  let _reqIdSeq = 1;
  const _pendingReq = new Map();

  // ------------------------------------------------------------ Helpers
  function send(msg){ vscode.postMessage(msg); }

  function bytesHuman(n){
    if (n < 1024) return n + ' B';
    if (n < 1024*1024) return (n/1024).toFixed(1) + ' KB';
    return (n/(1024*1024)).toFixed(1) + ' MB';
  }

  // Map extension -> data_type label (mirror metadata_spec.md §2)
  function detectDataType(filename){
    const lc = (filename || '').toLowerCase();
    if (lc.endsWith('.docx')) return 'DOC';
    if (lc.endsWith('.pdf'))  return 'DOC';
    if (lc.endsWith('.pptx')) return 'DOC';
    if (lc.endsWith('.md') || lc.endsWith('.markdown')) return 'DOC';
    if (lc.endsWith('.xlsx')) return 'DATA';
    return null;
  }

  function isExtAllowed(filename){
    if (!state.options) return true;          // optimistic until options arrive
    const lc = (filename || '').toLowerCase();
    return state.options.supported_extensions.some(ext => lc.endsWith(ext));
  }

  function setTab(t){ state.tab = t; render(); }

  function rpc(type, extra){
    return new Promise((resolve, reject) => {
      const reqId = _reqIdSeq++;
      _pendingReq.set(reqId, { resolve, reject });
      send(Object.assign({ type, reqId }, extra || {}));
      // 30s timeout safety
      setTimeout(() => {
        if (_pendingReq.has(reqId)) {
          _pendingReq.delete(reqId);
          reject(new Error('Request timed out'));
        }
      }, 30000);
    });
  }

  // ------------------------------------------------------------ Renderer
  function render(){
    root.innerHTML = '';
    if (state.showWelcome) {
      tabnav.style.display = 'none';
      renderWelcome();
      return;
    }
    tabnav.style.display = 'flex';
    paintTabs();
    if (state.tab === 'upload') renderUploadTab();
    else if (state.tab === 'bundle') renderBundleTab();
    else if (state.tab === 'search') renderSearchTab();
    else if (state.tab === 'agents') renderAgentsTab();
  }

  function paintTabs(){
    const tabs = tabnav.querySelectorAll('.tab');
    tabs.forEach(t => t.classList.toggle('active', t.dataset.tab === state.tab));
  }

  // ====================================================================
  // Welcome (Settings)
  // ====================================================================
  function renderWelcome(){
    const wrap = el('div');
    wrap.innerHTML = \`
      <h1>👋 Connect to your AI Data Hub server</h1>
      <p class="subtle">Enter your backend URL and API key. The key is stored in VS Code SecretStorage.</p>
      <label>Server URL</label>
      <input id="i-url" type="text" placeholder="http://110.15.177.125:8000" value="\${escapeHtml(state.config.baseUrl)}" />
      <label>API Key (leave empty if backend has AUTH_REQUIRED=false)</label>
      <input id="i-key" type="password" placeholder="••••••••••••" />
      <div class="toolbar">
        <button id="btn-test">Test Connection</button>
        <button id="btn-save" class="secondary">Save &amp; Continue</button>
      </div>
      <div id="status" class="status info" style="display:none"></div>
      <p class="hint">Backend endpoints used: <code>/api/system/health</code>, <code>/api/auth/keys/verify</code>, <code>/api/meta/options</code>.</p>
    \`;
    root.appendChild(wrap);

    on('btn-test', 'click', () => doConnect(false));
    on('btn-save', 'click', () => doConnect(true));
  }

  function doConnect(persist){
    const baseUrl = document.getElementById('i-url').value.trim();
    const apiKey  = document.getElementById('i-key').value;
    setStatus('info', 'Connecting…');
    send({ type: persist ? 'saveConfig' : 'testConnection', baseUrl, apiKey });
  }

  function setStatus(kind, text){
    const s = document.getElementById('status');
    if (!s) return;
    s.className = 'status ' + kind;
    s.textContent = text;
    s.style.display = text ? 'block' : 'none';
  }

  // ====================================================================
  // UPLOAD tab — original-file ingest (drop → form → sending → result)
  // ====================================================================
  function renderUploadTab(){
    const sc = state.upload.screen;
    if (sc === 'drop')    return renderUploadDrop();
    if (sc === 'form')    return renderUploadForm();
    if (sc === 'sending') return renderUploadSending();
    if (sc === 'result')  return renderUploadResult();
  }
  function goUpload(screen){ state.upload.screen = screen; render(); }

  function renderUploadDrop(){
    const wrap = el('div');
    const supportedHint = state.options ? state.options.supported_extensions.join(' · ') : '.docx · .pdf · .pptx · .md · .xlsx';
    const maxMb = state.options ? state.options.max_upload_mb : 50;
    wrap.innerHTML = \`
      <h1>Drop a file to upload</h1>
      <div id="dropzone" class="dropzone">
        <div class="big">📥</div>
        <div>Drop a file here, or <a href="#" id="pick">browse…</a></div>
        <div class="hint">\${escapeHtml(supportedHint)} · max \${maxMb} MB</div>
      </div>
      <input id="picker" type="file" style="display:none" />
      <p class="hint">Connected to: <code>\${escapeHtml(state.config.baseUrl)}</code>\${state.optionsError ? ' — options unavailable: ' + escapeHtml(state.optionsError) : ''}</p>
    \`;
    root.appendChild(wrap);

    const dz = document.getElementById('dropzone');
    const picker = document.getElementById('picker');
    document.getElementById('pick').addEventListener('click', (e)=>{ e.preventDefault(); picker.click(); });
    picker.addEventListener('change', () => { if (picker.files && picker.files[0]) acceptUploadFile(picker.files[0]); });

    dz.addEventListener('dragover', (e) => {
      e.preventDefault();
      const f = e.dataTransfer && e.dataTransfer.items && e.dataTransfer.items[0];
      const name = f && f.getAsFile ? (f.getAsFile() || {}).name : '';
      dz.classList.toggle('bad', name && !isExtAllowed(name));
      dz.classList.toggle('over', !dz.classList.contains('bad'));
    });
    dz.addEventListener('dragleave', () => { dz.classList.remove('over'); dz.classList.remove('bad'); });
    dz.addEventListener('drop', (e) => {
      e.preventDefault();
      e.stopPropagation();
      dz.classList.remove('over'); dz.classList.remove('bad');
      handleDroppedDataTransfer(e.dataTransfer, 'upload');
    });
  }

  // ====================================================================
  // Drag-drop fallback (VS Code webview 가 dataTransfer.files 를 비울 때)
  // ====================================================================
  function _decodeBase64ToBlob(b64, mime){
    const bin = atob(b64);
    const len = bin.length;
    const buf = new Uint8Array(len);
    for (let i = 0; i < len; i++) buf[i] = bin.charCodeAt(i);
    return new Blob([buf], { type: mime || 'application/octet-stream' });
  }

  function _blobToFile(blob, name){
    // File 생성자가 일부 환경에서 안 되면 Blob 에 name 만 붙여 반환.
    try { return new File([blob], name, { type: blob.type }); }
    catch { blob.name = name; return blob; }
  }

  function _extractPathFromDataTransfer(dt){
    if (!dt) return null;
    const types = dt.types ? Array.from(dt.types) : [];
    console.log('[aidh] dataTransfer types:', types);
    // 1. text/uri-list 가 가장 표준적
    let raw = '';
    try { raw = dt.getData('text/uri-list') || ''; } catch {}
    if (!raw) { try { raw = dt.getData('text/plain') || ''; } catch {} }
    if (!raw) return null;
    // uri-list is multiline, take first non-comment line.
    // NOTE: this code lives inside a backtick template literal, so any
    // backslash-r or backslash-n in source is processed at TS compile time.
    // Keep the regex escaped as double-backslash (or split on the literal char).
    const first = raw.split(/\\r?\\n/).map(function(s){ return s.trim(); }).find(function(s){ return s && s.charAt(0) !== '#'; });
    if (!first) return null;
    return first;
  }

  async function handleDroppedDataTransfer(dt, target){
    const dzId = target === 'bundle' ? 'bdz' : 'dropzone';
    const filesLen = (dt && dt.files) ? dt.files.length : 0;
    const itemsLen = (dt && dt.items) ? dt.items.length : 0;
    console.log('[aidh] drop event — files:', filesLen, 'items:', itemsLen);

    // (A) 정상 경로 — files 배열이 채워져 있음 (Chrome / 일부 VS Code 환경).
    let f = filesLen > 0 ? dt.files[0] : null;
    if (!f && itemsLen > 0 && dt.items) {
      for (let i = 0; i < dt.items.length; i++) {
        const it = dt.items[i];
        if (it && it.kind === 'file') {
          const got = it.getAsFile && it.getAsFile();
          if (got && got.size > 0) { f = got; break; }
        }
      }
    }
    if (f && f.size > 0) {
      if (target === 'bundle') acceptBundleFile(f);
      else acceptUploadFile(f);
      return;
    }

    // (B) 경로 폴백 — VS Code 가 text/uri-list 또는 text/plain 으로
    //     file:///... URI 를 전달하는 경우 (가장 흔함, Windows Explorer 드래그).
    const path = _extractPathFromDataTransfer(dt);
    if (path) {
      flashDropToast(dzId, '경로 받음 — 호스트에서 읽는 중…', 'ok');
      try {
        const loaded = await rpc('loadDroppedPath', { target, path });
        if (loaded && loaded.contentBase64) {
          const blob = _decodeBase64ToBlob(loaded.contentBase64, loaded.mimeType);
          const file = _blobToFile(blob, loaded.filename || 'file');
          if (target === 'bundle') acceptBundleFile(file);
          else acceptUploadFile(file);
          return;
        }
      } catch (err) {
        console.warn('[aidh] loadDroppedPath failed:', err);
      }
    }

    // (C) 최후 폴백 — OS 네이티브 파일 picker.
    flashDropToast(dzId, '드래그 데이터 없음 — 파일 선택 창을 엽니다…', 'bad');
    try {
      const loaded = await rpc('openFilePicker', { target });
      if (loaded && loaded.contentBase64) {
        const blob = _decodeBase64ToBlob(loaded.contentBase64, loaded.mimeType);
        const file = _blobToFile(blob, loaded.filename || 'file');
        if (target === 'bundle') acceptBundleFile(file);
        else acceptUploadFile(file);
      }
    } catch (err) {
      console.warn('[aidh] openFilePicker failed:', err);
      flashDropToast(dzId, '파일 선택 실패: ' + (err && err.message ? err.message : err), 'bad');
    }
  }

  function acceptUploadFile(file){
    console.log('[aidh] acceptUploadFile:', file.name, file.size, file.type);
    if (!isExtAllowed(file.name)) {
      flashDropToast('dropzone', '지원하지 않는 형식: ' + file.name, 'bad');
      return;
    }
    const dataType = detectDataType(file.name) || 'OTHER';
    state.upload.file = { file: file, dataType: dataType };
    state.upload.progress = 0;
    state.upload.error = null;
    state.upload.response = null;
    state.upload.autoFilled = null;
    flashDropToast('dropzone', '✓ 받았습니다: ' + file.name + ' — 폼으로 이동…', 'ok');
    setTimeout(() => goUpload('form'), 250);
  }

  function flashDropBad(id, msg){
    flashDropToast(id, msg, 'bad');
  }

  // 800ms 짜리 inline toast — dropzone 안에 뜨는 작은 알림.
  function flashDropToast(id, msg, kind){
    const dz = document.getElementById(id);
    if (!dz) { console.warn('[aidh] toast: no element', id, msg); return; }
    let t = dz.querySelector('.dz-toast');
    if (!t) {
      t = document.createElement('div');
      t.className = 'dz-toast';
      t.style.cssText = 'position:absolute;left:50%;bottom:8px;transform:translateX(-50%);padding:6px 14px;border-radius:6px;font-size:12px;z-index:10;';
      // dropzone 을 상대 위치로 만들어서 toast 가 안에 뜨게.
      if (getComputedStyle(dz).position === 'static') dz.style.position = 'relative';
      dz.appendChild(t);
    }
    t.textContent = msg;
    if (kind === 'ok') {
      t.style.background = 'rgba(50,180,90,0.18)';
      t.style.border = '1px solid rgba(50,180,90,0.6)';
      t.style.color = 'var(--vscode-foreground)';
      dz.classList.remove('bad');
    } else {
      t.style.background = 'rgba(220,60,60,0.18)';
      t.style.border = '1px solid rgba(220,60,60,0.6)';
      t.style.color = 'var(--vscode-foreground)';
      dz.classList.add('bad');
      setTimeout(()=>dz.classList.remove('bad'), 900);
    }
    if (t._fadeT) clearTimeout(t._fadeT);
    t._fadeT = setTimeout(() => { if (t.parentNode) t.parentNode.removeChild(t); }, 1800);
    console[kind === 'ok' ? 'log' : 'warn']('[aidh] toast:', msg);
  }

  function renderUploadForm(){
    if (!state.options) {
      const wrap = el('div');
      wrap.innerHTML = '<p class="muted">Loading metadata options…</p>';
      root.appendChild(wrap);
      send({ type: 'fetchOptions' });
      return;
    }
    const f = state.upload.file.file;
    const dt = state.upload.file.dataType;
    const opts = state.options;

    const wrap = el('div');
    wrap.innerHTML = \`
      <div class="file-card">
        <div class="meta">
          <div class="name">📂 \${escapeHtml(f.name)}</div>
          <div class="sub">\${dt} · \${bytesHuman(f.size)}</div>
        </div>
        <button id="btn-remove" class="secondary">Remove</button>
      </div>

      <h2>Identification</h2>
      <div class="row">
        <div>
          <label>Team *</label>
          <select id="i-team">\${selectOptions(opts.teams, '')}</select>
          <div id="e-team" class="field-error"></div>
        </div>
        <div>
          <label>Group *</label>
          <select id="i-group"><option value="">— pick team —</option></select>
          <div id="e-group" class="field-error"></div>
        </div>
        <div>
          <label>Year *</label>
          <input id="i-year" type="number" min="1990" max="2100" value="\${new Date().getFullYear()}" />
          <div id="e-year" class="field-error"></div>
        </div>
        <div>
          <label>Seq *</label>
          <input id="i-seq" type="number" min="1" max="999999" value="1" />
          <div id="e-seq" class="field-error"></div>
        </div>
      </div>

      <h2>Discoverability</h2>
      <label>Tags</label>
      <div id="chips-tags" class="chips"></div>
      <label>Agent scope (compatible with \${dt})</label>
      <select id="i-agent-add"><option value="">— add agent —</option>
        \${opts.agents.filter(a => !a.data_types || a.data_types.length === 0 || a.data_types.includes(dt))
                     .map(a => '<option value="'+escapeHtml(a.agent_type)+'">'+escapeHtml(a.name)+' ('+escapeHtml(a.agent_type)+')</option>').join('')}
      </select>
      <div id="chips-agents" class="chips" style="margin-top:6px"></div>

      <h2>Override (optional)</h2>
      <label>Title (leave empty to use auto-extract)</label>
      <input id="i-title" type="text" />
      <label>Summary</label>
      <textarea id="i-summary"></textarea>

      <details class="advanced" id="adv-section">
        <summary>Advanced metadata (Migration 0006) — classification, lifecycle, provenance</summary>

        <h3>Classification &amp; lifecycle</h3>
        <div class="row">
          <div>
            <label>Classification</label>
            <select id="i-classification">\${selectOptions(opts.classifications, 'internal')}</select>
          </div>
          <div>
            <label>Status</label>
            <select id="i-status">\${selectOptions(opts.statuses, 'draft')}</select>
          </div>
          <div>
            <label>Domain</label>
            <input id="i-domain" type="text" placeholder="e.g. battery, iga" />
          </div>
          <div>
            <label>Language</label>
            <select id="i-language">
              <option value="">auto</option>
              \${opts.languages.map(v => '<option value="'+escapeHtml(v)+'">'+escapeHtml(v)+'</option>').join('')}
            </select>
          </div>
        </div>

        <h3>Provenance</h3>
        <div class="row">
          <div>
            <label>Derivation</label>
            <select id="i-derivation">\${selectOptions(opts.derivations, 'original')}</select>
          </div>
          <div>
            <label>Source system</label>
            <input id="i-source-system" type="text" placeholder="e.g. confluence, sharepoint" />
          </div>
          <div>
            <label>Parent record ID</label>
            <input id="i-parent" type="text" placeholder="DOC-..." />
          </div>
        </div>

        <label>Subject keywords (comma-separated)</label>
        <div id="chips-subject" class="chips"></div>

        <h3>Quality &amp; validity</h3>
        <div class="row">
          <div>
            <label>Quality score (0–100)</label>
            <input id="i-quality" type="number" min="0" max="100" />
          </div>
          <div>
            <label>Valid from</label>
            <input id="i-valid-from" type="date" />
          </div>
          <div>
            <label>Valid until</label>
            <input id="i-valid-until" type="date" />
          </div>
        </div>
      </details>

      <div class="toolbar">
        <button id="btn-send">Send to Backend</button>
        <button id="btn-dryrun" class="secondary" title="Run converter only — no DB write">Send DRY-RUN</button>
      </div>
      <div id="form-status" class="status err" style="display:none"></div>
    \`;
    root.appendChild(wrap);

    // Wire chip inputs
    const tagsState = makeChips('chips-tags', 'add tag…');
    const agentsState = makeChipsFromSelect('chips-agents', 'i-agent-add');
    const subjectState = makeChips('chips-subject', 'add keyword…');

    // Team -> Group cascade
    const teamEl = document.getElementById('i-team');
    const groupEl = document.getElementById('i-group');
    function refillGroups(){
      const tm = teamEl.value;
      const list = (opts.groups[tm] || []);
      groupEl.innerHTML = '<option value="">—</option>' + list.map(t => '<option value="'+escapeHtml(t)+'">'+escapeHtml(t)+'</option>').join('');
    }
    teamEl.addEventListener('change', refillGroups);

    on('btn-remove', 'click', () => { state.upload.file = null; goUpload('drop'); });
    on('btn-send', 'click', () => {
      const values = collectForm({ tagsState, agentsState, subjectState });
      const errors = validateForm(values, opts);
      paintErrors(errors);
      if (errors.size > 0) return;
      state.upload.dryRun = false;
      goUpload('sending');
      startUpload(values);
    });
    on('btn-dryrun', 'click', () => {
      const values = collectForm({ tagsState, agentsState, subjectState });
      const errors = validateForm(values, opts);
      paintErrors(errors);
      if (errors.size > 0) return;
      state.upload.dryRun = true;
      goUpload('sending');
      startUpload(values);
    });
  }

  function makeChips(containerId, placeholder){
    const c = document.getElementById(containerId);
    const items = [];
    const input = document.createElement('input');
    input.type = 'text';
    input.placeholder = placeholder;
    c.appendChild(input);

    function repaint(){
      [...c.querySelectorAll('.chip')].forEach(n => n.remove());
      items.forEach((v, i) => {
        const chip = document.createElement('span');
        chip.className = 'chip';
        chip.innerHTML = escapeHtml(v) + ' <span class="x" data-i="'+i+'">✕</span>';
        c.insertBefore(chip, input);
        chip.querySelector('.x').addEventListener('click', () => { items.splice(i, 1); repaint(); });
      });
    }

    input.addEventListener('keydown', (e) => {
      if ((e.key === 'Enter' || e.key === ',') && input.value.trim()) {
        e.preventDefault();
        const v = input.value.trim().replace(/,$/, '');
        if (v && !items.includes(v)) { items.push(v); repaint(); }
        input.value = '';
      } else if (e.key === 'Backspace' && !input.value && items.length) {
        items.pop(); repaint();
      }
    });

    return { get: () => items.slice() };
  }

  function makeChipsFromSelect(containerId, selectId){
    const c = document.getElementById(containerId);
    const sel = document.getElementById(selectId);
    const items = [];
    function repaint(){
      c.innerHTML = '';
      items.forEach((v, i) => {
        const chip = document.createElement('span');
        chip.className = 'chip';
        chip.innerHTML = escapeHtml(v) + ' <span class="x">✕</span>';
        chip.querySelector('.x').addEventListener('click', () => { items.splice(i,1); repaint(); });
        c.appendChild(chip);
      });
    }
    sel.addEventListener('change', () => {
      const v = sel.value;
      if (v && !items.includes(v)) { items.push(v); repaint(); }
      sel.value = '';
    });
    return { get: () => items.slice() };
  }

  function collectForm(chips){
    return {
      team: val('i-team'),
      group: val('i-group'),
      year: parseInt(val('i-year') || '0', 10),
      seq:  parseInt(val('i-seq')  || '0', 10),
      classification: val('i-classification'),
      status: val('i-status'),
      domain: val('i-domain'),
      language: val('i-language'),
      tags: chips.tagsState.get(),
      agents: chips.agentsState.get(),
      subject_keywords: chips.subjectState.get(),
      title_override: val('i-title'),
      summary_override: val('i-summary'),
      quality_score: val('i-quality') === '' ? null : parseInt(val('i-quality'), 10),
      derivation: val('i-derivation'),
      valid_from: val('i-valid-from'),
      valid_until: val('i-valid-until'),
      source_system: val('i-source-system'),
      parent_record_id: val('i-parent'),
    };
  }

  function validateForm(v, opts){
    const errors = new Map();
    if (!v.team)  errors.set('team', 'Required');
    if (!v.group) errors.set('group', 'Required');
    if (!Number.isFinite(v.year) || v.year < 1990 || v.year > 2100) errors.set('year', '1990–2100');
    if (!Number.isFinite(v.seq) || v.seq < 1 || v.seq > 999999) errors.set('seq', '1–999999');
    if (v.quality_score !== null && (v.quality_score < 0 || v.quality_score > 100)) errors.set('quality', '0–100');
    if (v.valid_from && v.valid_until && v.valid_from > v.valid_until) errors.set('valid', 'from > until');
    if (state.upload.file && opts.max_upload_mb && state.upload.file.file.size > opts.max_upload_mb * 1024 * 1024) {
      errors.set('file', 'File exceeds max ' + opts.max_upload_mb + ' MB');
    }
    return errors;
  }

  function paintErrors(errors){
    ['team','group','year','seq'].forEach(k => {
      const e = document.getElementById('e-'+k);
      if (e) e.textContent = errors.get(k) || '';
    });
    const fs = document.getElementById('form-status');
    if (errors.size === 0) { fs.style.display='none'; fs.textContent=''; return; }
    const lines = [];
    if (errors.get('quality')) lines.push('Quality score: ' + errors.get('quality'));
    if (errors.get('valid'))   lines.push('Valid range: ' + errors.get('valid'));
    if (errors.get('file'))    lines.push(errors.get('file'));
    fs.textContent = lines.length ? lines.join(' · ') : 'Please fix the highlighted fields.';
    fs.style.display = 'block';
  }

  function renderUploadSending(){
    const wrap = el('div');
    wrap.innerHTML = \`
      <h1>Sending…</h1>
      <div class="file-card">
        <div class="meta">
          <div class="name">📂 \${escapeHtml(state.upload.file.file.name)}</div>
          <div class="sub">\${state.upload.file.dataType} · \${bytesHuman(state.upload.file.file.size)}</div>
        </div>
      </div>
      <div class="progress"><div id="bar"></div></div>
      <p id="pct" class="muted" style="margin-top:8px">0%</p>
      <div class="toolbar">
        <button id="btn-cancel" class="secondary">Cancel</button>
      </div>
    \`;
    root.appendChild(wrap);
    on('btn-cancel', 'click', () => { if (state.upload.xhr) state.upload.xhr.abort(); });
  }

  function startUpload(values){
    state.upload.pendingValues = values;
    state.upload.kind = 'file';
    send({ type: 'requestUploadCredentials' });
  }

  function performUpload(values, baseUrl, apiKey){
    const fd = new FormData();
    const u = state.upload;
    fd.append('file', u.file.file, u.file.file.name);
    fd.append('team', values.team);
    fd.append('group', values.group);
    fd.append('year', String(values.year));
    fd.append('seq',  String(values.seq));
    fd.append('classification', values.classification || 'internal');
    fd.append('status', values.status || 'draft');
    if (values.domain) fd.append('domain', values.domain);
    if (values.language && values.language !== 'auto') fd.append('language', values.language);
    if (values.tags.length)             fd.append('tags', values.tags.join(','));
    // server convention: form field stays "agents" (rename only the UI label)
    if (values.agents.length)           fd.append('agents', values.agents.join(','));
    if (values.subject_keywords.length) fd.append('subject_keywords', values.subject_keywords.join(','));
    if (values.title_override)   fd.append('title_override', values.title_override);
    if (values.summary_override) fd.append('summary_override', values.summary_override);
    if (values.derivation) fd.append('derivation', values.derivation);
    if (values.quality_score !== null && values.quality_score !== undefined) fd.append('quality_score', String(values.quality_score));
    if (values.valid_from)  fd.append('valid_from', values.valid_from);
    if (values.valid_until) fd.append('valid_until', values.valid_until);
    // Migration 0006 extended fields — server may not yet accept these on /api/convert/ingest;
    // we still send them so they round-trip when the schema catches up.
    if (values.source_system)    fd.append('source_system', values.source_system);
    if (values.parent_record_id) fd.append('parent_record_id', values.parent_record_id);

    const path = u.dryRun ? '/api/convert/' : '/api/convert/ingest';
    const url  = baseUrl.replace(/\\/+$/, '') + path;
    const xhr = new XMLHttpRequest();
    u.xhr = xhr;
    xhr.open('POST', url, true);
    if (apiKey) xhr.setRequestHeader('X-API-Key', apiKey);
    xhr.responseType = 'text';

    xhr.upload.addEventListener('progress', (e) => {
      if (!e.lengthComputable) return;
      const pct = Math.round((e.loaded / e.total) * 100);
      const bar = document.getElementById('bar');
      const p   = document.getElementById('pct');
      if (bar) bar.style.width = pct + '%';
      if (p) p.textContent = pct + '%';
    });

    xhr.addEventListener('load', () => {
      let body;
      try { body = JSON.parse(xhr.responseText); } catch { body = null; }
      if (xhr.status >= 200 && xhr.status < 300 && body) {
        u.response = body;
        u.error = null;
        if (!u.dryRun) {
          send({ type: 'uploadResult', ok: true, recordId: body.record_id, status: body.status });
          // Fetch full record so we can show auto-filled 0007 fields.
          if (body.record_id) {
            rpc('getRecordRequest', { id: body.record_id })
              .then((rec) => { state.upload.autoFilled = rec; render(); })
              .catch(() => { /* ignore — show what we have */ });
          }
        }
      } else {
        const code = body && body.error && body.error.code ? body.error.code : ('HTTP_' + xhr.status);
        const msg  = body && body.error && body.error.message
                       ? body.error.message
                       : (body && body.detail ? (typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)) : (xhr.responseText || xhr.statusText));
        const requestId = body && body.error && body.error.request_id ? body.error.request_id : undefined;
        u.error = { code, message: msg, requestId, httpStatus: xhr.status };
        u.response = null;
        send({
          type: 'uploadResult',
          ok: false,
          httpStatus: xhr.status,
          requestId,
          error: '['+code+'] '+msg,
        });
      }
      goUpload('result');
    });
    xhr.addEventListener('error', () => {
      u.error = { code: 'NETWORK', message: 'Network error' };
      send({ type: 'uploadResult', ok: false, error: 'Network error' });
      goUpload('result');
    });
    xhr.addEventListener('abort', () => {
      u.error = { code: 'ABORTED', message: 'Cancelled by user' };
      goUpload('drop');
    });

    xhr.send(fd);
  }

  function renderUploadResult(){
    const wrap = el('div');
    const u = state.upload;
    if (u.response && u.dryRun) {
      const safe = escapeHtml(JSON.stringify(u.response, null, 2));
      wrap.innerHTML = \`
        <h1>🔬 DRY-RUN preview</h1>
        <p class="muted">Converter ran successfully. Nothing was written to the database.</p>
        <pre class="json">\${safe}</pre>
        <div class="toolbar">
          <button id="btn-back-form" class="secondary">Back to form</button>
          <button id="btn-again">Start over</button>
        </div>
      \`;
    } else if (u.response) {
      const r = u.response;
      const af = state.upload.autoFilled;
      const autoBlock = af ? \`
        <h2>Agent discovery hints (Migration 0007)</h2>
        <div class="kv">
          <div class="k">agent_hints</div><div>\${af.agent_hints ? escapeHtml(String(af.agent_hints)) : '<span class="muted">— (auto-fill pending)</span>'}</div>
          <div class="k">query_examples</div><div>\${Array.isArray(af.query_examples) && af.query_examples.length
                ? '<ul style="margin:0;padding-left:18px">' + af.query_examples.map(q => '<li>'+escapeHtml(String(q))+'</li>').join('') + '</ul>'
                : '<span class="muted">—</span>'}</div>
          <div class="k">access_pattern</div><div>\${escapeHtml(String(af.access_pattern || 'occasional'))}</div>
        </div>
      \` : '<p class="muted" style="margin-top:8px">Loading auto-filled hints…</p>';
      wrap.innerHTML = \`
        <h1>✅ Uploaded</h1>
        <div class="kv">
          <div class="k">Record ID</div><div><code>\${escapeHtml(r.record_id)}</code></div>
          <div class="k">Status</div><div>\${escapeHtml(r.status)}</div>
          <div class="k">Sections</div><div>\${r.sections_written}</div>
          <div class="k">Title</div><div>\${escapeHtml(r.record.title)}</div>
        </div>
        \${autoBlock}
        <div class="toolbar">
          <button id="btn-again">Upload Another</button>
          <button id="btn-view-record" class="secondary">View record</button>
        </div>
      \`;
    } else {
      const e = u.error || { code: 'UNKNOWN', message: 'Unknown error' };
      const is401 = e.httpStatus === 401 || (e.code || '').toUpperCase().indexOf('API_KEY') !== -1;
      const reqId = e.requestId ? \`<div class="k">Request ID</div><div><code>\${escapeHtml(e.requestId)}</code></div>\` : '';
      wrap.innerHTML = \`
        <h1>❌ Upload failed</h1>
        <div class="kv">
          <div class="k">Code</div><div><code>\${escapeHtml(e.code)}</code></div>
          <div class="k">Reason</div><div>\${escapeHtml(e.message)}</div>
          \${reqId}
        </div>
        <div class="toolbar">
          \${is401 ? '<button id="btn-reauth">Re-enter API Key</button>' : ''}
          <button id="btn-again" class="secondary">Back</button>
        </div>
      \`;
    }
    root.appendChild(wrap);
    on('btn-again',     'click', () => { state.upload.file = null; state.upload.autoFilled = null; goUpload('drop'); });
    on('btn-back-form', 'click', () => goUpload('form'));
    on('btn-reauth',    'click', () => send({ type: 'promptApiKey' }));
    on('btn-view-record','click', () => {
      const r = state.upload.response;
      if (r && r.record_id) {
        state.tab = 'search';
        state.search.detail = null;
        state.search.detailLoading = true;
        render();
        rpc('getRecordRequest', { id: r.record_id })
          .then((rec) => { state.search.detail = rec; state.search.detailLoading = false; render(); })
          .catch((err) => { state.search.detailError = String(err.message || err); state.search.detailLoading = false; render(); });
      }
    });
  }

  // ====================================================================
  // BUNDLE tab — POST /api/ingest/bundle (zip)
  // ====================================================================
  function renderBundleTab(){
    const sc = state.bundle.screen;
    if (sc === 'drop')    return renderBundleDrop();
    if (sc === 'sending') return renderBundleSending();
    if (sc === 'result')  return renderBundleResult();
  }
  function goBundle(s){ state.bundle.screen = s; render(); }

  function renderBundleDrop(){
    const wrap = el('div');
    wrap.innerHTML = \`
      <h1>Upload a pre-converted bundle</h1>
      <p class="subtle">Drop a <code>.zip</code> containing the converter's JSON output and resource folder. The JSON inside the zip carries all metadata — no form needed.</p>
      <div id="bdz" class="dropzone">
        <div class="big">📦</div>
        <div>Drop a <code>.zip</code> bundle here, or <a href="#" id="bpick">browse…</a></div>
        <div class="hint">Bundle layout: <code>record.json</code> + resource files, or <code>{doc_id}.json</code> + <code>{doc_id}/</code> folder.</div>
      </div>
      <input id="bpicker" type="file" accept=".zip,application/zip" style="display:none" />
      <p class="hint">Endpoint: <code>POST /api/ingest/bundle</code> — only <code>.zip</code> is accepted client-side. Folders cannot be auto-zipped without an extra dependency.</p>
    \`;
    root.appendChild(wrap);

    const dz = document.getElementById('bdz');
    const picker = document.getElementById('bpicker');
    document.getElementById('bpick').addEventListener('click', (e) => { e.preventDefault(); picker.click(); });
    picker.addEventListener('change', () => { if (picker.files && picker.files[0]) acceptBundleFile(picker.files[0]); });

    dz.addEventListener('dragover', (e) => {
      e.preventDefault();
      const it = e.dataTransfer && e.dataTransfer.items && e.dataTransfer.items[0];
      const name = it && it.getAsFile ? (it.getAsFile() || {}).name : '';
      const ok = !name || name.toLowerCase().endsWith('.zip');
      dz.classList.toggle('bad', !ok);
      dz.classList.toggle('over', ok);
    });
    dz.addEventListener('dragleave', () => { dz.classList.remove('over'); dz.classList.remove('bad'); });
    dz.addEventListener('drop', (e) => {
      e.preventDefault();
      e.stopPropagation();
      dz.classList.remove('over'); dz.classList.remove('bad');
      handleDroppedDataTransfer(e.dataTransfer, 'bundle');
    });
  }

  function acceptBundleFile(file){
    console.log('[aidh] acceptBundleFile:', file.name, file.size);
    if (!file.name.toLowerCase().endsWith('.zip')) {
      flashDropToast('bdz', 'Bundle must be a .zip file: ' + file.name, 'bad');
      return;
    }
    state.bundle.file = file;
    state.bundle.progress = 0;
    state.bundle.error = null;
    state.bundle.response = null;
    state.upload.kind = 'bundle';
    flashDropToast('bdz', '✓ 받았습니다: ' + file.name + ' — 업로드 시작…', 'ok');
    setTimeout(() => {
      goBundle('sending');
      send({ type: 'requestUploadCredentials' });
    }, 250);
  }

  function renderBundleSending(){
    const wrap = el('div');
    const f = state.bundle.file;
    wrap.innerHTML = \`
      <h1>Uploading bundle…</h1>
      <div class="file-card">
        <div class="meta">
          <div class="name">📦 \${escapeHtml(f ? f.name : 'bundle.zip')}</div>
          <div class="sub">\${f ? bytesHuman(f.size) : ''}</div>
        </div>
      </div>
      <div class="progress"><div id="bbar"></div></div>
      <p id="bpct" class="muted" style="margin-top:8px">0%</p>
      <div class="toolbar">
        <button id="btn-bcancel" class="secondary">Cancel</button>
      </div>
    \`;
    root.appendChild(wrap);
    on('btn-bcancel', 'click', () => { if (state.bundle.xhr) state.bundle.xhr.abort(); });
  }

  function performBundleUpload(baseUrl, apiKey){
    const fd = new FormData();
    fd.append('file', state.bundle.file, state.bundle.file.name);
    const url = baseUrl.replace(/\\/+$/, '') + '/api/ingest/bundle';
    const xhr = new XMLHttpRequest();
    state.bundle.xhr = xhr;
    xhr.open('POST', url, true);
    if (apiKey) xhr.setRequestHeader('X-API-Key', apiKey);
    xhr.responseType = 'text';

    xhr.upload.addEventListener('progress', (e) => {
      if (!e.lengthComputable) return;
      const pct = Math.round((e.loaded / e.total) * 100);
      const bar = document.getElementById('bbar');
      const p   = document.getElementById('bpct');
      if (bar) bar.style.width = pct + '%';
      if (p) p.textContent = pct + '%';
    });

    xhr.addEventListener('load', () => {
      let body;
      try { body = JSON.parse(xhr.responseText); } catch { body = null; }
      if (xhr.status >= 200 && xhr.status < 300 && body) {
        state.bundle.response = body;
        state.bundle.error = null;
        send({ type: 'uploadResult', ok: true, recordId: body.id, status: 'inserted' });
      } else {
        const code = body && body.error && body.error.code ? body.error.code : ('HTTP_' + xhr.status);
        const msg  = body && body.error && body.error.message
                       ? body.error.message
                       : (body && body.detail ? (typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)) : (xhr.responseText || xhr.statusText));
        const requestId = body && body.error && body.error.request_id ? body.error.request_id : undefined;
        state.bundle.error = { code, message: msg, requestId, httpStatus: xhr.status };
        state.bundle.response = null;
        send({ type: 'uploadResult', ok: false, httpStatus: xhr.status, requestId, error: '['+code+'] '+msg });
      }
      goBundle('result');
    });
    xhr.addEventListener('error', () => {
      state.bundle.error = { code: 'NETWORK', message: 'Network error' };
      send({ type: 'uploadResult', ok: false, error: 'Network error' });
      goBundle('result');
    });
    xhr.addEventListener('abort', () => {
      state.bundle.error = { code: 'ABORTED', message: 'Cancelled by user' };
      goBundle('drop');
    });
    xhr.send(fd);
  }

  function renderBundleResult(){
    const wrap = el('div');
    if (state.bundle.response) {
      const r = state.bundle.response;
      const w = r.warnings || { missing_resources: [], extra_resources: [] };
      const missingHtml = (w.missing_resources && w.missing_resources.length)
        ? \`<div class="warn-box miss"><b>Missing resources</b> (referenced by JSON but not in zip):
             <ul>\${w.missing_resources.map(x => '<li>'+escapeHtml(String(x))+'</li>').join('')}</ul></div>\`
        : '';
      const extraHtml = (w.extra_resources && w.extra_resources.length)
        ? \`<div class="warn-box extra"><b>Unreferenced resources</b> (in zip but not referenced):
             <ul>\${w.extra_resources.map(x => '<li>'+escapeHtml(String(x))+'</li>').join('')}</ul></div>\`
        : '';
      wrap.innerHTML = \`
        <h1>✅ Bundle ingested</h1>
        <div class="kv">
          <div class="k">Record ID</div><div><a href="#" id="lnk-record"><code>\${escapeHtml(r.id)}</code></a></div>
          <div class="k">Data type</div><div>\${escapeHtml(r.data_type)}</div>
          <div class="k">Title</div><div>\${escapeHtml(r.title || '')}</div>
          <div class="k">Figures copied</div><div>\${r.figures_copied}</div>
          <div class="k">Attachments copied</div><div>\${r.attachments_copied}</div>
        </div>
        \${missingHtml}\${extraHtml}
        <div class="toolbar">
          <button id="btn-banother">Upload Another</button>
        </div>
      \`;
      root.appendChild(wrap);
      on('lnk-record', 'click', (e) => {
        e.preventDefault();
        state.tab = 'search';
        state.search.detail = null;
        state.search.detailLoading = true;
        render();
        rpc('getRecordRequest', { id: r.id })
          .then((rec) => { state.search.detail = rec; state.search.detailLoading = false; render(); })
          .catch((err) => { state.search.detailError = String(err.message || err); state.search.detailLoading = false; render(); });
      });
      on('btn-banother', 'click', () => { state.bundle.file = null; state.bundle.response = null; goBundle('drop'); });
    } else {
      const e = state.bundle.error || { code: 'UNKNOWN', message: 'Unknown error' };
      const is401 = e.httpStatus === 401 || (e.code || '').toUpperCase().indexOf('API_KEY') !== -1;
      const reqId = e.requestId ? \`<div class="k">Request ID</div><div><code>\${escapeHtml(e.requestId)}</code></div>\` : '';
      wrap.innerHTML = \`
        <h1>❌ Bundle failed</h1>
        <div class="kv">
          <div class="k">Code</div><div><code>\${escapeHtml(e.code)}</code></div>
          <div class="k">Reason</div><div>\${escapeHtml(e.message)}</div>
          \${reqId}
        </div>
        <div class="toolbar">
          \${is401 ? '<button id="btn-breauth">Re-enter API Key</button>' : ''}
          <button id="btn-banother" class="secondary">Back</button>
        </div>
      \`;
      root.appendChild(wrap);
      on('btn-breauth',  'click', () => send({ type: 'promptApiKey' }));
      on('btn-banother', 'click', () => { state.bundle.file = null; state.bundle.error = null; goBundle('drop'); });
    }
  }

  // ====================================================================
  // SEARCH tab — search + record detail + discover
  // ====================================================================
  function renderSearchTab(){
    const wrap = el('div');
    const s = state.search;
    const filtersJson = JSON.stringify(s.filters || {});
    const facetsJson = s.facets ? JSON.stringify(s.facets) : '';

    const facetBars = (s.facets ? renderFacetBars(s.facets, s.filters) : '');
    const resultsHtml = renderResultRows(s.results);

    let detailHtml = '';
    if (s.detailLoading) {
      detailHtml = '<p class="muted">Loading record…</p>';
    } else if (s.detailError) {
      detailHtml = '<div class="status err">'+escapeHtml(s.detailError)+'</div>';
    } else if (s.detail) {
      detailHtml = renderRecordDetail(s.detail);
    }

    let discoverHtml = '';
    if (s.discoverLoading) discoverHtml = '<p class="muted">Loading discover…</p>';
    else if (s.discoverError) discoverHtml = '<div class="status err">'+escapeHtml(s.discoverError)+'</div>';
    else if (s.discover) discoverHtml = renderDiscoverPanel(s.discover);

    wrap.innerHTML = \`
      <h1>Search &amp; Discovery</h1>
      <div class="row">
        <div style="flex:3">
          <label>Query</label>
          <input id="s-q" type="text" placeholder="Type a question, keyword, or comma-separated tags" value="\${escapeHtml(s.q)}" />
        </div>
        <div>
          <label>Mode</label>
          <select id="s-mode">
            <option value="semantic" \${s.mode==='semantic'?'selected':''}>semantic</option>
            <option value="fts" \${s.mode==='fts'?'selected':''}>fts</option>
            <option value="tag" \${s.mode==='tag'?'selected':''}>tag</option>
          </select>
        </div>
      </div>
      <div class="toolbar">
        <button id="s-go">Search</button>
        <button id="s-discover" class="secondary">Discover catalog</button>
      </div>
      <details class="advanced" \${(Object.keys(s.filters || {}).length ? 'open' : '')}>
        <summary>Filters (faceted search)</summary>
        <div class="row">
          <div>
            <label>Data type</label>
            <input id="f-data-type" type="text" placeholder="DOC,DATA" value="\${escapeHtml(s.filters.data_type || '')}" />
          </div>
          <div>
            <label>Classification</label>
            <input id="f-classification" type="text" placeholder="internal" value="\${escapeHtml(s.filters.classification || '')}" />
          </div>
          <div>
            <label>Domain</label>
            <input id="f-domain" type="text" value="\${escapeHtml(s.filters.domain || '')}" />
          </div>
          <div>
            <label>Agent</label>
            <input id="f-agent" type="text" placeholder="iga-analyst" value="\${escapeHtml(s.filters.agent || '')}" />
          </div>
        </div>
        <div class="row">
          <div>
            <label>Tags (CSV, AND)</label>
            <input id="f-tags" type="text" value="\${escapeHtml(s.filters.tags || '')}" />
          </div>
          <div>
            <label>Status</label>
            <input id="f-status" type="text" value="\${escapeHtml(s.filters.status || '')}" />
          </div>
          <div>
            <label>Min quality</label>
            <input id="f-quality" type="number" min="0" max="100" value="\${s.filters.min_quality != null ? String(s.filters.min_quality) : ''}" />
          </div>
        </div>
      </details>

      \${s.error ? '<div class="status err">'+escapeHtml(s.error)+'</div>' : ''}
      \${s.loading ? '<p class="muted" style="margin-top:8px">Searching…</p>' : ''}
      \${facetBars}
      <div class="results">\${resultsHtml}</div>
      \${detailHtml ? '<hr style="margin:18px 0;border:none;border-top:1px solid var(--vscode-panel-border, rgba(128,128,128,0.25))">' + detailHtml : ''}
      \${discoverHtml ? '<hr style="margin:18px 0;border:none;border-top:1px solid var(--vscode-panel-border, rgba(128,128,128,0.25))">' + discoverHtml : ''}
    \`;
    root.appendChild(wrap);

    on('s-go', 'click', runSearch);
    const qEl = document.getElementById('s-q');
    if (qEl) qEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') runSearch(); });
    on('s-discover', 'click', runDiscover);

    // result row clicks
    wrap.querySelectorAll('[data-rid]').forEach(node => {
      node.addEventListener('click', () => {
        const id = node.getAttribute('data-rid');
        if (id) loadRecord(id);
      });
    });
    // facet pill clicks
    wrap.querySelectorAll('.facet-pill').forEach(node => {
      node.addEventListener('click', () => {
        const facetKey = node.getAttribute('data-facet');
        const facetVal = node.getAttribute('data-value');
        toggleFilter(facetKey, facetVal);
      });
    });
  }

  function readFilterInputs(){
    const f = {};
    const grab = (id) => { const v = val(id); if (v) f[id.substring(2).replace(/-/g, '_')] = v; };
    // f-data-type → data_type, f-classification → classification, etc
    const map = {
      'f-data-type': 'data_type',
      'f-classification': 'classification',
      'f-domain': 'domain',
      'f-agent': 'agent',
      'f-tags': 'tags',
      'f-status': 'status',
    };
    for (const [id, key] of Object.entries(map)) {
      const v = val(id);
      if (v) f[key] = v;
    }
    const mq = val('f-quality');
    if (mq) f.min_quality = parseInt(mq, 10);
    return f;
  }

  function runSearch(){
    const q = val('s-q');
    const mode = val('s-mode') || 'semantic';
    state.search.mode = mode;
    state.search.q = q;
    state.search.error = null;
    state.search.detail = null;
    state.search.detailError = null;

    const filters = readFilterInputs();
    state.search.filters = filters;
    const useFaceted = Object.keys(filters).length > 0;

    if (!q && mode !== 'tag' && !useFaceted) {
      state.search.error = 'Enter a query or set at least one filter.';
      render();
      return;
    }

    state.search.loading = true;
    render();
    if (useFaceted) {
      const reqFilters = Object.assign({}, filters);
      if (q) reqFilters.q = q;
      if (mode === 'semantic' || mode === 'fts') reqFilters.mode = mode;
      rpc('searchFacetedRequest', { filters: reqFilters })
        .then((payload) => {
          state.search.results = { mode: payload.mode || mode, q: payload.q || q, items: payload.items, total: payload.total };
          state.search.facets = payload.facets;
          state.search.loading = false;
          render();
        })
        .catch((err) => {
          state.search.error = String(err.message || err);
          state.search.loading = false;
          render();
        });
    } else {
      rpc('searchRequest', { q, mode, limit: 20 })
        .then((payload) => {
          state.search.results = payload;
          state.search.facets = null;
          state.search.loading = false;
          render();
        })
        .catch((err) => {
          state.search.error = String(err.message || err);
          state.search.loading = false;
          render();
        });
    }
  }

  function loadRecord(id){
    state.search.detail = null;
    state.search.detailError = null;
    state.search.detailLoading = true;
    render();
    rpc('getRecordRequest', { id })
      .then((rec) => { state.search.detail = rec; state.search.detailLoading = false; render(); })
      .catch((err) => { state.search.detailError = String(err.message || err); state.search.detailLoading = false; render(); });
  }

  function runDiscover(){
    state.search.discover = null;
    state.search.discoverError = null;
    state.search.discoverLoading = true;
    render();
    rpc('discoverRequest', {})
      .then((payload) => { state.search.discover = payload; state.search.discoverLoading = false; render(); })
      .catch((err) => { state.search.discoverError = String(err.message || err); state.search.discoverLoading = false; render(); });
  }

  function toggleFilter(key, value){
    if (!key || !value) return;
    const f = Object.assign({}, state.search.filters || {});
    if (f[key] === value) delete f[key]; else f[key] = value;
    state.search.filters = f;
    runSearch();
  }

  function renderResultRows(resp){
    if (!resp || !resp.items) return '';
    if (!resp.items.length) {
      return '<p class="muted" style="margin-top:8px">No results.</p>';
    }
    const head = '<p class="muted" style="margin:6px 0">'+resp.total+' result(s) — mode: <code>'+escapeHtml(String(resp.mode || ''))+'</code></p>';
    const rows = resp.items.map(it => {
      const rid = it.record_id || it.id || '';
      const title = it.title || it.section_title || '(untitled)';
      const score = (typeof it.score === 'number') ? it.score.toFixed(3) : '';
      const snippet = it.snippet || (it.summary || '').slice(0, 240);
      const tags = Array.isArray(it.tags) ? it.tags : [];
      return \`<div class="result-row" data-rid="\${escapeHtml(String(rid))}">
        <div class="top">
          <span class="rid">\${escapeHtml(String(rid))}</span>
          <span class="title">\${escapeHtml(String(title))}</span>
          \${score ? '<span class="score">score '+escapeHtml(score)+'</span>' : ''}
        </div>
        \${snippet ? '<div class="snippet">'+escapeHtml(String(snippet))+'</div>' : ''}
        \${tags.length ? '<div class="tags">'+tags.slice(0,8).map(t => '<span class="chip">'+escapeHtml(String(t))+'</span>').join(' ')+'</div>' : ''}
      </div>\`;
    }).join('');
    return head + rows;
  }

  function renderFacetBars(facets, applied){
    const sections = [
      { key: 'data_type', label: 'Data type' },
      { key: 'tags', label: 'Tags' },
      { key: 'domain', label: 'Domain' },
      { key: 'agent', label: 'Agent' },
      { key: 'status', label: 'Status' },
      { key: 'classification', label: 'Classification' },
    ];
    const parts = [];
    for (const s of sections) {
      const counts = facets[s.key] || {};
      const entries = Object.entries(counts).slice(0, 8);
      if (!entries.length) continue;
      const pills = entries.map(([v, c]) => {
        const isActive = (applied || {})[s.key] === v;
        return \`<span class="facet-pill \${isActive?'active':''}" data-facet="\${escapeHtml(s.key)}" data-value="\${escapeHtml(v)}">\${escapeHtml(v)} (\${c})</span>\`;
      }).join('');
      parts.push('<div style="margin-top:6px"><span class="muted" style="font-size:11px">'+escapeHtml(s.label)+':</span> <span class="facet-bar">'+pills+'</span></div>');
    }
    return parts.join('');
  }

  function renderRecordDetail(rec){
    const tags = Array.isArray(rec.tags) ? rec.tags : [];
    const agents = Array.isArray(rec.agents) ? rec.agents : [];
    const sections = (rec.content && Array.isArray(rec.content.sections)) ? rec.content.sections : [];
    const ah = rec.agent_hints ? escapeHtml(String(rec.agent_hints)) : '';
    const qe = Array.isArray(rec.query_examples) && rec.query_examples.length
      ? '<ul style="margin:0;padding-left:18px">'+rec.query_examples.map(q => '<li>'+escapeHtml(String(q))+'</li>').join('')+'</ul>'
      : '<span class="muted">—</span>';
    const sectionsHtml = sections.length
      ? '<ul style="margin:0;padding-left:18px;font-size:12px">' + sections.slice(0, 30).map(s => {
          const lvl = (s.level != null) ? ('L'+s.level+' ') : '';
          return '<li>'+escapeHtml(lvl)+'<b>'+escapeHtml(s.title || s.section_id || '')+'</b> <span class="muted">('+(s.figure_refs?s.figure_refs.length:0)+'fig / '+(s.table_refs?s.table_refs.length:0)+'tbl)</span></li>';
        }).join('') + '</ul>'
      : '<span class="muted">—</span>';
    return \`
      <h2>Record \${escapeHtml(rec.id)}</h2>
      <div class="kv">
        <div class="k">Title</div><div>\${escapeHtml(rec.title || '')}</div>
        <div class="k">Data type</div><div>\${escapeHtml(rec.data_type || '')}</div>
        <div class="k">Team/Group</div><div>\${escapeHtml(rec.team || '')} / \${escapeHtml(rec.group || '')}</div>
        <div class="k">Year/Seq</div><div>\${rec.year ?? ''} / \${rec.seq ?? ''}</div>
        <div class="k">Classification</div><div>\${escapeHtml(rec.classification || '—')}</div>
        <div class="k">Status</div><div>\${escapeHtml(rec.status || '—')}</div>
        <div class="k">Domain</div><div>\${escapeHtml(rec.domain || '—')}</div>
        <div class="k">Language</div><div>\${escapeHtml(rec.language || '—')}</div>
        <div class="k">Quality</div><div>\${rec.quality_score != null ? rec.quality_score : '—'}</div>
        <div class="k">Tags</div><div>\${tags.length ? tags.map(t => '<span class="chip">'+escapeHtml(String(t))+'</span>').join(' ') : '<span class="muted">—</span>'}</div>
        <div class="k">Agent scope</div><div>\${agents.length ? agents.map(t => '<span class="chip">'+escapeHtml(String(t))+'</span>').join(' ') : '<span class="muted">—</span>'}</div>
        <div class="k">agent_hints</div><div>\${ah || '<span class="muted">—</span>'}</div>
        <div class="k">query_examples</div><div>\${qe}</div>
        <div class="k">access_pattern</div><div>\${escapeHtml(rec.access_pattern || '—')}</div>
        <div class="k">Sections</div><div>\${sectionsHtml}</div>
      </div>
      <p class="muted" style="margin-top:6px;font-size:11px">summary: \${escapeHtml((rec.summary || '').slice(0, 400))}</p>
    \`;
  }

  function renderDiscoverPanel(d){
    const tags = computeTopTags(d);
    const tagsHtml = tags.length
      ? tags.map(([t, c]) => '<span class="chip">'+escapeHtml(t)+' ('+c+')</span>').join(' ')
      : '<span class="muted">—</span>';
    const byType = d.by_data_type || {};
    const typeRows = Object.entries(byType).map(([k, v]) => '<div><span class="muted">'+escapeHtml(k)+'</span> <b>'+v+'</b></div>').join('');
    const agentRows = (Array.isArray(d.agents) ? d.agents : []).slice(0, 6)
      .map(a => '<li><b>'+escapeHtml(a.name||a.agent_type)+'</b> <span class="muted">('+(a.record_count||0)+' records)</span></li>').join('');
    return \`
      <h2>Discover catalog</h2>
      <div class="discover-card">
        <div class="big-num">\${d.total_records}</div>
        <div class="label">total records</div>
        <div class="row" style="margin-top:8px">\${typeRows || '<span class="muted">—</span>'}</div>
      </div>
      <h3>Top agents</h3>
      <ul style="margin:0 0 8px 18px">\${agentRows || '<li class="muted">—</li>'}</ul>
      <h3>Top tags</h3>
      <div>\${tagsHtml}</div>
    \`;
  }

  function computeTopTags(d){
    if (Array.isArray(d.top_tags) && d.top_tags.length) {
      return d.top_tags.slice(0, 20).map(t => [t.tag || String(t), t.count || 0]);
    }
    // fallback — derive from agents.common_tags
    const counts = {};
    if (Array.isArray(d.agents)) {
      for (const a of d.agents) {
        for (const t of (a.common_tags || [])) {
          counts[t] = (counts[t] || 0) + 1;
        }
      }
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 20);
  }

  // ====================================================================
  // AGENTS tab — full CRUD against /api/agents
  // ====================================================================
  // DATA_TYPE_CHOICES mirrors api_server's literal subset for AgentIn.data_types.
  // 백엔드 schemas/agent.py 의 DATA_TYPE_CHOICES 와 동일하게 유지.
  const AGENT_DATA_TYPES = ['DOC', 'DATA', 'SIM', 'CAD', 'LOG', 'FORM', 'OTHER'];

  function renderAgentsTab(){
    const a = state.agents;
    // Lazy load list when entering tab with no data.
    if (a.list === null && !a.loading && !a.error) {
      reloadAgentsList();
    }
    if (a.screen === 'form') return renderAgentsForm();
    return renderAgentsList();
  }

  function renderAgentsList(){
    const a = state.agents;
    const wrap = el('div');
    const bannerHtml = a.banner
      ? '<div class="agents-banner ' + a.banner.kind + '">' + escapeHtml(a.banner.text) + '</div>'
      : '';
    const errorHtml = a.error
      ? '<div class="status err">' + escapeHtml(a.error) + '</div>'
      : '';
    const loadingHtml = a.loading ? '<p class="muted" style="margin-top:8px">Loading agents…</p>' : '';

    let bodyHtml = '';
    if (!a.loading && Array.isArray(a.list)) {
      if (a.list.length === 0) {
        bodyHtml = '<p class="muted" style="margin-top:12px">No agents registered yet. Click <b>+ New agent</b> to create one.</p>';
      } else {
        const rows = a.list.map(function(ag){
          const isOpen = a.expanded === ag.agent_type;
          const dtChips = (ag.data_types || []).map(function(d){
            return '<span class="chip">' + escapeHtml(d) + '</span>';
          }).join(' ');
          const desc = (ag.description || '').trim();
          const baseRow = '<tr data-agent="' + escapeHtml(ag.agent_type) + '" class="' + (isOpen ? 'expanded' : '') + '">'
            + '<td class="mono">' + escapeHtml(ag.agent_type) + '</td>'
            + '<td>' + escapeHtml(ag.name || '') + '</td>'
            + '<td>' + (dtChips || '<span class="muted">—</span>') + '</td>'
            + '<td class="desc" title="' + escapeHtml(desc) + '">' + escapeHtml(desc || '—') + '</td>'
            + '<td><span class="muted">—</span></td>'
            + '</tr>';
          const detailRow = isOpen
            ? '<tr class="expanded-row"><td colspan="5" style="padding:0">' + renderAgentDetailBlock(ag) + '</td></tr>'
            : '';
          return baseRow + detailRow;
        }).join('');
        bodyHtml = '<table class="agents-table">'
          + '<thead><tr>'
          + '<th>agent_type</th><th>Name</th><th>Data types</th><th>Description</th><th>Records</th>'
          + '</tr></thead>'
          + '<tbody>' + rows + '</tbody>'
          + '</table>';
      }
    }

    wrap.innerHTML = '<h1>Agents</h1>'
      + '<p class="subtle">Manage agent definitions used to discover and consume records. Changes refresh the Upload tab\\'s agent dropdown.</p>'
      + '<div class="toolbar">'
      + '  <button id="ag-refresh" class="secondary">Refresh</button>'
      + '  <button id="ag-new">+ New agent</button>'
      + '</div>'
      + bannerHtml
      + errorHtml
      + loadingHtml
      + bodyHtml;
    root.appendChild(wrap);

    on('ag-refresh', 'click', function(){ reloadAgentsList(); });
    on('ag-new', 'click', function(){ openAgentForm(null); });

    // Row click → toggle expanded inline detail.
    wrap.querySelectorAll('tr[data-agent]').forEach(function(tr){
      tr.addEventListener('click', function(ev){
        // Ignore clicks that originated inside the expanded detail (so inner
        // buttons don't re-collapse).
        var t = ev.target;
        while (t && t !== tr) {
          if (t.classList && (t.classList.contains('agent-detail') || t.tagName === 'BUTTON' || t.tagName === 'A')) {
            return;
          }
          t = t.parentNode;
        }
        var atype = tr.getAttribute('data-agent');
        state.agents.expanded = (state.agents.expanded === atype) ? null : atype;
        render();
      });
    });

    // Wire detail-block actions (Edit / Delete / View records).
    wrap.querySelectorAll('[data-action]').forEach(function(node){
      node.addEventListener('click', function(ev){
        ev.preventDefault();
        ev.stopPropagation();
        var action = node.getAttribute('data-action');
        var atype = node.getAttribute('data-agent');
        if (action === 'edit') openAgentForm(atype);
        else if (action === 'delete') confirmDeleteAgent(atype);
        else if (action === 'view-records') {
          state.tab = 'search';
          state.search.filters = Object.assign({}, state.search.filters || {}, { agent: atype });
          state.search.q = state.search.q || '';
          render();
          runSearch();
        }
      });
    });
  }

  function renderAgentDetailBlock(ag){
    var tags = Array.isArray(ag.common_tags) ? ag.common_tags : [];
    var dts = Array.isArray(ag.data_types) ? ag.data_types : [];
    var tagHtml = tags.length
      ? tags.map(function(t){ return '<span class="chip">' + escapeHtml(t) + '</span>'; }).join(' ')
      : '<span class="muted">—</span>';
    var dtHtml = dts.length
      ? dts.map(function(t){ return '<span class="chip">' + escapeHtml(t) + '</span>'; }).join(' ')
      : '<span class="muted">—</span>';
    var created = ag.created_at ? escapeHtml(String(ag.created_at)) : '<span class="muted">—</span>';
    var descSafe = (ag.description || '').trim();
    return '<div class="agent-detail">'
      + '<div class="kv">'
      + '  <div class="k">agent_type</div><div><code>' + escapeHtml(ag.agent_type) + '</code></div>'
      + '  <div class="k">Name</div><div>' + escapeHtml(ag.name || '') + '</div>'
      + '  <div class="k">Description</div><div>' + (descSafe ? escapeHtml(descSafe) : '<span class="muted">—</span>') + '</div>'
      + '  <div class="k">Common tags</div><div>' + tagHtml + '</div>'
      + '  <div class="k">Data types</div><div>' + dtHtml + '</div>'
      + '  <div class="k">Created at</div><div>' + created + '</div>'
      + '</div>'
      + '<div class="toolbar">'
      + '  <button data-action="edit" data-agent="' + escapeHtml(ag.agent_type) + '">Edit</button>'
      + '  <button data-action="delete" data-agent="' + escapeHtml(ag.agent_type) + '" class="secondary">Delete</button>'
      + '  <a href="#" data-action="view-records" data-agent="' + escapeHtml(ag.agent_type) + '" style="align-self:center;margin-left:6px">View records →</a>'
      + '</div>'
      + '</div>';
  }

  function renderAgentsForm(){
    var a = state.agents;
    var fv = a.formValues;
    var isEdit = !!a.editing;
    var wrap = el('div');
    var checkboxes = AGENT_DATA_TYPES.map(function(dt){
      var checked = fv.data_types.indexOf(dt) !== -1 ? ' checked' : '';
      return '<label><input type="checkbox" data-dt="' + escapeHtml(dt) + '"' + checked + ' /> ' + escapeHtml(dt) + '</label>';
    }).join('');
    var errHtml = a.formError ? '<div class="status err">' + escapeHtml(a.formError) + '</div>' : '';
    var savingHtml = a.saving ? '<p class="muted" style="margin-top:8px">Saving…</p>' : '';

    wrap.innerHTML = '<h1>' + (isEdit ? 'Edit agent' : 'New agent') + '</h1>'
      + '<p class="subtle">' + (isEdit
          ? 'Editing <code>' + escapeHtml(a.editing) + '</code>. <code>agent_type</code> cannot be changed.'
          : 'Define a new agent. <code>agent_type</code> should be lowercase with hyphens (e.g. <code>iga-analyst</code>).') + '</p>'
      + '<div class="agent-form">'
      + '  <label>agent_type *</label>'
      + '  <input id="af-type" type="text" placeholder="iga-analyst" value="' + escapeHtml(fv.agent_type) + '"' + (isEdit ? ' readonly' : '') + ' />'
      + '  <label>Name *</label>'
      + '  <input id="af-name" type="text" placeholder="IGA Analyst" value="' + escapeHtml(fv.name) + '" />'
      + '  <label>Description</label>'
      + '  <textarea id="af-desc" rows="3" placeholder="Short blurb shown in the catalog">' + escapeHtml(fv.description) + '</textarea>'
      + '  <label>Common tags <span class="muted" style="font-size:11px">— Enter or comma to add</span></label>'
      + '  <div id="af-chips-tags" class="chips"></div>'
      + '  <label>Data types <span class="muted" style="font-size:11px">— record types this agent consumes</span></label>'
      + '  <div class="checkbox-row" id="af-dts">' + checkboxes + '</div>'
      + '</div>'
      + errHtml
      + savingHtml
      + '<div class="toolbar">'
      + '  <button id="af-save">' + (isEdit ? 'Save changes' : 'Create agent') + '</button>'
      + '  <button id="af-cancel" class="secondary">Cancel</button>'
      + '</div>';
    root.appendChild(wrap);

    // Wire chip input — seed with existing tags.
    var chips = makeChipsSeeded('af-chips-tags', 'add tag…', fv.common_tags || []);

    // data_types checkboxes — keep state.formValues.data_types in sync.
    document.querySelectorAll('#af-dts input[type="checkbox"]').forEach(function(cb){
      cb.addEventListener('change', function(){
        var dt = cb.getAttribute('data-dt');
        var arr = state.agents.formValues.data_types.slice();
        var idx = arr.indexOf(dt);
        if (cb.checked && idx === -1) arr.push(dt);
        else if (!cb.checked && idx !== -1) arr.splice(idx, 1);
        state.agents.formValues.data_types = arr;
      });
    });

    on('af-cancel', 'click', function(){
      state.agents.screen = 'list';
      state.agents.editing = null;
      state.agents.formError = null;
      render();
    });
    on('af-save', 'click', function(){
      // Snapshot inputs into formValues, then submit.
      var v = state.agents.formValues;
      v.agent_type = val('af-type').trim();
      v.name = val('af-name').trim();
      v.description = val('af-desc');
      v.common_tags = chips.get();
      // data_types already kept in sync via checkbox change handler.
      submitAgentForm();
    });
  }

  // Variant of makeChips that seeds initial items.
  function makeChipsSeeded(containerId, placeholder, seed){
    var c = document.getElementById(containerId);
    if (!c) return { get: function(){ return []; } };
    var items = (seed || []).slice();
    var input = document.createElement('input');
    input.type = 'text';
    input.placeholder = placeholder;

    function repaint(){
      c.innerHTML = '';
      items.forEach(function(v, i){
        var chip = document.createElement('span');
        chip.className = 'chip';
        chip.innerHTML = escapeHtml(v) + ' <span class="x" data-i="' + i + '">✕</span>';
        chip.querySelector('.x').addEventListener('click', function(){
          items.splice(i, 1); repaint();
        });
        c.appendChild(chip);
      });
      c.appendChild(input);
      input.focus();
    }

    input.addEventListener('keydown', function(e){
      if ((e.key === 'Enter' || e.key === ',') && input.value.trim()) {
        e.preventDefault();
        var v = input.value.trim().replace(/,$/, '');
        if (v && items.indexOf(v) === -1) { items.push(v); }
        input.value = '';
        repaint();
      } else if (e.key === 'Backspace' && !input.value && items.length) {
        items.pop(); repaint();
      }
    });

    repaint();
    return { get: function(){ return items.slice(); } };
  }

  function openAgentForm(agentType){
    if (agentType) {
      // Edit existing — seed from list.
      var found = (state.agents.list || []).find(function(x){ return x.agent_type === agentType; });
      if (!found) return;
      state.agents.editing = agentType;
      state.agents.formValues = {
        agent_type: found.agent_type,
        name: found.name || '',
        description: found.description || '',
        common_tags: (found.common_tags || []).slice(),
        data_types: (found.data_types || []).slice(),
      };
    } else {
      state.agents.editing = null;
      state.agents.formValues = {
        agent_type: '', name: '', description: '', common_tags: [], data_types: [],
      };
    }
    state.agents.formError = null;
    state.agents.screen = 'form';
    render();
  }

  function reloadAgentsList(){
    state.agents.loading = true;
    state.agents.error = null;
    render();
    rpc('listAgentsRequest', {})
      .then(function(payload){
        state.agents.list = Array.isArray(payload) ? payload : [];
        state.agents.loading = false;
        render();
      })
      .catch(function(err){
        state.agents.error = String((err && err.message) || err);
        state.agents.loading = false;
        render();
      });
  }

  function submitAgentForm(){
    var v = state.agents.formValues;
    if (!v.agent_type) { state.agents.formError = 'agent_type is required.'; render(); return; }
    if (!v.name) { state.agents.formError = 'name is required.'; render(); return; }
    state.agents.formError = null;
    state.agents.saving = true;
    render();
    var isEdit = !!state.agents.editing;
    var payload = {
      name: v.name,
      description: v.description || '',
      common_tags: v.common_tags || [],
      data_types: v.data_types || [],
    };
    var promise;
    if (isEdit) {
      promise = rpc('updateAgentRequest', { agentType: state.agents.editing, patch: payload });
    } else {
      var createBody = Object.assign({ agent_type: v.agent_type }, payload);
      promise = rpc('createAgentRequest', { payload: createBody });
    }
    promise
      .then(function(){
        state.agents.saving = false;
        state.agents.screen = 'list';
        state.agents.editing = null;
        state.agents.banner = { kind: 'ok', text: isEdit ? 'Agent updated.' : 'Agent created.' };
        // Refresh list — banner persists until next action.
        reloadAgentsList();
        // Banner auto-clears after a few seconds.
        setTimeout(function(){ if (state.agents.banner) { state.agents.banner = null; render(); } }, 3500);
      })
      .catch(function(err){
        state.agents.saving = false;
        state.agents.formError = String((err && err.message) || err);
        render();
      });
  }

  function confirmDeleteAgent(agentType){
    // Use a host-side confirmation via postMessage: host shows native warning
    // dialog. Simpler — embed an inline confirm-only RPC. For minimum surface
    // area we rely on a JS confirm fallback first; host will also show toast
    // on result. To match the spec, request host to show confirmation:
    // (we just call the delete endpoint after a window.confirm — VS Code
    // webview's confirm() works inside webviews and avoids new message types.)
    if (!window.confirm('Delete agent "' + agentType + '"? This removes the catalog entry but does not delete records.')) {
      return;
    }
    rpc('deleteAgentRequest', { agentType: agentType })
      .then(function(){
        state.agents.expanded = null;
        state.agents.banner = { kind: 'ok', text: 'Deleted agent ' + agentType + '.' };
        reloadAgentsList();
        setTimeout(function(){ if (state.agents.banner) { state.agents.banner = null; render(); } }, 3500);
      })
      .catch(function(err){
        state.agents.banner = { kind: 'err', text: 'Delete failed: ' + String((err && err.message) || err) };
        render();
      });
  }

  // ====================================================================
  // Inbound / boot
  // ====================================================================
  // Tab switching
  tabnav.querySelectorAll('.tab').forEach(t => {
    t.addEventListener('click', () => setTab(t.dataset.tab));
  });

  function el(tag){ return document.createElement(tag); }
  function val(id){ const e = document.getElementById(id); return e ? (e.value || '') : ''; }
  function on(id, ev, fn){ const e = document.getElementById(id); if (e) e.addEventListener(ev, fn); }
  function selectOptions(values, dflt){
    return values.map(v => '<option value="'+escapeHtml(v)+'"'+(v===dflt?' selected':'')+'>'+escapeHtml(v)+'</option>').join('');
  }
  function escapeHtml(s){
    return String(s == null ? '' : s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }

  headerSettingsBtn.addEventListener('click', () => { state.showWelcome = true; render(); });

  window.addEventListener('message', (event) => {
    const m = event.data;
    if (m.type === 'config') {
      state.config = { baseUrl: m.baseUrl, hasApiKey: m.hasApiKey, connected: m.connected };
      if (m.connected) {
        state.showWelcome = false;
        if (!state.options) send({ type: 'fetchOptions' });
      } else {
        state.showWelcome = true;
      }
      render();
    } else if (m.type === 'connection') {
      if (m.ok) {
        // 폴백 발생 시 입력 필드 자동 갱신 + 안내 메시지.
        if (m.fellBack && m.effectiveUrl) {
          state.config.baseUrl = m.effectiveUrl;
          const inp = document.getElementById('i-url');
          if (inp) inp.value = m.effectiveUrl;
        }
        const versionTag = m.health && m.health.version ? ' — server ' + m.health.version : '';
        const fallbackTag = m.fellBack && m.effectiveUrl
          ? ' (auto-fell back to ' + m.effectiveUrl + ')'
          : '';
        setStatus('ok', 'Connection OK' + versionTag + fallbackTag);
      } else {
        setStatus('err', 'Failed: ' + (m.error || 'unknown error'));
      }
    } else if (m.type === 'options') {
      if (m.ok) { state.options = m.payload; state.optionsError = null; }
      else      { state.optionsError = m.error || 'unknown'; }
      render();
    } else if (m.type === 'uploadCredentials') {
      if (!m.ok) {
        const err = { code: 'NO_CRED', message: m.error || 'No credentials' };
        if (state.upload.kind === 'bundle') {
          state.bundle.error = err;
          goBundle('result');
        } else {
          state.upload.error = err;
          goUpload('result');
        }
        return;
      }
      if (state.upload.kind === 'bundle') {
        performBundleUpload(m.baseUrl || '', m.apiKey || '');
      } else {
        const v = state.upload.pendingValues;
        state.upload.pendingValues = null;
        performUpload(v, m.baseUrl || '', m.apiKey || '');
      }
    } else if (m.type === 'searchResponse' || m.type === 'searchFacetedResponse'
               || m.type === 'getRecordResponse' || m.type === 'discoverResponse'
               || m.type === 'listAgentsResponse' || m.type === 'getAgentRecordsResponse'
               || m.type === 'createAgentResponse' || m.type === 'updateAgentResponse'
               || m.type === 'deleteAgentResponse') {
      const p = _pendingReq.get(m.reqId);
      if (!p) return;
      _pendingReq.delete(m.reqId);
      if (m.ok) p.resolve(m.payload != null ? m.payload : m);
      else p.reject(new Error(m.error || 'request failed'));
    } else if (m.type === 'optionsInvalidated') {
      // Host invalidated meta/options cache (e.g. after an agent CRUD op).
      // Drop our cached copy so the Upload form fetches fresh on next render.
      state.options = null;
    } else if (m.type === 'fileLoaded') {
      const p = _pendingReq.get(m.reqId);
      if (!p) return;
      _pendingReq.delete(m.reqId);
      if (m.ok) p.resolve(m);
      else p.reject(new Error(m.error || 'file load failed'));
    }
  });

  send({ type: 'ready' });
})();
`;
}

function randomNonce(): string {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let out = '';
  for (let i = 0; i < 32; i++) out += chars[Math.floor(Math.random() * chars.length)];
  return out;
}

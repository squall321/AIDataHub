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
  <title>Mobile eXperience AI Data Hub</title>
  <style>${styles()}</style>
</head>
<body>
  <header>
    <div class="title">Mobile eXperience AI Data Hub Uploader</div>
    <nav id="tabnav" class="tabnav" style="display:none">
      <button class="tab" data-tab="upload">Upload</button>
      <button class="tab" data-tab="bundle">Bundle</button>
      <button class="tab" data-tab="search">Search</button>
      <button class="tab" data-tab="agents">Agents</button>
      <button class="tab" data-tab="console">Console</button>
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
    config: { baseUrl: '', hasApiKey: false, connected: false },
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
        // v0.7.0 — expected-schema fields
        required_doc_type: '',
        required_tags: [],
        excluded_tags: [],
        // v0.13.0 — RAG recipe (Migration 0014)
        // Strings here, not numbers — UI keeps blank inputs as ''.
        // submitAgentForm() coerces to numbers / null before sending.
        retrieval_top_k: '',           // → retrieval_config.top_k
        retrieval_score_threshold: '', // → retrieval_config.score_threshold
        system_prompt: '',
        response_max_tokens: '',       // → response_config.max_tokens
        response_citation_required: false,
        response_refusal_message: '',  // → response_config.refusal_message
        sample_queries: [],
      },
      formError: null,          // form-level error message (string|null)
      saving: false,
      recordsByAgent: {},       // agent_type -> { loading, items, error }
      // v0.13.0 — Test preview (save-time dry-run of RAG recipe).
      preview: {
        query: '',
        loading: false,
        result: null,           // AgentPreviewOutT | null
        error: null,            // string | null
      },
      // v0.13.0 — History viewer (per agent_type).
      historyOpen: null,        // agent_type currently expanded, or null
      historyByAgent: {},       // agent_type -> { loading, items, error }
      historyDiffSelection: {}, // agent_type -> { left: id|null, right: id|null }
      // v0.13.0 — Resync sample embeddings — transient banner per agent.
      resyncByAgent: {},        // agent_type -> { loading, result, error }
      // v0.7.0 — doc_type taxonomy cache (DocTypeOutT[] | null)
      docTypes: null,
      docTypesLoading: false,
      docTypesError: null,
      // v0.7.0 — inline doc_type create mini-form state
      docTypeMiniForm: {
        open: false,
        prevSelection: '',      // restored on cancel
        values: { code: '', name: '', description: '', expected_sections: [] },
        error: null,
        saving: false,
      },
    },
    // Console tab (v0.9.0 — agent-discovery-console)
    console: {
      q: '',
      loading: false,
      error: null,
      results: null,        // recommend response or null
      selectedAgent: null,  // agent_type string
      systemPrompt: null,   // string | null
      contextMarkdown: null, // string | null
      contextJson: null,    // string | null
      busy: null,           // 'recommend' | 'prompt' | 'md' | 'json' | null
      // v0.11.0 — MCP client selector
      mcpClient: 'cline',   // cline | claude_desktop | claude_code | cursor | copilot | gemini
      // v0.12.0 — MCP auto-install state
      mcpInstalling: false,
      mcpInstallResult: null,  // { ok, action, configPath, shellCommand, error, hint } | null
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

  function rpc(type, extra, timeoutMs){
    return new Promise((resolve, reject) => {
      const reqId = _reqIdSeq++;
      _pendingReq.set(reqId, { resolve, reject });
      send(Object.assign({ type, reqId }, extra || {}));
      // Default 30s timeout safety. Some flows (file save dialogs) need longer.
      const t = (typeof timeoutMs === 'number' && timeoutMs > 0) ? timeoutMs : 30000;
      setTimeout(() => {
        if (_pendingReq.has(reqId)) {
          _pendingReq.delete(reqId);
          reject(new Error('Request timed out'));
        }
      }, t);
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
    else if (state.tab === 'console') renderConsoleTab();
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
      <h1>Connect to your Mobile eXperience AI Data Hub server</h1>
      <p class="subtle">Enter your backend URL and API key. The key is stored in VS Code SecretStorage.</p>
      <label>Server URL</label>
      <input id="i-url" type="text" placeholder="http://your-server:8001" value="\${escapeHtml(state.config.baseUrl)}" />
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
    flashDropToast('dropzone', '받았습니다: ' + file.name + ' — 폼으로 이동…', 'ok');
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
          <input id="i-seq" type="number" min="1" max="2147483647" value="1" />
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
    if (!Number.isFinite(v.seq) || v.seq < 1 || v.seq > 2147483647) errors.set('seq', '1–2,147,483,647');
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
        <details style="margin-top:12px;padding:8px 10px;border:1px dashed var(--vscode-panel-border);border-radius:4px">
          <summary style="cursor:pointer;font-weight:600">유사 부모(campaign) 연결 (선택)</summary>
          <p class="subtle" style="margin:6px 0">이 데이터와 포맷이 같았던 기존 레코드를 부모로 제안합니다. 확인 후 연결하면 계층(specimen→campaign)이 형성됩니다.</p>
          <div class="toolbar">
            <button id="btn-suggest-parent">유사 부모 후보 찾기</button>
          </div>
          <div id="parent-suggest-box" style="margin-top:8px"></div>
        </details>
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
    // v0.15.0 — 유사 부모 후보 찾기 + 확인 후 연결.
    on('btn-suggest-parent', 'click', () => {
      const r = state.upload.response;
      const box = document.getElementById('parent-suggest-box');
      if (!r || !r.record_id || !box) return;
      box.innerHTML = '<p class="muted">후보 검색 중…</p>';
      rpc('suggestParentRequest', { recordId: r.record_id, topK: 5 })
        .then((res) => {
          const cands = (res && res.candidates) || [];
          if (!cands.length) {
            box.innerHTML = '<p class="muted">' + escapeHtml(String((res && res.note) || '유사 부모 후보 없음 — 이 레코드가 campaign(부모)일 수 있습니다.')) + '</p>';
            return;
          }
          let html = '<table class="agents-table"><thead><tr><th>record_id</th><th>제목</th><th>conf</th><th>근거</th><th></th></tr></thead><tbody>';
          cands.forEach((c, i) => {
            html += '<tr>'
              + '<td><code style="font-size:11px">' + escapeHtml(String(c.record_id)) + '</code></td>'
              + '<td>' + escapeHtml(String(c.title || '')) + '</td>'
              + '<td>' + escapeHtml(String(c.confidence || '')) + ' (' + escapeHtml(String(c.score)) + ')</td>'
              + '<td style="font-size:11px">' + escapeHtml(String(c.why || '')) + '</td>'
              + '<td><button class="tiny" data-pp="' + i + '">이 부모로 연결</button></td>'
              + '</tr>';
          });
          html += '</tbody></table>';
          box.innerHTML = html;
          box.querySelectorAll('button[data-pp]').forEach((btn) => {
            btn.addEventListener('click', () => {
              const idx = parseInt(btn.getAttribute('data-pp'), 10);
              const pid = String(cands[idx].record_id);
              btn.textContent = '연결 중…';
              rpc('patchRecordRequest', { recordId: r.record_id, patch: { parent_record_id: pid } })
                .then((rec) => {
                  box.innerHTML = '<div class="status ok">부모 연결됨 → <code>' + escapeHtml(pid)
                    + '</code> (depth=' + escapeHtml(String((rec && rec.depth) != null ? rec.depth : '?')) + ')</div>';
                })
                .catch((err) => {
                  box.innerHTML = '<div class="status err">연결 실패: ' + escapeHtml(String(err.message || err)) + '</div>';
                });
            });
          });
        })
        .catch((err) => {
          box.innerHTML = '<div class="status err">후보 검색 실패: ' + escapeHtml(String(err.message || err)) + '</div>';
        });
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
    flashDropToast('bdz', '받았습니다: ' + file.name + ' — 업로드 시작…', 'ok');
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
        else if (action === 'download-template') downloadAgentTemplate(atype, node);
        else if (action === 'history') toggleAgentHistory(atype);
        else if (action === 'resync-samples') resyncAgentSamples(atype);
        else if (action === 'view-records') {
          state.tab = 'search';
          state.search.filters = Object.assign({}, state.search.filters || {}, { agent: atype });
          state.search.q = state.search.q || '';
          render();
          runSearch();
        }
      });
    });
    // v0.13.0 — history diff radio selection.
    wrap.querySelectorAll('input[type="radio"][data-hist-side]').forEach(function(rb){
      rb.addEventListener('change', function(){
        var side = rb.getAttribute('data-hist-side');
        var atype = rb.getAttribute('data-hist-agent');
        var id = parseInt(rb.getAttribute('data-hist-id') || '', 10);
        if (!atype || isNaN(id) || (side !== 'left' && side !== 'right')) return;
        if (!state.agents.historyDiffSelection) state.agents.historyDiffSelection = {};
        var cur = state.agents.historyDiffSelection[atype] || { left: null, right: null };
        cur[side] = id;
        state.agents.historyDiffSelection[atype] = cur;
        render();
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

    // v0.7.0 — expected-schema hint section.
    var rdt = ag.required_doc_type;
    var rtags = Array.isArray(ag.required_tags) ? ag.required_tags : [];
    var xtags = Array.isArray(ag.excluded_tags) ? ag.excluded_tags : [];
    var schemaHtml;
    if (!rdt && rtags.length === 0 && xtags.length === 0) {
      schemaHtml = '<div class="k">Expected schema</div><div><span class="muted">(any)</span></div>';
    } else {
      var rdtHtml = rdt ? '<code>' + escapeHtml(String(rdt)) + '</code>' : '<span class="muted">—</span>';
      var rtagsHtml = rtags.length
        ? rtags.map(function(t){ return '<span class="chip">' + escapeHtml(t) + '</span>'; }).join(' ')
        : '<span class="muted">—</span>';
      var xtagsHtml = xtags.length
        ? xtags.map(function(t){ return '<span class="chip">' + escapeHtml(t) + '</span>'; }).join(' ')
        : '<span class="muted">—</span>';
      schemaHtml =
          '<div class="k">required_doc_type</div><div>' + rdtHtml + '</div>'
        + '<div class="k">required_tags</div><div>' + rtagsHtml + '</div>'
        + '<div class="k">excluded_tags</div><div>' + xtagsHtml + '</div>';
    }

    // v0.13.0 — RAG recipe read view (Migration 0014).
    var rc = (ag.retrieval_config && typeof ag.retrieval_config === 'object') ? ag.retrieval_config : {};
    var rsp = (ag.response_config && typeof ag.response_config === 'object') ? ag.response_config : {};
    var samples = Array.isArray(ag.sample_queries) ? ag.sample_queries : [];
    var sysP = ag.system_prompt || '';
    var ragEmpty = (
      rc.top_k == null && rc.score_threshold == null
      && rsp.max_tokens == null && !rsp.citation_required && !rsp.refusal_message
      && !sysP && samples.length === 0
    );
    var ragHtml;
    if (ragEmpty) {
      ragHtml = '<div class="k">RAG recipe</div><div><span class="muted">(server defaults)</span></div>';
    } else {
      var rcParts = [];
      if (rc.top_k != null) rcParts.push('top_k=' + escapeHtml(String(rc.top_k)));
      if (rc.score_threshold != null) rcParts.push('score≥' + escapeHtml(String(rc.score_threshold)));
      var rcText = rcParts.length ? rcParts.join(', ') : '<span class="muted">(default)</span>';
      var rspParts = [];
      if (rsp.max_tokens != null) rspParts.push('max_tokens=' + escapeHtml(String(rsp.max_tokens)));
      if (rsp.citation_required) rspParts.push('citation_required');
      if (rsp.refusal_message) rspParts.push('refusal="' + escapeHtml(String(rsp.refusal_message)) + '"');
      var rspText = rspParts.length ? rspParts.join(', ') : '<span class="muted">(default)</span>';
      var sysHtml = sysP
        ? '<pre style="white-space:pre-wrap;margin:0;font-size:11px;background:rgba(128,128,128,0.08);padding:6px;border-radius:3px;max-height:120px;overflow:auto">' + escapeHtml(sysP) + '</pre>'
        : '<span class="muted">(generic fallback)</span>';
      var samplesHtml = samples.length
        ? samples.map(function(s){ return '<span class="chip">' + escapeHtml(s) + '</span>'; }).join(' ')
        : '<span class="muted">—</span>';
      ragHtml =
          '<div class="k">retrieval_config</div><div>' + rcText + '</div>'
        + '<div class="k">response_config</div><div>' + rspText + '</div>'
        + '<div class="k">system_prompt</div><div>' + sysHtml + '</div>'
        + '<div class="k">sample_queries</div><div>' + samplesHtml + '</div>';
    }

    // v0.13.0 — history block (visible when toolbar "history" toggled).
    var historyHtml = '';
    var historyOpen = state.agents.historyOpen === ag.agent_type;
    if (historyOpen) {
      var h = (state.agents.historyByAgent && state.agents.historyByAgent[ag.agent_type]) || { loading: true };
      historyHtml = renderAgentHistoryBlock(ag.agent_type, h);
    }

    // v0.13.0 — resync banner (transient, after sample-embedding sync).
    var resyncBannerHtml = '';
    var rs = (state.agents.resyncByAgent && state.agents.resyncByAgent[ag.agent_type]) || null;
    if (rs) {
      if (rs.loading) {
        resyncBannerHtml = '<div class="agents-banner" style="background:rgba(80,140,220,0.18);margin-top:8px">Resyncing sample embeddings…</div>';
      } else if (rs.error) {
        resyncBannerHtml = '<div class="agents-banner err" style="margin-top:8px">Resync failed: ' + escapeHtml(String(rs.error)) + '</div>';
      } else if (rs.result) {
        resyncBannerHtml = '<div class="agents-banner ok" style="margin-top:8px">'
          + '✓ Indexed ' + (rs.result.indexed_count || 0) + ' sample queries for routing.'
          + '</div>';
      }
    }

    // Resync button only meaningful when there are sample_queries to index.
    // Stale badge: server reports samples_stale=true when sample_queries.length
    // !== samples_indexed_count (e.g. sync failed silently). UI nudges admin to retry.
    var resyncBtnHtml = '';
    if (Array.isArray(ag.sample_queries) && ag.sample_queries.length > 0) {
      var rsLoading = rs && rs.loading;
      var stale = !!ag.samples_stale;
      var idx = (typeof ag.samples_indexed_count === 'number') ? ag.samples_indexed_count : ag.sample_queries.length;
      var staleBadge = stale
        ? ' <span class="chip" style="background:rgba(220,140,40,0.22);margin-left:4px" title="indexed=' + idx + ' vs ' + ag.sample_queries.length + ' samples — click Resync">stale</span>'
        : '';
      resyncBtnHtml = '<button data-action="resync-samples" data-agent="' + escapeHtml(ag.agent_type) + '" class="secondary"'
        + (rsLoading ? ' disabled' : '')
        + ' title="Re-embed sample_queries so recommend_agents picks them up (indexed=' + idx + '/' + ag.sample_queries.length + ')">'
        + (rsLoading ? 'Resyncing…' : 'Resync samples (' + idx + '/' + ag.sample_queries.length + ')')
        + '</button>' + staleBadge;
    }

    return '<div class="agent-detail">'
      + '<div class="kv">'
      + '  <div class="k">agent_type</div><div><code>' + escapeHtml(ag.agent_type) + '</code></div>'
      + '  <div class="k">Name</div><div>' + escapeHtml(ag.name || '') + '</div>'
      + '  <div class="k">Description</div><div>' + (descSafe ? escapeHtml(descSafe) : '<span class="muted">—</span>') + '</div>'
      + '  <div class="k">Common tags</div><div>' + tagHtml + '</div>'
      + '  <div class="k">Data types</div><div>' + dtHtml + '</div>'
      + '  ' + schemaHtml
      + '  ' + ragHtml
      + '  <div class="k">Created at</div><div>' + created + '</div>'
      + '</div>'
      + '<div class="toolbar">'
      + '  <button data-action="edit" data-agent="' + escapeHtml(ag.agent_type) + '">Edit</button>'
      + '  <button data-action="delete" data-agent="' + escapeHtml(ag.agent_type) + '" class="secondary">Delete</button>'
      + '  <button data-action="history" data-agent="' + escapeHtml(ag.agent_type) + '" class="secondary" title="View change history">'
        + (historyOpen ? 'Hide history' : 'View history')
        + '</button>'
      + '  ' + resyncBtnHtml
      + '  <button data-action="download-template" data-agent="' + escapeHtml(ag.agent_type) + '" class="secondary" title="Download a Word (.docx) template prefilled for this agent">📄 Download Word template</button>'
      + '  <a href="#" data-action="view-records" data-agent="' + escapeHtml(ag.agent_type) + '" style="align-self:center;margin-left:6px">View records →</a>'
      + '</div>'
      + resyncBannerHtml
      + historyHtml
      + '</div>';
  }

  // v0.13.0 — Render a history list block under the agent detail toolbar.
  // Supports two-way diff: each row has Left/Right radio. When both picked,
  // a "Compare" button reveals field-by-field diff against current snapshots.
  function renderAgentHistoryBlock(agentType, h){
    if (!h || h.loading) return '<p class="muted" style="margin:8px 0">Loading history…</p>';
    if (h.error) return '<div class="status err" style="margin-top:8px">' + escapeHtml(String(h.error)) + '</div>';
    var items = Array.isArray(h.items) ? h.items : [];
    if (!items.length) return '<p class="muted" style="margin:8px 0">(no history)</p>';
    var sel = (state.agents.historyDiffSelection && state.agents.historyDiffSelection[agentType]) || { left: null, right: null };
    var rows = items.map(function(row){
      var snap = (row && row.snapshot && typeof row.snapshot === 'object') ? row.snapshot : {};
      var snapPretty = '';
      try { snapPretty = JSON.stringify(snap, null, 2); }
      catch (e) { snapPretty = String(snap); }
      var opCls = row.operation === 'delete'
        ? 'background:rgba(220,60,60,0.18)'
        : (row.operation === 'create' ? 'background:rgba(50,180,90,0.18)' : 'background:rgba(80,140,220,0.18)');
      var leftChk = (sel.left === row.id) ? ' checked' : '';
      var rightChk = (sel.right === row.id) ? ' checked' : '';
      return '<tr>'
        + '<td style="white-space:nowrap"><label style="font-size:10px;margin-right:6px"><input type="radio" name="hist-left-' + escapeHtml(agentType) + '" data-hist-side="left" data-hist-agent="' + escapeHtml(agentType) + '" data-hist-id="' + row.id + '"' + leftChk + '/> L</label>'
        + '<label style="font-size:10px"><input type="radio" name="hist-right-' + escapeHtml(agentType) + '" data-hist-side="right" data-hist-agent="' + escapeHtml(agentType) + '" data-hist-id="' + row.id + '"' + rightChk + '/> R</label></td>'
        + '<td class="mono" style="font-size:11px">' + escapeHtml(String(row.changed_at || '')) + '</td>'
        + '<td><span class="chip" style="' + opCls + '">' + escapeHtml(String(row.operation || '')) + '</span></td>'
        + '<td class="mono" style="font-size:11px">' + escapeHtml(String(row.changed_by || '—')) + '</td>'
        + '<td><details><summary style="cursor:pointer;font-size:11px">snapshot</summary>'
        + '<pre style="white-space:pre-wrap;font-size:10px;max-height:240px;overflow:auto;background:rgba(128,128,128,0.06);padding:6px;border-radius:3px">' + escapeHtml(snapPretty) + '</pre>'
        + '</details></td>'
        + '</tr>';
    }).join('');
    var diffBlock = '';
    if (sel.left != null && sel.right != null && sel.left !== sel.right) {
      var leftRow = items.find(function(x){ return x.id === sel.left; });
      var rightRow = items.find(function(x){ return x.id === sel.right; });
      if (leftRow && rightRow) {
        diffBlock = renderAgentHistoryDiff(leftRow, rightRow);
      }
    } else if (sel.left != null || sel.right != null) {
      diffBlock = '<p class="muted" style="margin:8px 0;font-size:11px">두 행(L, R)을 선택하면 diff 가 표시됩니다.</p>';
    }
    return '<div style="margin-top:10px">'
      + '<div class="subtle" style="margin-bottom:4px">Change history for <code>' + escapeHtml(agentType) + '</code> — newest first, append-only. L/R 선택 시 비교.</div>'
      + '<table class="agents-table"><thead><tr>'
      + '<th style="width:60px">diff</th><th>changed_at</th><th>op</th><th>by</th><th>snapshot</th>'
      + '</tr></thead><tbody>' + rows + '</tbody></table>'
      + diffBlock
      + '</div>';
  }

  // v0.13.0 — Field-by-field diff between two history snapshots.
  function renderAgentHistoryDiff(leftRow, rightRow){
    var L = (leftRow.snapshot && typeof leftRow.snapshot === 'object') ? leftRow.snapshot : {};
    var R = (rightRow.snapshot && typeof rightRow.snapshot === 'object') ? rightRow.snapshot : {};
    var keys = {};
    Object.keys(L).forEach(function(k){ keys[k] = true; });
    Object.keys(R).forEach(function(k){ keys[k] = true; });
    var keysList = Object.keys(keys).sort();
    var diffs = [];
    function _str(v){ try { return JSON.stringify(v); } catch (e) { return String(v); } }
    keysList.forEach(function(k){
      var lv = L[k], rv = R[k];
      var lvS = _str(lv), rvS = _str(rv);
      if (lvS === rvS) return;
      diffs.push({ key: k, left: lvS, right: rvS });
    });
    if (!diffs.length) {
      return '<div class="agents-banner" style="background:rgba(128,128,128,0.18);margin-top:8px">선택한 두 버전이 동일합니다.</div>';
    }
    var lLabel = (leftRow.operation || '?') + ' @ ' + (leftRow.changed_at || '');
    var rLabel = (rightRow.operation || '?') + ' @ ' + (rightRow.changed_at || '');
    var body = diffs.map(function(d){
      return '<tr>'
        + '<td class="mono" style="font-size:11px;vertical-align:top">' + escapeHtml(d.key) + '</td>'
        + '<td style="vertical-align:top"><pre style="white-space:pre-wrap;font-size:10px;margin:0;color:#e08080">' + escapeHtml(d.left) + '</pre></td>'
        + '<td style="vertical-align:top"><pre style="white-space:pre-wrap;font-size:10px;margin:0;color:#80c080">' + escapeHtml(d.right) + '</pre></td>'
        + '</tr>';
    }).join('');
    return '<div style="margin-top:10px;border:1px solid var(--vscode-panel-border);border-radius:4px;padding:8px">'
      + '<div class="subtle" style="margin-bottom:6px;font-size:11px">Diff (' + diffs.length + ' fields)</div>'
      + '<table style="width:100%;border-collapse:collapse"><thead><tr>'
      + '<th style="text-align:left;width:160px">field</th>'
      + '<th style="text-align:left">L · ' + escapeHtml(lLabel) + '</th>'
      + '<th style="text-align:left">R · ' + escapeHtml(rLabel) + '</th>'
      + '</tr></thead><tbody>' + body + '</tbody></table>'
      + '</div>';
  }

  function renderAgentsForm(){
    var a = state.agents;
    var fv = a.formValues;
    var isEdit = !!a.editing;

    // Kick off doc_type fetch on first form open (cached afterwards).
    if (a.docTypes === null && !a.docTypesLoading) {
      reloadDocTypesList();
    }

    var wrap = el('div');
    var checkboxes = AGENT_DATA_TYPES.map(function(dt){
      var checked = fv.data_types.indexOf(dt) !== -1 ? ' checked' : '';
      return '<label><input type="checkbox" data-dt="' + escapeHtml(dt) + '"' + checked + ' /> ' + escapeHtml(dt) + '</label>';
    }).join('');
    var errHtml = a.formError ? '<div class="status err">' + escapeHtml(a.formError) + '</div>' : '';
    var savingHtml = a.saving ? '<p class="muted" style="margin-top:8px">Saving…</p>' : '';

    // Build doc_type dropdown options.
    var dtList = Array.isArray(a.docTypes) ? a.docTypes : [];
    var dtOpts = '<option value="">(none)</option>';
    var currentSel = fv.required_doc_type || '';
    var foundCurrent = false;
    for (var i = 0; i < dtList.length; i++) {
      var dt = dtList[i];
      var sel = (currentSel && dt.code === currentSel) ? ' selected' : '';
      if (sel) foundCurrent = true;
      dtOpts += '<option value="' + escapeHtml(dt.code) + '"' + sel + '>'
        + escapeHtml(dt.name || dt.code) + ' (' + escapeHtml(dt.code) + ')</option>';
    }
    // If editing an agent whose required_doc_type points to a code that's no
    // longer in the taxonomy, still show it (un-resolved) so we don't silently
    // drop it on save.
    if (currentSel && !foundCurrent) {
      dtOpts += '<option value="' + escapeHtml(currentSel) + '" selected>'
        + escapeHtml(currentSel) + ' (unknown)</option>';
    }
    dtOpts += '<option value="__add_new__">+ Add new doc_type...</option>';

    var dtLoadingHtml = a.docTypesLoading
      ? '<span class="muted" style="font-size:11px;margin-left:6px">loading taxonomy…</span>'
      : '';
    var dtErrorHtml = a.docTypesError
      ? '<div class="status err" style="margin-top:6px">' + escapeHtml(a.docTypesError) + '</div>'
      : '';

    // Inline mini-form for creating a new doc_type.
    var mini = a.docTypeMiniForm;
    var miniHtml = '';
    if (mini.open) {
      var miniErr = mini.error
        ? '<div class="status err" style="margin-top:6px">' + escapeHtml(mini.error) + '</div>'
        : '';
      var miniSaving = mini.saving
        ? '<p class="muted" style="margin-top:6px">Creating…</p>'
        : '';
      miniHtml =
          '<div class="agent-form" id="dt-mini" style="margin-top:8px;padding:10px;border:1px dashed var(--vscode-panel-border);border-radius:4px">'
        + '  <div style="font-weight:600;margin-bottom:6px">New doc_type</div>'
        + '  <label>code *</label>'
        + '  <input id="dt-code" type="text" placeholder="manual" value="' + escapeHtml(mini.values.code) + '" />'
        + '  <label>name *</label>'
        + '  <input id="dt-name" type="text" placeholder="User manual" value="' + escapeHtml(mini.values.name) + '" />'
        + '  <label>description</label>'
        + '  <textarea id="dt-desc" rows="2" placeholder="Short blurb">' + escapeHtml(mini.values.description) + '</textarea>'
        + '  <label>expected_sections <span class="muted" style="font-size:11px">— Enter or comma to add</span></label>'
        + '  <div id="dt-chips-sections" class="chips"></div>'
        + miniErr
        + miniSaving
        + '  <div class="toolbar" style="margin-top:8px">'
        + '    <button id="dt-save">Save doc_type</button>'
        + '    <button id="dt-cancel" class="secondary">Cancel</button>'
        + '  </div>'
        + '</div>';
    }

    var draftBlock = isEdit ? '' : (
        '<details style="margin:8px 0;padding:8px 10px;border:1px dashed var(--vscode-panel-border);border-radius:4px"'
        + (a.draftBusy ? ' open' : '') + '>'
      + '  <summary style="cursor:pointer;font-weight:600">LLM 초안 생성 (선택)</summary>'
      + '  <p class="subtle" style="margin:6px 0">기존 허브 데이터를 분석해 agent 정의 초안을 자동 제안합니다. OPENAI_API_KEY 가 있으면 LLM, 없으면 빈도 휴리스틱. 결과는 폼에 채워지며 검토·수정 후 저장하세요.</p>'
      + '  <label>의도 힌트 (선택) <span class="muted" style="font-size:11px">— 예: "LS-DYNA 메시 매핑 자동화"</span></label>'
      + '  <input id="af-draft-hint" type="text" placeholder="비워두면 최근 레코드 표본으로 추론" value="' + escapeHtml(a.draftHint || '') + '" />'
      + '  <label style="margin-top:8px">데이터 군 한정 (선택) <span class="muted" style="font-size:11px">— 이 태그/타입에 해당하는 레코드만 분석</span></label>'
      + '  <div id="af-draft-tagchips" class="chips"></div>'
      + '  <input id="af-draft-dt" type="text" style="margin-top:4px" placeholder="data_type 필터 (콤마, 예: DOC,SIM) — 비우면 전체" value="' + escapeHtml(a.draftDataTypes || '') + '" />'
      + '  <div class="toolbar" style="margin-top:8px">'
      + '    <button id="af-draft-gen"' + (a.draftBusy ? ' disabled' : '') + '>' + (a.draftBusy ? '생성 중…' : '초안 생성') + '</button>'
      + (a.draftNote ? '<span class="muted" style="font-size:11px;align-self:center">' + escapeHtml(a.draftNote) + '</span>' : '')
      + '  </div>'
      + (a.draftError ? '<div class="status err" style="margin-top:6px">' + escapeHtml(a.draftError) + '</div>' : '')
      + '</details>'
    );

    wrap.innerHTML = '<h1>' + (isEdit ? 'Edit agent' : 'New agent') + '</h1>'
      + '<p class="subtle">' + (isEdit
          ? 'Editing <code>' + escapeHtml(a.editing) + '</code>. <code>agent_type</code> cannot be changed.'
          : 'Define a new agent. <code>agent_type</code> should be lowercase with hyphens (e.g. <code>iga-analyst</code>).') + '</p>'
      + draftBlock
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
      + '<details style="margin-top:10px"' + ((fv.required_doc_type || (fv.required_tags || []).length || (fv.excluded_tags || []).length) ? ' open' : '') + '>'
      + '  <summary style="cursor:pointer;font-weight:600">Expected schema (optional)</summary>'
      + '  <div class="agent-form" style="margin-top:6px">'
      + '    <label>Required doc_type <span class="muted" style="font-size:11px">— record\\'s meta.doc_type must equal this</span>' + dtLoadingHtml + '</label>'
      + '    <select id="af-doc-type">' + dtOpts + '</select>'
      + dtErrorHtml
      + miniHtml
      + '    <label>Required tags <span class="muted" style="font-size:11px">— record must carry all of these</span></label>'
      + '    <div id="af-chips-rtags" class="chips"></div>'
      + '    <label>Excluded tags <span class="muted" style="font-size:11px">— record must carry none of these</span></label>'
      + '    <div id="af-chips-xtags" class="chips"></div>'
      + '  </div>'
      + '</details>'
      // ---- RAG retrieval (Migration 0014) ---------------------------
      + '<details style="margin-top:10px"' + ((fv.retrieval_top_k || fv.retrieval_score_threshold) ? ' open' : '') + '>'
      + '  <summary style="cursor:pointer;font-weight:600">RAG retrieval (optional)</summary>'
      + '  <p class="subtle" style="margin:6px 0">검색 시 적용할 동작. 비워두면 서버 기본값 사용.</p>'
      + '  <div class="agent-form" style="margin-top:6px">'
      + '    <label>top_k <span class="muted" style="font-size:11px">— 검색 결과 수 (1~20, default 5)</span></label>'
      + '    <input id="af-top-k" type="number" min="1" max="20" step="1" placeholder="5" value="' + escapeHtml(fv.retrieval_top_k) + '" />'
      + '    <label>score_threshold <span class="muted" style="font-size:11px">— 최소 신뢰도 (0.0~1.0, default 0.6). 미만 결과는 LLM 에 전달되지 않음.</span></label>'
      + '    <input id="af-score-threshold" type="number" min="0" max="1" step="0.05" placeholder="0.6" value="' + escapeHtml(fv.retrieval_score_threshold) + '" />'
      + '  </div>'
      + '</details>'
      // ---- Response style (Migration 0014) --------------------------
      + '<details style="margin-top:10px"' + ((fv.system_prompt || fv.response_max_tokens || fv.response_citation_required || fv.response_refusal_message || (fv.sample_queries || []).length) ? ' open' : '') + '>'
      + '  <summary style="cursor:pointer;font-weight:600">Response style (optional)</summary>'
      + '  <p class="subtle" style="margin:6px 0">LLM 응답 스타일·거절 정책·라우팅 힌트.</p>'
      + '  <div class="agent-form" style="margin-top:6px">'
      + '    <label>system_prompt <span class="muted" style="font-size:11px">— LLM 에 그대로 주입. 비워두면 generic 폴백. 도구 가이드(API 목록)는 자동으로 뒤에 append 됨 — 끄려면 본문에 <code>&lt;!-- no-tool-guide --&gt;</code> 포함. 치환: <code>{base_url}</code>, <code>{agent_type}</code>, <code>{agent_name}</code>.</span></label>'
      + '    <textarea id="af-system-prompt" rows="6" placeholder="당신은 ___ 전문 보조입니다. 2~3문장 이내로 답하고, 출처는 record_id §섹션 형식으로 인용하세요. 자료에 없으면 「해당 자료를 찾지 못했습니다」라고만 답합니다.">' + escapeHtml(fv.system_prompt) + '</textarea>'
      + '    <label>max_tokens <span class="muted" style="font-size:11px">— LLM 응답 토큰 상한 (50~2000, default 200)</span></label>'
      + '    <input id="af-max-tokens" type="number" min="50" max="2000" step="10" placeholder="200" value="' + escapeHtml(fv.response_max_tokens) + '" />'
      + '    <label><input id="af-citation-required" type="checkbox"' + (fv.response_citation_required ? ' checked' : '') + ' /> citation_required <span class="muted" style="font-size:11px">— 응답에 record_id 인용이 없으면 거절</span></label>'
      + '    <label>refusal_message <span class="muted" style="font-size:11px">— score_threshold 미만일 때 응답 문구</span></label>'
      + '    <input id="af-refusal-message" type="text" placeholder="해당 자료를 찾지 못했습니다." value="' + escapeHtml(fv.response_refusal_message) + '" />'
      + '    <label>sample_queries <span class="muted" style="font-size:11px">— 라우팅 정확도용 예시 질문 (Enter 또는 콤마로 추가)</span></label>'
      + '    <div id="af-chips-samples" class="chips"></div>'
      + '  </div>'
      + '</details>'
      // ---- Test preview (Migration 0014) ----------------------------
      + renderAgentPreviewBlock()
      + errHtml
      + savingHtml
      + (isEdit ? '' :
          '<label style="margin-top:10px"><input id="af-bind-matching" type="checkbox"'
          + (a.bindAfterSave ? ' checked' : '') + ' /> 저장 후 기대 스키마에 맞는 기존 레코드 자동 바인딩'
          + ' <span class="muted" style="font-size:11px">— required_doc_type / required_tags / common_tags 일치 레코드의 agents 배열에 추가</span></label>')
      + '<div class="toolbar">'
      + '  <button id="af-save">' + (isEdit ? 'Save changes' : 'Create agent') + '</button>'
      + '  <button id="af-cancel" class="secondary">Cancel</button>'
      + '</div>';
    root.appendChild(wrap);

    // Wire chip input — seed with existing tags.
    var chips = makeChipsSeeded('af-chips-tags', 'add tag…', fv.common_tags || []);
    var rtagChips = makeChipsSeeded('af-chips-rtags', 'add required tag…', fv.required_tags || []);
    var xtagChips = makeChipsSeeded('af-chips-xtags', 'add excluded tag…', fv.excluded_tags || []);
    var sampleChips = makeChipsSeeded('af-chips-samples', 'add sample query…', fv.sample_queries || []);
    var draftTagChips = isEdit ? null : makeChipsSeeded('af-draft-tagchips', 'add tag filter…', a.draftTags || []);

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

    // doc_type dropdown change handler.
    on('af-doc-type', 'change', function(){
      var sel = document.getElementById('af-doc-type');
      if (!sel) return;
      var v = sel.value;
      if (v === '__add_new__') {
        // Open the inline mini-form. Remember the previous selection so Cancel
        // can revert back.
        // Persist current chip state into formValues so a re-render preserves it.
        var fv2 = state.agents.formValues;
        fv2.agent_type = val('af-type').trim();
        fv2.name = val('af-name').trim();
        fv2.description = val('af-desc');
        fv2.common_tags = chips.get();
        fv2.required_tags = rtagChips.get();
        fv2.excluded_tags = xtagChips.get();
        state.agents.docTypeMiniForm = {
          open: true,
          prevSelection: fv2.required_doc_type || '',
          values: { code: '', name: '', description: '', expected_sections: [] },
          error: null,
          saving: false,
        };
        render();
        return;
      }
      state.agents.formValues.required_doc_type = v;
    });

    // Mini-form wiring (only when open).
    if (mini.open) {
      var sectionChips = makeChipsSeeded('dt-chips-sections', 'add expected section…', mini.values.expected_sections || []);
      on('dt-cancel', 'click', function(){
        // Revert dropdown selection.
        state.agents.formValues.required_doc_type = state.agents.docTypeMiniForm.prevSelection || '';
        state.agents.docTypeMiniForm = {
          open: false,
          prevSelection: '',
          values: { code: '', name: '', description: '', expected_sections: [] },
          error: null,
          saving: false,
        };
        render();
      });
      on('dt-save', 'click', function(){
        var code = val('dt-code').trim();
        var name = val('dt-name').trim();
        var desc = val('dt-desc');
        var sections = sectionChips.get();
        if (!code) {
          state.agents.docTypeMiniForm.error = 'code is required.';
          state.agents.docTypeMiniForm.values = { code: code, name: name, description: desc, expected_sections: sections };
          render();
          return;
        }
        if (!name) {
          state.agents.docTypeMiniForm.error = 'name is required.';
          state.agents.docTypeMiniForm.values = { code: code, name: name, description: desc, expected_sections: sections };
          render();
          return;
        }
        state.agents.docTypeMiniForm.error = null;
        state.agents.docTypeMiniForm.saving = true;
        state.agents.docTypeMiniForm.values = { code: code, name: name, description: desc, expected_sections: sections };
        render();
        rpc('createDocTypeRequest', { payload: { code: code, name: name, description: desc, expected_sections: sections } })
          .then(function(payload){
            // Refresh dropdown list, auto-select new code, close mini-form.
            var newCode = (payload && payload.code) ? payload.code : code;
            state.agents.formValues.required_doc_type = newCode;
            state.agents.docTypeMiniForm = {
              open: false,
              prevSelection: '',
              values: { code: '', name: '', description: '', expected_sections: [] },
              error: null,
              saving: false,
            };
            // Append into local cache so re-render shows it without round-trip.
            if (Array.isArray(state.agents.docTypes)) {
              var exists = state.agents.docTypes.some(function(d){ return d.code === newCode; });
              if (!exists) state.agents.docTypes = state.agents.docTypes.concat([payload]);
            }
            render();
            // Background refresh to stay consistent.
            reloadDocTypesList();
          })
          .catch(function(err){
            state.agents.docTypeMiniForm.saving = false;
            var msg = String((err && err.message) || err);
            // 409 conflict → friendlier hint.
            if (/409|already/i.test(msg)) {
              msg = 'doc_type "' + code + '" already exists.';
            }
            state.agents.docTypeMiniForm.error = msg;
            render();
          });
      });
    }

    on('af-cancel', 'click', function(){
      state.agents.screen = 'list';
      state.agents.editing = null;
      state.agents.formError = null;
      // Reset mini-form if it was open.
      state.agents.docTypeMiniForm = {
        open: false,
        prevSelection: '',
        values: { code: '', name: '', description: '', expected_sections: [] },
        error: null,
        saving: false,
      };
      render();
    });
    // v0.14.0 — LLM 초안 생성: 폼 채움.
    on('af-draft-gen', 'click', function(){
      var hint = val('af-draft-hint').trim();
      var dtRaw = val('af-draft-dt').trim();
      var dtList = dtRaw ? dtRaw.split(',').map(function(s){return s.trim();}).filter(Boolean) : [];
      var tagList = draftTagChips ? draftTagChips.get() : [];
      state.agents.draftHint = hint;
      state.agents.draftDataTypes = dtRaw;
      state.agents.draftTags = tagList;
      state.agents.draftBusy = true;
      state.agents.draftError = null;
      state.agents.draftNote = null;
      render();
      rpc('draftAgentRequest', { payload: {
        hint: hint || null,
        filter_tags: tagList,
        filter_data_types: dtList,
      } }, 60000)
        .then(function(d){
          state.agents.draftBusy = false;
          var v = state.agents.formValues;
          if (d.agent_type) v.agent_type = String(d.agent_type);
          if (d.name) v.name = String(d.name);
          if (d.description != null) v.description = String(d.description);
          if (Array.isArray(d.common_tags)) v.common_tags = d.common_tags;
          if (Array.isArray(d.data_types)) v.data_types = d.data_types;
          if (d.required_doc_type != null) v.required_doc_type = String(d.required_doc_type);
          if (Array.isArray(d.required_tags)) v.required_tags = d.required_tags;
          if (Array.isArray(d.excluded_tags)) v.excluded_tags = d.excluded_tags;
          var rc = d.retrieval_config || {};
          if (rc.top_k != null) v.retrieval_top_k = String(rc.top_k);
          if (rc.score_threshold != null) v.retrieval_score_threshold = String(rc.score_threshold);
          if (d.system_prompt != null) v.system_prompt = String(d.system_prompt);
          var rsp = d.response_config || {};
          if (rsp.max_tokens != null) v.response_max_tokens = String(rsp.max_tokens);
          if (rsp.citation_required != null) v.response_citation_required = !!rsp.citation_required;
          if (rsp.refusal_message != null) v.response_refusal_message = String(rsp.refusal_message);
          if (Array.isArray(d.sample_queries)) v.sample_queries = d.sample_queries;
          var rc = (d._signal && d._signal.record_count != null) ? d._signal.record_count : '?';
          state.agents.draftNote = (d._source === 'llm' ? 'LLM 초안 적용됨' : '휴리스틱 초안 적용됨')
            + ' (분석 레코드 ' + rc + '건)'
            + (d._note ? ' — ' + d._note : '');
          render();
        })
        .catch(function(err){
          state.agents.draftBusy = false;
          state.agents.draftError = String((err && err.message) || err);
          render();
        });
    });
    on('af-bind-matching', 'change', function(e){
      state.agents.bindAfterSave = !!(e.target && e.target.checked);
    });
    on('af-save', 'click', function(){
      // Snapshot inputs into formValues, then submit.
      var v = state.agents.formValues;
      v.agent_type = val('af-type').trim();
      v.name = val('af-name').trim();
      v.description = val('af-desc');
      v.common_tags = chips.get();
      v.required_tags = rtagChips.get();
      v.excluded_tags = xtagChips.get();
      var sel = document.getElementById('af-doc-type');
      if (sel && sel.value !== '__add_new__') {
        v.required_doc_type = sel.value || '';
      }
      // data_types already kept in sync via checkbox change handler.
      // v0.13.0 — RAG recipe fields.
      v.retrieval_top_k = val('af-top-k').trim();
      v.retrieval_score_threshold = val('af-score-threshold').trim();
      v.system_prompt = val('af-system-prompt');
      v.response_max_tokens = val('af-max-tokens').trim();
      var cbCite = document.getElementById('af-citation-required');
      v.response_citation_required = !!(cbCite && cbCite.checked);
      v.response_refusal_message = val('af-refusal-message');
      v.sample_queries = sampleChips.get();
      submitAgentForm();
    });
    // v0.13.0 — Test preview button.
    on('af-preview-run', 'click', function(){
      // Snapshot current form into formValues (without saving) so the preview
      // uses whatever the operator is currently editing.
      var v = state.agents.formValues;
      v.retrieval_top_k = val('af-top-k').trim();
      v.retrieval_score_threshold = val('af-score-threshold').trim();
      v.system_prompt = val('af-system-prompt');
      v.response_max_tokens = val('af-max-tokens').trim();
      var cbC = document.getElementById('af-citation-required');
      v.response_citation_required = !!(cbC && cbC.checked);
      v.response_refusal_message = val('af-refusal-message');
      v.sample_queries = sampleChips.get();
      var q = val('af-preview-query').trim();
      state.agents.preview.query = q;
      if (!q) {
        state.agents.preview.error = 'Test query is required.';
        state.agents.preview.result = null;
        render();
        return;
      }
      runAgentPreview();
    });
  }

  // v0.13.0 — Preview block renderer. Reads state.agents.preview.
  function renderAgentPreviewBlock(){
    var pv = state.agents.preview || { query: '', loading: false, result: null, error: null };
    var resultHtml = '';
    if (pv.loading) {
      resultHtml = '<p class="muted" style="margin:8px 0">Running preview…</p>';
    } else if (pv.error) {
      resultHtml = '<div class="status err" style="margin-top:8px">' + escapeHtml(pv.error) + '</div>';
    } else if (pv.result) {
      var r = pv.result;
      var bits = [];
      bits.push('hits=' + (Array.isArray(r.hits) ? r.hits.length : 0));
      bits.push((r.hits_above_threshold || 0) + ' above threshold');
      if (r.threshold != null) bits.push('threshold=' + r.threshold);
      bits.push('LLM=' + (r.llm_used ? 'on' : 'off'));
      var summaryNote = r.llm_note ? '<div class="subtle" style="font-size:11px;margin-top:2px">' + escapeHtml(String(r.llm_note)) + '</div>' : '';
      var summary = '<div class="subtle" style="margin-top:8px">' + escapeHtml(bits.join(' · ')) + '</div>' + summaryNote;
      var body;
      if (r.refused) {
        body = '<div class="status err" style="margin-top:6px"><strong>Refused.</strong> '
          + escapeHtml(String(r.refusal_message || '')) + '</div>';
      } else if (r.answer) {
        body = '<div style="margin-top:6px;background:rgba(50,180,90,0.10);padding:10px;border-radius:4px;white-space:pre-wrap;font-size:12px">'
          + escapeHtml(String(r.answer)) + '</div>';
      } else {
        body = '<p class="muted" style="margin:6px 0">(LLM 답변 없음 — 아래 검색 결과를 확인)</p>';
      }
      var hitsList = '';
      if (Array.isArray(r.hits) && r.hits.length) {
        var items = r.hits.map(function(h){
          var snip = (h.snippet || '').substring(0, 240);
          return '<li style="margin-bottom:6px"><code>' + escapeHtml(String(h.record_id)) + ' §' + escapeHtml(String(h.section_id)) + '</code> '
            + '<span class="muted">(score=' + Number(h.score || 0).toFixed(3) + ')</span> '
            + escapeHtml(String(h.section_title || ''))
            + '<br/><span class="muted" style="font-size:10px">' + escapeHtml(snip) + '</span></li>';
        }).join('');
        hitsList = '<details style="margin-top:8px"><summary style="cursor:pointer">Retrieved chunks (' + r.hits.length + ')</summary>'
          + '<ul style="font-size:11px;padding-left:18px;margin-top:6px">' + items + '</ul></details>';
      }
      resultHtml = summary + body + hitsList;
    }
    var qSafe = escapeHtml((state.agents.preview && state.agents.preview.query) || '');
    var btnLabel = pv.loading ? 'Running…' : 'Run preview';
    return '<details style="margin-top:10px"' + ((pv.query || pv.result || pv.error) ? ' open' : '') + '>'
      + '  <summary style="cursor:pointer;font-weight:600">Test preview (저장 전 dry-run)</summary>'
      + '  <p class="subtle" style="margin:6px 0">현재 폼 값으로 검색 + (OPENAI_API_KEY 가 설정된 경우) LLM 답변까지 미리 확인.</p>'
      + '  <div class="agent-form">'
      + '    <label>Test query</label>'
      + '    <textarea id="af-preview-query" rows="2" placeholder="예: KooRemapper map 옵션은?">' + qSafe + '</textarea>'
      + '    <div class="toolbar" style="margin-top:6px">'
      + '      <button id="af-preview-run"' + (pv.loading ? ' disabled' : '') + '>' + btnLabel + '</button>'
      + '    </div>'
      + '    <div id="af-preview-result" style="margin-top:4px">' + resultHtml + '</div>'
      + '  </div>'
      + '</details>';
  }

  // v0.13.0 — Trigger preview using current formValues.
  function runAgentPreview(){
    var v = state.agents.formValues;
    function _num(s){ if (s === '' || s == null) return null; var n = Number(s); return isFinite(n) ? n : null; }
    var retrieval = {};
    var topK = _num(v.retrieval_top_k);
    if (topK != null) retrieval.top_k = topK;
    var thr = _num(v.retrieval_score_threshold);
    if (thr != null) retrieval.score_threshold = thr;
    var response = {};
    var maxT = _num(v.response_max_tokens);
    if (maxT != null) response.max_tokens = maxT;
    if (v.response_citation_required) response.citation_required = true;
    if (v.response_refusal_message) response.refusal_message = v.response_refusal_message;
    var sysPrompt = (v.system_prompt && v.system_prompt.trim()) ? v.system_prompt : null;
    // If editing existing agent, scope the search to its records.
    var agentType = state.agents.editing || null;
    var payload = {
      query: state.agents.preview.query,
      agent_type: agentType,
      retrieval_config: retrieval,
      system_prompt: sysPrompt,
      response_config: response,
    };
    state.agents.preview.loading = true;
    state.agents.preview.error = null;
    render();
    rpc('previewAgentRecipeRequest', { payload: payload })
      .then(function(resp){
        state.agents.preview.loading = false;
        state.agents.preview.result = resp || null;
        render();
      })
      .catch(function(err){
        state.agents.preview.loading = false;
        state.agents.preview.error = String((err && err.message) || err);
        render();
      });
  }

  function reloadDocTypesList(){
    state.agents.docTypesLoading = true;
    state.agents.docTypesError = null;
    rpc('listDocTypesRequest', {})
      .then(function(payload){
        state.agents.docTypes = Array.isArray(payload) ? payload : [];
        state.agents.docTypesLoading = false;
        // Re-render if we are still on the agents form so the dropdown updates.
        if (state.tab === 'agents' && state.agents.screen === 'form') render();
      })
      .catch(function(err){
        state.agents.docTypesLoading = false;
        // Non-fatal — surface as small inline notice; form still works without it.
        state.agents.docTypes = [];
        state.agents.docTypesError = 'doc_type taxonomy unavailable: ' + String((err && err.message) || err);
        if (state.tab === 'agents' && state.agents.screen === 'form') render();
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
      var rc = (found.retrieval_config && typeof found.retrieval_config === 'object') ? found.retrieval_config : {};
      var rsp = (found.response_config && typeof found.response_config === 'object') ? found.response_config : {};
      state.agents.editing = agentType;
      state.agents.formValues = {
        agent_type: found.agent_type,
        name: found.name || '',
        description: found.description || '',
        common_tags: (found.common_tags || []).slice(),
        data_types: (found.data_types || []).slice(),
        required_doc_type: found.required_doc_type || '',
        required_tags: (found.required_tags || []).slice(),
        excluded_tags: (found.excluded_tags || []).slice(),
        retrieval_top_k: (rc.top_k != null) ? String(rc.top_k) : '',
        retrieval_score_threshold: (rc.score_threshold != null) ? String(rc.score_threshold) : '',
        system_prompt: found.system_prompt || '',
        response_max_tokens: (rsp.max_tokens != null) ? String(rsp.max_tokens) : '',
        response_citation_required: !!rsp.citation_required,
        response_refusal_message: rsp.refusal_message || '',
        sample_queries: (found.sample_queries || []).slice(),
      };
    } else {
      state.agents.editing = null;
      state.agents.formValues = {
        agent_type: '', name: '', description: '', common_tags: [], data_types: [],
        required_doc_type: '', required_tags: [], excluded_tags: [],
        retrieval_top_k: '', retrieval_score_threshold: '',
        system_prompt: '',
        response_max_tokens: '', response_citation_required: false, response_refusal_message: '',
        sample_queries: [],
      };
    }
    state.agents.formError = null;
    state.agents.docTypeMiniForm = {
      open: false,
      prevSelection: '',
      values: { code: '', name: '', description: '', expected_sections: [] },
      error: null,
      saving: false,
    };
    // v0.13.0 — reset preview state per form open.
    state.agents.preview = { query: '', loading: false, result: null, error: null };
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

  // v0.13.0 — Re-embed agent.sample_queries on the server so recommend_agents
  // picks up routing hints. Banner auto-clears.
  function resyncAgentSamples(agentType){
    if (!agentType) return;
    if (!state.agents.resyncByAgent) state.agents.resyncByAgent = {};
    state.agents.resyncByAgent[agentType] = { loading: true, result: null, error: null };
    render();
    rpc('resyncAgentSamplesRequest', { agentType: agentType })
      .then(function(payload){
        state.agents.resyncByAgent[agentType] = { loading: false, result: payload || null, error: null };
        render();
        setTimeout(function(){
          if (state.agents.resyncByAgent && state.agents.resyncByAgent[agentType]) {
            delete state.agents.resyncByAgent[agentType];
            render();
          }
        }, 4000);
      })
      .catch(function(err){
        state.agents.resyncByAgent[agentType] = {
          loading: false,
          result: null,
          error: String((err && err.message) || err),
        };
        render();
      });
  }

  // v0.13.0 — Toggle the inline history panel for an agent. Loads on first open.
  function toggleAgentHistory(agentType){
    if (!agentType) return;
    if (state.agents.historyOpen === agentType) {
      state.agents.historyOpen = null;
      render();
      return;
    }
    state.agents.historyOpen = agentType;
    if (!state.agents.historyByAgent) state.agents.historyByAgent = {};
    var cache = state.agents.historyByAgent[agentType];
    if (cache && Array.isArray(cache.items) && !cache.error) {
      // Cached — render immediately, no refetch.
      render();
      return;
    }
    state.agents.historyByAgent[agentType] = { loading: true, items: null, error: null };
    render();
    rpc('listAgentHistoryRequest', { agentType: agentType, limit: 50 })
      .then(function(payload){
        state.agents.historyByAgent[agentType] = {
          loading: false,
          items: Array.isArray(payload) ? payload : [],
          error: null,
        };
        render();
      })
      .catch(function(err){
        state.agents.historyByAgent[agentType] = {
          loading: false,
          items: null,
          error: String((err && err.message) || err),
        };
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
    // Normalize required_doc_type: send null when blank so backend distinguishes
    // "no filter" from "explicit empty string". required_tags / excluded_tags
    // always send the array (possibly empty).
    var rdt = (v.required_doc_type && v.required_doc_type.trim()) ? v.required_doc_type.trim() : null;

    // ---- v0.13.0 — RAG recipe ------------------------------------------
    // 빈 입력은 키 자체를 생략 → 서버가 default 적용. 숫자 입력은 coerce.
    function _num(s){ if (s === '' || s == null) return null; var n = Number(s); return isFinite(n) ? n : null; }
    var retrieval = {};
    var topK = _num(v.retrieval_top_k);
    if (topK != null) retrieval.top_k = topK;
    var thr = _num(v.retrieval_score_threshold);
    if (thr != null) retrieval.score_threshold = thr;

    var response = {};
    var maxT = _num(v.response_max_tokens);
    if (maxT != null) response.max_tokens = maxT;
    if (v.response_citation_required) response.citation_required = true;
    if (v.response_refusal_message) response.refusal_message = v.response_refusal_message;

    var sysPrompt = (v.system_prompt && v.system_prompt.trim()) ? v.system_prompt : null;

    var payload = {
      name: v.name,
      description: v.description || '',
      common_tags: v.common_tags || [],
      data_types: v.data_types || [],
      required_doc_type: rdt,
      required_tags: v.required_tags || [],
      excluded_tags: v.excluded_tags || [],
      retrieval_config: retrieval,
      system_prompt: sysPrompt,
      response_config: response,
      sample_queries: v.sample_queries || [],
    };
    var promise;
    if (isEdit) {
      promise = rpc('updateAgentRequest', { agentType: state.agents.editing, patch: payload });
    } else {
      var createBody = Object.assign({ agent_type: v.agent_type }, payload);
      promise = rpc('createAgentRequest', { payload: createBody });
    }
    var doBind = !isEdit && !!state.agents.bindAfterSave;
    var newAgentType = v.agent_type;
    promise
      .then(function(){
        state.agents.saving = false;
        state.agents.screen = 'list';
        state.agents.editing = null;
        state.agents.banner = { kind: 'ok', text: isEdit ? 'Agent updated.' : 'Agent created.' };
        reloadAgentsList();
        // v0.14.0 — 생성 직후 매칭 레코드 자동 바인딩 (루프 닫기).
        if (doBind) {
          rpc('bindMatchingRequest', { agentType: newAgentType, limit: 500 }, 60000)
            .then(function(r){
              var n = (r && r.bound_count != null) ? r.bound_count : 0;
              state.agents.banner = { kind: 'ok', text: 'Agent created. ' + n + '개 레코드 자동 바인딩됨.' };
              reloadAgentsList();
              setTimeout(function(){ if (state.agents.banner) { state.agents.banner = null; render(); } }, 4000);
            })
            .catch(function(e){
              state.agents.banner = { kind: 'err', text: 'Agent 생성됨, 바인딩 실패: ' + String((e && e.message) || e) };
              render();
            });
        } else {
          setTimeout(function(){ if (state.agents.banner) { state.agents.banner = null; render(); } }, 3500);
        }
      })
      .catch(function(err){
        state.agents.saving = false;
        state.agents.formError = String((err && err.message) || err);
        render();
      });
  }

  function downloadAgentTemplate(agentType, btn){
    // Visual feedback on the button itself so the user sees something
    // happen while VS Code's save dialog warms up + the fetch round-trips.
    var origLabel = null;
    if (btn && btn.tagName === 'BUTTON') {
      origLabel = btn.textContent;
      btn.textContent = 'Preparing…';
      btn.disabled = true;
    }
    function restore(){
      if (btn && btn.tagName === 'BUTTON' && origLabel != null) {
        btn.textContent = origLabel;
        btn.disabled = false;
      }
    }
    // 5-minute timeout — user may pause on the Save dialog.
    rpc('downloadAgentTemplateRequest', { agentType: agentType }, 5 * 60 * 1000)
      .then(function(resp){
        restore();
        if (resp && resp.savedPath) {
          state.agents.banner = { kind: 'ok', text: 'Saved to ' + resp.savedPath };
        } else {
          // ok=true but no savedPath shouldn't happen, fall back to generic.
          state.agents.banner = { kind: 'ok', text: 'Template saved.' };
        }
        render();
        setTimeout(function(){ if (state.agents.banner) { state.agents.banner = null; render(); } }, 5000);
      })
      .catch(function(err){
        restore();
        var msg = String((err && err.message) || err);
        if (msg === 'cancelled') return; // user cancelled save dialog — stay quiet
        state.agents.banner = { kind: 'err', text: 'Download failed: ' + msg };
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
  // Console tab (v0.9.0 — agent-discovery-console)
  // ====================================================================
  function renderConsoleTab(){
    const c = state.console;
    const html = [];
    html.push('<div class="panel-pad">');
    html.push('  <h2 style="margin:0 0 8px;">자연어로 할 일 시작하기</h2>');
    html.push('  <p class="muted" style="margin:0 0 14px;">');
    html.push('    질문이나 작업 의도를 입력하면 → 가장 적합한 agent 추천 → 그 agent에 대한 ');
    html.push('    <em>system prompt</em>·<em>context bundle</em>을 Cline/Qwen 같은 LLM에 그대로 붙여넣을 수 있게 안내합니다.');
    html.push('  </p>');
    html.push('  <div style="display:flex; gap:8px; margin-bottom:14px;">');
    html.push('    <input id="console-q" type="text" class="input" style="flex:1;" placeholder="예: LS-DYNA 메시 매핑 자동화 도구 사용법 알려줘" value="' + escapeHtml(c.q) + '">');
    html.push('    <button id="console-go" class="btn primary"' + (c.loading?' disabled':'') + '>' + (c.loading?'추천 중…':'추천 받기') + '</button>');
    html.push('  </div>');

    if (c.error) html.push('<div class="banner err">' + escapeHtml(c.error) + '</div>');

    if (c.results) {
      const agents = c.results.agents || [];
      if (agents.length === 0) {
        html.push('<div class="banner muted">매칭되는 agent가 없습니다. 다른 표현으로 시도하거나 record를 더 적재하세요.</div>');
      } else {
        html.push('<h3 style="margin:18px 0 8px;">추천 agents</h3>');
        agents.forEach(a => {
          const sel = c.selectedAgent === a.agent_type;
          html.push('<div class="card" style="margin-bottom:10px; padding:10px 12px; border:1px solid ' + (sel?'#4a8aff':'#3a3a3a') + '; border-radius:6px;">');
          html.push('  <div style="display:flex; justify-content:space-between; align-items:flex-start;">');
          html.push('    <div style="flex:1;">');
          html.push('      <div><strong>' + escapeHtml(a.name) + '</strong> <code style="opacity:.7;">(' + escapeHtml(a.agent_type) + ')</code></div>');
          html.push('      <div class="muted" style="font-size:.92em; margin:4px 0;">' + escapeHtml(a.description) + '</div>');
          html.push('      <div class="muted" style="font-size:.85em;">score: <strong>' + (a.score||0).toFixed(2) + '</strong> · ');
          var rs = (a.matched_records || 0) + ' records / ' + (a.matched_sections || 0) + ' sections';
          if (a.matched_samples) rs += ' / ' + a.matched_samples + ' samples';
          html.push('        ' + rs + ' · ');
          html.push('        tags: ' + (a.common_tags||[]).slice(0,5).map(escapeHtml).join(', ') + '</div>');
          html.push('      <div class="muted" style="font-size:.82em; opacity:.7; margin-top:4px;">' + escapeHtml(a.why) + '</div>');
          html.push('    </div>');
          html.push('    <button class="btn console-pick" data-agent="' + escapeHtml(a.agent_type) + '">' + (sel?'선택됨':'이 agent 선택') + '</button>');
          html.push('  </div>');
          html.push('</div>');
        });
      }
    }

    if (c.selectedAgent) {
      html.push('<hr style="border:none; border-top:1px solid #333; margin:20px 0;">');
      html.push('<h3 style="margin:0 0 8px;">선택된 agent: <code>' + escapeHtml(c.selectedAgent) + '</code></h3>');
      html.push('<p class="muted" style="margin:0 0 12px;">아래 3가지를 Cline/Qwen에 복사·붙여넣으면 챗봇 셋업 완료:</p>');

      // System prompt
      html.push('<div class="card" style="padding:10px 12px; margin-bottom:10px;">');
      html.push('  <div style="display:flex; justify-content:space-between; align-items:center;">');
      html.push('    <strong>1. System prompt</strong>  <span class="muted">— Cline custom instructions에 붙여넣기</span>');
      html.push('    <div>');
      html.push('      <button id="console-load-prompt" class="btn"' + (c.busy==='prompt'?' disabled':'') + '>' + (c.busy==='prompt'?'로드 중…':'불러오기') + '</button>');
      html.push('      <button id="console-copy-prompt" class="btn"' + (c.systemPrompt?'':' disabled') + '>복사</button>');
      html.push('    </div>');
      html.push('  </div>');
      if (c.busy === 'prompt') {
        html.push('  <pre style="margin-top:8px; padding:10px; opacity:.7;">로드 중…</pre>');
      } else if (c.systemPrompt) {
        html.push('  <pre style="margin-top:8px; max-height:280px; overflow:auto; background:#1e1e1e; padding:10px; border-radius:4px; font-size:.85em;">' + escapeHtml(c.systemPrompt) + '</pre>');
      }
      html.push('</div>');

      // Context bundle markdown
      html.push('<div class="card" style="padding:10px 12px; margin-bottom:10px;">');
      html.push('  <div style="display:flex; justify-content:space-between; align-items:center;">');
      html.push('    <strong>2. Context bundle (Markdown)</strong> <span class="muted">— RAG payload, LLM 첫 메시지 컨텍스트로</span>');
      html.push('    <div>');
      html.push('      <button id="console-load-md" class="btn"' + (c.busy==='md'?' disabled':'') + '>' + (c.busy==='md'?'로드 중…':'불러오기') + '</button>');
      html.push('      <button id="console-copy-md" class="btn"' + (c.contextMarkdown?'':' disabled') + '>복사</button>');
      html.push('    </div>');
      html.push('  </div>');
      if (c.busy === 'md') {
        html.push('  <pre style="margin-top:8px; padding:10px; opacity:.7;">로드 중…</pre>');
      } else if (c.contextMarkdown) {
        html.push('  <pre style="margin-top:8px; max-height:280px; overflow:auto; background:#1e1e1e; padding:10px; border-radius:4px; font-size:.85em;">' + escapeHtml(c.contextMarkdown) + '</pre>');
      }
      html.push('</div>');

      // Context bundle json
      html.push('<div class="card" style="padding:10px 12px; margin-bottom:10px;">');
      html.push('  <div style="display:flex; justify-content:space-between; align-items:center;">');
      html.push('    <strong>3. Context bundle (JSON)</strong> <span class="muted">— tool/function 호출 백엔드용</span>');
      html.push('    <div>');
      html.push('      <button id="console-load-json" class="btn"' + (c.busy==='json'?' disabled':'') + '>' + (c.busy==='json'?'로드 중…':'불러오기') + '</button>');
      html.push('      <button id="console-copy-json" class="btn"' + (c.contextJson?'':' disabled') + '>복사</button>');
      html.push('    </div>');
      html.push('  </div>');
      if (c.busy === 'json') {
        html.push('  <pre style="margin-top:8px; padding:10px; opacity:.7;">로드 중…</pre>');
      } else if (c.contextJson) {
        html.push('  <pre style="margin-top:8px; max-height:280px; overflow:auto; background:#1e1e1e; padding:10px; border-radius:4px; font-size:.85em;">' + escapeHtml(c.contextJson) + '</pre>');
      }
      html.push('</div>');

      // MCP 자동 등록 — 클라이언트별 토글 (v0.11.0)
      var mcpClient = state.console.mcpClient || 'cline';
      var spec = _mcpSpecFor(mcpClient, state.config.baseUrl);
      html.push('<div class="card" style="padding:10px 12px; margin-bottom:10px; border:1px solid #6a4cff;">');
      html.push('  <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">');
      html.push('    <strong>MCP 자동 등록 (권장)</strong>');
      html.push('    <div style="display:flex; gap:8px; align-items:center;">');
      html.push('      <label class="muted" style="font-size:.88em;">클라이언트:</label>');
      html.push('      <select id="console-mcp-client" class="input" style="padding:4px 8px;">');
      [
        ['cline', 'Cline (VSCode)'],
        ['claude_desktop', 'Claude Desktop'],
        ['claude_code', 'Claude Code (CLI)'],
        ['cursor', 'Cursor'],
        ['copilot', 'VSCode Copilot Chat'],
        ['gemini', 'Gemini CLI'],
        ['codex', 'Codex CLI (OpenAI)'],
      ].forEach(function(p){
        html.push('<option value="' + p[0] + '"' + (p[0]===mcpClient?' selected':'') + '>' + p[1] + '</option>');
      });
      html.push('      </select>');
      html.push('      <button id="console-install-mcp" class="btn"' + (c.mcpInstalling?' disabled':'') + ' title="MCP 서버 등록 + System prompt 자동 주입 (Claude Desktop/Gemini 는 prompt 수동)">' + (c.mcpInstalling?'설치 중…':'자동 설치 (MCP + Prompt)') + '</button>');
      html.push('      <button id="console-copy-mcp" class="btn">복사</button>');
      html.push('    </div>');
      html.push('  </div>');
      html.push('  <p class="muted" style="margin:6px 0 4px; font-size:.85em;"><strong>설정 위치:</strong> ' + escapeHtml(spec.location) + '</p>');
      html.push('  <pre style="margin-top:4px; max-height:200px; overflow:auto; background:#1e1e1e; padding:10px; border-radius:4px; font-size:.85em;">' + escapeHtml(spec.body) + '</pre>');
      html.push('  <p class="muted" style="margin:6px 0 0; font-size:.85em;">' + escapeHtml(spec.note) + '</p>');
      if (c.mcpInstallResult) {
        var r = c.mcpInstallResult;
        var kind = r.ok ? 'ok' : 'err';
        var bg = r.ok ? '#2a4d3a' : '#5c2d2d';
        var line1 = r.ok
          ? 'MCP 서버 등록 (' + (r.action || '?') + ')' + (r.configPath ? ': ' + r.configPath : '') + (r.shellCommand ? ' [$ ' + r.shellCommand + ']' : '')
          : '설치 실패: ' + (r.error || 'unknown');
        var line2 = '';
        if (r.ok) {
          if (r.promptAction === 'created' || r.promptAction === 'updated') {
            line2 = 'System prompt ' + r.promptAction + ' → ' + (r.promptPath || '');
          } else if (r.promptAction === 'manual') {
            line2 = 'System prompt: 수동 복사 필요 — ' + (r.promptPath || '저장 경로 없음');
          } else if (r.promptAction === 'skipped') {
            line2 = 'System prompt: 로드된 prompt 없음 → "System prompt 불러오기" 후 다시 자동설치하면 함께 주입';
          }
        }
        html.push('  <div class="banner ' + kind + '" style="margin-top:8px; background:' + bg + '; padding:8px 10px; border-radius:4px; font-size:.85em;">'
          + escapeHtml(line1)
          + (r.hint ? ' <span class="muted">— ' + escapeHtml(r.hint) + '</span>' : '')
          + (line2 ? '<br/>' + escapeHtml(line2) : '')
          + '</div>');
      }
      html.push('</div>');

      html.push('<div class="banner" style="background:#2d3a5c; padding:10px 12px; border-radius:6px; margin-top:14px;">');
      html.push('  <strong>대안 (수동 모드)</strong>: MCP 미지원 LLM 이면 위 1 / 2 / 3 을 차례로 복사해서 Cline 의 Custom Instructions + 첫 메시지에 붙여넣는다.');
      html.push('</div>');
    }

    html.push('</div>');
    root.innerHTML = html.join('');

    // Event handlers
    on('console-q', 'keydown', function(e){ if (e.key === 'Enter') doRecommend(); });
    on('console-go', 'click', doRecommend);
    document.querySelectorAll('.console-pick').forEach(function(btn){
      btn.addEventListener('click', function(){
        state.console.selectedAgent = btn.getAttribute('data-agent');
        // clear previous prompt/bundle from prior selection
        state.console.systemPrompt = null;
        state.console.contextMarkdown = null;
        state.console.contextJson = null;
        render();
      });
    });
    on('console-load-prompt', 'click', function(){ loadConsoleText('prompt'); });
    on('console-load-md', 'click', function(){ loadConsoleText('md'); });
    on('console-load-json', 'click', function(){ loadConsoleText('json'); });
    on('console-copy-prompt', 'click', function(){ copyConsoleText(state.console.systemPrompt, 'System prompt'); });
    on('console-copy-md', 'click', function(){ copyConsoleText(state.console.contextMarkdown, 'Context (markdown)'); });
    on('console-copy-json', 'click', function(){ copyConsoleText(state.console.contextJson, 'Context (json)'); });
    on('console-copy-mcp', 'click', function(){
      var spec = _mcpSpecFor(state.console.mcpClient || 'cline', state.config.baseUrl);
      copyConsoleText(spec.body, 'MCP registration (' + (state.console.mcpClient || 'cline') + ')');
    });
    on('console-install-mcp', 'click', function(){ installMcp(); });
    on('console-mcp-client', 'change', function(e){
      state.console.mcpClient = e.target.value;
      state.console.mcpInstallResult = null;
      render();
    });
  }

  // ── MCP 자동 설치 (v0.12.0 → v0.13.0 — system_prompt 도 같이 자동 주입) ──
  function installMcp(){
    var client = state.console.mcpClient || 'cline';
    try { console.log('[aidh] installMcp click', client, 'baseUrl=', state.config.baseUrl); } catch(_){}
    state.console.mcpInstalling = true;
    state.console.mcpInstallResult = null;
    render();
    // v0.13.0 — prompt 가 아직 로드 안 됐고 agent 선택돼 있으면 먼저 자동 로드.
    var needPrompt = !state.console.systemPrompt && !!state.console.selectedAgent;
    var go = function(){ _sendInstallRequest(client); };
    if (needPrompt) {
      loadConsoleText('prompt', go);
    } else {
      go();
    }
  }

  function _sendInstallRequest(client){
    var rid = _reqIdSeq++;
    _pendingReq.set(rid, function(msg){
      try { console.log('[aidh] installMcp response', msg); } catch(_){}
      state.console.mcpInstalling = false;
      state.console.mcpInstallResult = {
        ok: !!msg.ok,
        action: msg.action,
        configPath: msg.configPath,
        shellCommand: msg.shellCommand,
        error: msg.error,
        hint: msg.hint,
        promptAction: msg.promptAction,
        promptPath: msg.promptPath,
        promptError: msg.promptError,
      };
      render();
    });
    // 진단용 timeout fallback — prompt 주입 포함이라 LLM 호출 없어도 fs 작업 약간 더 → 20s.
    setTimeout(function(){
      if (_pendingReq.has(rid)) {
        _pendingReq.delete(rid);
        state.console.mcpInstalling = false;
        state.console.mcpInstallResult = {
          ok: false,
          error: 'host 응답 timeout (20s) — VSCode를 Reload Window 후 패널을 다시 여세요',
        };
        render();
      }
    }, 20000);
    send({
      type: 'installMcpConfigRequest',
      reqId: rid,
      client: client,
      baseUrl: state.config.baseUrl,
      systemPrompt: state.console.systemPrompt || null,
      agentType: state.console.selectedAgent || null,
    });
    try { console.log('[aidh] installMcp sent rid=', rid); } catch(_){}
  }

  // ── MCP 클라이언트별 등록 코드 생성 ──────────────────────────────
  function _mcpUrl(baseUrl){
    return (baseUrl || 'http://<host>:8001').replace(/\\/+$/, '') + '/mcp/';
  }

  function _mcpSpecFor(client, baseUrl){
    var url = _mcpUrl(baseUrl);
    if (client === 'claude_code') {
      return {
        location: '터미널에서 한 줄 명령으로 등록',
        body: 'claude mcp add aidatahub --transport http ' + url,
        note: '명령 실행 후 Claude Code 재시작 (또는 새 세션 시작). 7개 도구 자동 인식.',
      };
    }
    if (client === 'copilot') {
      var cfg = { 'chat.mcp.servers': { aidatahub: { url: url } } };
      return {
        location: '.vscode/settings.json (워크스페이스) 또는 사용자 settings.json',
        body: JSON.stringify(cfg, null, 2),
        note: 'VSCode 재시작 후 Copilot Chat 의 도구 패널에서 자동 발견됨. (Copilot v0.20+ 필요)',
      };
    }
    if (client === 'gemini') {
      var cfgG = { mcpServers: { aidatahub: { url: url } } };
      return {
        location: '~/.gemini/mcp.json 또는 셸: gemini config mcp add aidatahub ' + url,
        body: JSON.stringify(cfgG, null, 2),
        note: 'Gemini CLI 0.x+ 에서 MCP HTTP transport 지원. CLI 명령이 더 간단하다.',
      };
    }
    if (client === 'codex') {
      return {
        location: '~/.codex/config.toml (자동설치가 [mcp_servers.aidatahub] 블록 작성)',
        body: '[mcp_servers.aidatahub]\nurl = "' + url + '"',
        note: 'Codex CLI 재시작 후 새 세션부터 도구 인식. system_prompt 는 AGENTS.md 에 주입됨.',
      };
    }
    // Cline / Claude Desktop / Cursor — 모두 동일 mcpServers 구조
    var cfgC = { mcpServers: { aidatahub: { url: url } } };
    var loc, note;
    if (client === 'claude_desktop') {
      loc = 'mac: ~/Library/Application Support/Claude/claude_desktop_config.json  |  win: %APPDATA%\\\\Claude\\\\claude_desktop_config.json';
      note = 'Claude Desktop 재시작 후 도구 자동 발견. (1.x+ 에서 HTTP transport 지원)';
    } else if (client === 'cursor') {
      loc = 'Cursor → Settings → MCP → Add Server';
      note = 'JSON 직접 편집 대신 UI 에서 name=aidatahub, url=' + url + ' 입력해도 동일.';
    } else {
      loc = '.vscode/cline_mcp_settings.json 또는 Cline UI → MCP Servers';
      note = 'Cline 재시작 후 7개 도구가 자동 인식된다. 도구별 권한은 Cline UI 에서 조정 가능.';
    }
    return { location: loc, body: JSON.stringify(cfgC, null, 2), note: note };
  }

  function doRecommend(){
    const qEl = document.getElementById('console-q');
    const q = (qEl && qEl.value || '').trim();
    if (!q) return;
    state.console.q = q;
    state.console.loading = true;
    state.console.error = null;
    state.console.results = null;
    state.console.selectedAgent = null;
    state.console.systemPrompt = null;
    state.console.contextMarkdown = null;
    state.console.contextJson = null;
    render();
    const rid = _reqIdSeq++;
    _pendingReq.set(rid, function(msg){
      state.console.loading = false;
      if (msg.ok) {
        state.console.results = msg.payload;
      } else {
        state.console.error = msg.error || 'Recommendation failed.';
      }
      render();
    });
    send({ type: 'recommendAgentsRequest', reqId: rid, q: q, topK: 5 });
  }

  function loadConsoleText(kind, onDone){
    if (!state.console.selectedAgent) {
      try { console.warn('[aidh] loadConsoleText: no agent selected'); } catch(_){}
      if (typeof onDone === 'function') onDone();
      return;
    }
    try { console.log('[aidh] loadConsoleText start', kind, state.console.selectedAgent); } catch(_){}
    state.console.busy = kind;
    state.console.error = null;
    render();
    const rid = _reqIdSeq++;
    if (kind === 'prompt') {
      _pendingReq.set(rid, function(msg){
        try { console.log('[aidh] system-prompt response', msg.ok, (msg.text||'').length); } catch(_){}
        state.console.busy = null;
        if (msg.ok) state.console.systemPrompt = msg.text || '(empty)';
        else state.console.error = msg.error || 'system-prompt load failed.';
        render();
        if (typeof onDone === 'function') onDone();
      });
      send({ type: 'getSystemPromptRequest', reqId: rid, agentType: state.console.selectedAgent, baseUrlOverride: state.config.baseUrl });
      try { console.log('[aidh] system-prompt sent rid=', rid); } catch(_){}
    } else {
      _pendingReq.set(rid, function(msg){
        try { console.log('[aidh] context-bundle response', kind, msg.ok, (msg.text||'').length); } catch(_){}
        state.console.busy = null;
        if (msg.ok) {
          if (kind === 'md') state.console.contextMarkdown = msg.text || '(empty)';
          else state.console.contextJson = msg.text || '(empty)';
        } else {
          state.console.error = msg.error || 'context-bundle load failed.';
        }
        render();
        if (typeof onDone === 'function') onDone();
      });
      send({ type: 'getContextBundleRequest', reqId: rid, agentType: state.console.selectedAgent, format: (kind === 'md' ? 'markdown' : 'json') });
      try { console.log('[aidh] context-bundle sent rid=', rid, 'kind=', kind); } catch(_){}
    }
  }

  function copyConsoleText(text, label){
    if (!text) return;
    const rid = _reqIdSeq++;
    _pendingReq.set(rid, function(){ /* status bar shows confirmation */ });
    send({ type: 'copyToClipboardRequest', reqId: rid, text: text, label: label });
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
               || m.type === 'deleteAgentResponse'
               || m.type === 'downloadAgentTemplateResponse'
               || m.type === 'draftAgentResponse' || m.type === 'bindMatchingResponse'
               || m.type === 'suggestParentResponse' || m.type === 'patchRecordResponse'
               || m.type === 'listDocTypesResponse' || m.type === 'createDocTypeResponse') {
      const p = _pendingReq.get(m.reqId);
      if (!p) return;
      _pendingReq.delete(m.reqId);
      if (m.ok) p.resolve(m.payload != null ? m.payload : m);
      else p.reject(new Error(m.error || 'request failed'));
    } else if (m.type === 'recommendAgentsResponse'
               || m.type === 'getContextBundleResponse'
               || m.type === 'getSystemPromptResponse'
               || m.type === 'copyToClipboardResponse'
               || m.type === 'installMcpConfigResponse') {
      // Console tab uses callback-style dispatch (not promise).
      const cb = _pendingReq.get(m.reqId);
      if (!cb) return;
      _pendingReq.delete(m.reqId);
      try { cb(m); } catch (_) { /* swallow */ }
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

/**
 * Webview HTML/CSS/JS shell.
 *
 * Single-document SPA with 4 screens (welcome, drop, form, sending, result)
 * controlled by a tiny state machine. Vanilla JS — no bundler needed.
 *
 * Webview ↔ Host messages are described in `protocol.ts`. We re-encode the
 * message types as runtime strings here since we can't import TS types into
 * a string-embedded script.
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
}
header .title { font-weight: 600; font-size: 14px; }
main { padding: 24px; max-width: 720px; margin: 0 auto; }
h1 { font-size: 18px; margin: 0 0 8px; }
h2 { font-size: 14px; margin: 18px 0 8px; opacity: 0.85; font-weight: 600; }
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

.row { display: flex; gap: 8px; align-items: stretch; }
.row > * { flex: 1; }
.row.tight { gap: 6px; }

.toolbar { display: flex; gap: 8px; margin-top: 16px; }

.status { margin-top: 16px; padding: 8px 12px; border-radius: 2px; }
.status.ok  { background: rgba(0,160,0,0.15); color: var(--vscode-testing-iconPassed, #4caf50); }
.status.err { background: rgba(200,0,0,0.15); color: var(--vscode-errorForeground, #f44336); }
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
.kv { display: grid; grid-template-columns: 110px 1fr; gap: 4px 12px; font-size: 12px; }
.kv .k { opacity: 0.7; }
`;
}

function clientScript(): string {
  return `
(function(){
  const vscode = acquireVsCodeApi();
  const root = document.getElementById('root');
  const headerSettingsBtn = document.getElementById('btn-settings');

  // ------------------------------------------------------------ State
  const state = {
    screen: 'welcome',          // welcome | drop | form | sending | result
    config: { baseUrl: '', hasApiKey: false, connected: false },
    options: null,              // MetaOptions
    optionsError: null,
    file: null,                 // { file: File, dataType: string }
    upload: { progress: 0, error: null, response: null },
  };

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

  function go(screen){ state.screen = screen; render(); }

  // ------------------------------------------------------------ Renderer
  function render(){
    root.innerHTML = '';
    if (state.screen === 'welcome') return renderWelcome();
    if (state.screen === 'drop')    return renderDrop();
    if (state.screen === 'form')    return renderForm();
    if (state.screen === 'sending') return renderSending();
    if (state.screen === 'result')  return renderResult();
  }

  // ------- Welcome (Settings) -----------------------------------------
  function renderWelcome(){
    const wrap = el('div');
    wrap.innerHTML = \`
      <h1>👋 Connect to your AI Data Hub server</h1>
      <p class="subtle">Enter your backend URL and API key. The key is stored in VS Code SecretStorage.</p>
      <label>Server URL</label>
      <input id="i-url" type="text" placeholder="http://10.10.20.5:8000" value="\${escapeHtml(state.config.baseUrl)}" />
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
    const el = document.getElementById('status');
    if (!el) return;
    el.className = 'status ' + kind;
    el.textContent = text;
    el.style.display = text ? 'block' : 'none';
  }

  // ------- DropZone -----------------------------------------------------
  function renderDrop(){
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
    picker.addEventListener('change', () => { if (picker.files && picker.files[0]) acceptFile(picker.files[0]); });

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
      dz.classList.remove('over'); dz.classList.remove('bad');
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) acceptFile(f);
    });
  }

  function acceptFile(file){
    if (!isExtAllowed(file.name)) {
      alertToast('Unsupported file type: ' + file.name);
      return;
    }
    const dataType = detectDataType(file.name) || 'OTHER';
    state.file = { file, dataType };
    state.upload = { progress: 0, error: null, response: null };
    go('form');
  }

  function alertToast(msg){
    const dz = document.getElementById('dropzone');
    if (dz) {
      dz.classList.add('bad');
      setTimeout(()=>dz.classList.remove('bad'), 900);
    }
    console.warn(msg);
  }

  // ------- Metadata Form -----------------------------------------------
  function renderForm(){
    if (!state.options) {
      const wrap = el('div');
      wrap.innerHTML = '<p class="muted">Loading metadata options…</p>';
      root.appendChild(wrap);
      send({ type: 'fetchOptions' });
      return;
    }
    const f = state.file.file;
    const dt = state.file.dataType;
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
          <label>Division *</label>
          <select id="i-division">\${selectOptions(opts.divisions, '')}</select>
          <div id="e-division" class="field-error"></div>
        </div>
        <div>
          <label>Team *</label>
          <select id="i-team"><option value="">— pick division —</option></select>
          <div id="e-team" class="field-error"></div>
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

      <h2>Classification</h2>
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
          <select id="i-language">\${selectOptions(opts.languages, 'ko')}</select>
        </div>
      </div>

      <h2>Discoverability</h2>
      <label>Tags</label>
      <div id="chips-tags" class="chips"></div>
      <label>Agents (compatible with \${dt})</label>
      <select id="i-agent-add"><option value="">— add agent —</option>
        \${opts.agents.filter(a => !a.data_types || a.data_types.length === 0 || a.data_types.includes(dt))
                     .map(a => '<option value="'+escapeHtml(a.agent_type)+'">'+escapeHtml(a.name)+' ('+escapeHtml(a.agent_type)+')</option>').join('')}
      </select>
      <div id="chips-agents" class="chips" style="margin-top:6px"></div>
      <label>Subject keywords</label>
      <div id="chips-subject" class="chips"></div>

      <h2>Override (optional)</h2>
      <label>Title (leave empty to use auto-extract)</label>
      <input id="i-title" type="text" />
      <label>Summary</label>
      <textarea id="i-summary"></textarea>

      <h2>Quality (optional)</h2>
      <div class="row">
        <div>
          <label>Quality score (0–100)</label>
          <input id="i-quality" type="number" min="0" max="100" />
        </div>
        <div>
          <label>Derivation</label>
          <select id="i-derivation">\${selectOptions(opts.derivations, 'original')}</select>
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

      <div class="toolbar">
        <button id="btn-send">Send to Backend</button>
      </div>
      <div id="form-status" class="status err" style="display:none"></div>
    \`;
    root.appendChild(wrap);

    // Wire chip inputs
    const tagsState = makeChips('chips-tags', 'add tag…');
    const agentsState = makeChipsFromSelect('chips-agents', 'i-agent-add');
    const subjectState = makeChips('chips-subject', 'add keyword…');

    // Division -> Team cascade
    const divEl = document.getElementById('i-division');
    const teamEl = document.getElementById('i-team');
    function refillTeams(){
      const div = divEl.value;
      const list = (opts.teams[div] || []);
      teamEl.innerHTML = '<option value="">—</option>' + list.map(t => '<option value="'+escapeHtml(t)+'">'+escapeHtml(t)+'</option>').join('');
    }
    divEl.addEventListener('change', refillTeams);

    on('btn-remove', 'click', () => { state.file = null; go('drop'); });
    on('btn-send', 'click', () => {
      const values = collectForm({ tagsState, agentsState, subjectState });
      const errors = validateForm(values, opts);
      paintErrors(errors);
      if (errors.size > 0) return;
      go('sending');
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
      // Remove all existing chip nodes (keep the input at the end)
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
      division: val('i-division'),
      team: val('i-team'),
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
    };
  }

  function validateForm(v, opts){
    const errors = new Map();
    if (!v.division) errors.set('division', 'Required');
    if (!v.team)     errors.set('team', 'Required');
    if (!Number.isFinite(v.year) || v.year < 1990 || v.year > 2100) errors.set('year', '1990–2100');
    if (!Number.isFinite(v.seq) || v.seq < 1 || v.seq > 999999) errors.set('seq', '1–999999');
    if (v.quality_score !== null && (v.quality_score < 0 || v.quality_score > 100)) errors.set('quality', '0–100');
    if (v.valid_from && v.valid_until && v.valid_from > v.valid_until) errors.set('valid', 'from > until');
    // file size
    if (state.file && opts.max_upload_mb && state.file.file.size > opts.max_upload_mb * 1024 * 1024) {
      errors.set('file', 'File exceeds max ' + opts.max_upload_mb + ' MB');
    }
    return errors;
  }

  function paintErrors(errors){
    ['division','team','year','seq'].forEach(k => {
      const el = document.getElementById('e-'+k);
      if (el) el.textContent = errors.get(k) || '';
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

  // ------- Sending screen ----------------------------------------------
  function renderSending(){
    const wrap = el('div');
    wrap.innerHTML = \`
      <h1>Sending…</h1>
      <div class="file-card">
        <div class="meta">
          <div class="name">📂 \${escapeHtml(state.file.file.name)}</div>
          <div class="sub">\${state.file.dataType} · \${bytesHuman(state.file.file.size)}</div>
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
    state.pendingValues = values;
    send({ type: 'requestUploadCredentials' });
  }

  function performUpload(values, baseUrl, apiKey){
    const fd = new FormData();
    fd.append('file', state.file.file, state.file.file.name);
    fd.append('division', values.division);
    fd.append('team', values.team);
    fd.append('year', String(values.year));
    fd.append('seq',  String(values.seq));
    fd.append('classification', values.classification);
    fd.append('status', values.status);
    if (values.domain) fd.append('domain', values.domain);
    fd.append('language', values.language);
    if (values.tags.length)             fd.append('tags', values.tags.join(','));
    if (values.agents.length)           fd.append('agents', values.agents.join(','));
    if (values.subject_keywords.length) fd.append('subject_keywords', values.subject_keywords.join(','));
    if (values.title_override)   fd.append('title_override', values.title_override);
    if (values.summary_override) fd.append('summary_override', values.summary_override);
    fd.append('derivation', values.derivation);
    if (values.quality_score !== null && values.quality_score !== undefined) fd.append('quality_score', String(values.quality_score));
    if (values.valid_from)  fd.append('valid_from', values.valid_from);
    if (values.valid_until) fd.append('valid_until', values.valid_until);

    const url = baseUrl.replace(/\\/+$/, '') + '/api/convert/ingest';
    const xhr = new XMLHttpRequest();
    state.upload.xhr = xhr;
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
        state.upload.response = body;
        state.upload.error = null;
        send({ type: 'uploadResult', ok: true, recordId: body.record_id });
      } else {
        const code = body && body.error && body.error.code ? body.error.code : ('HTTP_' + xhr.status);
        const msg  = body && body.error && body.error.message
                       ? body.error.message
                       : (body && body.detail ? (typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)) : (xhr.responseText || xhr.statusText));
        state.upload.error = { code, message: msg };
        state.upload.response = null;
        send({ type: 'uploadResult', ok: false, error: '['+code+'] '+msg });
      }
      go('result');
    });
    xhr.addEventListener('error', () => {
      state.upload.error = { code: 'NETWORK', message: 'Network error' };
      send({ type: 'uploadResult', ok: false, error: 'Network error' });
      go('result');
    });
    xhr.addEventListener('abort', () => {
      state.upload.error = { code: 'ABORTED', message: 'Cancelled by user' };
      go('drop');
    });

    xhr.send(fd);
  }

  // ------- Result -------------------------------------------------------
  function renderResult(){
    const wrap = el('div');
    if (state.upload.response) {
      const r = state.upload.response;
      wrap.innerHTML = \`
        <h1>✅ Uploaded</h1>
        <div class="kv">
          <div class="k">Record ID</div><div><code>\${escapeHtml(r.record_id)}</code></div>
          <div class="k">Status</div><div>\${escapeHtml(r.status)}</div>
          <div class="k">Sections</div><div>\${r.sections_written}</div>
          <div class="k">Title</div><div>\${escapeHtml(r.record.title)}</div>
        </div>
        <div class="toolbar">
          <button id="btn-again">Upload Another</button>
        </div>
      \`;
    } else {
      const e = state.upload.error || { code: 'UNKNOWN', message: 'Unknown error' };
      wrap.innerHTML = \`
        <h1>❌ Upload failed</h1>
        <div class="kv">
          <div class="k">Code</div><div><code>\${escapeHtml(e.code)}</code></div>
          <div class="k">Reason</div><div>\${escapeHtml(e.message)}</div>
        </div>
        <div class="toolbar">
          <button id="btn-again">Back</button>
        </div>
      \`;
    }
    root.appendChild(wrap);
    on('btn-again', 'click', () => { state.file = null; go('drop'); });
  }

  // ------------------------------------------------------------ DOM utils
  function el(tag){ const x = document.createElement(tag); return x; }
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

  // ------------------------------------------------------------ Header
  headerSettingsBtn.addEventListener('click', () => go('welcome'));

  // ------------------------------------------------------------ Inbound
  window.addEventListener('message', (event) => {
    const m = event.data;
    if (m.type === 'config') {
      state.config = { baseUrl: m.baseUrl, hasApiKey: m.hasApiKey, connected: m.connected };
      if (state.screen === 'welcome' && m.connected) { go('drop'); send({ type: 'fetchOptions' }); return; }
      if (!m.connected && state.screen !== 'welcome') { go('welcome'); return; }
      render();
    } else if (m.type === 'connection') {
      if (m.ok) {
        setStatus('ok', 'Connection OK' + (m.health && m.health.version ? ' — server ' + m.health.version : ''));
      } else {
        setStatus('err', 'Failed: ' + (m.error || 'unknown error'));
      }
    } else if (m.type === 'options') {
      if (m.ok) { state.options = m.payload; state.optionsError = null; }
      else      { state.optionsError = m.error || 'unknown'; }
      render();
    } else if (m.type === 'uploadCredentials') {
      if (!m.ok) {
        state.upload.error = { code: 'NO_CRED', message: m.error || 'No credentials' };
        go('result');
        return;
      }
      const v = state.pendingValues;
      state.pendingValues = null;
      performUpload(v, m.baseUrl || '', m.apiKey || '');
    }
  });

  // Boot
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

# AI Data Hub Uploader — User Guide

A VS Code extension to interact with your AI Data Hub backend (`api_server`) directly from VS Code:

- **Upload tab** — drag a document (`.docx` / `.pdf` / `.pptx` / `.md` / `.html` / `.xlsx`) → server converts → DB 적재. Advanced metadata (classification / status / domain / derivation / valid_from-until / quality_score / language / parent_record_id / subject_keywords / source_system) supported.
- **Bundle tab** — drop a pre-converted `.zip` (JSON + figures/attachments folder) → `POST /api/ingest/bundle` → server skips conversion, places resources at static mounts.
- **Search tab** — semantic / fts / tag 검색 + faceted filter (data_type / classification / domain / agent) + record detail viewer + `/api/discover` 전체 카탈로그.
- **Agents tab (v0.6)** — full CRUD on agent definitions via `/api/agents`: list / view / create / edit / delete. Changes invalidate the Upload tab's agent dropdown cache so newly registered agents become pickable immediately.

No CLI, no `curl`, no Python.

> **What's new in v0.6.0** — fourth tab **Agents** for managing agent definitions inline (no DB shell required). Agent list shows `agent_type` / name / data types / description; click a row to inline-expand the full record with **Edit** / **Delete** / **View records** actions. New / Edit form supports chip-input common tags and a `DOC/DATA/SIM/CAD/LOG/FORM/OTHER` checkbox grid for data types.

**Version**: 0.6.0 (2026-05-11) — Agents CRUD tab added. Previous: 0.5.0 = Bundle + 0006 metadata + Search.

---

## 1. Install

### From the `.vsix` (recommended)

```powershell
code --install-extension ai-data-hub-uploader-0.6.0.vsix
```

The extension ships with the prebuilt JS in `out/`. No `npm install` required on the user side.

### Verify

`Ctrl+Shift+P` → start typing `AI Data Hub` — you should see three commands:

- **AI Data Hub: Open Uploader**
- **AI Data Hub: Settings**
- **AI Data Hub: Reset Connection**

---

## 2. First launch

The first time the extension activates (or whenever its connection state is reset) it opens a **Webview tab titled `AI Data Hub`** with the **Settings (Welcome) screen**:

```
👋 Connect to your AI Data Hub server
   Server URL  [ http://10.10.20.5:8000        ]
   API Key     [ ••••••••••••••••••••••••••     ]
   [ Test Connection ]   [ Save & Continue ]
```

1. Type the backend URL (your `api_server`'s host:port — typically `http://localhost:8000` for local dev).
2. Paste your API key. **The key is saved in VS Code's `SecretStorage`, never in `settings.json`.**
3. Click **Test Connection**.
   - The extension calls `GET /api/system/health` (with legacy `/health` fallback).
   - If `auth_required=true`, it then calls `POST /api/auth/keys/verify` to validate the key.
   - On success a green banner says `Connection OK`.
4. Click **Save & Continue** to persist and switch to the upload screen.

> If your server has `AUTH_REQUIRED=false`, you can leave the API key blank.

---

## 3. Test connection

The Settings screen's **Test Connection** button is intentionally non-destructive — it only validates and shows the result; it does not save the key. Use it whenever you need to confirm:

- The server is reachable (no firewall in the way).
- The API key is valid.
- The backend's enum options (`/api/meta/options`) load correctly (cached for 5 min).

If something is off, the toast/banner shows a code + message:

| Code | Cause |
|------|-------|
| `NETWORK` / `ECONNREFUSED` | Server URL wrong or backend not running. |
| `INVALID_API_KEY` / 401 | API key is wrong or expired. |
| `HTTP_500` | Backend error — check `api_server` logs. |

---

## 4. Upload a file

After `Save & Continue` the panel switches to the **Drop Zone**:

```
        ╔══════════════════════════════════════════╗
        ║      📥 Drop a file here to upload        ║
        ║   .docx · .pdf · .pptx · .md · .xlsx     ║
        ║         or click to browse...            ║
        ╚══════════════════════════════════════════╝
```

1. Drop a file (or click `browse…` and pick one).
2. Unsupported extensions show a red border + toast — only the file types listed above (sourced from the backend `/api/meta/options`) are accepted.
3. Files larger than the server's `MAX_UPLOAD_MB` are blocked client-side before upload starts.

The panel then auto-switches to the **Metadata Form**:

| Group | Fields |
|-------|--------|
| Identification (required) | `team`, `group`, `year`, `seq` |
| Classification | `classification` (default `internal`), `status` (default `draft`), `domain`, `language` (default `ko`) |
| Discoverability | `tags`, `agents`, `subject_keywords` (chip inputs — Enter or comma to add) |
| Override (optional) | `title`, `summary` (leave empty = use auto-extracted from the converter) |
| Quality (optional) | `quality_score` 0–100, `derivation`, `valid_from`, `valid_until` |

- `team → group` cascades: picking a team refills the group list.
- `agents` is filtered to those whose `data_types` matches the inferred type (DOC/DATA).
- `Send to Backend` stays disabled until team / group / year (1990–2100) / seq (1–999999) are valid.

### DRY-RUN

Click **Send DRY-RUN** instead of *Send to Backend* to call `POST /api/convert/` (converter only — **does not write to the DB**). You'll see the converter's parsed JSON output, useful for previewing the title / summary the backend would store.

### Send

`Send to Backend` issues `POST /api/convert/ingest` as `multipart/form-data` from the webview using `XMLHttpRequest` so you get a real-time **upload progress bar**. On success a toast shows:

```
AI Data Hub: uploaded DOC-HE-CAE-2026-000001 (inserted)
```

`status` is one of `inserted` / `updated` / `skipped` (skipped = identical content hash already in the DB).

### Error handling

| HTTP | UI |
|------|-----|
| 401 | Toast offers **Re-enter API key** — click and a VS Code input box (password mode) appears; the new key is stored in `SecretStorage` and the panel reconnects. |
| 413 | `PAYLOAD_TOO_LARGE` toast — file exceeds backend `MAX_UPLOAD_MB`. |
| 415 | `UNSUPPORTED_FORMAT` — extension not in the backend's allow-list. |
| 422 | `VALIDATION_ERROR` with the first detail message. |
| 500 | Generic `CONVERSION_FAILED` — the result screen shows the backend's `request_id` so support can correlate logs. |

---

## 4b. Agents tab (v0.6)

The **Agents** tab manages agent definitions registered with the backend (`/api/agents`). Each agent declares the data types it consumes and a set of common tags; the Upload tab's agent dropdown is filtered by these definitions.

```
┌────────── Agents ──────────────────────────────────────────────┐
│  [ Refresh ]   [ + New agent ]                                  │
│                                                                 │
│  agent_type        Name              Data types   Description   │
│  ─────────────────────────────────────────────────────────────  │
│  iga-analyst       IGA 해석 분석가      DOC DATA      …            │
│  cae-reviewer      CAE Reviewer       DOC SIM       …            │
└─────────────────────────────────────────────────────────────────┘
```

| Action | How |
|--------|-----|
| **List** | Tab loads `GET /api/agents` on first open. Use **Refresh** to re-fetch. |
| **View detail** | Click any row → inline panel shows all fields (`agent_type`, `name`, `description`, `common_tags`, `data_types`, `created_at`). |
| **Create** | Click **+ New agent** → form. Fields: `agent_type` (required, lowercase-hyphen recommended), `name` (required), `description`, `common_tags` (Enter / comma to add chips), `data_types` (checkboxes: DOC / DATA / SIM / CAD / LOG / FORM / OTHER). Save → `POST /api/agents`. |
| **Edit** | Detail panel → **Edit**. `agent_type` becomes read-only; other fields pre-fill. Save → `PATCH /api/agents/{agent_type}`. |
| **Delete** | Detail panel → **Delete**. A confirmation prompt appears; on accept → `DELETE /api/agents/{agent_type}`. The catalog entry is removed; existing records remain. |
| **View records** | Detail panel → **View records →**. Jumps to the Search tab with `agent={agent_type}` filter applied and runs the search. |

After any successful create / update / delete, the meta/options cache is invalidated server-side and on the client, so the Upload tab's filtered agent dropdown reflects the change on its next render.

### Errors

| HTTP | Cause |
|------|-------|
| 409  | `agent_type` already exists — pick a different one. |
| 404  | Agent was deleted in another session — refresh. |
| 422  | Field validation (empty name, invalid data_type, etc.). |

---

## 5. Useful commands

| Command palette | Effect |
|-----------------|--------|
| `AI Data Hub: Open Uploader` | Open / focus the panel. |
| `AI Data Hub: Settings` | Same panel — switch to Settings screen via the ⚙ in the header. |
| `AI Data Hub: Reset Connection` | Wipe baseUrl + apiKey + connected flag → next open re-runs the Welcome screen. |

---

## 6. Troubleshooting

### A. "Server unreachable"
- Check the URL — protocol included? (`http://` not `localhost`).
- Try `curl http://<host>:8000/api/system/health` from the same machine.
- Corporate firewall / VPN may block; try the IP form rather than the hostname.

### B. "Invalid API key" repeatedly
- Run `AI Data Hub: Reset Connection`, then re-enter both URL and key.
- Confirm the key isn't expired with the backend admin (`api_server` `keys` table).
- The webview never sees the API key in plain text after save — only the host has it in `SecretStorage`.

### C. The panel is blank / shows "Loading metadata options…" forever
- The cached enum response from `/api/meta/options` may be stale. Run `AI Data Hub: Reset Connection` (clears cache) and reconnect.
- Open the Webview Developer Tools (`Help → Toggle Developer Tools` while focused on the panel) to inspect console errors.

### D. Drag-and-drop doesn't react
- Webviews need the file dropped exactly inside the dashed border.
- VS Code on some Linux desktops blocks DnD between apps — use the `browse…` fallback link.

### E. "PAYLOAD_TOO_LARGE" but file is small
- The backend reads `MAX_UPLOAD_MB` from `api_server`'s settings. If you bumped it on the server, **restart the extension panel** (close and reopen) so the cache reloads.

### F. `vsce package` complains about license
- Add a `LICENSE` file to the repo root, or add `"license": "UNLICENSED"` (already set) and run with `--allow-missing-repository`.

---

## 7. Developer (F5) workflow

If you want to iterate on the extension code:

```powershell
git clone <repo>
cd vscode_extension
npm install
code .
# In VS Code: F5 to launch an "Extension Development Host" window.
```

In the new window: `Ctrl+Shift+P` → `AI Data Hub: Open Uploader`. Live reload by pressing `Ctrl+R` in that window after changes are recompiled (`npx tsc -w`).

---

## 8. Build a new `.vsix`

```powershell
npx tsc -p .                                    # → out/
npx @vscode/vsce package --allow-missing-repository --no-dependencies
# → ai-data-hub-uploader-<version>.vsix
```

Bump `version` in `package.json` for each new build.

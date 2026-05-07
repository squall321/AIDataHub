# UX Flow — AI Data Hub Uploader

## 화면 1. 설치 직후 첫 활성화 (Welcome / Settings)

**트리거**: `extension.ts.activate()` 안에서 `globalState.get('aidh.connected') !== true` 일 때 자동으로 Webview Panel 을 새 탭에 연다.

```
┌─ AI Data Hub  •  Welcome  ─────────────────────────────────┐
│                                                            │
│   👋  Connect to your AI Data Hub server                    │
│                                                            │
│   Server URL                                               │
│   [ http://10.10.20.5:8000              ]                  │
│                                                            │
│   API Key                                                  │
│   [ ••••••••••••••••••••••••••••       ] [👁]             │
│                                                            │
│   [ Test Connection ]   [ Save & Continue ]               │
│                                                            │
│   ⓘ  Your API key is stored in VS Code SecretStorage.      │
│      It is never written to settings.json.                 │
└────────────────────────────────────────────────────────────┘
```

**동작 (Test Connection)**:
1. `GET {url}/health` — 200 이 아니면 "Server unreachable" 토스트.
2. `POST {url}/api/auth/keys/verify` (백엔드 신규) — 401/403 이면 "Invalid API key".
3. `GET {url}/api/meta/options` — 응답을 메모리에 캐시.
4. 모두 OK → `globalState.update('aidh.connected', true)` + `secretStorage.store('aidh.apiKey', ...)` + 화면 2 로 전환.

**동작 (Save & Continue)** = Test Connection 과 동일 + 자동 전환.

## 화면 2. Drop Zone

```
┌─ AI Data Hub  •  Upload  ────────────────────[⚙ Settings]──┐
│                                                            │
│                                                            │
│        ╔══════════════════════════════════════════╗        │
│        ║                                          ║        │
│        ║      📥  Drop a file here to upload       ║        │
│        ║                                          ║        │
│        ║   .docx · .pdf · .pptx · .md · .xlsx     ║        │
│        ║                                          ║        │
│        ║         or click to browse...            ║        │
│        ║                                          ║        │
│        ╚══════════════════════════════════════════╝        │
│                                                            │
│  Connected to:  http://10.10.20.5:8000                    │
└────────────────────────────────────────────────────────────┘
```

- 우측 상단 ⚙ 클릭 → 화면 1 (편집 모드).
- 드롭 영역은 VS Code Webview 의 `<input type="file">` + `dragover/drop` 이벤트로 구현.
- 지원 외 확장자 드롭 → 빨간 보더 + 토스트 "Unsupported file type".
- 드롭 성공 → 화면 3 으로 전환.

## 화면 3. Metadata Form

[`metadata_spec.md`](metadata_spec.md) 의 폼을 그대로 렌더링.

- 상단에 파일 카드 (파일명, 추론된 `data_type`, 사이즈).
- 우측 상단 [Remove] → 화면 2 로 복귀.
- 필수 필드 미충족 시 [Send] 비활성 + 부족한 필드 빨간 라벨.

## 화면 4. Sending / Result

**Sending (진행률)**:

```
┌─ AI Data Hub  •  Sending...  ──────────────────────────────┐
│                                                            │
│  📂 iga_guide.docx                                         │
│                                                            │
│  Uploading...   [████████░░░░░░░░] 47%                     │
│                                                            │
│  [ Cancel ]                                                │
└────────────────────────────────────────────────────────────┘
```

진행률은 Webview 안의 `XMLHttpRequest.upload.onprogress` 또는 `fetch` 의 stream 으로 계산.

**Success**:

```
┌─ AI Data Hub  •  Done  ────────────────────────────────────┐
│                                                            │
│  ✅  Uploaded                                              │
│                                                            │
│  Record ID:  DOC-HE-CAE-2026-000001                        │
│  Status:     inserted                                      │
│  Sections:   12                                            │
│                                                            │
│  [ Upload Another ]    [ Open in Browser ]                 │
└────────────────────────────────────────────────────────────┘
```

`Open in Browser` → `vscode.env.openExternal(http://{baseUrl}/api/records/{id})`.

**Error**:

```
┌─ AI Data Hub  •  Failed  ──────────────────────────────────┐
│                                                            │
│  ❌  Upload failed                                          │
│                                                            │
│  Code:    PAYLOAD_TOO_LARGE                                │
│  Reason:  업로드 크기 초과: 78 MB > 50 MB                  │
│                                                            │
│  [ Retry ]    [ Back ]                                     │
└────────────────────────────────────────────────────────────┘
```

## 상태 머신

```
   ┌──────────────┐  /health + key OK   ┌──────────────┐
   │   Welcome    ├─────────────────────►   DropZone   │
   └──────┬───────┘                     └──────┬───────┘
          ▲                                    │ drop
          │ click ⚙                            ▼
          │                              ┌─────────────┐
          │ ◄────────────────────────────┤    Form     │
          │                              └──────┬──────┘
          │                                     │ Send
          │                                     ▼
          │                              ┌─────────────┐
          │                              │   Sending   │
          │                              └──────┬──────┘
          │                                     │
          │                          ┌──────────┴──────────┐
          │                          ▼                     ▼
          │                    ┌──────────┐          ┌──────────┐
          │                    │ Success  │          │  Error   │
          │                    └────┬─────┘          └────┬─────┘
          │                         │ Upload Another       │ Retry
          └─────────────────────────┴──────────────────────┘
```

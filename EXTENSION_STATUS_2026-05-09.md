# VS Code Extension — 현재 상태 (2026-05-09)

> 검사 대상: `d:/Personal/AI_data/vscode_extension/`

---

## 한 줄 요약

`ai-data-hub-uploader-0.4.0.vsix` (20,784 B / ~20 KB) 가 빌드/패키징까지 완료된 **즉시 설치 가능한** 상태이며, Welcome → DropZone → Form → Sending(progress) → Result 의 5-screen SPA 가 동작한다. 백엔드 e2e 실 테스트(특히 Cline SR 같은 외부 통합)는 사용자가 직접 검증해야 하고, USER_GUIDE 에 스크린샷이 빠진 것을 제외하면 코드/문서/패키징은 정합성을 갖춘 상태다.

---

## 1. 설치 / 패키징

| 항목 | 값 |
|------|-----|
| `.vsix` 파일 | `ai-data-hub-uploader-0.4.0.vsix` (20,784 bytes, 2026-05-08 11:13) |
| `name` / `displayName` | `ai-data-hub-uploader` / `AI Data Hub Uploader` |
| `version` | `0.1.0` (package.json L5) |
| `publisher` | `squall321` (L6) |
| `engines.vscode` | `^1.85.0` (L25) |
| `description` | `Drag-and-drop documents (.docx/.pdf/.pptx/.md/.xlsx) into your AI Data Hub backend with one click.` |
| `activationEvents` | `onStartupFinished` — 빈 워크스페이스에서도 활성화됨 |
| `main` | `./out/extension.js` — 컴파일 산출물 존재 (`out/extension.js` 2,338 B 등) |
| `contributes.commands` | `aidh.openUploader`, `aidh.openSettings`, `aidh.resetConnection` 3개 |
| view contribution | 없음 (Webview 패널 1개만 사용 — `vscode.window.createWebviewPanel`) |

**설치 명령** (USER_GUIDE.md L11-13):

```powershell
code --install-extension ai-data-hub-uploader-0.4.0.vsix
```

**Lock 동기화 경고**: `package.json` 은 `0.1.0` 인데 `package-lock.json` 의 self-name 블록은 `0.0.1` 로 남아있음. 설치/실행에는 영향 없지만 다음 빌드 시 `npm install` 로 재정렬 권장.

---

## 2. UI 구성 (텍스트로)

5-screen 단일 Webview SPA — `src/webview/html.ts` 안에 HTML/CSS/JS 가 한 파일로 인라인 (CSP `script-src 'nonce-...'` 적용, L14-19).

### Header (모든 화면 공통, html.ts L30-35)

```
[ AI Data Hub Uploader ]                                  [ ⚙ ]
```

⚙ 클릭 → 언제든 Welcome (Settings) 로 복귀 (L750).

### (1) Welcome / Settings (L225-245)

```
👋 Connect to your AI Data Hub server
   Server URL  [ http://10.10.20.5:8000               ]
   API Key     [ ••••••••••••                          ]   (password)
   [ Test Connection ]   [ Save & Continue ]
   (status banner — info / ok / err)
   hint: /api/system/health · /api/auth/keys/verify · /api/meta/options
```

### (2) DropZone (L263-297)

```
Drop a file to upload
+--------------------------------------------+
|                  📥                        |
|   Drop a file here, or browse…             |
|   .docx · .pdf · .pptx · .md · .xlsx       |
|   max <max_upload_mb from /api/meta/options>|
+--------------------------------------------+
Connected to: http://...        (options error 표시)
```

DragOver 시 `over` 클래스, 미지원 확장자는 `bad` 빨간 테두리 (L284-296).

### (3) Metadata Form (L321-431) — 폼 필드 전체

| 그룹 | 필드 (\* 필수) | 입력 타입 |
|------|---------------|----------|
| **Identification** | `division*`, `team*`, `year*` (1990–2100), `seq*` (1–999999) | select / select / number / number |
| **Classification** | `classification` (default `internal`), `status` (default `draft`), `domain`, `language` (default `ko`) | select / select / text / select |
| **Discoverability** | `tags`, `agents` (DT 호환만 필터), `subject_keywords` | chip 입력 (Enter/콤마) / select-add chips |
| **Override (optional)** | `title`, `summary` | text / textarea |
| **Quality (optional)** | `quality_score` (0–100), `derivation` (default `original`), `valid_from`, `valid_until` | number / select / date / date |

- division 변경 시 team 옵션 자동 리필 (L441-446 cascade).
- 검증: division/team/year/seq 외에 file size > `max_upload_mb` 도 클라이언트 차단 (L555-558).
- 버튼: `Send to Backend` / `Send DRY-RUN` (L425-428).

### (4) Sending (L578-596)

```
Sending…
[file card: 📂 filename · DOC · 1.2 MB]
[==========░░░░░░░░░░] 47%
[ Cancel ]   ← XHR.abort()
```

진행률은 `XMLHttpRequest.upload.progress` 의 `loaded/total` 로 실시간 갱신 (L632-639).

### (5) Result (L684-734)

- **성공** (ingest): `✅ Uploaded` + Record ID / Status / Sections / Title + `[Upload Another]`.
- **DRY-RUN 성공**: `🔬 DRY-RUN preview` + `<pre>` 안에 변환기 JSON + `[Back to form] [Start over]`.
- **실패**: `❌ Upload failed` + Code / Reason / Request ID, 401 일 때만 `[Re-enter API Key]` 버튼 노출.

---

## 3. 데이터 흐름

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Webview (html.ts)                                                       │
│                                                                          │
│  파일 drop/browse  → state.file = {file, dataType}                       │
│  → 폼 입력 + validate                                                    │
│  → Send 클릭 → send({type:'requestUploadCredentials'})                   │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │ postMessage
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Extension Host (panel.ts L88-102)                                       │
│  case 'requestUploadCredentials':                                        │
│    baseUrl = store.getBaseUrl()                                          │
│    apiKey  = await store.getApiKey()   ← SecretStorage                  │
│    post({type:'uploadCredentials', baseUrl, apiKey})                     │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │ 한 번만 webview 로 전달
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Webview performUpload() (html.ts L603-681) ─ XHR 직접 호출              │
│                                                                          │
│  fd = FormData()                                                         │
│  fd.append('file', file, name)                                           │
│  fd.append(division/team/year/seq/classification/status/...)             │
│  fd.append(tags/agents/subject_keywords  ← join(','))                    │
│  fd.append(title_override/summary_override/derivation/quality_score/...) │
│                                                                          │
│  url = baseUrl + (dryRun ? '/api/convert/' : '/api/convert/ingest')      │
│  xhr.setRequestHeader('X-API-Key', apiKey)                               │
│  xhr.upload.onprogress → progress bar                                    │
│  xhr.onload → JSON.parse(responseText)                                   │
│    2xx → state.upload.response = body                                    │
│           send({type:'uploadResult', ok:true, recordId, status})         │
│    err → parse body.error.{code,message,request_id}                      │
│           send({type:'uploadResult', ok:false, httpStatus, requestId})   │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │ postMessage
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Extension Host (panel.ts L103-128)                                      │
│  case 'uploadResult':                                                    │
│    ok        → vscode.window.showInformationMessage(`uploaded ${id}`)    │
│    401       → showErrorMessage + "Re-enter API key" → InputBox(password)│
│    other err → showErrorMessage + [request_id] 포함                      │
└──────────────────────────────────────────────────────────────────────────┘
```

핵심 포인트:

- **API Key 저장**: `SecretStorage` (`configStore.ts` L33 `context.secrets.get/store/delete` — 키 이름 `aidh.apiKey`). settings.json 에는 절대 노출되지 않음.
- **baseUrl/connected**: `globalState` (configStore.ts L17/24).
- **헤더**: `apiClient.ts` L51 `if (this.apiKey) h['X-API-Key'] = this.apiKey;`
- **파일 바이트는 postMessage 를 통과하지 않음** — 호스트가 baseUrl/apiKey 한 번만 webview 로 넘기고, 실제 업로드는 webview 에서 XHR 로 직접 (apiClient.ts L100-104 주석에 명시).
- **/api/meta/options** 캐시: `OptionsCache` 5분 TTL (`optionsCache.ts` L9).
- **호환성**: `/api/system/health` 가 404 면 자동으로 legacy `/health` 폴백 (apiClient.ts L67-78).

---

## 4. 사용자 가이드 (`USER_GUIDE.md`, 7,833 B / 187 줄)

| § | 제목 | 상태 |
|----|------|------|
| 1 | Install (.vsix + verify 명령 3개) | ✅ |
| 2 | First launch — Settings/Welcome 화면 walkthrough | ✅ (텍스트 mock-up 있음, 스크린샷 ❌) |
| 3 | Test connection — 에러 코드 표 (NETWORK / INVALID_API_KEY / HTTP_500) | ✅ |
| 4 | Upload a file — DropZone + 폼 + DRY-RUN + Send + 에러 표 (401/413/415/422/500) | ✅ |
| 5 | Useful commands — 3개 명령 표 | ✅ |
| 6 | Troubleshooting A~F (서버 불가, 401 반복, 빈 패널, DnD 무반응, 413, vsce license) | ✅ |
| 7 | Developer (F5) workflow | ✅ |
| 8 | Build a new `.vsix` | ✅ |

**빠진 것**: 실제 캡처 이미지(스크린샷). 모든 화면이 ASCII / 코드블록 mock-up 으로 표현됨. 이미지가 들어갈 placeholder 도 따로 없음 — 즉 "스크린샷 자리" 표시는 없으나 후속 polish 로 추가하면 좋음.

---

## 5. 빌드 / 배포 명령 (package.json L49-55)

```powershell
npm run build     # = tsc -p ./                           → out/
npm run watch     # = tsc -watch -p ./                    개발용
npm run lint      # = tsc --noEmit                        타입 체크만
npm run package   # = vsce package                        → .vsix
```

`vscode:prepublish` 가 `npm run build` 를 호출하므로 `npm run package` 한 줄로 빌드+패키징 가능 (단, 첫 실행 시 USER_GUIDE 의 권장은 `--allow-missing-repository --no-dependencies` 플래그를 동반).

산출물 확인:

```
out/
├── extension.js (2,338 B)  ← entry
├── client/   apiClient.js, types.js
├── state/    configStore.js, optionsCache.js
└── webview/  html.js (31,386 B), panel.js (9,094 B), protocol.js
```

`.vscodeignore` 가 `src/**`, `docs/**`, `*.map`, `**/*.ts` 를 제외하고 `out/**/*.js` 와 `media/**` 만 포함하도록 화이트리스트 처리 — 그래서 vsix 가 21 KB 로 매우 작음.

---

## 6. 즉시 사용 가능 여부

**⚠ 일부 제약 — 즉시 설치/UI 동작은 OK, 백엔드 e2e 만 사용자 검증 필요**

### ✅ 가능한 것
- `code --install-extension ai-data-hub-uploader-0.4.0.vsix` 한 줄로 설치.
- 명령 팔레트에서 3개 명령(open/settings/reset) 사용.
- 첫 활성화 시 connected=false 라 자동으로 Welcome 패널 오픈 (`extension.ts` L21-23).
- 5-screen 흐름 (Welcome → Drop → Form → Sending → Result) 코드상 완결.
- 401 시 `Re-enter API key` 토스트 + InputBox 재입력 분기 (`panel.ts` L111-119).
- DRY-RUN 미리보기 + 실 ingest 분기 (`/api/convert/` vs `/api/convert/ingest`).

### ⚠ 검증 필요 / 후속 작업 후보
1. **사용자 e2e 미검증**: 실제 백엔드 (`api_server`) 와의 종단 테스트 — 특히 `/api/system/health` 가 `auth_required:true` 일 때 + `/api/meta/options` 응답 형식이 `MetaOptions` (types.ts L13-30) 와 정확히 일치하는지. Cline SR 같은 외부 호출자 통합 시나리오 별도 미테스트.
2. **USER_GUIDE 스크린샷 부재**: 텍스트 mock-up 만 있어 비개발자에게 첫인상이 약함.
3. **`package-lock.json` 버전 드리프트**: lock 안의 self-name 블록이 `0.0.1` 로 남아있음 — `npm install` 한 번 다시 돌려서 lock 갱신하면 깔끔.
4. **마이너**: `media/` 에 `icon.svg` 만 있고 `package.json` 에 `"icon"` 필드가 등록돼 있지 않아 marketplace 노출 시 기본 아이콘이 사용됨.

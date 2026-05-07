# Architecture — AI Data Hub Uploader

## 1. 컴포넌트 토폴로지

```
┌──────────────── VS Code ─────────────────┐         ┌──────────────────┐
│                                          │         │                  │
│  Extension Host (Node.js)                │         │  api_server      │
│  ┌────────────────────────────────────┐  │         │  (FastAPI)       │
│  │ extension.ts                        │  │         │                  │
│  │  • activate() → open Webview tab    │  │         │   GET /health    │
│  │  • register commands                │  │         │   GET /api/meta/ │
│  │  • SecretStorage / globalState      │  │         │       options    │
│  └─────────────┬───────────────────────┘  │         │   POST /api/     │
│                │ postMessage              │         │       convert/   │
│  ┌─────────────▼───────────────────────┐  │         │       ingest     │
│  │ Webview Panel (renderer)            │  │  HTTPS  │   POST /api/auth │
│  │  • React UI                         │◄─┼─────────┤        /keys/    │
│  │  • screens: Welcome/Drop/Form/Done  │  │         │        verify    │
│  │  • XHR with upload progress         │  │         │                  │
│  └─────────────────────────────────────┘  │         └──────────────────┘
└──────────────────────────────────────────┘
```

## 2. 책임 분담 (Extension Host vs Webview)

| 책임                         | Host | Webview |
|------------------------------|------|---------|
| Webview 패널 생성/포커스     |  ✅   |   —     |
| API Key 읽기/쓰기 (Secret)   |  ✅   |   —     |
| baseUrl 읽기/쓰기 (global)   |  ✅   |   —     |
| `/health`, `/meta/options`   |  ✅   |   —     |
| Settings UI 렌더링           |  —   |   ✅    |
| DropZone / Form UI           |  —   |   ✅    |
| 파일 멀티파트 업로드 (XHR)   |  —   |   ✅    |
| 진행률 이벤트 처리           |  —   |   ✅    |

> **API 호출 위치 결정**: 비-업로드 호출(`/health`, `/meta/options`, `/auth/keys/verify`)은 Extension Host 가 수행. 업로드는 Webview 가 직접(원본 `File` 객체에 host 가 접근하면 base64 직렬화로 비효율). 이 분담을 **`postMessage` 메시지 프로토콜** 로 깔끔하게 분리한다.

## 3. 메시지 프로토콜 (Host ↔ Webview)

```ts
// Webview → Host
type WebviewToHost =
  | { type: 'getConfig' }                                 // baseUrl + apiKey
  | { type: 'saveConfig'; baseUrl: string; apiKey: string }
  | { type: 'testConnection'; baseUrl: string; apiKey: string }
  | { type: 'fetchOptions' }                              // /api/meta/options
  | { type: 'openExternal'; url: string };

// Host → Webview
type HostToWebview =
  | { type: 'config';     baseUrl: string; hasApiKey: boolean }
  | { type: 'connection'; ok: boolean; error?: string }
  | { type: 'options';    payload: MetaOptions; error?: string };
```

**보안 원칙**:
- API Key 평문은 Webview 로 보내지 않는다 (`hasApiKey: boolean` 만).
- 업로드 시 Webview 가 `apiKey` 가 필요하면 host 가 헤더 인젝션 프록시 역할을 하지 않고, **단발성 토큰** 형태로 짧게 노출 후 메모리에서 즉시 폐기 (또는 Webview 가 host 에 'requestUploadHeaders' 를 요청 → host 가 헤더만 즉시 응답).

대안 (선호): host 가 Webview 의 업로드 stream 을 받아 자체 fetch 하는 패턴 — 큰 파일에서는 부담. 1 차 구현은 토큰 단발 노출 패턴으로 시작.

## 4. 모듈 트리 (구현 시)

```
src/
├── extension.ts                 # 진입점
├── webview/
│   ├── panel.ts                 # Webview Panel 생성/메시지 라우팅
│   ├── index.html               # Webview HTML shell
│   ├── app.tsx                  # React 루트 + 라우팅(상태머신)
│   ├── screens/
│   │   ├── WelcomeScreen.tsx
│   │   ├── DropZoneScreen.tsx
│   │   ├── FormScreen.tsx
│   │   ├── SendingScreen.tsx
│   │   └── ResultScreen.tsx
│   └── components/
│       ├── TagInput.tsx
│       └── FileCard.tsx
├── client/
│   ├── apiClient.ts             # fetch 래퍼 (host 측)
│   ├── uploader.ts              # XHR 진행률 (webview 측)
│   └── errors.ts                # 백엔드 envelope → UI 메시지
└── state/
    ├── secretStore.ts           # vscode.SecretStorage 래퍼
    ├── configStore.ts           # globalState 래퍼
    └── optionsCache.ts          # /meta/options 결과 캐시 (TTL 5min)
```

## 5. 활성화 트리거

`package.json` 의 `activationEvents` 와 명령:

```jsonc
{
  "activationEvents": [
    "onStartupFinished"
  ],
  "contributes": {
    "commands": [
      { "command": "aidh.openUploader", "title": "AI Data Hub: Open Uploader" },
      { "command": "aidh.openSettings", "title": "AI Data Hub: Settings" },
      { "command": "aidh.resetConnection", "title": "AI Data Hub: Reset Connection" }
    ],
    "viewsContainers": {
      "activitybar": [
        { "id": "aidh", "title": "AI Data Hub", "icon": "media/icon.svg" }
      ]
    }
  }
}
```

`onStartupFinished` + `globalState.aidh.connected !== true` 일 때만 자동으로 `aidh.openUploader` 를 호출.

## 6. CSP / 보안

Webview HTML 의 Content-Security-Policy:
```
default-src 'none';
img-src vscode-resource: https:;
script-src 'nonce-{nonce}';
style-src vscode-resource: 'unsafe-inline';
connect-src {baseUrl};
```

- `connect-src` 에 사용자가 입력한 `baseUrl` 을 동적으로 주입 (정규화 후).
- 프로토콜 미지정 시 `http://` 로 폴백, IP 만 입력 시 `:8000` 자동 추가 가능.

## 7. 에러 매핑

| 백엔드 응답                            | UI 메시지                                |
|----------------------------------------|------------------------------------------|
| `connection refused` / `ECONNREFUSED`  | "서버에 연결할 수 없습니다. IP/포트 확인" |
| 401 / `INVALID_API_KEY`                | "API Key 가 유효하지 않습니다"            |
| 413 / `PAYLOAD_TOO_LARGE`              | "파일 크기 초과 ({max}MB)"                |
| 415 / `UNSUPPORTED_FORMAT`             | "지원하지 않는 형식: {ext}"               |
| 422 / `VALIDATION_ERROR`               | 첫 detail 메시지 표시                     |
| 500 / `CONVERSION_FAILED`              | "변환 실패 — 백엔드 로그 확인 필요"       |
| 501 / `PDF_NOT_AVAILABLE`              | "PDF 변환기 미설치"                       |

## 8. 테스트 전략

- **Unit (host)**: `apiClient`, `errors` — `vitest` + `nock`/`msw-node`.
- **Webview UI**: storybook 으로 화면별 스냅샷 + jest-dom.
- **E2E**: VS Code `@vscode/test-electron` — 가짜 백엔드(`fastify` mock) 띄우고 활성화→설정→드롭→Send→결과 시나리오.

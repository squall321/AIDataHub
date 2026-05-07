# AI Data Hub Uploader — Plan

| 항목      | 값                                                |
|-----------|---------------------------------------------------|
| Feature   | `vscode_extension` (AI Data Hub Uploader)        |
| Phase     | Plan                                              |
| Date      | 2026-05-08                                        |
| 백엔드    | `AI_data/api_server` (FastAPI, port 8000)         |
| 사용자    | 사업부 현장 엔지니어 (CLI 비숙련자)               |

---

## 1. 배경 / 문제

`api_server` 는 **`POST /api/convert/ingest`** 로 원본 파일(`.docx`/`.xlsx`/`.pptx`/`.md`/`.pdf`) 을 직접 받아 변환→DB 적재까지 한 번에 처리해 준다. 그러나 이 엔드포인트를 호출하려면:

1. `curl` / `requests` 같은 HTTP 클라이언트 사용법
2. `division` / `team` / `year` / `seq` / `tags` / `agents` 등 **메타 폼 필드 값**
3. `X-API-Key` 헤더

이 모두가 필요하다. 사업부 엔지니어 입장에서는 **CLI 진입 자체가 진입 장벽** 이고, 결과적으로 데이터 적재가 한두 명에게만 집중된다.

## 2. 해결책

- VS Code 확장으로 **드래그 & 드롭 + 폼 1회 입력 → 적재** 를 제공.
- 첫 실행 시 백엔드 IP / API Key 만 한 번 설정하면 이후는 **파일을 끌어다 놓고 보내기** 만으로 완결.

## 3. 산출물 범위 (Scope)

### In scope
- VS Code 확장 (TypeScript, Webview 기반 UI).
- 첫 실행 시 자동으로 새 탭에 **Settings 페이지** 오픈.
- IP/Port/API Key 입력 → `GET /health` + `GET /api/agents` 로 연결 검증.
- 드롭존 화면 (`webview` panel).
- 파일 종류 자동 인식 → 메타데이터 폼 동적 표시.
- 메타데이터 폼 검증.
- `POST /api/convert/ingest` 호출 → 진행률/결과 표시.
- API Key 는 VS Code `SecretStorage` 에 저장 (`settings.json` 평문 금지).

### Out of scope (이 사이클에서 제외)
- 자체 인증 로그인 화면 (백엔드의 `X-API-Key` 만 사용).
- 적재된 레코드 조회/검색 화면 (별도 사이클에서).
- 멀티 파일 동시 업로드 진행률 그래프 (단일 파일부터 우선).
- pgvector 임베딩 트리거 (백엔드가 자동으로 처리).

## 4. UX 흐름

자세한 내용은 [`ux_flow.md`](ux_flow.md) 참고. 핵심 4 단계:

```
[1] First launch
    └─ 활성화 시 Webview 패널 "AI Data Hub" 자동 오픈
       → 화면: Settings (IP / Port / API Key)
       → [Test Connection] → /health → /api/agents → 토스트 OK

[2] Normal launch (이미 설정 + 연결 OK)
    └─ Activity Bar 아이콘 → "AI Data Hub" Webview 오픈
       → 화면: Drop Zone

[3] File drop
    └─ 파일이 드롭되면:
       - 확장자 → data_type 자동 추론
       - 백엔드 /api/meta/options 응답으로 Division/Team/Agents 셀렉트박스 채움
       - 메타데이터 폼 표시 (필수/선택 분리)
       - [Send] 버튼 활성화는 필수 필드가 모두 채워져야 가능

[4] Send
    └─ POST /api/convert/ingest (multipart)
       - 진행률(업로드) 표시
       - 응답 status (inserted / updated / skipped) 토스트
       - 실패 시 백엔드 error envelope 의 message 그대로 표시
```

## 5. 메타데이터 양식

[`metadata_spec.md`](metadata_spec.md) 가 **단일 진실 (single source of truth)**. 이 문서는 백엔드 수정계획서(`api_server/docs/extension_integration_plan.md`) 와 1:1 일치해야 한다.

핵심 합의:

- **조직/식별 (4)**: `division`, `team`, `year`, `seq`
- **분류 (4)**: `classification`, `status`, `domain`, `language`
- **검색/연결 (3)**: `tags`, `agents`, `subject_keywords`
- **타이틀/요약 (2, optional override)**: `title`, `summary`
- **품질 (3, optional)**: `quality_score`, `valid_from`, `valid_until`

`division` / `team` / `agents` / `classification` / `status` 등은 **백엔드가 권위적인 enum 목록을 내려주는 신규 엔드포인트** 가 필요 — 이게 백엔드 수정계획서의 핵심 요청.

## 6. 아키텍처 / 모듈

자세한 내용은 [`architecture.md`](architecture.md). 요약:

| 레이어        | 모듈                          | 역할                                            |
|---------------|-------------------------------|-------------------------------------------------|
| Activation    | `extension.ts`                | 첫 실행 감지, Webview 패널 라이프사이클          |
| Webview UI    | `webview/app.tsx` + html/css  | Settings / DropZone / Form / Result 화면         |
| API Client    | `client/apiClient.ts`         | fetch 래퍼, baseUrl/apiKey 주입, 에러 정규화     |
| State         | `state/secretStore.ts`        | API Key 보관 (`SecretStorage`)                   |
|               | `state/configStore.ts`        | baseUrl 보관 (`globalState`)                     |
| Messaging     | postMessage bridge            | Webview ↔ Extension Host (파일/시크릿 접근)     |

기술 스택:

- TypeScript 5+, `@types/vscode` ^1.85
- Webview UI: 가벼운 React + Vite (esbuild 도 가능, 의존성 최소화 우선)
- HTTP: 표준 `fetch` (Node 18+ 동봉) — 서드파티 클라이언트 미사용
- 패키징: `vsce package`

## 7. 백엔드 변경 요청

확장이 잘 동작하려면 **백엔드의 작은 변경** 이 필요하다. 별도 문서로 분리:

- `api_server/docs/extension_integration_plan.md`

요약:
1. **CORS 설정**: `vscode-webview://*` origin 허용.
2. **`GET /api/meta/options`** 신규 — Division / Team / Agents / Classification / Status 등 enum 목록 반환.
3. **`POST /api/auth/keys/verify`** 신규 — 보유 키가 유효한지만 헤더 검사 (boot 키 불필요).
4. **에러 envelope 통일** — `/api/convert/ingest` 도 `{error:{code,message,request_id}}` 로 보장.
5. **업로드 진행률**: HTTP 표준 chunked 가 충분하므로 추가 작업 불필요 (Webview 의 `XMLHttpRequest` 의 `upload.onprogress` 사용).

## 8. 검증 (Done Criteria)

- [ ] 새로 설치한 VS Code 에 `.vsix` 설치 → 첫 활성화 시 Settings 탭이 자동으로 뜬다.
- [ ] 잘못된 IP/Key 로 [Test Connection] → 에러 메시지가 사람이 읽을 수 있는 형태.
- [ ] 정상 IP/Key → DropZone 진입.
- [ ] `.docx` 드롭 → division/team 셀렉트 박스가 백엔드에서 받은 옵션으로 채워짐.
- [ ] 필수 필드 미입력 → [Send] 비활성.
- [ ] [Send] → `record_id` 와 `status` 가 표시됨.
- [ ] 동일 파일 재전송 → `status: skipped` 처리됨이 명확히 보임.
- [ ] API Key 가 `settings.json` 에 평문 저장되지 않음 (`SecretStorage` 검증).
- [ ] 5MB 이상 파일 업로드 시 진행률 게이지가 움직임.

## 9. 일정 (러프)

| 단계 | 산출물                               | 예상   |
|------|--------------------------------------|--------|
| P1   | Plan/Design 문서 (이 폴더)            | D+0    |
| P2   | 백엔드 변경 머지 (extension_integration_plan) | D+2 |
| P3   | 확장 스캐폴딩 (`yo code`) + Settings | D+3    |
| P4   | DropZone + 메타 폼                   | D+5    |
| P5   | `/api/convert/ingest` 연동 + 진행률  | D+6    |
| P6   | 에러 처리 / SecretStorage / e2e      | D+7    |
| P7   | `.vsix` 패키징 + 사용자 가이드       | D+8    |

## 10. 위험 / 완화

| 위험                                   | 완화                                         |
|----------------------------------------|---------------------------------------------|
| 백엔드 enum 옵션이 늦게 확정됨         | 옵션 미수신 시 자유 입력 텍스트박스로 폴백  |
| Webview 가 sandboxed → fetch CORS 차단 | 백엔드에서 `vscode-webview` origin 허용     |
| 큰 파일 업로드 중 끊김                 | `MAX_UPLOAD_MB` 표시 + 사전 사이즈 차단     |
| API Key 탈취                           | `SecretStorage` + 화면 마스킹 + 복사 금지   |
| `division` 가 free-form 인 기존 데이터 | 백엔드 옵션 응답에 `allow_custom: true` 옵션|

## 11. 다음 액션

1. 본 문서 + `metadata_spec.md` + `architecture.md` 리뷰.
2. `api_server/docs/extension_integration_plan.md` 를 백엔드 에이전트(`bkit:bkend-expert`) 에게 넘김.
3. 백엔드 변경 머지 후 P3 (확장 스캐폴딩) 시작.

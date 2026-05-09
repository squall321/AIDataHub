# AI Data Hub — 클라이언트 셋업 가이드

> 클라이언트 측에서 AI Data Hub API 를 소비하는 모든 주체를 위한 **참조 문서**.
> 입문 / 빠른 시작은 `client_setup/README.md` 를 보고, 이 문서는 reference 로 활용하라.

---

## 클라이언트 = 누구

| 역할 | 사용 목적 | 주력 도구 |
|------|-----------|-----------|
| **AI 에이전트** (Claude / GPT / Cline SR / 자체 모델) | 자연어 질의 → 본문 인용 → 답변 생성 | REST `/api/ask`, `/api/search`, `/api/records/{id}` 또는 MCP 도구 |
| **사업부 엔지니어** (비개발자) | 자기 부서 자료 적재 (`.docx/.xlsx/.pptx/.pdf/.md/.html`) — **GUI 선호** | **VS Code Extension** (`vscode_extension/ai-data-hub-uploader-0.1.0.vsix`) |
| **사업부 엔지니어** (CLI 친화) | 같은 적재 — **명령어/CI 친화** | `client_setup/ingest.bat` |
| **분석가** | 자료 검색 / 관련 자료 탐색 | `client_setup/search.bat`, `related.bat`, `ask.bat` |

세 부류 모두 **동일한 API Key + 서버 URL** 을 공유한다. 차이는 **GUI 인지 명령어인지** 뿐.

---

## 두 가지 클라이언트 옵션

이 배포 패키지는 **두 가지 클라이언트** 를 제공한다. 사용자 환경/선호에 따라 선택.

### A. VS Code Extension (GUI, 비개발자 친화)

- 위치: [`vscode_extension/ai-data-hub-uploader-0.1.0.vsix`](./vscode_extension/ai-data-hub-uploader-0.1.0.vsix)
- 설치: VS Code 명령 팔레트 → `Extensions: Install from VSIX...` 선택 또는
  ```bat
  code --install-extension vscode_extension\ai-data-hub-uploader-0.1.0.vsix
  ```
- 사용: VS Code 명령 팔레트 → `AI Data Hub: Open Uploader` → Webview 5-step
  (Welcome → DropZone → Form → Sending → Result)
- API Key 저장: VS Code SecretStorage (`aidh.apiKey`) — 평문 디스크 저장 X
- 자세한 사용법: [`vscode_extension/USER_GUIDE.md`](./vscode_extension/USER_GUIDE.md)

**언제 쓰나**: 자료를 가끔(주 1~2회) 적재하는 사업부 엔지니어. 파일 끌어다 놓기 → 폼 입력 → 전송.

### B. 배치 파일 (CLI, 자동화 친화)

- 위치: [`client_setup/`](./client_setup/) 폴더
- 명령 6종: `ask.bat / search.bat / get.bat / ingest.bat / related.bat / show_guide.bat`
- API Key 저장: `client_setup/config.ini` (운영자 PC 에서 보호)

**언제 쓰나**: AI 에이전트 / 분석가 / 자동화 스크립트 / 일괄 적재. 명령행에서 직접 쓰거나 다른 스크립트가 호출.

> 둘 다 같은 백엔드 API 를 쓰므로, 한 사용자가 둘 다 사용해도 OK (config.ini 와 SecretStorage 가 서로 분리).

---

## 한 줄 요약

> `config.ini` 채우고 `setup.bat` 더블클릭 → 이후 `ask.bat` / `search.bat` 명령으로 끝.

---

## 사전 요구

- Windows 10 / 11 (PowerShell 5.1+ 기본 탑재)
- API 서버 주소 + API Key (서버 운영자에게 받아라)
- (선택) AI 모델 사이즈 결정 — `tiny` / `small` / `medium` / `large`
- 필수 권한: 명령 프롬프트에서 `powershell` 실행 가능 (대부분 사내 PC 기본 허용)

---

## 3단계 셋업

### 1. config.ini 작성

`config.example.ini` 를 같은 폴더에 `config.ini` 로 복사 후 편집.
(또는 `setup.bat` 를 한 번 실행하면 자동 복사된다.)

```ini
[server]
base_url = http://aidh.intra.example.com:8000   ; API 서버 주소
api_key  = your-issued-api-key                  ; 발급받은 X-API-Key

[model]
size = small                                    ; tiny | small | medium | large

[upload]
division = HE                                   ; ingest 시 기본값
team     = CAE
year     = 2026
seq      = 0                                    ; 0 → MAX+1 자동 부여

[output]
guide_dir = docs                                ; 가이드 저장 폴더
```

| 키 | 필수 | 설명 |
|----|------|------|
| `server.base_url` | O | 서버 호스트 + 포트 (`http://...:8000`) |
| `server.api_key` | O | `X-API-Key` 헤더에 첨부됨 |
| `model.size` | O | 가이드 자동 매칭에 사용 |
| `upload.*` | △ | 비워도 ingest 시 기본 이름으로 ingest |
| `output.guide_dir` | △ | 기본값 `docs` |

### 2. setup.bat 더블클릭

자동 처리 (4단계):

1. `config.ini` 검증 (필드 누락 / 기본값 미수정 검출)
2. 서버 연결 테스트 — `GET /api/system/health`
3. **모델 사이즈에 맞는 가이드 자동 다운로드** — `GET /api/docs/agent-guide?size={size}` → `docs/AGENT_API_GUIDE_<SIZE>.md` (~10–50 KB)
4. **카탈로그 캐시** — `GET /api/discover` → `docs/discover_cache.json`

### 3. 일상 사용

| 명령 | 매핑 API | 용도 |
|------|----------|------|
| `ask.bat "질문"` | `POST /api/ask` | 자연어 질의 |
| `search.bat <mode> "키워드" [limit]` | `GET /api/search` | 검색 (4종) |
| `get.bat <record_id>` | `GET /api/records/{id}` | 레코드 본문 조회 |
| `ingest.bat "<file>"` | `POST /api/convert/ingest` | 자료 적재 |
| `related.bat <record_id> [mode] [limit]` | `GET /api/records/{id}/related` | 비슷한 레코드 |
| `show_guide.bat` | (로컬) | 자기 모델 가이드 출력 |

---

## AI 에이전트가 사용하는 흐름

### Cline SR / Claude Desktop / 자체 LLM

1. **가이드 흡수** — `docs/AGENT_API_GUIDE_<SIZE>.md` 를 시스템 프롬프트에 주입
2. **매 질의마다** — `POST /api/ask` (자연어) 또는 `GET /api/search?mode=semantic&q=...` (키워드)
3. **응답의 `record_id`** → `GET /api/records/{id}` 로 본문 흡수
4. **답변 생성** + 출처 인용 (record_id, division/team/year/seq)

#### 시스템 프롬프트 예시

```text
You are an AI assistant connected to the AI Data Hub at {base_url}.
For queries about company documents, follow this workflow:
  1) POST /api/ask with body {"q": "..."} for natural language
  2) GET /api/search?mode=semantic&q=... for keyword
  3) GET /api/records/{id} for full content
Authentication: include header `X-API-Key: <api_key>` on every request.
Full guide: GET /api/docs/agent-guide?size=small
Always cite the record_id of any source you reference.
```

### MCP 도구 사용 (Cline SR / Claude Desktop)

서버는 MCP 도구를 함께 노출한다. agent 가 도구를 호출할 수 있을 때 다음을 사용:

| 도구 | 설명 |
|------|------|
| `discover_schema()` | 자료 구조 / 필드 / 기본 워크플로우 |
| `discover_capabilities()` | 검색 모드, 임계값, 폴백 사다리 |
| `ask(q)` | 자연어 질의 (raw `/api/ask` 의 wrapper) |
| `search(mode, q, limit)` | 의미/전문/태그/키워드 검색 |
| `get_record(record_id)` | 본문 조회 |
| `find_related(record_id, mode)` | 비슷한 레코드 |
| `explain_field(field)` | 필드 의미 설명 |
| `ingest(file_path, ...)` | 자료 적재 |

MCP 클라이언트(예: Claude Desktop)에서 `aidh-mcp` 서버를 등록하면 자동 노출.

---

## 모델 사이즈별 가이드 매트릭스

`config.ini` 의 `[model] size` 가 가이드 선택을 결정. 서버는 4종 가이드 중 해당 사이즈를 반환.

| size | 모델 예시 | 가이드 분량 (단어) | 토큰 예산 |
|------|-----------|-------------------:|----------:|
| `tiny` | phi-3-mini, smolllm-1.7B, qwen-2.5-1.5B | ~1500 | < 8K |
| `small` | llama-3 8B, qwen-2.5 7B, mistral 7B | ~4000 | ~16K |
| `medium` | llama-3 70B, qwen-2.5 32B/72B, mixtral | ~6000 | ~24K |
| `large` | Claude Opus, GPT-4o, Gemini frontier | ~10000 | ~40K |

가이드 차이:

- **tiny** — cheatsheet 형식. 5개 핵심 endpoint + 5개 에러 코드.
- **small** — 표 위주, 결정 트리 간략.
- **medium** — 폴백 사다리, 신뢰도 조정 룰.
- **large** — 디자인 결정 배경 + 신뢰도 평가 + 인용 정책.

자동 매칭 — `setup.bat` 가 `config.ini` 의 `size` 값으로 가이드 한 종만 다운로드.

---

## 흔한 에러 (PowerShell 헬퍼가 자동 매핑)

`lib/_common.ps1` 의 `Invoke-Aidh` 가 다음을 자동으로 친절하게 표시:

| 코드 | 메시지 (사용자에게 노출) | 대응 |
|------|------------------------|------|
| 401 | 인증 실패 — config.ini 의 api_key 확인 | api_key 발급/오타 확인 |
| 403 | 권한 부족 — 운영자에게 scope 확인 요청 | API key 권한 상승 요청 |
| 404 | 자원 없음 — id / 경로 확인 | record_id, URL 점검 |
| 422 | 검증 실패 — 응답의 detail 확인 | request body 의 필드/타입 |
| 429 | 너무 많은 요청 — 잠시 후 재시도 | rate-limit, backoff |
| 500 | 서버 오류 — 운영자에게 로그 확인 요청 | 재현 케이스 운영자 전달 |
| 503 | embedding 미준비 — fts 모드로 폴백 | `search.bat fts ...` 로 재시도 |

> 503 은 서버 부팅 직후 / 백필 중에 일시적으로 나타날 수 있다. semantic 대신 fts 로 자동 폴백 권장.

---

## 시연 워크플로우 (5분)

```bat
:: 1) 셋업 (한 번만)
setup.bat

:: 2) 자료 적재
ingest.bat "C:\reports\my_doc.docx"
::    → record_id: DOC-HE-CAE-2026-XXXXXX 받음

:: 3) 자연어 질의
ask.bat "이 문서의 핵심 내용은?"

:: 4) 의미 검색
search.bat semantic "stress strain"

:: 5) 비슷한 자료 찾기
related.bat DOC-HE-CAE-2026-001001

:: (옵션) 모델 가이드 다시 보기
show_guide.bat
```

---

## 검색 모드 선택 가이드

| 상황 | 추천 모드 | 비고 |
|------|----------|------|
| 의미는 같지만 단어가 다를 때 ("강도" vs "stress") | `semantic` | embedding 필요 (503 시 fts 폴백) |
| 특정 한국어 표현 정확히 매칭 | `fts` | 한국어 형태소 분석 |
| 이미 정해진 태그 분류 | `tag` | 정확 매칭, 빠름 |
| 단순 부분 문자열 (이름/제목) | `keyword` | LIKE 검색, 가장 단순 |

503 발생 시 `semantic → fts → keyword` 순으로 폴백.

---

## 폴더 구조

```text
client_setup/
├── README.md                ← 빠른 시작 (입문)
├── config.example.ini       ← 템플릿 (수정 X)
├── config.ini               ← 사용자 작성 (gitignore 권장)
├── setup.bat                ← 첫 실행
├── ask.bat
├── search.bat
├── get.bat
├── ingest.bat
├── related.bat
├── show_guide.bat
├── lib/                     ← 내부 (수정 X)
│   ├── _common.ps1          ← config 읽기 + REST 호출 + 에러 매핑
│   ├── setup.ps1
│   ├── ask.ps1
│   ├── search.ps1
│   ├── get.ps1
│   ├── ingest.ps1
│   ├── related.ps1
│   └── show_guide.ps1
└── docs/                    ← setup.bat 후 자동 생성
    ├── AGENT_API_GUIDE_<SIZE>.md   ← 자기 모델 사이즈 가이드
    └── discover_cache.json         ← /api/discover 응답 캐시
```

---

## 운영 팁

### 가이드 갱신
서버에서 가이드 새 버전을 배포하면 `setup.bat` 만 다시 실행하면 된다.
(기존 `docs/AGENT_API_GUIDE_<SIZE>.md` 를 덮어쓴다.)

### config.ini 보안
- `api_key` 는 **개인별로 발급**받은 값을 넣어라. 공유 금지.
- 폴더 전체를 gitignore / .git 에서 제외하거나, 최소 `config.ini` 한 줄만 ignore 등록.

### 여러 환경 병용 (dev / prod)
- `client_setup/` 폴더를 통째로 `client_setup_dev/`, `client_setup_prod/` 로 복제
- 각 폴더의 `config.ini` 를 다르게 설정

### 사이즈 변경
`config.ini` 의 `model.size` 값을 수정한 뒤 `setup.bat` 재실행 → 새 가이드 다운로드.

---

## 한 줄 요약 (재확인)

> **config.ini 채우고 setup.bat 한 번. 이후 ask/search/get/ingest 만.**
> AI 에이전트는 `docs/AGENT_API_GUIDE_<SIZE>.md` 를 시스템 프롬프트에 주입하면 끝.

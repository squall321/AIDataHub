# AI Data Hub 클라이언트 셋업

이 폴더의 배치 파일들로 **사용자가 신경쓰지 않고** AI Data Hub API 를 사용한다.
모든 명령은 **Windows 탐색기에서 더블클릭** 또는 **명령 프롬프트** 에서 실행 가능.

## 빠른 시작 (3단계)

### 1. config.ini 만들기

`config.example.ini` 를 같은 폴더에 `config.ini` 로 복사한다.

```text
config.example.ini  →  config.ini  (복사)
```

`setup.bat` 를 한 번 실행하면 config.ini 가 없을 때 자동으로 복사한다.

### 2. config.ini 편집

메모장 또는 VS Code 로 `config.ini` 열어서 다음을 채운다:

```ini
[server]
base_url = http://localhost:8000        # API 서버 주소
api_key  = your-api-key-here            # 운영자에게 발급받은 키

[model]
size = small                            # tiny | small | medium | large
```

### 3. setup.bat 더블클릭

자동으로 다음을 수행한다:

1. config.ini 검증
2. 서버 연결 테스트 (`/api/system/health`)
3. **자기 모델 사이즈에 맞는 가이드 다운로드** (`/api/docs/agent-guide?size=...`)
4. 카탈로그 캐시 (`/api/discover` → `docs/discover_cache.json`)

이후 사용 가능.

---

## 사용 가능한 명령어

### `ask.bat "질문"`
자연어 질의 → 자동 답변 (`POST /api/ask`).

```bat
ask.bat "AI 도입 현황은?"
ask.bat "낙하 시뮬레이션 결과를 알려줘"
```

### `search.bat <mode> "키워드" [limit]`
4가지 모드로 검색 (`GET /api/search`).

| mode | 용도 |
|------|------|
| `semantic` | 벡터 유사도 (의미 기반) |
| `fts` | 한국어 형태소 전문 검색 |
| `tag` | 정확한 태그 필터 |
| `keyword` | 단순 LIKE 검색 |

```bat
search.bat semantic "stress strain"
search.bat fts "낙하 시뮬레이션" 10
search.bat tag "IGA,NURBS"
```

### `get.bat <record_id>`
한 레코드 전체 조회 (`GET /api/records/{id}`).

```bat
get.bat DOC-HE-CAE-2026-001001
```

### `ingest.bat "<file_path>"`
자료 적재 (`POST /api/convert/ingest`). `.docx .xlsx .pptx .md .pdf .html` 지원.

```bat
ingest.bat "C:\Users\me\Documents\report.docx"
```

`config.ini` 의 `[upload]` 섹션이 division/team/year/seq 기본값을 결정.

### `related.bat <record_id> [mode] [limit]`
비슷한 레코드 찾기 (`GET /api/records/{id}/related`).

```bat
related.bat DOC-HE-CAE-2026-001001
related.bat DOC-HE-CAE-2026-001001 tag 10
```

### `show_guide.bat`
자기 모델 사이즈에 맞는 가이드 출력 (`docs/AGENT_API_GUIDE_{SIZE}.md`).

---

## 폴더 구조

```text
client_setup/
├── README.md                ← 이 파일
├── config.example.ini       ← 템플릿
├── config.ini               ← 사용자 작성 (gitignore 권장)
├── setup.bat                ← 첫 실행
├── ask.bat
├── search.bat
├── get.bat
├── ingest.bat
├── related.bat
├── show_guide.bat
├── lib/                     ← 내부 사용 (수정 X)
│   ├── _common.ps1          ← 공용 헬퍼 (config 읽기, REST 호출)
│   ├── setup.ps1
│   ├── ask.ps1
│   ├── search.ps1
│   ├── get.ps1
│   ├── ingest.ps1
│   ├── related.ps1
│   └── show_guide.ps1
└── docs/                    ← setup.bat 실행 후 자동 생성
    ├── AGENT_API_GUIDE_<SIZE>.md
    └── discover_cache.json
```

---

## 모델 사이즈 → 가이드 자동 매칭

`config.ini` 의 `[model] size` 가 가리키는 모델 크기를 API 서버에 전달.
서버는 4종 가이드 중 해당 사이즈 파일을 반환.

| size | 모델 예시 | 가이드 분량 |
|------|-----------|-------------|
| `tiny` | phi-3-mini, smolllm-1.7B, qwen-2.5-1.5B | ~1500 단어 (cheatsheet) |
| `small` | llama-3 8B, qwen-2.5 7B, mistral 7B | ~4000 단어 (표 위주) |
| `medium` | llama-3 70B, qwen-2.5 32B/72B | ~6000 단어 (결정+폴백 사다리) |
| `large` | Claude Opus, GPT-4o, Gemini | ~10000 단어 (디자인 결정 + 신뢰도 평가) |

가이드는 `docs/` 폴더에 markdown 으로 저장. AI agent 의 시스템 프롬프트에 `Get-Content docs/AGENT_API_GUIDE_*.md` 로 주입.

---

## 에러 자동 처리

PowerShell 헬퍼 (`lib/_common.ps1`) 가 다음을 자동 처리:

| 코드 | 메시지 |
|------|--------|
| 401 | "인증 실패 — config.ini 의 api_key 확인" |
| 403 | "권한 부족 — 운영자에게 scope 확인 요청" |
| 404 | "자원 없음 — id / 경로 확인" |
| 422 | "검증 실패 — 응답의 detail 확인" |
| 429 | "너무 많은 요청 — 잠시 후 재시도" |
| 503 | "embedding 미준비 — fts 모드로 폴백" |

---

## 흔한 운영 흐름

```bat
:: 1) 한 번만 — 첫 셋업
setup.bat

:: 2) 일상 사용
ask.bat "배터리 낙하 시뮬레이션 결과는?"
search.bat semantic "stress concentration"
get.bat DOC-HE-CAE-2026-001001
related.bat DOC-HE-CAE-2026-001001

:: 3) 새 자료 적재
ingest.bat "C:\reports\new_iga_analysis.docx"

:: 4) 가이드 갱신 (서버 가이드가 새 버전이면)
setup.bat
```

---

## 한 줄 요약

> **config.ini 만 채우고 setup.bat 한 번 더블클릭하면 끝.**
> 이후 ask/search/get/ingest 배치 파일이 모든 API 호출을 자동 처리.

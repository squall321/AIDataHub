# AI Data API Server

사업부 문서 AI 데이터화 플랫폼의 백엔드 API 서버.
**Word(.docx) → JSON → PostgreSQL → REST/MCP** 파이프라인으로,
사내 LLM 에이전트(Cline SR 등)가 사업부 문서/데이터를 표준 인터페이스로 조회할 수 있게 한다.

## 아키텍처

```text
[사업부 문서 .docx]
         │
         ▼
   ┌──────────┐  Word 파서 (python-docx + lxml)
   │converter │  → schema_v1 JSON
   └────┬─────┘
        │
        ▼
   ┌──────────┐  RecordIn 정규화 (data_type별 검증)
   │ ingest   │  → SQLAlchemy upsert
   └────┬─────┘
        │
        ▼
   ┌──────────┐  records / record_sections / agents / agent_records
   │PostgreSQL│  + GIN 인덱스 (tags / agents / JSONB)
   └────┬─────┘
        │
        ├──── REST ───────► /api/data, /api/records, /api/search,
        │                   /api/agents, /api/analytics, /api/views,
        │                   /api/attachments, /api/auth, /metrics
        │
        ├──── 정적 ───────► /figures/{...}, /attachments/{...}
        │
        └──── MCP stdio ──► Cline SR / Claude Desktop / etc.
                            (query_data, list_agents, get_record, search)
```

세부 다이어그램과 ID 포맷은 [`docs/data_model.md`](docs/data_model.md) 참고.

## Quick Start (5 commands)

```powershell
# 1. venv + 의존성
py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt

# 2. PostgreSQL 준비 (Docker 예)
docker run -d --name ai-data-pg -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=ai_data -p 5432:5432 postgres:16

# 3. 마이그레이션
$env:PYTHONPATH = "src"; alembic upgrade head

# 4. 첫 레코드 적재
python -m converter "d:\tmp\iga_guide_test.docx" --division HE --team CAE --year 2026 --seq 1 --output-dir output ; python -m api.ingest .\output\HE-CAE-2026-0000000001.json

# 5. 서버 기동
python -m api.main
```

API 문서: <http://localhost:8000/docs>

## 컴포넌트 구조

```text
api_server/
├── src/
│   ├── api/                 # FastAPI 앱
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── db/              # SQLAlchemy 2.0 (Base/엔진/모델)
│   │   ├── schemas/         # Pydantic (RecordIn/Out, data_type별)
│   │   ├── ingest/          # JSON → DB writer + CLI (`python -m api.ingest`)
│   │   ├── routes/          # /api/data, /api/records, /api/search, ...
│   │   └── services/        # 검색/통계 서비스 로직
│   ├── converter/           # .docx → schema_v1 JSON 변환기
│   └── mcp_server/          # MCP stdio 서버 (Cline SR 진입점)
├── alembic/                 # DB 마이그레이션 (asyncpg)
├── tests/
│   ├── conftest.py          # SQLite 인메모리 + 표본 픽스처
│   ├── test_health.py
│   ├── test_legacy_compat.py
│   └── integration/
│       └── test_full_flow.py
├── docs/
│   ├── api_reference.md
│   ├── mcp_integration.md
│   ├── data_model.md
│   └── setup_guide.md
├── requirements.txt
├── pyproject.toml
└── alembic.ini
```

## 기술 스택

| 레이어       | 선택                                          |
|--------------|-----------------------------------------------|
| Web          | FastAPI + uvicorn                             |
| DB           | PostgreSQL 16+ / pgvector(시맨틱 검색)        |
| ORM          | SQLAlchemy 2.0 (asyncio) + asyncpg            |
| 마이그레이션 | Alembic (0001~0009)                           |
| 검증         | Pydantic v2 / pydantic-settings               |
| Word 파서    | python-docx, lxml                             |
| Excel 파서   | openpyxl                                      |
| MCP          | `mcp` Python SDK (>=1.2.0, stdio)             |
| 인증         | API key (X-API-Key, sha256 해시)              |
| 관측성       | prometheus-client + JSON 구조화 로그          |
| 테스트       | pytest + pytest-asyncio + httpx + aiosqlite   |

## 데이터 타입

| data_type      | 설명             | 예시 ID                   |
|----------------|------------------|---------------------------|
| DOC            | 문서/가이드      | `DOC-HE-CAE-2026-0000000001`  |
| DATA           | 측정/시험 데이터 | `DATA-HE-CAE-2026-0000000018` |
| SIM            | 시뮬레이션 결과  | `SIM-HE-CAE-2026-0000000045`  |
| CAD            | CAD 모델         | `CAD-HE-CAE-2026-0000000012`  |
| LOG/FORM/OTHER | 보조             | (동일 패턴)               |

ID 포맷, 스키마 디테일: [`docs/data_model.md`](docs/data_model.md).

## 첨부파일 일반화

그림뿐 아니라 9종 첨부 모두 캡션 의무 + 메타데이터(해시·크기·MIME) 보존.
실제 파일은 `ATTACHMENTS_DIR` 하위에 `{record_id}/A001.{ext}` 로 저장,
`/attachments/{...}` 정적 마운트 또는 `GET /api/records/{id}/attachments` 메타 조회.

| kind | 예시 | 변환기 (소스 → DOC/DATA JSON) |
|------|------|------------------------------|
| figure | png, jpg, gif, wmf, emf | (첨부로만) |
| document | docx, **pdf** | docx → `converter`, **pdf → `pdf_converter`** |
| spreadsheet | xlsx, csv | xlsx → `excel_converter` |
| slide | pptx | pptx → `ppt_converter` |
| markup | md, markdown | md → `md_converter` |
| media | mp3, mp4 | (첨부로만) |
| archive | zip, 7z | (첨부로만) |
| cad | step, catpart, sldprt | (첨부로만) |
| drawing | dwg, dxf | (첨부로만) |
| data | json, xml, yaml | (첨부로만) |
| other | (그 외) | — |

## 분류 API (views)

같은 일반화 데이터에서 모양별로 슬라이스해서 가져온다:

```
GET /api/views/hierarchical          # sections+blocks 있는 것만 (DOC 등)
GET /api/views/tabular               # tables 있는 것만 (DATA, DOC 일부)
GET /api/views/generalized           # 모든 record, content 제외 slim 응답

GET /api/records?capabilities=sections&classification=approved&domain=...
GET /api/records/{id}/sections       # 특정 record의 섹션만
GET /api/records/{id}/tables
GET /api/records/{id}/figures
GET /api/records/{id}/blocks?section_id=X.Y
```

`capabilities` 는 INSERT 시 content 모양에서 자동 산출
(`sections`, `blocks`, `tables`, `figures`, `attachments`, `embeddings`).

## 시맨틱 검색 (pgvector)

`record_sections.embedding VECTOR(384)` + ivfflat 인덱스. 백필:

```powershell
python -m api.embed                  # 전체 미임베딩 섹션 백필
python -m api.embed --record-id DOC-HE-CAE-2026-0000000001
```

`OPENAI_API_KEY` 가 환경변수에 있으면 `OpenAIEmbedder` 자동 사용,
아니면 결정론적 더미 임베더(테스트용) 폴백.

## 인증 + 관측성

```
X-API-Key: <key>             # AUTH_REQUIRED=true 일 때 의무
GET /api/auth/keys           # 부트스트랩 키로 신규 키 발급
GET /metrics                 # Prometheus 텍스트 (요청 수/지연 히스토그램)
```

JSON 구조화 로그(`request_id` 추적), 통일 에러 envelope:

```json
{ "error": { "code": "NOT_FOUND", "message": "...", "request_id": "..." } }
```

## 서버사이드 변환

`/api/convert` 엔드포인트로 **변환과 적재를 서버에서 한 번에** 처리할 수 있다.
이전에는 사용자가 자신의 머신에서 CLI(`python -m converter ...`)를 돌려 JSON 을
만든 뒤 별도로 적재해야 했지만, 이제는 원본 파일을 그대로 업로드하면 끝난다.

지원 확장자: `.docx`, `.xlsx`, `.pptx`, `.md`, `.markdown`, `.pdf`. 업로드 크기 상한은
환경변수 `MAX_UPLOAD_MB`(기본 50MB) 로 조정한다.

> PDF 는 정보 손실이 가장 큰 포맷이다 — 변환 품질이 작성자의 PDF 작성 표준 준수 여부에
> 강하게 의존한다. 자세한 가이드는 [`pdf_to_json_conversion_rules.md`](../pdf_to_json_conversion_rules.md) 참조.

엔드포인트:

- `POST /api/convert/`        : 변환만 — 결과 JSON 을 그대로 돌려준다.
- `POST /api/convert/ingest`  : 변환 + DB INSERT/UPDATE — 멱등(content_hash 동일 시 skip).

curl 예시:

```bash
# 변환만
curl -X POST http://localhost:8000/api/convert/ \
  -H "X-API-Key: $KEY" \
  -F "file=@iga_guide.docx" \
  -F "division=HE" -F "team=CAE" -F "year=2026" -F "seq=1" \
  -F "tags=IGA,LS-DYNA" -F "agents=iga-analyst"

# 변환 + 적재 (record_id / status / record 요약 반환)
curl -X POST http://localhost:8000/api/convert/ingest \
  -H "X-API-Key: $KEY" \
  -F "file=@iga_guide.docx" \
  -F "division=HE" -F "team=CAE" -F "year=2026" -F "seq=1" \
  -F "tags=IGA,LS-DYNA" -F "agents=iga-analyst"
```

자세한 폼 필드/응답/에러 코드는 [docs/api_reference.md](docs/api_reference.md)
의 `POST /api/convert` / `POST /api/convert/ingest` 섹션 참조.

## 테스트

```powershell
$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"
pytest -v --tb=short
```

Agent 1/2/3 산출물이 아직 없는 환경에서도 collection-safe (skip 처리). 자세한 픽스처 설계는 `tests/conftest.py` 주석 참조.

## 문서

| 문서                                                       | 내용                                                       |
|------------------------------------------------------------|------------------------------------------------------------|
| [**docs/AGENT_ONBOARDING.md**](docs/AGENT_ONBOARDING.md)   | **AI 에이전트가 가장 먼저 읽을 문서** (Discovery / RAG 사용법) |
| [docs/setup_guide.md](docs/setup_guide.md)                 | 설치 → 마이그레이션 → 서버/MCP 기동까지                    |
| [docs/api_reference.md](docs/api_reference.md)             | REST 엔드포인트별 파라미터/응답/curl 예제                  |
| [docs/data_model.md](docs/data_model.md)                   | ID 포맷, 테이블 ER, 인덱스, 흔한 쿼리                      |
| [docs/mcp_integration.md](docs/mcp_integration.md)         | MCP 도구, Cline SR 등록 JSON, 트러블슈팅                   |

### Discovery / RAG-friendly API (Agent 30)

LLM 에이전트가 백엔드 source 를 읽지 않고도 허브를 사용할 수 있도록
다음 5개 엔드포인트가 있다:

```
GET /api/discover         # 허브 전체 카탈로그 (60초 캐시) — 시작점
GET /api/schema           # 머신 리더블 JSON Schema (draft-2020-12)
GET /api/hints?context=…  # 자연어 힌트 카탈로그
GET /api/docs/llm.txt     # LLM 1회 주입용 통합 마크다운 (5-10KB)
POST /api/ask             # 자연어 쿼리 → interpreted_query + results
```

또한 record 자체에 4개의 agent-friendly 메타가 추가됐다 (Migration 0007):
`agent_hints`, `related_record_ids`, `query_examples`, `access_pattern`.
자세한 내용은 [`docs/AGENT_ONBOARDING.md`](docs/AGENT_ONBOARDING.md) 참고.

## 라이선스

내부 사용. 외부 배포 전 라이선스 정책 확인 필요.

## Local Development with Docker

신규 개발자는 Docker compose 경로로 한 번에 환경을 띄울 수 있다 (PostgreSQL 직접 설치 불필요).

```powershell
# 1) PostgreSQL 컨테이너 기동 (pgvector/pgvector:pg16 이미지)
docker compose up -d postgres

# 2) DB 마이그레이션
$env:PYTHONPATH = "src"
alembic upgrade head

# 3) 표준 에이전트 5종 시드 (멱등)
python -m api.seed
# 또는 dry-run 으로 변경 계획만 확인
python -m api.seed --dry-run

# 4) 데이터 적재
python -m api.ingest AI_data/examples/HE-CAE-2026-0000000001.json

# 5) API 서버
python -m api.main
```

`docker compose up -d` 만 실행하면 `postgres` + `api` 두 컨테이너가 같이 뜬다 (build 포함).

원-커맨드 부트스트랩:

```powershell
pwsh ./scripts/start_dev.ps1     # Windows PowerShell
# 또는
./scripts/start_dev.sh           # POSIX
```

### End-to-End 스모크 검증

PostgreSQL/Docker 가 없는 환경에서도 임시 SQLite DB 로 ingest → API 라우터까지 한 번에 검증할 수 있다.

```powershell
$env:PYTHONPATH = "src"
python scripts/smoke_test.py
```

스모크 스크립트는 매 실행마다 새 임시 DB 를 생성·삭제하므로 재실행이 안전하다.

## 표준 에이전트

`python -m api.seed` 가 등록하는 5종 에이전트:

| agent_type            | name              | data_types     |
|-----------------------|-------------------|----------------|
| `iga-analyst`         | IGA 해석 분석가   | DOC, SIM, DATA |
| `cae-reporter`        | CAE 보고서 작성자 | DOC, SIM, DATA |
| `material-reviewer`   | 재료 물성 검토자  | DOC, DATA      |
| `process-checker`     | 공정 절차 검증자  | DOC, FORM      |
| `code-assistant`      | 코드 어시스턴트   | DOC            |

자세한 정의는 `src/api/seed/agents_data.py` 참고. 멱등(upsert)이라 재실행해도 안전.

## VS Code 확장 연동

사업부 엔지니어가 자기 자료를 직접 적재할 수 있는 VS Code 확장(`AI_data/vscode_extension`)이 vsix 패키징 완료 상태다.
설치 후 API URL + X-API-Key 만 설정하면 드래그·드롭 → 메타 폼 → Send → record_id 확인까지 1분이면 끝난다.
사용자 가이드는 [`docs/user_guide_for_engineers.md`](docs/user_guide_for_engineers.md)
와 `vscode_extension/docs/USER_GUIDE.md` 두 곳에 있다 (대상 독자 분리).

확장이 호출하는 엔드포인트:

- `GET /api/system/health` — 인증 모드 / 버전 / 빌드 메타.
- `GET /api/meta/options` — 폼 셀렉트박스 옵션 카탈로그 (5분 캐시).
- `POST /api/auth/keys/verify` — 발급 키 유효성 검증 (부트스트랩 미요구).
- `POST /api/convert/ingest` — 파일 + 메타 폼 → 변환 → DB 적재.
  - 확장 폼 필드: `status` / `language` / `subject_keywords` / `derivation` /
    `quality_score` / `valid_from` / `valid_until` / `title_override` /
    `summary_override` (전부 선택).

CORS 는 `vscode-webview://*` 정규식을 항상 허용. 추가 오리진은
`EXTRA_ALLOWED_ORIGINS` 환경변수로 콤마 구분 지정.

자세한 계약은 `docs/api_reference.md`, `docs/extension_integration_plan.md`,
`vscode_extension/docs/metadata_spec.md` 참고.

## AI Agent Discovery / RAG

AI 에이전트(Cline SR, Claude Desktop, 자체 RAG 봇 등)가 백엔드 source 한 줄 읽지 않고
허브를 사용할 수 있도록 self-describing 계약을 제공한다. 에이전트의 표준 패턴은
**`GET /api/discover` → `POST /api/ask`** 두 호출이다 — 첫 호출로 카탈로그·agent 목록·
starting_points 를 파악하고, 두 번째 호출로 자연어 질의를 `interpreted_query` + `results` +
`follow_up_queries` 로 받는다. 단일 진입점으로 5~10 KB 마크다운만 주입하고 싶다면
`/api/docs/llm.txt` 하나면 충분하다.

자세한 사용법·MCP 도구 표·5 가지 시나리오·"하지 말 것" 목록은
[`docs/AGENT_ONBOARDING.md`](docs/AGENT_ONBOARDING.md) 참고.

## 거버넌스 (Migration 0008)

`audit_log` 테이블에 모든 INSERT/UPDATE/DELETE 가 actor·request_id·before/after JSON 과 함께 자동 기록된다.
DELETE 는 기본 soft delete(`deleted_at` + `deleted_by`)로 동작하고, 일반 조회 경로는 deleted 레코드를
자동 제외한다. 같은 자료의 개정 이력은 `parent_record_id` + `version` chain (lineage)으로 추적되며,
`GET /api/records/{id}/diff?from=…&to=…` 엔드포인트로 두 버전 간 필드/섹션 차이를 JSON 으로 받을 수 있다.
record 단위 조회/검색 카운터(`usage_stats`)도 누적되어 인기도·미사용 자료 식별에 쓰인다.

## 확장성 (Migration 0009)

대규모 적재 시나리오를 위해 네 가지 메커니즘이 추가됐다:

- **Auto-seq**: division/team/year 만 지정하고 seq 를 비우면 서버가 충돌 없는 번호를 자동 할당.
- **Batch ingest**: `POST /api/convert/ingest/batch` 가 다중 파일 멀티파트 업로드를 한 번에 처리.
- **Async job queue**: 무거운 변환(PDF OCR, 대형 PPT 등)은 background job 으로 들어가고
  `GET /api/jobs/{id}` 로 폴링.
- **Auto-embedding trigger**: `auto_embed_on_insert=true` 면 INSERT 시 임베딩 백필 큐에 자동 등록.
- **Attachment 영구화**: 임시 디렉토리 → 해시 기반 영구 저장. 동일 파일은 자동 dedupe.

운영 도입 순서·체크리스트는 [`docs/go_live_checklist.md`](docs/go_live_checklist.md) 참고.

## 변환기 보강 (이번 웨이브)

- **PDF OCR fallback**: 텍스트 레이어가 없는 스캔 PDF 는 tesseract 폴백 (`--ocr` 옵션). tesseract 는 별도 설치 필요.
- **PPT 차트 추출**: 차트 슬라이드의 데이터 시리즈를 표로 추출(애니메이션·빌드 효과는 미지원).
- **Excel 다중 표 분리**: 한 시트에 여러 표가 있어도 빈 행/열 기준으로 분리해 각각 `tables[]` 로 등록.

변환기별 알려진 한계는 [`docs/converter_limits.md`](docs/converter_limits.md) 한눈에 정리.

## 사업부 사용자 가이드 / FAQ

- [`docs/user_guide_for_engineers.md`](docs/user_guide_for_engineers.md) — 사업부 엔지니어 관점 (API key 발급 → VS Code 확장 → 첫 업로드 → 메타 입력 팁).
- [`docs/FAQ.md`](docs/FAQ.md) — "변환 결과가 이상해요" / "PDF OCR 안 돼요" / "_META 시트가 뭐예요" 등 자주 묻는 8 가지.

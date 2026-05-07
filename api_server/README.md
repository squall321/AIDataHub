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
python -m converter "d:\tmp\iga_guide_test.docx" --division HE --team CAE --year 2026 --seq 1 --output-dir output ; python -m api.ingest .\output\HE-CAE-2026-000001.json

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
| 마이그레이션 | Alembic (0001~0006)                           |
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
| DOC            | 문서/가이드      | `DOC-HE-CAE-2026-000001`  |
| DATA           | 측정/시험 데이터 | `DATA-HE-CAE-2026-000018` |
| SIM            | 시뮬레이션 결과  | `SIM-HE-CAE-2026-000045`  |
| CAD            | CAD 모델         | `CAD-HE-CAE-2026-000012`  |
| LOG/FORM/OTHER | 보조             | (동일 패턴)               |

ID 포맷, 스키마 디테일: [`docs/data_model.md`](docs/data_model.md).

## 첨부파일 일반화

그림뿐 아니라 9종 첨부 모두 캡션 의무 + 메타데이터(해시·크기·MIME) 보존.
실제 파일은 `ATTACHMENTS_DIR` 하위에 `{record_id}/A001.{ext}` 로 저장,
`/attachments/{...}` 정적 마운트 또는 `GET /api/records/{id}/attachments` 메타 조회.

| kind | 예시 |
|------|------|
| figure | png, jpg, gif, wmf, emf |
| document | pdf, docx, hwp |
| spreadsheet | xlsx, csv |
| media | mp3, mp4 |
| archive | zip, 7z |
| cad | step, catpart, sldprt |
| drawing | dwg, dxf |
| data | json, xml, yaml |
| other | (그 외) |

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
python -m api.embed --record-id DOC-HE-CAE-2026-000001
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

## 테스트

```powershell
$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"
pytest -v --tb=short
```

Agent 1/2/3 산출물이 아직 없는 환경에서도 collection-safe (skip 처리). 자세한 픽스처 설계는 `tests/conftest.py` 주석 참조.

## 문서

| 문서                                               | 내용                                      |
|----------------------------------------------------|-------------------------------------------|
| [docs/setup_guide.md](docs/setup_guide.md)         | 설치 → 마이그레이션 → 서버/MCP 기동까지   |
| [docs/api_reference.md](docs/api_reference.md)     | REST 엔드포인트별 파라미터/응답/curl 예제 |
| [docs/data_model.md](docs/data_model.md)           | ID 포맷, 테이블 ER, 인덱스, 흔한 쿼리     |
| [docs/mcp_integration.md](docs/mcp_integration.md) | MCP 도구, Cline SR 등록 JSON, 트러블슈팅  |

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
python -m api.ingest AI_data/examples/HE-CAE-2026-000001.json

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

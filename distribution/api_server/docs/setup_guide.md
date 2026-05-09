# Setup Guide

처음부터 끝까지 — 설치 → DB 마이그레이션 → 첫 레코드 적재 → API 서버 → MCP 서버.

## 1. 사전 요구사항

| 항목         | 버전 / 비고                                            |
|--------------|--------------------------------------------------------|
| OS           | Windows 10/11, macOS, Linux                            |
| Python       | **3.12** (`>=3.12`)                                    |
| PostgreSQL   | **16+** (로컬 또는 Docker)                             |
| Git          | 최신                                                    |
| (옵션) Cline SR | VS Code Cline 확장 (MCP 클라이언트)                  |

### 1.1 (권장) Docker compose 한 방에 띄우기

신규 개발자는 저장소 루트(`api_server/`)의 `docker-compose.yml` 을 활용하는 것이
가장 빠르다. PostgreSQL 16 + pgvector 가 미리 설치된 이미지를 사용하며 healthcheck
가 정의되어 있다.

```powershell
# 저장소 클론 후 (아래 2단계 참고)
docker compose up -d postgres
```

`docker compose ps` 로 `healthy` 상태를 확인한 뒤 4단계(마이그레이션) 로 진행하면 된다.
원-커맨드 부트스트랩(`scripts/start_dev.ps1` / `scripts/start_dev.sh`) 도 사용 가능 —
이 스크립트는 `compose up` → healthcheck 대기 → `alembic upgrade head` →
`python -m api.seed` → `python -m api.main` 까지 순차 실행한다.

### 1.2 (대안) PostgreSQL 직접 설치 (Docker 단독)

compose 를 쓰지 않는 경우:

```powershell
docker run -d --name ai-data-pg `
  -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres `
  -e POSTGRES_DB=ai_data -p 5432:5432 `
  postgres:16
```

## 2. 저장소 클론 & 가상환경

```powershell
git clone <repo-url> AI_data
cd AI_data\api_server

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt
```

설치되는 핵심 패키지: `fastapi`, `uvicorn`, `sqlalchemy[asyncio]`, `asyncpg`,
`alembic`, `pydantic-settings`, `python-docx`, `httpx`, `mcp`, `pytest`, ...

## 3. 환경 변수 (`.env`)

`.env.example` 이 있다면 복사, 없으면 다음 내용으로 새로 만든다.

```ini
# DB
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_data

# API server
API_HOST=0.0.0.0
API_PORT=8000
API_RELOAD=true

# 그림 바이너리 저장소 (정적 마운트 /figures 의 루트)
# 변환기 출력 + ingest 복사 + 정적 서빙이 모두 이 경로를 공유한다
FIGURES_DIR=./figures

# Logging
LOG_LEVEL=INFO

# Async job queue (S3, Agent 32) — in-memory, optional
AUTO_EMBED_ON_INSERT=false   # true 이면 record INSERT/UPDATE 후 임베딩 잡 자동 등록
JOBS_TTL_SECONDS=3600        # 완료된 잡 보관 TTL (초)
JOBS_LIST_LIMIT=100          # /api/jobs 응답 상한
```

### 3.1 (옵션) PDF OCR — Tesseract 설치 (Agent 32 / S6)

스캔 PDF 의 빈 페이지를 OCR 처리하려면 시스템 레벨의 Tesseract 바이너리가 필요하다.

**Windows**:

1. <https://github.com/UB-Mannheim/tesseract/wiki> 에서 인스톨러 다운로드.
2. 기본 경로 `C:\Program Files\Tesseract-OCR\` 설치 후 `PATH` 에 추가.
3. (선택) 한국어 인식: `kor.traineddata` 를 `tessdata/` 에 배치.
4. 파이썬 의존성:

   ```powershell
   pip install pytesseract pdf2image pillow
   ```

   `pdf2image` 는 추가로 [poppler](https://github.com/oschwartz10612/poppler-windows/releases/) 가 필요.

5. 변환기 사용 예:

   ```powershell
   python -m pdf_converter scanned.pdf --division HE --team CAE --year 2026 --ocr --ocr-lang eng+kor
   ```

> Tesseract 또는 의존성이 없으면 `--ocr` 플래그는 안전하게 무시되고 경고만 남는다.

## 4. DB 마이그레이션

```powershell
$env:PYTHONPATH = "src"
alembic upgrade head
```

성공 시 `records`, `record_sections`, `agents`, `agent_records` 테이블 + 인덱스 생성.

확인:

```powershell
psql -h localhost -U postgres -d ai_data -c "\dt"
```

## 5. 첫 레코드 적재

방법 A — **Word 변환 → Ingest** (권장):

```powershell
# 1) Word → JSON
python -m converter "d:\tmp\iga_guide_test.docx" `
  --division HE --team CAE --year 2026 --seq 1 `
  --output-dir output

# 2) JSON → DB
python -m api.ingest .\output\HE-CAE-2026-000001.json
```

방법 B — **JSON 파일 직접**:

```powershell
python -m api.ingest path\to\record.json
```

성공 시 `records` 테이블에 1행이 들어가고, `record_sections` 에 섹션이 풀어진다.

## 6. API 서버 기동

```powershell
python -m api.main
# 또는
uvicorn api.main:app --reload --app-dir src
```

확인:

```powershell
curl http://localhost:8000/health
# {"status":"ok"}

curl "http://localhost:8000/api/records?limit=1"
```

API 문서: <http://localhost:8000/docs>

## 7. MCP 서버 기동 (Cline SR 등 LLM 클라이언트 연결)

별도 터미널에서:

```powershell
$env:PYTHONPATH = "src"
$env:API_URL = "http://localhost:8000"
python -m mcp_server
```

MCP 는 stdio 트랜스포트라 서버가 표준입력 대기 상태로 멈춰있는 것이 정상.
실제 사용은 클라이언트(Cline 등)가 spawn 하는 방식 — 자세한 등록은
[`mcp_integration.md`](./mcp_integration.md) 참조.

## 8. 검증 체크리스트

```powershell
# 1) 의존성
python -c "import fastapi, sqlalchemy, mcp; print('ok')"

# 2) 모듈 로드
python -c "from api.main import app; print(app.title)"
python -c "from mcp_server import server; print('mcp ok')"

# 3) DB 연결
python -c "from api.db.base import engine; import asyncio; asyncio.run(engine.dispose())"

# 4) 단위 + 통합 테스트
$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"
pytest -v --tb=short
```

기대 결과:
- `tests/test_health.py` : 2 passed
- `tests/test_legacy_compat.py` : 4 passed (docx 없으면 일부 skip)
- `tests/integration/test_full_flow.py` : 일부 의존성에 따라 pass/skip

## 9. 자주 겪는 문제

| 증상                                         | 원인 / 조치                                          |
|---------------------------------------------|------------------------------------------------------|
| `psycopg2` 어쩌고                            | 우리는 `asyncpg` 사용. 혼동되는 import 없는지 확인. |
| `sqlalchemy.exc.OperationalError: ...connect refused` | Postgres 미기동. `docker start ai-data-pg` 또는 서비스 시작. |
| `ModuleNotFoundError: api`                  | `PYTHONPATH=src` 누락. 또는 venv 미활성화.           |
| Windows에서 한글 깨짐                        | `chcp 65001`, `PYTHONIOENCODING=utf-8` 설정.         |
| pytest 가 collection 단계에서 죽음          | conftest 의 의존 모듈이 import 시점에 실패. `pytest -x --tb=long` 으로 확인. |

## 10. 다음 단계

- pgvector + 시맨틱 검색 마이그레이션
- 인증/권한 (사내 SSO 또는 토큰)
- 운영 배포(Docker compose, systemd unit, etc.)

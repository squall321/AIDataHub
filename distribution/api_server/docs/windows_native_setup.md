# Windows 네이티브 셋업 (Docker / WSL 없이)

WSL2 미지원 환경 또는 Docker Desktop 라이선스 부담이 있는 경우, **PostgreSQL을 Windows에 네이티브로 설치**하여 전체 스택을 운영할 수 있다. 이 문서는 그 절차를 단계별로 정리한다.

---

## 0. 사전 점검

| 항목 | 필요 |
|------|------|
| Windows | 10/11 (Pro/Enterprise/Home 모두 가능) |
| Python | 3.12.x (이미 설치됨) |
| 관리자 권한 | PostgreSQL 설치 시 필요 |
| 네트워크 포트 | 5432 (PostgreSQL 기본) — 점유되어 있지 않은지 확인 |

포트 점유 확인:

```powershell
netstat -ano | findstr :5432
```

결과가 나오면 다른 PostgreSQL이 이미 실행 중이거나 다른 프로세스가 점유한 것 — 정리 후 진행.

---

## 1. PostgreSQL 16 설치

### 1.1 인스톨러 다운로드

<https://www.postgresql.org/download/windows/> → **EDB Installer** → 16.x 64-bit 다운로드.

### 1.2 설치 옵션

| 항목 | 권장값 |
|------|--------|
| Installation Directory | `C:\Program Files\PostgreSQL\16` (기본) |
| Data Directory | `C:\Program Files\PostgreSQL\16\data` (기본) |
| Password (postgres 슈퍼유저) | **`postgres`** (개발용) — 운영은 강력한 값 |
| Port | `5432` |
| Locale | `Korean, Korea` 또는 `Default locale` |
| Stack Builder | 일단 건너뛰기 (pgvector는 별도 설치) |

설치 완료 후 시스템 트레이에 PostgreSQL 서비스가 자동 시작됨. **재부팅 후에도 자동 실행**.

### 1.3 설치 검증

```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -h localhost
```

비밀번호 입력 후 다음 출력이 나오면 성공:

```text
psql (16.x)
postgres=#
```

`\q` 로 종료.

### 1.4 환경변수 등록 (선택)

매번 전체 경로를 치기 싫다면:

```powershell
# 시스템 PATH 에 추가 (관리자 PowerShell)
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";C:\Program Files\PostgreSQL\16\bin", "Machine")
```

새 PowerShell 창을 열면 `psql`, `pg_dump` 등을 직접 호출 가능.

---

## 2. 데이터베이스 생성

```powershell
psql -U postgres -h localhost -c "CREATE DATABASE ai_data;"
```

또는 psql 안에서:

```sql
CREATE DATABASE ai_data;
\c ai_data
```

확인:

```powershell
psql -U postgres -h localhost -d ai_data -c "SELECT version();"
```

---

## 3. pgvector 확장 (시맨틱 검색용 — 선택)

pgvector는 시맨틱 검색에 쓰지만 필수는 아니다. **없어도 ILIKE 폴백으로 동작**한다.

### 3.1 설치 옵션 A: 바이너리 (가장 간단)

PostgreSQL 16 + 64-bit 환경:

1. <https://github.com/pgvector/pgvector/releases> 에서 Windows 빌드를 찾는다 (커뮤니티 빌드 위주).
2. 또는 [maven.pgxn.org/](https://pgxn.org/) 에서 검색.
3. 압축을 풀어서 두 파일을 PostgreSQL 디렉터리에 복사:
   - `vector.dll` → `C:\Program Files\PostgreSQL\16\lib\`
   - `vector.control` 및 `vector--*.sql` → `C:\Program Files\PostgreSQL\16\share\extension\`

### 3.2 설치 옵션 B: 직접 빌드 (Visual Studio 필요)

```powershell
# Visual Studio Build Tools (C++) 필요
git clone --branch v0.7.4 https://github.com/pgvector/pgvector.git
cd pgvector
nmake /F Makefile.win
nmake /F Makefile.win install
```

### 3.3 옵션 C: 건너뛰기

pgvector를 설치하지 않으면:

- `alembic upgrade head` 가 0004 마이그레이션에서 `CREATE EXTENSION vector` 부분에서 멈춘다.
- 회피: 환경변수로 시맨틱 검색을 비활성화 후 진행 (또는 0004 마이그레이션을 수동 편집).

권장: **개발 초반에는 pgvector 없이 진행**, 시맨틱 검색이 필요해지면 그때 설치.

### 3.4 확장 활성화

```powershell
psql -U postgres -h localhost -d ai_data -c "CREATE EXTENSION vector;"
```

---

## 4. Python 의존성 설치 (이미 했다면 생략)

```powershell
cd d:\Personal\AI_data\api_server
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

---

## 5. 환경변수(.env) 설정

`api_server\.env` 파일 생성:

```ini
# PostgreSQL 연결 (네이티브 Windows 설치)
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_data

# API 서버
API_HOST=0.0.0.0
API_PORT=8000

# 첨부 파일 저장 경로 (절대 경로 권장)
ATTACHMENTS_DIR=d:/Personal/AI_data/api_server/attachments
FIGURES_DIR=d:/Personal/AI_data/api_server/figures

# 인증 (개발 단계는 false)
AUTH_REQUIRED=false

# 로깅
LOG_FORMAT=text
LOG_LEVEL=INFO

# 메트릭
ENABLE_METRICS=true
```

---

## 6. DB 마이그레이션 적용

```powershell
$env:PYTHONPATH = "src"
alembic upgrade head
```

성공 시 6개 마이그레이션(0001~0006)이 순차 적용된다.

확인:

```powershell
psql -U postgres -d ai_data -c "\dt"
```

`agent_records`, `agents`, `alembic_version`, `api_keys`, `record_attachments`, `record_sections`, `records` 테이블이 보이면 성공.

---

## 7. 표준 에이전트 시드

```powershell
python -m api.seed
```

5종 에이전트(iga-analyst / cae-reporter / material-reviewer / process-checker / code-assistant)가 등록된다.

---

## 8. 첫 데이터 적재 (스모크 검증)

### 8.1 변환

```powershell
python -m converter "d:\tmp\iga_guide_test.docx" `
    --division HE --team CAE --year 2026 --seq 1 `
    --output-dir output
```

### 8.2 적재

```powershell
python -m api.ingest .\output\DOC-HE-CAE-2026-000001.json
```

### 8.3 확인

```powershell
psql -U postgres -d ai_data -c "SELECT id, title, data_type FROM records;"
```

---

## 9. API 서버 기동

```powershell
python -m api.main
```

또는 직접 uvicorn:

```powershell
uvicorn api.main:app --reload --port 8000 --app-dir src
```

기본 주소:

- API: <http://localhost:8000>
- 자동 문서: <http://localhost:8000/docs>
- 메트릭: <http://localhost:8000/metrics>

---

## 10. 서버사이드 업로드 검증

브라우저 또는 curl 로:

```powershell
curl.exe -X POST http://localhost:8000/api/convert/ingest `
    -F "file=@d:\tmp\iga_guide_test.docx" `
    -F "division=HE" -F "team=CAE" -F "year=2026" -F "seq=2" `
    -F "tags=IGA,KooRemapper" -F "agents=iga-analyst"
```

응답:

```json
{
  "record_id": "DOC-HE-CAE-2026-000002",
  "status": "inserted",
  "sections_written": 12,
  "record": { ... }
}
```

---

## 트러블슈팅

| 증상 | 원인·해결 |
|------|----------|
| `connection refused` | PostgreSQL 서비스가 멈춤 — 서비스 관리자(`services.msc`)에서 `postgresql-x64-16` 시작 |
| `password authentication failed` | `.env` 의 비밀번호와 설치 시 입력값 불일치 — 변경 또는 `pg_hba.conf` 검토 |
| `extension vector does not exist` | pgvector 미설치 — 3장 참조 또는 0004 마이그레이션 일시 우회 |
| `port 5432 already in use` | 다른 PG 인스턴스가 점유 — `services.msc` 에서 정리하거나 `.env` 의 포트 변경 |
| 한글 깨짐 | DB 인코딩 확인: `psql -c "SHOW server_encoding;"` → `UTF8` 이어야 함 |
| `alembic` 명령 없음 | venv 활성화 필요: `.\.venv\Scripts\Activate.ps1` |

---

## 옵션: SQLite 로 더 간단하게 (단일 사용자 개발용)

Postgres 설치 자체가 부담이라면 SQLite로 시작 가능:

`.env` 에서:

```ini
DATABASE_URL=sqlite+aiosqlite:///./ai_data_dev.db
```

제약:

- pgvector 사용 불가 (시맨틱 검색은 ILIKE 폴백)
- ARRAY/JSONB 일부 기능은 어댑터 통해 동작 (느릴 수 있음)
- 단일 프로세스만 안전 (멀티 워커 X)

테스트·1인 개발에는 충분하다. 운영 단계에서 PostgreSQL 로 마이그레이션.

---

## 운영 진입 체크리스트

- [ ] PostgreSQL 16 서비스 자동 시작 설정됨
- [ ] `ai_data` 데이터베이스 생성, 인코딩 UTF-8
- [ ] (선택) pgvector 확장 활성화
- [ ] `.env` 의 비밀번호 변경 (운영용 강력한 값)
- [ ] `AUTH_REQUIRED=true` 로 변경 + 부트스트랩 키 발급
- [ ] 백업 일정: `pg_dump -U postgres -d ai_data > backup.sql` 정기 실행
- [ ] 방화벽: 5432 포트는 사내망 안에서만 접근 가능하게
- [ ] API 서버: Windows 서비스로 등록 (NSSM 또는 schtasks 사용)

---

이상으로 Docker 없이 Windows 네이티브 환경에서 전체 스택을 운영할 수 있다. WSL2 도 필요 없고, 모든 컴포넌트는 표준 Windows 프로세스로 동작한다.

# Load Test — AI Data Hub API

Locust 기반 부하 시나리오 4종.

> **주의**: 이 스크립트는 dev 의존성으로만 사용한다. 운영 런타임 의존성에 포함되지
> 않는다 (별도 `requirements.txt` 분리).

---

## 설치

```powershell
# 1) venv 활성화
& "d:\Personal\AI_data\api_server\.venv\Scripts\activate.ps1"

# 2) locust 만 설치
pip install -r scripts/load_test/requirements.txt
```

또는 직접:

```powershell
& "d:\Personal\AI_data\api_server\.venv\Scripts\python.exe" -m pip install "locust>=2.20.0"
```

---

## 실행

### Web UI (대화형)

```powershell
locust -f scripts/load_test/locustfile.py --host http://localhost:8000
# 브라우저: http://localhost:8089
```

### Headless (CI / 자동화)

```powershell
# 30초, 10 users, ramp 2 users/s, read-heavy 만
locust -f scripts/load_test/locustfile.py `
       --headless --run-time 30s `
       --host http://localhost:8000 `
       -u 10 -r 2 `
       --tags read
```

### 시나리오별 실행

| 시나리오     | tag    | 권장 인자                              |
|--------------|--------|----------------------------------------|
| Read-heavy   | `read` | `-u 100 -r 1 --run-time 60s`           |
| Write-heavy  | `write`| `-u 50 -r 5 --run-time 60s`            |
| MCP mix      | `mcp`  | `-u 30 -r 3 --run-time 60s`            |
| Burst        | `burst`| `-u 200 -r 50 --run-time 30s`          |

```powershell
locust -f scripts/load_test/locustfile.py --headless `
       --host http://localhost:8000 `
       -u 100 -r 1 --run-time 60s --tags read
```

### 인증 필요 환경

`AUTH_REQUIRED=true` 인 경우:

```powershell
$env:API_KEY = "<your-key>"
locust -f scripts/load_test/locustfile.py --host http://localhost:8000 ...
```

---

## 권장 베이스라인 (rough — 1 host, 16 GB RAM, PostgreSQL local)

| 메트릭                                | 목표                       |
|---------------------------------------|----------------------------|
| `GET /api/records` p50                | < 100 ms                    |
| `GET /api/data` p95                    | < 500 ms                    |
| `POST /api/ask` p95 (LLM-off mode)     | < 2 s                       |
| `POST /api/records` (small) 동시 100   | 에러 0%                     |
| `/api/discover` p95                    | < 300 ms (60s 캐시 기준)    |
| `/api/search?mode=fts` p95             | < 500 ms                    |

> 위 수치는 권장 기준일 뿐, 실제 환경(디스크 I/O, DB 인덱스 상태, 네트워크,
> CPU)에 따라 차이가 크다. 변동성 큰 메트릭(특히 `/api/ask`)은 배포 전후
> 회귀(regression) 비교가 더 의미 있다.

---

## 결과 해석 가이드

### 1. p50 기준치 초과

- 의미: 평균 사용자 체감 속도 저하.
- 우선 점검: DB 인덱스 (특히 `record.tags GIN`, `record_sections.fts`),
  N+1 쿼리, 응답 직렬화.

### 2. p95 / p99 가 p50 의 5배 이상

- 의미: tail latency 가 큼 → GC pause, DB lock contention, 느린 쿼리 의심.
- 점검: `/metrics` 의 `http_request_duration_seconds_bucket` 분포,
  PostgreSQL `pg_stat_activity` / `pg_stat_statements`.

### 3. 에러율 > 1%

- 5xx → 서버 측 (DB 풀 고갈, 메모리, 미들웨어 예외) — 로그 확인.
- 4xx → 부하 스크립트 페이로드 호환성 문제일 수도 있음 (현 locustfile 은
  422 를 success 로 처리하지만 실제 운영에선 422 도 모니터링).

### 4. RPS 가 horizontal scaling 후에도 안 늘어남

- DB 가 병목. read replica / 인덱스 / 쿼리 튜닝.
- 또는 GIL — sync 워커 → uvicorn workers ≥ 2~CPU 수.

### 5. 인제스트 (POST /api/records) 시 latency 폭증

- DB write contention. 배치 ingest API (`/api/ingest/batch`) 사용 권장.
- `auto_embed_on_insert=true` 인 경우 임베딩 작업이 백그라운드로 큐잉되며
  지연 폭발 가능 — 잡 큐 외부화 검토.

---

## 운영 권고

- locust 는 dev 의존성이다. 운영 컨테이너 이미지에 포함하지 않는다.
- CI 에서는 30s headless 만 돌려 회귀 게이트로 활용.
  실제 부하 시험은 staging 환경에서 수행한다.
- 결과 저장: `--csv=reports/load_$(date +%s)` 옵션으로 시계열 비교.

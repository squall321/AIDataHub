# Observability — `/metrics` 활용 가이드

`api_server` 는 `prometheus-client` 기반 미들웨어(`MetricsMiddleware`)와
`/metrics` 엔드포인트를 내장한다. 본 문서는 운영자 / SRE / 인프라 담당자가
어떤 메트릭을 어떻게 모니터링하면 되는지 정리한다.

---

## 1. `/metrics` 엔드포인트

| 항목         | 값                                             |
|--------------|------------------------------------------------|
| Path         | `GET /metrics`                                  |
| Content-Type | `text/plain; version=0.0.4; charset=utf-8`      |
| 인증         | (현재) 없음. 사내망 또는 IP allowlist 권장.     |
| 활성화 토글  | `ENABLE_METRICS=true` (기본값 true)             |
| Self-noise   | `/metrics` 자체는 카운터에 잡히지 않음           |

```powershell
curl http://localhost:8000/metrics
```

### Prometheus 스크레이프 예시 (`prometheus.yml`)

```yaml
scrape_configs:
  - job_name: ai-data-api
    metrics_path: /metrics
    scrape_interval: 15s
    scrape_timeout: 10s
    static_configs:
      - targets:
          - "localhost:8000"
        labels:
          service: ai-data-api
          env: dev
```

도커 환경에서 다중 인스턴스를 운영한다면 `static_configs` 대신 DNS service
discovery 또는 `docker_sd_configs` 를 쓴다.

---

## 2. 핵심 메트릭 5종

| #  | 메트릭                                       | 타입       | 라벨                       | 의미                                        |
|----|----------------------------------------------|------------|----------------------------|---------------------------------------------|
| 1  | `http_requests_total`                        | Counter    | `method`, `path`, `status` | 누적 요청 수 (path = 라우트 템플릿).        |
| 2  | `http_request_duration_seconds`              | Histogram  | `method`, `path`           | 지연 분포 (default buckets).                |
| 3  | `http_requests_total{status=~"5.."}`         | derived    | —                          | 에러 카운터 (PromQL 로 산출).                |
| 4  | `http_requests_in_flight` (확장 시)          | Gauge      | —                          | 진행 중 요청 (현재 미내장 — 4.2 절 참고).   |
| 5  | `ingest_records_total` (확장 시)             | Counter    | `status`                   | 인제스트 처리량 (현재 미내장).               |

### 2.1 요청 수 (RPS)

```promql
sum(rate(http_requests_total[1m])) by (path)
```

### 2.2 지연 (p50 / p95 / p99)

```promql
histogram_quantile(
  0.95,
  sum(rate(http_request_duration_seconds_bucket[5m])) by (le, path)
)
```

### 2.3 에러율

```promql
sum(rate(http_requests_total{status=~"5.."}[5m]))
  /
sum(rate(http_requests_total[5m]))
```

### 2.4 활성 연결 (보강 가능)

현재 미들웨어에는 in-flight gauge 가 없다. 필요하면 다음을 추가한다:

```python
# api/middleware/metrics.py 후속 PR 안
IN_FLIGHT = Gauge("http_requests_in_flight", "Active HTTP requests")
async def dispatch(...):
    IN_FLIGHT.inc()
    try:
        ...
    finally:
        IN_FLIGHT.dec()
```

### 2.5 인제스트 처리량

`POST /api/records` 또는 `POST /api/ingest/batch` 호출 수로 근사 가능:

```promql
sum(rate(http_requests_total{path=~"/api/(records|ingest/batch)", method="POST"}[5m]))
```

장기적으로는 도메인 메트릭(`ingest_records_total{status="ok|fail"}`)을
서비스 레이어에서 직접 노출하는 것이 더 정확하다.

---

## 3. 추천 알람 임계치 (참고)

> 환경에 따라 조정. 첫 1주는 임계 값을 헐겁게 두고 false positive 를 줄인 후
> 점진적으로 타이트닝한다.

| 알람                                   | 조건                                                                 | 심각도 |
|----------------------------------------|----------------------------------------------------------------------|--------|
| API 5xx 비율 급증                       | `error_rate > 1%` for 5m                                            | high   |
| API p95 지연 폭증                        | `p95(/api/data) > 1s` for 10m                                       | medium |
| `/api/ask` p95 폭증                     | `p95(/api/ask) > 5s` for 10m                                         | medium |
| Health endpoint 200 != 응답              | `http_requests_total{path="/health", status="200"}` increase 정지   | high   |
| Scrape 실패                              | `up{job="ai-data-api"} == 0` for 2m                                 | high   |
| 인제스트 latency 폭증                    | `p95(POST /api/records) > 2s` for 10m                                | medium |
| 컨테이너 메모리 / CPU                    | (인프라 메트릭) `container_memory_usage_bytes` 80% over 30m         | medium |

PromQL 예시 (5xx):

```promql
(
  sum(rate(http_requests_total{status=~"5.."}[5m]))
    /
  sum(rate(http_requests_total[5m]))
) > 0.01
```

---

## 4. Grafana 패널 권장

| 패널          | 쿼리                                                                                |
|---------------|-------------------------------------------------------------------------------------|
| RPS by path   | `sum(rate(http_requests_total[1m])) by (path)`                                      |
| Error rate    | `sum(rate(http_requests_total{status=~"5.."}[5m])) by (path)`                       |
| p50 by path   | `histogram_quantile(0.5, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, path))` |
| p95 by path   | `histogram_quantile(0.95, ...)`                                                     |
| p99 by path   | `histogram_quantile(0.99, ...)`                                                     |
| 4xx vs 5xx    | `sum(rate(http_requests_total{status=~"4.."}[5m])) by (path)` + 5..               |

---

## 5. 트러블슈팅

| 증상                                       | 점검                                                                                  |
|--------------------------------------------|---------------------------------------------------------------------------------------|
| `/metrics` 가 404                          | `ENABLE_METRICS=true` 환경변수 + 서버 재시작.                                         |
| 메트릭은 보이는데 path 가 `<unmatched>` 만 | 라우트 템플릿이 매칭 안 됨 (404 직전 미들웨어 단계). 로깅 미들웨어로 url 확인.         |
| Prometheus 가 `up == 0`                     | scrape_timeout 초과. 서버 부하 / DB 락 의심.                                          |
| label cardinality 폭발                     | path 라벨은 라우트 템플릿(고정 셋)이지만 클라이언트 임의 헤더가 추가 라벨로 들어가면 위험. 미들웨어 직접 수정 금지. |
| Histogram bucket 분해능 부족               | `prometheus_client.Histogram` default buckets 외 도메인 특화 buckets 적용 검토.       |

---

## 6. 보안

- `/metrics` 는 인증이 없다. 운영에서는:
  - 사내망(VPN) 안에서만 접근 가능하게 한다, 또는
  - 프록시(Nginx/Traefik)에서 `/metrics` 만 IP allowlist 또는 mTLS 적용.
- 메트릭에는 사용자 식별 정보가 없도록 유지한다 (현재 라벨은 `method`, `path`,
  `status` 만 — PII 없음).

---

## 7. 참고

- 미들웨어 소스: `src/api/middleware/metrics.py`
- 라우트: `src/api/routes/metrics.py`
- 부하 시 메트릭 검증: `scripts/load_test/` 와 함께 사용.

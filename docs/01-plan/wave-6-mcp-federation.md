# Wave-6 — MCP Federation / Proxy

작성일: 2026-05-23
선행: wave-1~3 (MCP 기본 인프라), wave-4 (동적 도구 패턴), wave-5 (도구 업로드)
목표: AIDataHub 가 **다수의 외부 FastMCP 서버를 단일 진입점으로 프록시**. 사용자는 Claude Desktop / Cursor / Cline 에 AIDataHub 1개만 등록하면 사내 5~10개 MCP 서버의 모든 도구·프롬프트·리소스를 통합 사용. wave-5 와 독립적, 병행 진행 가능.

---

## 1. 동기 + 사용 시나리오

| 시나리오 | 현재 (federation 없을 때) | wave-6 후 |
|---|---|---|
| 사내 분석팀 MCP + 보안팀 MCP + CI팀 MCP + AIDataHub | 클라이언트에 4개 따로 등록 | AIDataHub 1개만 등록 |
| HQ 가 만든 도구를 지점이 발견 | 지점이 HQ 서버 URL 직접 등록 | HQ AIDataHub 를 지점 AIDataHub 가 federation |
| 외부 SaaS MCP (Slack, GitHub 등) 통합 | 클라이언트별로 토큰 관리 | AIDataHub 에서 중앙 토큰 + 통합 감사 |
| 클라이언트 등록 부담 | 사용자별 N 회 등록 | 1 회 등록 |

---

## 2. 결정된 정책 (제안 — 사용자 확인 필요)

| 정책 | 제안 |
|---|---|
| 네임스페이스 구분자 | **double underscore** (`upstream_alias__tool_name`) — 모든 MCP 클라이언트 호환 |
| upstream transport 1순위 | HTTP (Streamable MCP) — stdio 는 Phase 2 |
| AIDataHub 자체 도구 prefix | **없음** — `agent_search`, `discover` 등 그대로 |
| upstream 인증 토큰 | env 또는 vault 만 — DB 평문 저장 거절 |
| TLS 검증 default | ON — 사내 self-signed 만 per-upstream `tls_verify: false` 허용 (감사 로그) |
| upstream 다운 시 동작 | tools/list 응답에서 해당 도구 제외, 백오프 재연결 |
| 실패 호출 처리 | MCP 표준 에러 + retry-after 헤더, retry 책임은 클라이언트 |
| 호출 권한 모델 (P1) | 모든 클라이언트가 모든 upstream 사용 가능 — RBAC 은 P4 |
| 호출 latency 허용 | +5~20ms (LAN 1 홉) — 클라이언트 SLA 에는 무영향 |

---

## 3. 4 Phase 분할

### Phase 1 — 핵심 Proxy (HTTP transport, 단일 upstream 검증)

**산출물**

- `api/services/mcp_federation.py` — upstream 클라이언트 풀 + namespace 부여 + 호출 dispatch
- `api/routes/mcp_upstreams.py` — `GET/POST/DELETE /api/mcp/upstreams`
- alembic 0023 — `mcp_upstreams` + `mcp_proxy_calls` 테이블
- `api/mcp_runtime.py` — 부팅 시 `register_all_upstreams(mcp)` 호출
- 매니페스트 파일 형식: `config/upstream_mcps.yaml` (또는 admin UI 가 DB 에 기록)
- 단위 + 통합 테스트

**파이프라인 단계 (부팅 시)**

```
1. config 로드 (upstream_mcps.yaml 또는 mcp_upstreams 테이블)
2. 각 upstream 에 대해:
   a. health check (POST /mcp/ + initialize JSON-RPC)
   b. tools/list, prompts/list, resources/list 호출
   c. 이름 충돌 검사 + namespace 자동 부여 (alias__tool)
   d. AIDataHub FastMCP 에 wrapper 도구 동적 등록 (wave-4 add_tool 인프라 재사용)
3. 백그라운드 worker — 60초 간격 ping, 죽은 upstream 의 tool 자동 비활성
```

**호출 dispatch (`tools/call` wrapper)**

```python
async def upstream_wrapper(**kwargs):
    # 호출 시점에 upstream 의 원본 tool 이름 복원
    raw_tool = wrapper._aidh_upstream_tool  # 클로저에 저장된 메타
    client = _client_pool[wrapper._aidh_upstream_alias]
    try:
        result = await client.call_tool(raw_tool, arguments=kwargs, timeout=60)
    except UpstreamTimeout:
        return {"error": "upstream_timeout", "upstream": alias, "retry_after_sec": 5}
    audit_log_insert(caller, upstream, raw_tool, latency, status=ok)
    return result
```

**스키마 (alembic 0023)**

```sql
CREATE TABLE mcp_upstreams (
  alias TEXT PRIMARY KEY,
  transport TEXT NOT NULL,           -- http | stdio
  url TEXT,                          -- http 일 때
  command TEXT,                      -- stdio 일 때
  command_args JSONB,                -- stdio 일 때
  auth JSONB,                        -- {type, env_var} — 토큰 자체는 미저장
  description_prefix TEXT DEFAULT '',
  tls_verify BOOLEAN NOT NULL DEFAULT true,
  enabled BOOLEAN NOT NULL DEFAULT true,
  rate_limit_per_min INT DEFAULT 100,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_health_check_at TIMESTAMPTZ,
  last_health_status TEXT,           -- ok | unreachable | auth_failed | ...
  last_tool_count INT
);

CREATE TABLE mcp_proxy_calls (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  caller TEXT,                       -- 인증 후 식별자
  upstream_alias TEXT NOT NULL,
  raw_tool_name TEXT NOT NULL,
  exposed_tool_name TEXT NOT NULL,   -- namespaced (alias__tool)
  latency_ms INT NOT NULL,
  status TEXT NOT NULL,              -- ok | upstream_error | upstream_timeout | auth_failed
  error_code TEXT,
  client_ip INET,
  request_id TEXT
);
CREATE INDEX idx_proxy_calls_alias_ts ON mcp_proxy_calls (upstream_alias, ts DESC);
```

**API**

```
GET    /api/mcp/upstreams              → 전체 upstream + 상태
POST   /api/mcp/upstreams              → 추가 (admin)
PATCH  /api/mcp/upstreams/{alias}      → enabled toggle / 메타 수정
DELETE /api/mcp/upstreams/{alias}      → 제거 (tool 자동 dereg)
POST   /api/mcp/upstreams/{alias}/ping → 즉시 health check
GET    /api/metrics/mcp/proxy          → 호출 집계 (대시보드용)
```

### Phase 2 — stdio transport + 다중 upstream + 헬스체크

- stdio MCP (npx 으로 띄우는 외부 도구) 지원
- subprocess 풀 관리 — 죽으면 자동 재시작 (백오프)
- 동시 upstream 5~10개 안정 운영
- 자동 비활성/복구 — 5분 연속 실패 시 비활성, 복구 감지 시 자동 재활성

### Phase 3 — Admin UI (dashboard 신규 탭)

- upstream 목록 + 상태 (up/down/latency/tool count)
- 추가 form (alias / transport / url / auth env_var / TLS toggle)
- per-upstream toggle (enable/disable)
- 호출 로그 tail
- 도구별 호출 수 + p95 latency 그래프

### Phase 4 — RBAC + Rate limit + C-4 연계

- 클라이언트 식별 (인증 게이트 C-4 완성 전제)
- per-client 권한: 어떤 upstream 의 어떤 도구 호출 가능한지 매트릭스
- per-upstream rate limit (default 100/min, admin 조정 가능)
- 토큰 권한 위임 (downstream 사용자 token 을 upstream 으로 forward — OAuth-like)

---

## 4. 핵심 아키텍처

### 네임스페이스 규칙

- `<upstream_alias>__<tool_name>` (double underscore)
- alias 는 영문 snake_case, `^[a-z][a-z0-9_]{2,30}$`
- tool name 은 upstream 의 원본 그대로 (변경 안 함)
- 충돌 시: 첫 등록 우선, 뒤 등록은 거부 + 사용자 알림 (admin 이 alias 변경 권장)
- AIDataHub 자체 도구 (built-in 14개) 는 prefix 없이 그대로 — wave-6 가 자체 도구를 prefix 처리 안 함

### Transport — HTTP (Phase 1)

- `mcp.client.streamable_http_client` 사용
- 연결 풀: upstream 당 최대 4 connection, idle timeout 300s
- 재연결 정책: exponential backoff (1s → 2s → 4s → 8s, 최대 60s)
- 헤더 통과: `X-Request-ID`, custom `Authorization` (per-upstream auth 설정)

### Transport — stdio (Phase 2)

- `mcp.client.stdio_client` 사용
- subprocess 가 죽으면 백오프 재시작
- env 전달: 매니페스트 `env_passthrough: [VAR]` 만 통과 (기본은 PATH만)
- 격리: subprocess 가 우리 host 권한으로 동작 — wave-4 게이트 동등 적용 (timeout, semaphore 등)

### 매니페스트 (config/upstream_mcps.yaml)

```yaml
upstreams:
  - alias: analytics
    url: http://analytics-mcp.internal:8002/mcp/
    transport: http
    auth:
      type: bearer
      env_var: ANALYTICS_MCP_TOKEN
    description_prefix: "[분석] "
    tls_verify: true
    enabled: true

  - alias: slack
    transport: stdio
    command: npx
    command_args: ["-y", "@modelcontextprotocol/server-slack"]
    env_passthrough: [SLACK_BOT_TOKEN]
    enabled: true

  - alias: hq
    url: https://aidh-hq.example.com/mcp/
    transport: http
    auth:
      type: bearer
      env_var: HQ_AIDH_TOKEN
    tls_verify: true
    enabled: false  # 일단 끄고 셋업, 검증 후 enable
```

---

## 5. Pre-flight Validation (upstream 추가 시)

| 검사 | 차단 조건 | 에러 코드 |
|---|---|---|
| alias regex | `^[a-z][a-z0-9_]{2,30}$` 위반 | `INVALID_ALIAS` |
| alias 중복 | 같은 alias 이미 존재 | `ALIAS_TAKEN` |
| URL 도달 가능 | http connect 실패 (5s timeout) | `UNREACHABLE` |
| MCP 프로토콜 응답 | initialize 응답 형식 위반 | `NOT_MCP_SERVER` |
| 인증 통과 | auth_var 누락 또는 401 | `AUTH_FAILED` |
| Tool 충돌 | upstream tool name 이 우리 built-in 또는 다른 upstream 의 노출 이름과 겹침 | `TOOL_CONFLICT` (해결책: alias 변경) |
| TLS 검증 강제 (운영 모드) | tls_verify=false 인데 admin 명시 승인 없음 | `TLS_VERIFY_REQUIRED` |

---

## 6. Audit Log + Observability

- 모든 proxy 호출은 `mcp_proxy_calls` 에 1행 INSERT
- 기존 `/api/metrics/mcp` (wave-1 T5) 와 별 라인 — `/api/metrics/mcp/proxy` 신규
- 집계: upstream 별 호출 수, 평균 latency, p95, 에러율, 도구별 사용 빈도
- alerting hook (Phase 3): 에러율 > 50% / 5분 → admin 알림 (이메일/슬랙 webhook)

---

## 7. 보안

| 위협 | 완화 |
|---|---|
| 임의 URL 등록 → 외부 leak | admin only — `POST /api/mcp/upstreams` 권한 게이트 (C-4 연계 P4) |
| TLS verify off 남용 | per-upstream 명시 + audit 로그 + 운영 모드는 강제 ON |
| 토큰 평문 저장 | DB 에는 `env_var` 만 저장. 실토큰은 .env / vault. 절대 표시 X |
| upstream 이 악성 응답 (오버사이즈, stream 폭주) | 응답 size limit (default 10MB) / streaming 시간 상한 (default 30s) |
| 권한 위임 우회 | upstream 토큰은 항상 AIDataHub 의 단일 service account — 사용자별 분리는 P4 RBAC |
| Rate limit 회피 | per-caller + per-upstream 양쪽 적용 (P4) |
| 무한 federation 루프 (A→B→A) | upstream alias 화이트리스트 + AIDataHub 자체 URL 등록 거절 |

---

## 8. 에러 메시지 카탈로그

| 코드 | 의미 | 사용자 액션 |
|---|---|---|
| `UPSTREAM_UNREACHABLE` | TCP 연결 실패 / DNS 미해석 | admin 알림 — upstream URL/방화벽 점검 |
| `UPSTREAM_AUTH_FAILED` | 401/403 응답 | env_var 의 토큰 갱신 필요 |
| `UPSTREAM_TIMEOUT` | 응답 시간 초과 (default 60s) | upstream 부하 또는 도구 자체 timeout 매니페스트 상향 |
| `UPSTREAM_PROTOCOL_ERROR` | MCP JSON-RPC 형식 위반 응답 | upstream 호환성 — 버전 확인 |
| `TOOL_NOT_EXPOSED` | upstream 은 살아있으나 해당 tool 이 더 이상 list 에 없음 | upstream 에서 도구 제거됨 — sync 후 우리 측 캐시 갱신 |
| `RATE_LIMITED` | per-upstream 한도 초과 | 호출 분산 또는 admin 한도 상향 |
| `TOOL_CONFLICT` | 다른 upstream 또는 built-in 과 이름 충돌 | upstream alias 변경 |

---

## 9. 수락 테스트 (DoD)

Phase 1 통과 기준:

| 항목 | 목표 |
|---|---|
| HTTP upstream 1개 등록 → tool list 노출 latency | < 5s (부팅 시) |
| 호출 dispatch latency 오버헤드 | < 20ms (LAN, sif warm 무관) |
| upstream 다운 → tool list 응답에서 제외 latency | < 60s (1 ping cycle) |
| upstream 복구 → 자동 재노출 latency | < 60s |
| 동시 호출 throughput (단일 upstream) | 100 req/s @ p95<200ms (upstream 자체 성능 한도) |
| 충돌 도구 등록 거절 | 100% |
| TLS verify off 시 audit 로그 기록 | 100% |
| `mcp_proxy_calls` 적재 누락 | 0% (호출당 1행 보장) |

Phase 4 통과 추가:

| 항목 | 목표 |
|---|---|
| per-client 도구 권한 매트릭스 | 정책 변경 즉시 반영 (TTL 60s) |
| Rate limit 정확성 | 한도의 ±5% 이내 |

---

## 10. 트레이드오프 + 위험

| 장점 | 단점 |
|---|---|
| 클라이언트 등록 부담 1/N | AIDataHub 가 SPOF — 다운 시 모든 도구 접근 끊김 |
| 중앙 감사·로깅 | 호출당 latency +5~20ms (LAN 1홉) |
| 토큰 통합 관리 | upstream schema 변경 시 우리 캐시 stale 가능 → 주기 sync 필요 |
| 사용자 UX 일관 | upstream 별 quirk (특수 인자, 에러 포맷) 처리 부담 |
| wave-5 와 시너지 (사이트 간 도구 공유) | 무한 루프 위험 — 화이트리스트 강제 |

---

## 11. wave-5 와의 결합 시너지

- wave-5 로 등록된 도구 → wave-6 federation 으로 다른 AIDataHub 인스턴스가 사용 가능
- 사이트 간 도구 공유 패턴:

```
[HQ AIDataHub]
  - wave-5 로 stress_strain_plot 업로드 → mcp_uploads
  - FastMCP 에 stress_strain_plot 노출

[지점 AIDataHub]
  - config 에 HQ 를 upstream 으로 등록 (alias=hq, url=https://aidh-hq...)
  - wave-6 가 자동 발견 → 지점 도구로 hq__stress_strain_plot 노출
  - 지점 사용자가 자연어로 호출 → 지점 AIDataHub 가 HQ 로 dispatch → 결과 반환
```

→ 사실상 **MCP-as-a-Service** 인프라. 도구 개발은 HQ, 사용은 전사.

---

## 12. wave-5 vs wave-6 진행 순서

| 옵션 | 장점 | 단점 |
|---|---|---|
| A. wave-5 P1 먼저 → wave-6 P1 | wave-5 가 더 즉시 가치 (도구 자체 늘림) | wave-6 가 늦어짐 |
| B. wave-6 P1 먼저 → wave-5 P1 | 사내 기존 MCP 서버 통합 빨라짐 | 통합할 upstream 이 없으면 의미 약함 |
| **C. 두 P1 병행 (권장)** | 파일 충돌 없음 (mcp_federation.py vs mcp_upload_svc.py 분리). 둘 다 백엔드 | 리뷰 부담 약간 증가 |

**옵션 C 권장** — 단일 개발자라도 트랙 분리 가능. 사용자 결정 신호 대기.

---

## 13. 글로사리 (비전공 사용자용)

| 용어 | 1줄 설명 |
|---|---|
| Federation / Proxy | 여러 MCP 서버를 1개로 합쳐서 노출하는 패턴 |
| Upstream | federation 대상이 되는 외부 MCP 서버 (분석팀 MCP 등) |
| Alias | upstream 을 식별하는 짧은 영문 이름 (예: analytics) |
| Namespace prefix | 도구 이름 앞에 alias 를 붙여서 충돌 방지 (`analytics__report`) |
| RBAC (Role-Based Access Control) | 사용자 역할 별로 도구 호출 권한 매트릭스 |
| MCP-as-a-Service | 중앙에서 도구를 만들고 여러 곳이 federation 으로 공유 |

---

## 14. 완료 정의 (DoD)

**Phase 1** 가 다음을 만족할 때 완료:

- [ ] HTTP upstream 1개 (`config/upstream_mcps.yaml` 또는 admin UI) 등록 → 자동 tool list 노출
- [ ] 자연어 호출 → upstream dispatch → 응답 반환 end-to-end (Claude Desktop / Cursor 등에서 검증)
- [ ] upstream 다운 시 tool 자동 비활성 + 복구 시 재활성
- [ ] `mcp_proxy_calls` 에 호출 1행 적재 확인
- [ ] 단위 + 통합 테스트 통과
- [ ] 에러 코드 카탈로그 (12종 중 핵심 6종) 응답 검증

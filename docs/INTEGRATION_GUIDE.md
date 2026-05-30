# AX Hub — 새 외부 시스템 통합 가이드 (단일 진실의 소스)

> 사업부의 다른 시스템(VOC / 위키 / CAE / 시뮬레이션 / IoT 등) 을 AX Hub 에
> 연결할 때 **이 문서 하나만 따르면** 끝나도록 만든 표준 절차서.
> SignalForge + MX White Paper 실제 통합 + 라이브 검증에서 발견된 11개
> 함정과 해결을 모두 반영.

작성일: 2026-05-30
최신 검증: SF 2 mock VOC + MXWP 3 published 문서 — 5 record 정상 적재
AX Hub 버전: v0.8+ (alembic 0027 적용 필요)

---

## 0. TL;DR — 5분 이내 의사결정

새 외부 시스템 1개를 연결하려면 **5가지만 결정**:

1. **시스템 이름** — 영문 짧은 식별자 (예: `signalforge-gs`, `mxwp`, `heaxhub`)
2. **list endpoint URL** — 데이터 목록을 GET 할 한 줄 (예: `/api/v1/items`)
3. **인증 키** — 외부 시스템이 발급한 토큰 (환경변수로 보관)
4. **외부 record 의 고유 식별자 키** — 응답 JSON 의 어느 키가 record id 인지 (`id` / `document_id` / `uuid` 등)
5. **본문/제목/태그 위치** — top-level 인지 중첩(`content.metadata`)인지

→ 이 5가지를 [config/sync_sources.yml](../config/sync_sources.yml) 에 yaml 1개 항목으로 추가 → 부팅 → 적재 끝.

세부는 아래 9장 + 부록 2.

---

## 1. 사전 조사 체크리스트 — 외부 시스템 분석 (15~30분)

새 시스템 통합 전 다음을 확인. 답이 안 나오면 통합 진행 막지 말고 통합 도중 알게 됨 (라이브 검증에서 다 잡혔음).

### 1-1. 응답 형식
```
[ ] GET {base_url}/{list_endpoint}?limit=2 호출 가능 (200)
[ ] 응답이:
    □ raw array `[item, item]`
    □ {items: [...], next_cursor: ...} (cursor 기반)
    □ {data: [...], meta: {next_offset, ...}} (offset envelope)
    □ {results: [...], count, next}
[ ] items 의 키 목록 — 특히 id 키 이름 확인 (id / document_id / uuid)
[ ] title / body 위치 (top-level vs content.X)
[ ] metadata / tags 위치 (top-level vs content.metadata)
```

### 1-2. 인증
```
[ ] 헤더 이름: X-API-Key (가장 흔함) / Authorization / Cookie
[ ] 토큰 발급 방법: 외부 시스템 admin UI / curl / DB INSERT
[ ] dev 모드에서 인증 없이 호출 가능한가? (테스트 시 편리)
```

### 1-3. 페이지네이션
```
[ ] limit/offset 지원?
[ ] cursor (next_cursor) 지원?
[ ] since/until (시간 필터)?
[ ] 페이지네이션 없음 (작은 데이터셋, 일괄 다 받음)?
[ ] max page_size 한도 확인 (보통 100~500)
```

### 1-4. detail endpoint 필요 여부
```
[ ] list 응답에 body / sections / metadata 가 다 있나?
    YES → detail 불필요 (예: SignalForge VOC 응답)
    NO  → detail_endpoint 필요 (예: MXWP — list 는 메타만, detail 에 sections)
[ ] detail URL 패턴 (예: /api/v1/items/{slug} 또는 /{id})
```

### 1-5. 식별자 길이
```
[ ] id 타입과 길이:
    □ int (1~21억)
    □ short string (~20 chars)
    □ UUID (36 chars)
    □ ULID (26 chars)
[ ] section_id 가 따로 있다면 그것도 확인 (record_sections 컬럼 20자 한도)
[ ] 20자 초과 시 — sync_svc 가 자동 truncate (충돌 위험 낮음)
```

### 1-6. 조인 객체 (FK)
```
[ ] response 에 product / platform / user 같은 joined object 있나?
    YES → product.code, platform.code 같은 path 매핑 가능
    NO  → product_id (int) 만 — tag 가 숫자가 되거나 매핑 누락
```

### 1-7. 시간 필드
```
[ ] created_at / updated_at / published_at / processed_at 중 어느 게 있나?
[ ] server-side since 필터가 어느 필드 기준?
[ ] NULL 가능 여부 (NLP 미완료 record 등)
```

### 1-8. server-side filter
```
[ ] status, country, sentiment 등 쿼리 파라미터 필터 가능?
[ ] 그 외 hierarchy 필터 (part_slug, division 등)
```

### 1-9. 응답 샘플 저장
```
[ ] curl 응답 1건을 yaml 작성 시 참조용으로 저장
```

---

## 2. AX Hub 운영자 절차 (1회 셋업 30분)

### Step 1 — 환경 준비 (모든 source 공통, 한 번만)

```bash
# BOOTSTRAP_API_KEY 설정 (이미 있으면 skip)
grep -q "^BOOTSTRAP_API_KEY=." deploy/apptainer/.env || \
  echo "BOOTSTRAP_API_KEY=$(openssl rand -hex 16)" >> deploy/apptainer/.env

# 서버 재시작 (alembic upgrade 자동)
bash deploy/apptainer/stop.sh && bash boot.sh

# 정식 API key 발급 (한 번만 — 이후 모든 작업에 사용)
BOOTSTRAP_KEY=$(grep "^BOOTSTRAP_API_KEY=" deploy/apptainer/.env | cut -d= -f2)
curl -X POST -H "X-API-Key: $BOOTSTRAP_KEY" \
  -H "Content-Type: application/json" \
  http://localhost:8001/api/auth/keys \
  -d '{"name":"sync_admin","department":"ops","rate_limit_per_min":600,"agent_scopes":["*"]}'
# 응답의 "key": "sk_..." 를 .env 또는 secret manager 에 보관
# export AIDH_KEY=sk_...
```

### Step 2 — org master 등록 (해당 team/group 가 처음일 때)

```bash
curl -X POST -H "X-API-Key: $AIDH_KEY" -H "Content-Type: application/json" \
  http://localhost:8001/api/org/teams \
  -d '{"code":"HE","name":"Hardware Experience","is_active":true}'

curl -X POST -H "X-API-Key: $AIDH_KEY" -H "Content-Type: application/json" \
  http://localhost:8001/api/org/groups \
  -d '{"team_code":"HE","code":"AX","name":"AX Hub","is_active":true}'
```

이미 있으면 409 — 정상.

### Step 3 — config/sync_sources.yml 에 항목 추가

[4장](#4-configsync_sourcesyml--모든-옵션-가이드) + [5장](#5-매핑-룰-cookbook--시나리오별) 참조.

### Step 4 — 환경변수 설정

```bash
export NEW_SOURCE_API_KEY=xxx
# 또는 deploy/apptainer/.env / systemd unit 파일에 추가
```

### Step 5 — 재시작 → 자동 등록

```bash
bash boot.sh

# 로그 확인:
tail -30 /tmp/aidh_boot.log | grep sync_bootstrap
# 정상: "sync_sources bootstrap — created=1 updated=0 ... file=config/sync_sources.yml"
```

### Step 6 — dry-run 검증 (필수)

```bash
# 1) 등록된 source 목록 확인 — 새 source id 찾기
curl -s -H "X-API-Key: $AIDH_KEY" http://localhost:8001/api/sync/sources | jq

# 2) dry-run (저장 X)
SOURCE_ID=N  # 위에서 확인한 id
curl -X POST -H "X-API-Key: $AIDH_KEY" \
  "http://localhost:8001/api/sync/sources/$SOURCE_ID/verify?max_pages=1"

# 결과:
#   "status": "ok", "fetched": N, "dead_letter_count": 0  → 진행 OK
#   "failed": N > 0  → dead_letter 분석 필요
curl -s -H "X-API-Key: $AIDH_KEY" \
  "http://localhost:8001/api/sync/runs/{run_id}" | jq '.dead_letter[:3]'
```

### Step 7 — 실 운영 cron 등록

```bash
crontab -e
# 30분 주기:
*/30 * * * * curl -fsS -X POST -H "X-API-Key: $AIDH_KEY" \
  http://localhost:8001/api/sync/sources/N/run > /tmp/aidh_sync_N.log 2>&1
```

### Step 8 — Dashboard 확인

`http://localhost:8001/dashboard/` → **09 연결 소스** 탭 →
KPI 5개 (등록/활성/정상/에러/미실행) + 테이블 + 액션 버튼.

---

## 3. 외부 시스템 측 책임 (해당 팀에게 안내)

| 책임 | 필수 여부 | 작업량 |
|---|---|---|
| **API 키 발급** — AX Hub 가 호출할 때 사용할 토큰 | 필수 | 5분 |
| **list endpoint 가 안정적으로 응답** (200, JSON, 페이지네이션) | 필수 | 0~? (보통 이미 있음) |
| **detail endpoint** (list 가 메타만 줄 때) | 필요시 | 보통 이미 있음 |
| **응답에 since/updated_at 필드 포함** (변경분만 가져오기) | 권장 | 0~? |
| **server-side filter** (status, category 등) | 권장 | 0 |
| **API rate-limit 사양 명시** (4 req/s 등) | 권장 | 0 |
| **응답 envelope 표준화** (`{data:[], meta:{}}` 권장) | 옵션 | 0 |
| **PII 마스킹 처리** (개인정보 들어가는 경우) | 컴플라이언스 | 데이터 성격별 |

**중요**: 우리 어댑터는 **상대 측 코드 변경 0** 이 가능하게 설계됨. 기존 API 그대로 사용. 매핑 룰은 AX Hub 측 yaml 1개로 흡수.

---

## 4. config/sync_sources.yml — 모든 옵션 가이드

```yaml
sources:
  - name: "my-new-source"           # [필수] 영문 unique 식별자
    description: "한 줄 설명"         # 권장

    # ---------- 외부 시스템 접속 ----------
    base_url: "http://api.foo.local:8080"   # [필수] 환경별 조정 (컨테이너 이름 vs IP)
    api_key_env: MY_SOURCE_KEY             # [권장] 환경변수 이름. 평문 금지.
    # api_key_file: "/run/secrets/foo_key" # [옵션] Docker secret 등
    # api_key: "..."                       # [비권장] 평문
    auth_header: "X-API-Key"               # [옵션] 기본 X-API-Key. Bearer 면 "Authorization"

    # ---------- API endpoint ----------
    list_endpoint: "/api/v1/items"         # [필수] GET path. '/'로 시작.
    list_method: "GET"                     # [옵션] 기본 GET
    detail_endpoint: "/api/v1/items/{id}"  # [옵션] {slug}/{id} 자동 치환
                                           # list 응답이 메타만 줄 때 필수

    # ---------- 페이지네이션 ----------
    cursor_param: "cursor"                 # 또는 "offset"
    since_param: "since"                   # 서버 미지원 시 "_unsupported_..." (자동 skip)
    limit_param: "limit"
    page_size: 100                         # 한 페이지 record 수

    # ---------- 안전 장치 ----------
    max_rps: 2.0                           # 우리 측 throttle (req/s)
    retry_max: 3                           # http error 시 재시도
    retry_backoff_sec: 2.0                 # 지수 backoff
    trust_pii_masked: false                # false 면 classification 강제 confidential

    # ---------- 매핑 룰 (다음 절 참조) ----------
    mapping_rules: { ... }

    # ---------- 운영 ----------
    schedule_cron: "*/30 * * * *"          # 문서용 (실 cron 은 crontab 등록)
    enabled: true                          # false 면 자동 sync 안 됨
```

**환경변수 옵션**:
- `AIDH_SYNC_BOOTSTRAP=false` — 부팅 자동 등록 비활성
- `AIDH_SYNC_CONFIG_FILE=/path/to/yml` — yaml 경로 변경
- `AIDH_SYNC_ALLOW_INTERNAL=true` — private IP / 비표준 포트 허용 (개발/사내)
- `AIDH_SYNC_ALLOW_DNS_UNRESOLVED=true` — DNS 실패 통과 (운영 시 비권장)

---

## 5. 매핑 룰 cookbook — 시나리오별 예시

### A. 가장 단순 — 자체 완비형 list 응답

```yaml
mapping_rules:
  id_field: id
  title_field: title
  body_field: body
  data_type: DOC
  team: XX
  group: YY
  doc_type: my_doctype
  agents: [my-agent]
  classification: internal
  language: ko
```

### B. detail 필요 (MXWP 패턴)

```yaml
list_endpoint: /api/v1/documents
detail_endpoint: /api/v1/documents/{slug}   # ← 자동 호출
mapping_rules:
  id_field: id                              # detail 응답의 id 키
  title_field: title
  sections_field: sections                  # detail 의 sections 평탄화
  tags_fields:                              # detail 의 metadata 안
    - metadata.tags
    - metadata.owners
    - status
  tags_prefix:
    metadata.owners: "author:"
    status: "status:"
  subject_keywords_fields:
    - metadata.keywords
    - metadata.tags
  valid_from_field: created_at
  year_field: created_at
  data_type: DOC
  team: MX
  group: WP
  doc_type: whitepaper
  agents: [mx-whitepaper-analyst]
  classification: internal
  language: ko
  list_filter: { status: published }         # server-side filter
  attachments_field: content.attachments
  attachment_mode: url_ref
  attachment_url_template: "{base_url}/api/v1/files/{image_id}"
```

### C. template + tags_prefix (SF VOC 패턴)

```yaml
mapping_rules:
  id_field: id
  title_template: "{content_original|truncate:80}"
  summary_template: "{product.code} VOC — {sentiment_label} ({platform.code}, {country_code})"
  body_field: content_original
  tags_fields: [categories, country_code, sentiment_label]
  tags_prefix:
    country_code: "country:"
    sentiment_label: "sentiment:"
    categories: "voc:"
```

지원 필터:
- `{path.to.field|truncate:80}` — 80자 자르고 `...` 추가
- `{path|upper}` / `{path|lower}` — 대소문자
- `{path|default:val}` — 빈 값 fallback

### D. 2단 transform 체인 (sentiment → severity → quality)

```yaml
mapping_rules:
  sentiment_field: sentiment_label
  transform:
    sentiment_to_severity:
      very_negative: critical
      negative: major
      neutral: info
      positive: info
    severity_to_quality_score:
      critical: 100
      major: 75
      minor: 50
      info: 25
```

severity_field 가 이미 있다면 sentiment_to_severity 단계 생략.

### E. server-side filter — `list_filter`

```yaml
mapping_rules:
  list_filter:
    status: published
    category: tech
    locale: ko
```

이 키들이 자동으로 query param 으로 주입.

### F. record 거부 (filter)

```yaml
mapping_rules:
  filter:
    require_processed_at: true              # processed_at 이 truthy 여야 함
    pii_masked: true                        # 직접 비교 — pii_masked 가 True 만 통과
    sentiment_label__in: [negative, very_negative]   # 리스트 매칭
    country_code__not_in: [XX, YY]          # 리스트에 없어야
```

### G. attachment passthrough

```yaml
mapping_rules:
  attachments_field: "content.attachments"  # 응답의 attachment 배열 위치
  attachment_mode: url_ref                  # url 만 보존 (다운 X)
  attachment_url_template: "{base_url}/files/{image_id}"
```

### H. 한 source 안에서 여러 doc_type 동적 분류 (현재 미지원)

지원 안 됨. 동적 분류 필요 시 source 를 여러 개 등록 (예: signalforge-gs / signalforge-gz).

---

## 6. 함정 매트릭스 — 라이브 검증에서 발견된 11개

| # | 함정 | 어떻게 알 수 있나 | 해결 |
|---|---|---|---|
| 1 | `BOOTSTRAP_API_KEY` 빈 값 | `/api/auth/keys` 발급 시 401 | `.env` 설정 + 재시작 (Step 1) |
| 2 | base_url 컨테이너 이름 | dry-run `ConnectTimeout` | localhost 또는 호스트 IP / 방화벽 |
| 3 | `id_field` 키 이름 다름 | dead_letter `missing external id field` | 응답 샘플 확인 후 yaml 수정 |
| 4 | response 에 FK 조인 객체 없음 (product.code) | tags 일부 누락 | _id (int) 사용 또는 외부 backend 보강 요청 |
| 5 | list 가 메타만 → detail 필요 | dead_letter `missing title` / `missing body` | yaml 에 `detail_endpoint` 추가 (자동 호출) |
| 6 | ULID id (26 chars) — section_id VARCHAR(20) 초과 | dead_letter `StringDataRightTruncationError` | section 의 `number` 같은 짧은 키 우선 (자동) |
| 7 | org_teams / groups 미등록 | dead_letter `team X is not registered or inactive` | Step 2 절차 |
| 8 | filter 필드 컬럼 없음 (e.g. pii_masked) | 모든 row reject `filter failed: pii_masked=None != True` | yaml filter 에서 그 키 제거. PII 보호는 trust_pii_masked=false 로 |
| 9 | 인증 헤더 이름 다름 | 401 | `auth_header: "Authorization"` 등 |
| 10 | response envelope (data 키) | items 비어 보임 | sync_svc 가 자동 unwrap (data/items/results) — 이미 처리 |
| 11 | series_code vs product_code (SF) | list_endpoint URL 매칭 안 됨 / 빈 응답 | specific code 사용 (`GS25` not `GS`) — 시리즈별 source 분할 |

---

## 7. Troubleshooting — 에러 메시지별 즉시 해결

| HTTP / 에러 | 원인 | 해결 |
|---|---|---|
| `401 authentication required` | X-API-Key 없음/잘못 | 헤더 점검. 신규 키는 `/api/auth/keys` POST |
| `409 sync_source busy: another run is in progress` | 다른 run 진행 중 | 30분 대기 (자동 stale 처리) 또는 직접 update: `UPDATE sync_runs SET status='error', finished_at=NOW() WHERE status='running';` |
| `400 base_url rejected: localhost blocked` | SSRF 가드 (운영 모드) | `AIDH_SYNC_ALLOW_INTERNAL=true` |
| `400 base_url rejected: non-standard port: 8080` | port 80/443 외 | 위와 동일 |
| `400 base_url rejected: metadata endpoint blocked` | 169.254.169.254 등 — 진짜 위험 | base_url 점검 (정말 외부인지 확인) |
| `400 base_url rejected: dns resolution failed` | DNS 못 풀림 | hostname 점검. 또는 `AIDH_SYNC_ALLOW_DNS_UNRESOLVED=true` |
| `ConnectTimeout` | 외부 서버 미도달 | base_url 정확성, 방화벽, 외부 서버 상태 |
| `dead_letter: missing external id field 'X'` | id_field 잘못 | 응답 샘플 확인 후 yaml 수정 |
| `dead_letter: missing title (no title_field/title_template resolved)` | title 매핑 잘못 | title_field 또는 title_template 추가 |
| `dead_letter: filter failed: X is falsy` | server 데이터 NULL | yaml filter 의 require_X 검토 |
| `dead_letter: team 'XX' is not registered or inactive` | org master 없음 | Step 2 절차 |
| `dead_letter: StringDataRightTruncationError ... character varying(20)` | section_id 길이 초과 (ULID 등) | section 응답에 짧은 `number` 있나 — 자동 우선. 없으면 `~` truncate (자동) |
| `MissingGreenlet: greenlet_spawn has not been called` | (해소됨) ORM expired | 다시 발생하면 sync_svc 의 `expire_on_commit=False` 누락 |

---

## 8. 운영 모니터링

| 위치 | 무엇 | 권장 빈도 |
|---|---|---|
| `/dashboard/` → 09 연결 소스 | KPI + 테이블 + Verify/Run/이력 액션 | 매일 |
| `GET /api/sync/sources` | 전체 source 목록 + 상태 (programmatic) | 모니터링 스크립트 |
| `GET /api/sync/sources/{id}/runs?limit=10` | 최근 N 실행 이력 | 디버깅 시 |
| `GET /api/sync/runs/{run_id}` | 단일 run 상세 (dead_letter PII 마스킹 후 포함) | 실패 분석 |
| `/tmp/aidh_boot.log` | 부팅 자동 등록 결과 | 부팅 후 |
| `deploy/apptainer/logs/uvicorn.log` | sync_bootstrap + sync_run + 에러 trace | 에러 발생 시 |
| `crontab -l` | cron 등록 상태 | 한 번 |

### 알림 추천 (선택)
- last_status=`error` 가 N회 연속 → Slack/메일
- last_sync_at 이 1시간 이상 갱신 안 됨 → 알림
- failed_count / fetched_count > 0.5 → 매핑 룰 점검 알림

---

## 9. 새 시스템 추가 워크스루 — 가상 예시 (HEAXHub)

### Step 0 — 사전 조사 (30분)

```bash
# 응답 형식 확인
curl http://heaxhub:4040/api/v1/projects?limit=2
# 응답: [{id, name, status, created_at, content: {body, sections, metadata}}, ...]

# detail 필요?
# NO — list 응답에 content 전체 있음

# 페이지네이션
curl http://heaxhub:4040/api/v1/projects?limit=2&offset=10
# offset 동작 OK

# 인증
# Authorization: Bearer xxx
```

### Step 1 — yaml 추가

```yaml
# config/sync_sources.yml 의 sources: 리스트에 추가

  - name: heaxhub
    description: "HE AX Hub 프로젝트 동기화"
    base_url: "http://heaxhub:4040"
    api_key_env: HEAXHUB_KEY
    auth_header: "Authorization"   # ★ Bearer 토큰
    list_endpoint: "/api/v1/projects"
    cursor_param: offset
    since_param: "_unsupported_..." # since 미지원 시
    limit_param: limit
    page_size: 50
    max_rps: 2.0
    enabled: true
    schedule_cron: "*/60 * * * *"   # 매시간
    mapping_rules:
      id_field: id
      title_field: name
      body_field: content.body
      sections_field: content.sections
      tags_fields:
        - content.metadata.tags
        - status
      tags_prefix:
        status: "status:"
      subject_keywords_fields:
        - content.metadata.keywords
      valid_from_field: created_at
      year_field: created_at
      data_type: DOC
      team: HE
      group: AX
      doc_type: project_brief        # ★ doc_type 신규 등록 필요
      agents: [he-ax-analyst]        # ★ agent 신규 등록 필요
      classification: internal
      language: ko
```

### Step 2 — Bearer 토큰 형식 보강 (보통 `Bearer xxx`)

```bash
export HEAXHUB_KEY="Bearer abc123"
# 또는 yaml 에 prefix 박힘 — api_key_env 가 그대로 헤더 값으로 들어감
```

### Step 3 — org / doc_type / agent 사전 등록

```bash
# HE team 이미 있다면 skip
curl -X POST -H "X-API-Key: $AIDH_KEY" -H "Content-Type: application/json" \
  http://localhost:8001/api/org/groups \
  -d '{"team_code":"HE","code":"AX","name":"AX Hub","is_active":true}'

# 신규 doc_type
curl -X POST -H "X-API-Key: $AIDH_KEY" -H "Content-Type: application/json" \
  http://localhost:8001/api/doc-types \
  -d '{"code":"project_brief","name":"Project Brief","mode":"llm_context"}'

# 신규 agent (간단)
curl -X POST -H "X-API-Key: $AIDH_KEY" -H "Content-Type: application/json" \
  http://localhost:8001/api/agents \
  -d '{"agent_type":"he-ax-analyst","name":"HE AX Analyst","required_doc_type":"project_brief","sample_queries":["..."]}'
```

### Step 4 — 재시작 + dry-run + cron — Step 5, 6, 7 (위 2장)

```bash
bash boot.sh
# 자동 등록 — sync_sources 에 heaxhub row 생성
# 다음 sync_sources/{id}/verify → 적재 확인 → cron 등록
```

---

## 부록 A — 외부 시스템 팀에게 줄 1페이지 안내

> 이 파일을 그대로 외부 팀에게 보내도 됨

**제목**: AX Hub 연동 요청 — 필요한 것 + 우리가 제공하는 것

### 우리가 요청하는 것

| # | 항목 | 필수/권장 | 비고 |
|---|---|---|---|
| 1 | API 키 발급 | 필수 | X-API-Key 또는 Bearer 헤더 |
| 2 | list endpoint URL | 필수 | 보통 이미 있음 (analytics / list API) |
| 3 | API rate-limit 사양 | 권장 | 4 req/s 등 |
| 4 | detail endpoint URL (list 가 메타만 줄 때) | 필요시 | 보통 이미 있음 |
| 5 | server-side updated_at filter | 권장 | 매번 전체 동기화 → 증분 동기화로 비용 절감 |
| 6 | 응답 envelope 표준화 | 옵션 | `{data:[], meta:{cursor/offset, total}}` 권장 |

### 우리가 제공하는 것

- AX Hub 어댑터 코드 — 외부 시스템 측 코드 변경 **0**
- 외부 시스템 측 통합 폴더: `integrations/aidatahub/` (선택 — push 모드 시)
- 검증 키트 zip: `GET /api/schema/ingest-kit.zip` — LLM 으로 데이터 정제 시
- 결과 record 검색 API + Dashboard

### 통합 후 운영

- AX Hub 측이 cron 으로 30분 주기 pull
- 또는 외부 시스템 측이 webhook push (real-time)
- 동기화 실패 시 AX Hub 의 sync_runs.dead_letter 에 원인 기록 (PII 마스킹 후)

문의: AX Hub 운영팀

---

## 부록 B — AX Hub 운영자 1페이지 체크리스트

### 1회 setup (30분)

```
[ ] Step 1: BOOTSTRAP_API_KEY 설정 + 재시작 + API key 발급
[ ] Step 2: org_teams / org_groups 등록 (해당 team/group 없을 때)
[ ] config/sync_sources.yml 에 항목 추가 — 5장 cookbook 참조
[ ] 환경변수 설정 (api_key_env 가리키는 값)
[ ] 재시작 — bash boot.sh
[ ] /tmp/aidh_boot.log 확인 — created/updated 결과
[ ] Step 6: dry-run /api/sync/sources/{id}/verify?max_pages=1
    fetched > 0 + dead_letter_count = 0 이어야 진행
[ ] dead_letter 있으면 6장 함정 매트릭스 참조
[ ] Step 7: crontab 등록
[ ] Step 8: Dashboard 확인
```

### 매일 모니터링

```
[ ] /dashboard/ 09 탭 — KPI 확인 (에러 0)
[ ] failed/fetched 비율 > 0.5 인 source 있나 → 매핑 룰 점검
[ ] last_sync_at 이 schedule_cron 보다 늦은가 → cron 점검
```

### 신규 시스템 추가 (per source 30~60분)

```
[ ] 1장 사전 조사 체크리스트 (15~30분)
[ ] 5장 cookbook 보고 매핑 룰 작성
[ ] Step 3-7 절차
[ ] 신규 doc_type 필요하면 POST /api/doc-types (mode 명시)
[ ] 신규 agent 필요하면 POST /api/agents
```

### 트러블슈팅 우선순위

```
1. 401 → API key 헤더 점검
2. 409 busy → 30분 대기 또는 sync_runs status='error' 강제
3. ConnectTimeout → base_url + 외부 서버 상태
4. dead_letter 분석 → 6장 함정 매트릭스
5. 그 외 → uvicorn.log 의 stacktrace
```

---

## 부록 C — 관련 파일/문서 인덱스

| 파일 | 용도 |
|---|---|
| `config/sync_sources.yml` | 단일 진실의 소스 — 모든 외부 연결 yaml |
| `api_server/src/api/services/sync_svc.py` | sync engine (transform_record / run_sync / detail fetch / heartbeat lock) |
| `api_server/src/api/services/sync_bootstrap.py` | 부팅 시 yaml → DB 자동 등록 |
| `api_server/src/api/services/url_safety.py` | SSRF 방지 (IPv4-mapped IPv6, 메타데이터 endpoint, 포트 allowlist) |
| `api_server/src/api/routes/sync.py` | /api/sync/* REST endpoint (9개) |
| `api_server/src/api/db/models.py` | SyncSource / SyncRun / ExternalIdMap |
| `api_server/alembic/versions/0026_*` | doc_types.mode + external_id_map |
| `api_server/alembic/versions/0027_*` | sync_sources + sync_runs |
| `examples/MX/voc-signalforge/` | SignalForge 통합 예제 (검증된 매핑) |
| `examples/MX/whitepaper-mxwp/` | MXWP 통합 예제 (검증된 매핑) |
| `SignalForge/integrations/aidatahub/` | 외부 측 push 어댑터 (SF) |
| `MXWhitePaper/integrations/aidatahub/` | 외부 측 push 어댑터 (MXWP) |
| `docs/04-report/3-source-integration.md` | 초기 통합 보고서 |
| `docs/INTEGRATION_GUIDE.md` | **본 문서 — 새 시스템 통합 표준 절차** |

---

## 부록 D — 검증 이력 (라이브 동작 확인됨)

| 일자 | 검증 항목 | 결과 |
|---|---|---|
| 2026-05-30 | SF mock VOC 2건 (id 318615/318616) → AX Hub records | OK (classification=confidential, quality_score=75/25, sentiment chain) |
| 2026-05-30 | MXWP 3 published 문서 (월결산/온보딩/GPU) → AX Hub records | OK (subsections, metadata.tags, ULID section_id 자동 truncate) |
| 2026-05-30 | 연속 sync 호출 — busy lock leak | 해소 (sync_runs.status + 30min heartbeat) |
| 2026-05-30 | SSRF base_url 검증 (메타데이터/포트/IPv6) | 다 차단 |
| 2026-05-30 | 39 doc_types + Connected Sources Dashboard | OK |

총 4 commit 누적: `bdec4ce` (1차 audit) / `120a2d7` (2차 audit SSRF) / `85467f4` (라이브 detail fetch + greenlet + section_id) / `d1e4d4b` (advisory lock 제거 + SF filter)

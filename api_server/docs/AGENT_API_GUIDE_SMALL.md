# AGENT_API_GUIDE — AI Data Hub REST API

> Reference for AI agents (3B–7B). Read once, start analyzing.
> Companion: `AGENT_ONBOARDING.md` (intro). This file = full reference.

---

## 1. One-line summary (요약)

REST + MCP API. Single PostgreSQL backend (`records` table). Stores documents (문서, document) / tables (표, data) / simulations (시뮬레이션, sim) / CAD / logs / forms. Returns JSON. Optional natural-language query endpoint `POST /api/ask`.

Base URL (default): `http://localhost:8000`
Service title: `AI Data Hub`
Contract version: `1.0`

---

## 2. Authentication (인증)

| 항목 (item) | 값 (value) |
|---|---|
| Header name | `X-API-Key` |
| Auth dependency | `require_api_key` (FastAPI Depends) |
| Default mode | `AUTH_REQUIRED=false` (dev) — calls succeed without header |
| Strict mode | `AUTH_REQUIRED=true` — every call needs header |
| Bootstrap key | env `BOOTSTRAP_API_KEY` — needed for `POST /api/auth/keys`, `DELETE /api/records/{id}?hard=true` |
| Hash storage | SHA-256 (plaintext returned once at issue) |
| Verify endpoint | `POST /api/auth/keys/verify` → `{ok, key_name, agent_scopes}` |

Failure response (401):

```json
{"error":{"code":"AUTHENTICATION_ERROR","message":"missing or invalid X-API-Key","details":{},"request_id":"..."}}
```

Key issuance flow:
1. Operator sets `BOOTSTRAP_API_KEY=<bootstrap>` in env.
2. `POST /api/auth/keys` with header `X-API-Key: <bootstrap>` and body `{name, agent_scopes, department?, expires_at?}`.
3. Response includes `key` (plaintext, returned once).
4. Agents use that plaintext as `X-API-Key`.

---

## 3. Data model (데이터 모델)

### 3.1 Table `records` — 모든 컬럼 (all columns)

| 필드 (field) | 타입 (type) | NULL | 의미 (meaning) | 예시 (example) |
|---|---|---|---|---|
| `id` | str(80) PK | no | record id, 사람이 읽음 | `DOC-HE-CAE-2026-000001` |
| `data_type` | str(20) | no | enum: DOC/DATA/SIM/CAD/LOG/FORM/OTHER | `DOC` |
| `team` | str(10) | no | 팀 (team) 코드 | `HE` |
| `group` | str(20) | no | 그룹 (group) 코드 | `CAE` |
| `year` | int | no | 4-digit | `2026` |
| `seq` | int | no | sequence per (data_type, team, group, year) | `1` |
| `title` | text | no | 제목 (title) | `IGA tensile test report` |
| `summary` | text | no | 요약 (summary), 기본 "" | `2024 sample IGA results` |
| `tags` | text[] | no | 태그 배열 (tag array), 기본 `{}` | `{"iga","tensile","2024"}` |
| `agents` | text[] | no | 사용 가능 에이전트 타입 (agent_type) | `{"iga-analyst"}` |
| `schema_version` | str(10) | no | 기본 `"1.0"` | `1.0` |
| `content` | jsonb | no | data_type 별 페이로드 dict | `{"sections":[...]}` |
| `content_hash` | str(64) | yes | SHA-256 of content | `"a1b2..."` |
| `source_file` | text | yes | 원본 파일명 (source filename) | `"report.docx"` |
| `has_attachments` | bool | no | 기본 false | `true` |
| `attachment_count` | int | no | 기본 0 | `3` |
| `author` | str(100) | no | 기본 "" | `"홍길동"` |
| `department` | str(100) | no | 기본 "" | `"HE/CAE"` |
| `project` | str(100) | yes | 프로젝트명 | `"EV-2026"` |
| `version` | str(20) | no | 기본 `"1.0"` | `"1.2"` |
| `classification` | str(20) | no | enum (§12) | `"internal"` |
| `status` | str(20) | no | enum (§12) | `"approved"` |
| `domain` | str(100) | yes | 도메인 자유 문자열 | `"thermal"` |
| `subject_keywords` | text[] | no | 주제 키워드 배열 | `{"battery","cell"}` |
| `source_system` | str(50) | yes | 출처 시스템 | `"PLM"` |
| `language` | str(10) | no | 기본 `"ko"`. enum: ko/en/mixed/ja/zh | `"ko"` |
| `parent_record_id` | str(80) FK | yes | self-FK to records.id | `"DOC-HE-CAE-2026-000001"` |
| `derivation` | str(20) | no | enum (§12), 기본 `"original"` | `"translated"` |
| `capabilities` | text[] | no | 자동 산출 라벨 배열 (§12) | `{"sections","tables"}` |
| `quality_score` | smallint | yes | 0..100 | `85` |
| `valid_from` | date | yes | 유효 시작일 | `2026-01-01` |
| `valid_until` | date | yes | 유효 종료일 | `2026-12-31` |
| `agent_hints` | text | yes | 에이전트용 자유 힌트 텍스트 | `"figure 3 has key plot"` |
| `related_record_ids` | text[] | no | 수동 큐레이션 관련 ID 배열 | `{"DOC-HE-CAE-2025-000123"}` |
| `query_examples` | text[] | no | 자연어 쿼리 예시 | `{"IGA 결과 보여줘"}` |
| `access_pattern` | str(20) | no | enum (§12), 기본 `"occasional"` | `"frequent"` |
| `deleted_at` | timestamptz | yes | soft-delete 시간 (NULL = 활성) | `null` |
| `read_count` | int | no | 조회수 (read count), 기본 0 | `42` |
| `last_accessed_at` | timestamptz | yes | 마지막 조회 시간 | `2026-05-01T...` |
| `created_at` | timestamptz | no | 생성 시간 | `2026-01-15T...` |
| `updated_at` | timestamptz | no | 갱신 시간 (auto onupdate) | `2026-05-09T...` |

Unique constraint: `(data_type, team, group, year, seq)`.

### 3.2 Table `record_sections` — 섹션 (section, RAG chunk)

| 필드 | 타입 | NULL | 의미 | 예시 |
|---|---|---|---|---|
| `id` | bigserial PK | no | 내부 PK | `1234` |
| `record_id` | str(80) FK | no | parent record id | `DOC-HE-CAE-2026-000001` |
| `section_id` | str(20) | no | 섹션 식별자 | `"S001"` |
| `level` | smallint | no | 헤딩 레벨 (1=H1) | `2` |
| `title` | text | no | 섹션 제목 | `"3.1 시험 절차"` |
| `content_text` | text | no | 본문 평문 (text body) | `"시편을 ..."` |
| `figure_refs` | text[] | no | 참조한 figure id | `{"F001"}` |
| `table_refs` | text[] | no | 참조한 table id | `{"T002"}` |
| `embedding` | vector(384) | yes | pgvector 임베딩 | `[0.12,...]` |
| `embedded_at` | timestamptz | yes | 임베딩 시각 | `2026-04-01T...` |
| `embedding_model` | str(100) | yes | 사용 모델명 | `"all-MiniLM-L6-v2"` |

Unique: `(record_id, section_id)`.

### 3.3 Table `record_attachments` — 첨부 (attachment)

| 필드 | 타입 | NULL | 의미 | 예시 |
|---|---|---|---|---|
| `id` | str(80) PK | no | `{record_id}-A{nnn}` | `DOC-HE-CAE-2026-000001-A001` |
| `record_id` | str(80) FK | no | parent | `DOC-HE-CAE-2026-000001` |
| `number` | int | no | 1부터 시작 | `1` |
| `kind` | str(20) | no | enum (§12), 9 종 | `"figure"` |
| `caption` | text | no | 캡션 필수, placeholder `"(캡션 누락 — 검수 필요)"` | `"Stress curve"` |
| `file_name` | text | yes | 원본 파일명 | `"fig3.png"` |
| `file_path` | text | yes | attachments_dir 기준 상대 경로 | `"DOC-HE-CAE-2026-000001/A001.png"` |
| `mime_type` | str(100) | yes | MIME | `"image/png"` |
| `size_bytes` | bigint | yes | 바이트 크기 | `204800` |
| `hash_sha256` | str(64) | yes | 파일 해시 | `"abcd..."` |
| `section_ref` | str(20) | yes | 참조 섹션 id | `"S003"` |
| `extra` | jsonb | no | 자유 메타 dict, 기본 `{}` | `{"page":7}` |
| `created_at` | timestamptz | no | 생성 시각 | `2026-01-15T...` |

Binary 위치: `/attachments/{record_id}/A{nnn}.{ext}` (static mount). Figure binaries 별도 마운트: `/figures/{...}`.

### 3.4 Table `agents` — 에이전트 메타

| 필드 | 타입 | NULL | 의미 |
|---|---|---|---|
| `agent_type` | str(50) PK | no | 식별자, 예 `"iga-analyst"` |
| `name` | text | no | 표시 이름 |
| `description` | text | no | 설명, 기본 "" |
| `common_tags` | text[] | no | 자주 쓰는 태그 |
| `data_types` | text[] | no | 다루는 data_type 배열 |
| `created_at` | timestamptz | no | 생성 시각 |

### 3.5 Table `agent_records` — N:M

`(agent_type, record_id)` PK + `priority smallint default 1` (1..5 권장).

### 3.6 Table `audit_log`

`id bigserial PK` / `record_id str(80)` / `actor str(100)` / `action str(50)` (enum: INSERT/UPDATE/DELETE/RESTORE/VIEW) / `field_changes jsonb {field:[old,new]}` / `request_id str(64)` / `created_at timestamptz`.

### 3.7 Table `api_keys`

`id int PK` / `key_hash str(64)` / `name str(100)` / `agent_scopes text[]` / `department str(100)?` / `created_at` / `expires_at?` / `revoked bool` / `last_used_at?`.

---

## 4. ID format (ID 형식)

### 4.1 Pattern (정식)

```
{DATA_TYPE}-{TEAM}-{GROUP}-{YEAR}-{SEQ}
```

| 토큰 (token) | 규칙 (rule) | 예 |
|---|---|---|
| `DATA_TYPE` | enum: DOC/DATA/SIM/CAD/LOG/FORM/OTHER | `DOC` |
| `TEAM` | 2~4 uppercase ASCII | `HE` |
| `GROUP` | 2~5 uppercase ASCII | `CAE` |
| `YEAR` | 4 digits, 2020..2099 | `2026` |
| `SEQ` | 6 digits zero-pad, 000001..999999 | `000001` |

Example: `DOC-HE-CAE-2026-000001`

### 4.2 Legacy pattern (data_type 누락 — 호환)

```
{TEAM}-{GROUP}-{YEAR}-{SEQ}
```

Parsed with default `data_type="DOC"`. `normalize_id()` adds prefix.

### 4.3 Attachment ID

```
{record_id}-A{number:03d}
예: DOC-HE-CAE-2026-000001-A001
```

(레거시: `-F{nnn}` 도 허용)

### 4.4 Known TEAM / GROUP seed (`api/seed/teams.py`)

| TEAM | GROUPS |
|---|---|
| HE | CAE, Test, Design |
| EV | BMS, Battery, Motor |
| PT | Material, Process |
| DA | AI, Data |
| MX | MFG, QA |
| VD | DEV, PLM |

---

## 5. Endpoint catalog (엔드포인트 카탈로그)

| 메서드 | 경로 | 용도 | 주요 파라미터 | 응답 키 (top-level) |
|---|---|---|---|---|
| GET | `/` | 서비스 식별 | — | `service, version, status` |
| GET | `/health` | minimal liveness | — | `status` |
| GET | `/api/system/health` | health + meta | — | `status, version, auth_required, build` |
| GET | `/api/discover` | 전체 카탈로그 | `?no_cache` | `version, total_records, by_data_type, by_division, by_classification, agents, data_types_explained, starting_points, schema_url, hints_url, llm_doc_url, ask_url, generated_at` |
| GET | `/api/schema` | JSON Schema (draft-2020-12) | — | `$schema, properties, oneOf` |
| GET | `/api/hints` | 자연어 힌트 | `?context=` | `context, available_contexts, hints` |
| GET | `/api/docs/llm.txt` | 통합 마크다운 | — | text/plain |
| POST | `/api/ask` | 자연어 → 결과 | body `{query, limit}` | `interpreted_query, results, total_matched, follow_up_queries` |
| GET | `/api/records` | 목록 + 필터 | `data_type, team, group, year, agent[], tag[], q, include_deleted, limit, offset` | `items, total, limit, offset` |
| GET | `/api/records/{id}` | 단건 조회 | `?include_deleted` | full RecordOut |
| POST | `/api/records` | 생성 (직접 INSERT) | body `RecordIn` | RecordOut (201) |
| PATCH | `/api/records/{id}` | 부분 수정 | body `{summary?, tags?, agents?, project?, version?}` | RecordOut |
| DELETE | `/api/records/{id}` | soft delete (`?hard=true`로 hard) | `?hard` | 204 |
| POST | `/api/records/{id}/restore` | soft delete 복원 | — | RecordOut |
| GET | `/api/records/{id}/lineage` | 조상/자손 | — | `record_id, self, ancestors[], descendants[], ancestor_count, descendant_count` |
| GET | `/api/records/{id}/diff?from=` | 두 레코드 diff | `?from=` | `from, to, meta_changes, section_changes[], block_changes` |
| GET | `/api/records/{id}/attachments` | 첨부 목록 | `?kind` | array of AttachmentOut |
| GET | `/api/records/{id}/attachments/{att_id}` | 첨부 단건 | — | AttachmentOut |
| GET | `/api/attachments` | 전역 첨부 검색 | `?kind, ?record_id, limit, offset` | array of AttachmentOut |
| GET | `/api/data` | agent-scoped 검색 | `agent (req), query?, data_types[]?, limit` | `agent, query, results[], total_matched` |
| GET | `/api/search?mode=tag` | 태그 AND 검색 | `tags[] (req), limit, offset` | `mode, tags, items, total, limit, offset` |
| GET | `/api/search?mode=fts` | 전문 검색 | `q (req), limit, offset` | `mode, q, items, total, limit, offset` |
| GET | `/api/search?mode=semantic` | pgvector cosine | `q (req), limit` | `mode, q, items, total, limit, offset` |
| GET | `/api/agents` | 에이전트 목록 | — | array of AgentOut |
| GET | `/api/agents/{type}` | 에이전트 단건 | — | AgentOut |
| GET | `/api/agents/{type}/records` | 해당 에이전트 record 목록 | — | array of RecordOut |
| POST | `/api/agents` | 에이전트 생성 | body `AgentIn` | AgentOut (201) |
| PATCH | `/api/agents/{type}` | 에이전트 수정 | body `AgentPatch` | AgentOut |
| DELETE | `/api/agents/{type}` | 에이전트 삭제 | — | 204 |
| GET | `/api/analytics/distribution` | 분포 통계 | — | counts dict |
| GET | `/api/analytics/common-tags` | 에이전트별 상위 태그 | `agent (req), limit` | array |
| GET | `/api/analytics/cross-agent` | 에이전트 간 공유 record | `agents[] (req)` | dict |
| GET | `/api/analytics/timeline` | 월별 카운트 | `year (req)` | dict |
| GET | `/api/analytics/usage` | 상위 read_count | `limit` | `items, total, limit` |
| GET | `/api/meta/options` | UI 옵션 (셀렉트박스) | — | `version, teams, groups, agents, classifications, statuses, derivations, languages, data_types, supported_extensions, max_upload_mb, allow_custom` |
| GET | `/api/taxonomy/tags` | 태그 + 빈도 + data_type 분포 | `q?, min_count?, limit?` | `total, items[{tag,count,data_types,agents}]` |
| GET | `/api/taxonomy/tags/resolve` | 비공식 → 정식 태그 매핑 (synonym) | `q (req), limit?` | `query, normalized, candidates[{tag,score,method,count}]` |
| GET | `/api/taxonomy/data-types` | data_type 분포 + 추천 사용 패턴 | — | `items[{data_type,count,description,subtypes,schema_url}]` |
| GET | `/api/taxonomy/domains` | domain 필드 분포 | — | `items[{domain,count}]` |
| GET | `/api/taxonomy/agents` | agent 카탈로그 + record 수 + 주요 태그 | — | `items[{agent_type,record_count,common_tags,data_types}]` |
| GET | `/api/taxonomy/classification` | classification enum + 의미 + 분포 | — | `field, items[{value,description,count}]` |
| GET | `/api/taxonomy/status` | status enum + 의미 + 분포 | — | `field, items[{value,description,count}]` |
| GET | `/api/taxonomy/access-pattern` | access_pattern enum + 의미 + 분포 | — | `field, items[{value,description,count}]` |
| POST | `/api/convert/` | 파일 → JSON (no DB) | multipart `file, team, group, year, seq, tags, agents, classification, domain` | converter dict |
| POST | `/api/convert/ingest` | 파일 → JSON → DB | 위 + `status, language, subject_keywords, derivation, quality_score, valid_from, valid_until, title_override, summary_override, agent_hints, related_record_ids, query_examples, access_pattern, persist_attachments` | `record_id, status, sections_written, assigned_seq, attachments_persisted, record` |
| POST | `/api/jobs/embed` | 임베딩 backfill | body `{record_id?, record_ids?[]}` | job dict (202) |
| GET | `/api/jobs/{job_id}` | 잡 상태 | — | job dict |
| GET | `/api/jobs` | 잡 목록 | `kind?, limit?` | `jobs` |
| POST | `/api/auth/keys` | 키 발급 (bootstrap) | body `ApiKeyIn` | ApiKeyCreated incl. `key` (201) |
| GET | `/api/auth/keys` | 키 목록 (bootstrap) | — | array of ApiKeyOut |
| POST | `/api/auth/keys/verify` | 키 검증 (any key) | — | `ok, key_name, agent_scopes` |
| DELETE | `/api/auth/keys/{key_id}` | 키 폐기 (bootstrap) | — | 204 |
| GET | `/metrics` | Prometheus text | — | text/plain (only if `ENABLE_METRICS=true`) |

Static mounts:
- `/figures/{record_id}/F{nnn}.{ext}` — figure binary
- `/attachments/{record_id}/A{nnn}.{ext}` — attachment binary (when configured)

---

## 6. Search workflow (검색 워크플로우 — 가장 중요)

### 6.1 결정 트리 (자연어 질의 → 검색 모드)

```
사용자 질의 (user query)
        │
        ▼
1. POST /api/ask                  ← LLM 키 OR 키워드 폴백 (항상 동작)
        │  결과 부족?
        ▼
2. GET /api/search?mode=semantic  ← pgvector cosine (의미 유사)
        │  결과 부족 / pgvector 없음?
        ▼
3. GET /api/search?mode=fts       ← ILIKE %q% (title + summary + section)
        │  여전히 부족?
        ▼
4. GET /api/search?mode=tag       ← AND 태그 필터 (정확 매칭)
        │
        ▼
5. GET /api/records?q=...         ← 단순 ILIKE (fallback, low recall)
```

### 6.2 모드 비교 (mode comparison)

| 모드 (mode) | 강점 (pros) | 약점 (cons) | 언제 (when) |
|---|---|---|---|
| `ask` | 자연어 해석 + follow-up | LLM 키 필요 (없으면 키워드만) | 사용자가 평문 질문 |
| `semantic` | 동의어·재구성 강함 | pgvector + embedding 필요 | 질의어가 본문 단어와 다를 때 |
| `fts` | 빠르고 정확 매칭 | 동의어 약함 | 정확한 단어 검색 |
| `tag` | 카테고리 정확 | 태그 사전이 필요 | 사용자가 태그를 알 때 |
| `keyword (records?q=)` | 가장 단순 | recall 낮음 | 마지막 폴백 |

### 6.3 응답 모양 (response shape)

```json
{"mode":"fts","q":"IGA","items":[{"record_id":"DOC-HE-CAE-2026-000001","title":"...","data_type":"DOC","section_id":"S003","section_title":"...","snippet":"...","tags":[]}],"total":7,"limit":20,"offset":0}
```

`mode=semantic` 의 `items[].score` 는 0..1 (1 = 동일).

### 6.4 의미 그룹 패턴 (semantic groups — NEW)

같은 의미의 record 군을 한 번에 묶어 받고 싶을 때 사용. 작은 AI 의 컨텍스트
한도 안에서 여러 record 를 한 그룹으로 모아 추론하는 데 최적.

| 엔드포인트 (endpoint) | 입력 (input) | 출력 (output) |
|---|---|---|
| `POST /api/groups/auto` | `{q, n_groups, limit_per_group, min_score}` | `{groups:[{label, common_tags, size, representative_record, records:[...]}]}` |
| `GET /api/records/{id}/cluster?mode=semantic\|tag\|hybrid` | path id + mode | `{anchor_record, items:[{id,score,shared_tags,...}]}` |
| `POST /api/records/bulk` | `{ids:[...], include_sections}` | `{items:[{...record}], missing:[...]}` |

워크플로우 (3 step):

```
1. POST /api/groups/auto body={"q":"AI 도입 현황","n_groups":3}
   → groups[i].records[].id 추출
2. POST /api/records/bulk body={"ids":[id1,id2,...],"include_sections":true}
   → 한 번에 모든 record 와 섹션 가져옴 (N+1 회피)
3. groups[i].label / common_tags 로 그룹 의도 표시
```

mode 비교 — `/api/records/{id}/cluster`:

| mode | 신호 | 언제 |
|---|---|---|
| `semantic` | embedding cosine ≥ `sim_threshold` (기본 0.85) | 의미가 비슷한 record |
| `tag` | tag jaccard ≥ `tag_threshold` (기본 0.6) | 큐레이션된 태그 활용 |
| `hybrid` | semantic 0.6 + tag 0.4 가중 | 둘 다 활용 (기본 권장) |

---

## 7. MCP tool catalog (MCP 도구)

`api_server/src/mcp_server/server.py` 에서 `@mcp.tool()` 로 등록.

| 도구명 (tool) | 입력 (input) | 출력 (output) | 용도 (use) |
|---|---|---|---|
| `discover_schema` | (none) | `{discover, schema}` | **항상 먼저** — 전체 카탈로그 + JSON Schema |
| `discover_capabilities` | `agent_type: str` | `{agent, record_count, sample_records, follow_up}` | 한 에이전트의 데이터 범위 |
| `ask` | `query: str, limit: int=5` | `{interpreted_query, results, total_matched, follow_up_queries}` | 자연어 검색 |
| `find_related` | `record_id: str, mode: str="auto"` | `{related[], by_mode:{tags,graph,semantic}}` | 관련 record (mode: tags/graph/semantic/auto) |
| `explain_field` | `field_name: str` | `{field_name, spec, is_enum, allowed_values, type, description}` | 필드 메타 |
| `explain_schema` | `field_name: str` | 동일 | `explain_field` alias |
| `query_data` | `agent: str, query: str="", limit: int=5` | `{results}` | agent-scoped 검색 = `GET /api/data` |
| `list_agents` | (none) | `{agents}` | 에이전트 목록 |
| `get_record` | `record_id: str` | full record dict | record 단건 |
| `search` | `mode: str, query: str="", tags: list?` | `{results}` 또는 mode별 응답 | tag/fts/semantic |

도구 호출 환경변수: `API_URL` (기본 `http://localhost:8000`), `API_TIMEOUT` (기본 30초). `MAX_LIMIT=20`.

---

## 8. Common workflows (워크플로우 step-by-step)

### 8.1 자연어 질의 → 답변 (NL query → answer)

```
1. POST /api/ask                       body: {"query":"<user_text>","limit":5}
2. 응답.results[].record_id 수집
3. 각 id 마다 GET /api/records/{id}    상세 record
4. 응답.follow_up_queries 표시         다음 단계 가이드
5. (선택) 응답.results 가 비어있으면   GET /api/search?mode=semantic&q=<text>
```

### 8.2 태그로 필터링한 record 목록 (filter by tag)

```
1. GET /api/discover                   현재 보유 태그 분포 확인
2. GET /api/analytics/common-tags?agent=<x>&limit=20
3. GET /api/search?mode=tag&tags=<t1>&tags=<t2>&limit=20
4. items[].record_id → GET /api/records/{id}
```

### 8.3 한 record 의 첨부 그림 다운로드 (download figure)

```
1. GET /api/records/{id}/attachments?kind=figure
2. 응답[i].file_path 또는 id "{record_id}-A001"
3. HTTP GET /attachments/{record_id}/A001.png   (정적 마운트)
4. MIME = 응답[i].mime_type
```

### 8.4 비슷한 record 찾기 (related records)

```
1. GET /api/records/{id}                            base.related_record_ids, base.tags
2. GET /api/search?mode=semantic&q=<base.title>     의미 유사
3. GET /api/search?mode=tag&tags=<base.tags[0]>     태그 공유
4. GET /api/records/{id}/lineage                    조상/자손
5. dedup → 결과 합치기                              MCP find_related 가 동일 동작
```

### 8.5 분야별 통계 (distribution analytics)

```
1. GET /api/analytics/distribution                  by_data_type / by_division / by_team / by_year
2. GET /api/analytics/timeline?year=2026            월별 카운트
3. GET /api/analytics/cross-agent?agents=A&agents=B 교집합
4. GET /api/analytics/usage?limit=20                인기 record (read_count 정렬)
```

### 8.6 새 record 적재 (ingest new file)

```
1. multipart POST /api/convert/ingest               file=<doc.docx>, team=HE, group=CAE, year=2026, seq=0
                                                    seq=0 면 backend 가 MAX(seq)+1 자동 할당
2. 응답.record_id 확보
3. (선택) POST /api/jobs/embed body {"record_id":"<id>"}   임베딩 backfill
4. GET /api/records/{id}                            적재 확인
```

또는 직접 INSERT:
```
1. POST /api/records  body=RecordIn  (id는 §4 형식)
2. 409 면 이미 존재 — PATCH /api/records/{id} 로 갱신
```

### 8.7 record 변경 이력 추적 (lineage + diff)

```
1. GET /api/records/{id}/lineage                    ancestors, descendants 트리
2. lineage.ancestors[0].id 를 from 으로:
   GET /api/records/{id}/diff?from=<ancestor_id>    meta_changes + section_changes[]
3. section_changes[i].content_diff 는 unified diff 텍스트
4. (감사) audit_log 는 직접 노출되지 않음 — DB 만 기록
```

### 8.8 DATA 타입 워크플로우 (시험·시뮬 결과 분석)

```
1. 카탈로그 — 어떤 DATA 가 있나 한눈에:
   GET /api/data?domain=material-test&min_rows=10
   → items[] = [{id, title, columns, rows, units, context}, ...]

2. 컬럼 정의 확인 (이 데이터가 뭔지):
   GET /api/data/{id}/columns
   → items[] = [{column, description, unit, dtype}, ...]

3. 행 페이징 또는 컬럼=값 필터:
   GET /api/data/{id}/rows?limit=100&where=Region:Yield
   → {headers, units, total_rows, rows: [[...]]}

4. 통계 집계 (직접 평균/최대/최소 계산 X):
   GET /api/data/{id}/aggregate?op=max&column=Stress
   → {result: 450.0, unit: "MPa"}

   group_by 도 됨:
   GET /api/data/{id}/aggregate?op=max&column=Stress&group_by=Region
   → result: [{Region:"UTS", max_Stress:450}, ...]

규칙:
- op ∈ {avg, max, min, sum, count}. count 는 column 생략 가능.
- group_by 컬럼은 enum/문자열 권장. dtype=enum 인 컬럼이 좋다.
- 잘못된 op 또는 unknown column → 422.
```

### 8.9 다층 필터링 (faceted search) — 다음 query 좁힘

```
1. 키워드 + 다축 필터 (AND 조합):
   GET /api/search/faceted?q=stress&data_type=DATA&tags=Tensile&min_quality=80

2. 응답:
   {
     total: 7,
     items: [...],
     facets: {
       data_type: {DOC: 5, DATA: 2},
       tags: {stress: 7, IGA: 3, ...},
       domain: {cae: 5, material-test: 2},
       agent: {iga-analyst: 3, cae-reporter: 4},
       status: {approved: 6, review: 1}
     }
   }

3. facets 가 다음 좁힘 후보를 안내한다:
   - "DOC가 5개로 많다 → data_type=DOC 추가하면 줄어든다"
   - "approved status 가 대부분 → status=approved 추가해도 손실 적다"

4. 태그 매칭 (any/all 모드):
   GET /api/search/by-tags?tags=IGA,NURBS&match=all   (AND, default)
   GET /api/search/by-tags?tags=IGA,NURBS&match=any   (OR)
```

---

## 9. Response schemas — 핵심 키 (common keys)

### 9.1 RecordOut (most endpoints returning a record)

| 키 | 타입 | 의미 | 예시 |
|---|---|---|---|
| `id` | str | 레코드 ID | `"DOC-HE-CAE-2026-000001"` |
| `data_type` | str | 콘텐츠 타입 | `"DOC"` |
| `team`, `group` | str | 분류 키 | `"HE"`, `"CAE"` |
| `year`, `seq` | int | 분류 키 | `2026`, `1` |
| `title`, `summary` | str | 텍스트 메타 | — |
| `tags`, `agents` | str[] | 배열 메타 | `["iga"]` |
| `content` | obj | data_type 별 페이로드 | `{"sections":[...]}` |
| `content_hash` | str/null | SHA-256 | — |
| `classification`, `status`, `derivation`, `access_pattern`, `language` | str | enum (§12) | — |
| `domain`, `source_system`, `parent_record_id`, `agent_hints` | str/null | optional | — |
| `subject_keywords`, `capabilities`, `related_record_ids`, `query_examples` | str[] | array | — |
| `quality_score` | int/null | 0..100 | — |
| `valid_from`, `valid_until` | date/null | ISO date | — |
| `has_attachments`, `attachment_count` | bool, int | 첨부 요약 | — |
| `created_at`, `updated_at` | datetime | ISO-8601 | — |

### 9.2 List response (`GET /api/records`)

```json
{"items":[<RecordOut>,...],"total":42,"limit":20,"offset":0}
```

### 9.3 `POST /api/ask` response

```json
{"interpreted_query":{"source":"llm|keyword","filters":{"data_type":"DOC","year":2026}},"results":[<record>...],"total_matched":7,"follow_up_queries":["..."]}
```

### 9.4 Section diff (`/api/records/{id}/diff`)

```json
{"from":"<id_a>","to":"<id_b>","meta_changes":{"title":["old","new"]},"section_changes":[{"section_id":"S001","kind":"modified|added|removed","title_changes":["old","new"]|null,"content_diff":"<unified_diff_text>"}],"block_changes":"identical|summary"}
```

### 9.5 AttachmentOut

```json
{"id":"DOC-HE-CAE-2026-000001-A001","record_id":"...","number":1,"kind":"figure","caption":"...","file_name":"fig.png","file_path":"DOC-HE-CAE-2026-000001/A001.png","mime_type":"image/png","size_bytes":204800,"hash_sha256":"...","section_ref":"S003","extra":{},"created_at":"..."}
```

### 9.6 Discover payload (`GET /api/discover`)

```json
{"version":"1.0","title":"AI Data Hub","total_records":N,"by_data_type":{"DOC":x,"DATA":y},"by_division":{"HE":n},"by_classification":{"internal":n},"agents":[{"agent_type":"iga-analyst","name":"...","record_count":k,"common_tags":[],"data_types":[],"sample_query":"/api/data?agent=iga-analyst"}],"data_types_explained":{"DOC":"...","DATA":"..."},"starting_points":["GET /api/agents","..."],"schema_url":"/api/schema","hints_url":"/api/hints","llm_doc_url":"/api/docs/llm.txt","ask_url":"/api/ask","generated_at":"..."}
```

---

## 10. Error handling (에러 처리)

표준 에러 응답:

```json
{"error":{"code":"<CODE>","message":"<msg>","details":{...},"request_id":"<uuid>"}}
```

| HTTP | code | 의미 (meaning) | 대응 (action) |
|---|---|---|---|
| 400 | `BAD_REQUEST` | 잘못된 요청 (mode=tag without tags 등) | `details.detail` 확인 |
| 401 | `AUTHENTICATION_ERROR` | 인증 실패 | `X-API-Key` 확인 / `BOOTSTRAP_API_KEY` 확인 |
| 403 | `AUTHORIZATION_ERROR` | 권한 부족 (예: hard delete) | bootstrap 키 사용 |
| 404 | `NOT_FOUND` | record/agent/job/key 없음 | id 재확인 — `GET /api/records?...` 로 검색 |
| 405 | `METHOD_NOT_ALLOWED` | 잘못된 HTTP 메서드 | 표 §5 참조 |
| 409 | `CONFLICT` | 중복 (record id 중복 등) | PATCH 로 변경 또는 다른 seq |
| 413 | `PAYLOAD_TOO_LARGE` | 업로드 크기 초과 | `details.max_bytes` 미만으로 |
| 422 | `VALIDATION_ERROR` | 검증 실패 (Pydantic) | `details.errors[]` 의 `loc` + `msg` 참조 |
| 429 | `RATE_LIMIT` | 요청 과다 | 백오프 후 재시도 |
| 503 | `(http_error)` | semantic search embedder 미준비 | `mode=fts` 로 폴백 |
| 500 | `INTERNAL_ERROR` | 서버 오류 | `details.type` 보고 |

응답 헤더: 모든 응답에 `X-Request-ID` 포함 (있을 때).

---

## 11. Pagination (페이지네이션)

| 엔드포인트 | limit 기본 | limit 최대 | offset |
|---|---|---|---|
| `GET /api/records` | 20 | 100 | 0+ |
| `GET /api/search` (tag/fts) | 20 | 100 | 0+ |
| `GET /api/search?mode=semantic` | 20 (= top_k) | 100 | n/a |
| `GET /api/data` | 5 | 20 | n/a |
| `POST /api/ask` (limit) | 5 | 50 | n/a |
| `GET /api/attachments` | 50 | 500 | 0+ |
| `GET /api/analytics/common-tags`, `usage` | 20 | 100 | n/a |

응답 모양:

```json
{"items":[...],"total":N,"limit":L,"offset":O}
```

순서: `updated_at DESC, id DESC` (records). 마지막 페이지 판단: `offset + len(items) >= total`.

---

## 12. Enums — 분명한 값 목록 (closed enums)

### 12.1 `data_type` (7)

| 값 | 의미 |
|---|---|
| `DOC` | 문서 (document) — sections + blocks 트리 |
| `DATA` | 표 데이터 (tabular) — headers + rows |
| `SIM` | 시뮬레이션 (simulation) — solver + inputs/outputs |
| `CAD` | 3D CAD 모델 메타 |
| `LOG` | 로그·시계열 (free-form) |
| `FORM` | 양식·체크리스트 (free-form) |
| `OTHER` | 기타 |

### 12.2 `classification` (4)

`public` < `internal` (default) < `confidential` < `restricted`

### 12.3 `status` (4)

`draft` (default) → `review` → `approved` → `deprecated`

### 12.4 `derivation` (4)

`original` (default), `extracted`, `aggregated`, `translated`

### 12.5 `access_pattern` (3)

`frequent`, `occasional` (default), `rare`

### 12.6 `language` (5+, open)

`ko` (default), `en`, `ja`, `zh`, `mixed`. (meta options 는 ko/en/ja/zh; schema 는 ko/en/mixed.)

### 12.7 `attachment.kind` (9)

| 값 | 포함 확장자 (subset) |
|---|---|
| `figure` | png, jpg, jpeg, gif, bmp, wmf, emf, svg, tif, tiff, webp |
| `document` | pdf, doc, docx, hwp, hwpx, txt, rtf, odt |
| `spreadsheet` | xlsx, xls, xlsm, csv, tsv, ods |
| `media` | mp3, wav, ogg, flac, mp4, avi, mov, mkv, webm, m4a |
| `archive` | zip, tar, gz, 7z, rar, bz2, xz |
| `cad` | step, stp, iges, igs, catpart, sldprt, prt, x_t, stl |
| `drawing` | dwg, dxf |
| `data` | json, xml, yaml, yml, toml |
| `other` | (위 외 모든 것 — fallback) |

### 12.8 `capabilities[]` (자동 산출, 13)

`sections`, `blocks`, `tables`, `figures`, `attachments`, `embeddings`, `rows`, `headers`, `samples`, `files`, `components`, `inputs`, `outputs`

INSERT 시 backend 가 `content` 모양 분석으로 채움 — 클라이언트가 직접 보낼 필요 없음.

### 12.9 `audit_log.action` (6)

`INSERT`, `UPDATE`, `DELETE`, `RESTORE`, `VIEW`, `ACCESS`

### 12.10 `search.mode` (3)

`tag`, `fts`, `semantic`

### 12.11 `find_related.mode` (4)

`tags`, `graph`, `semantic`, `auto`

### 12.12 Job kinds (1+)

`embed` (현재). 응답 `{job_id, kind, status: pending|running|done|failed, progress, result, error}`.

### 12.13 DOC content shape (`content` JSON)

```
{ meta:{}, toc:[{id,level,title}], sections:[{id,level,title,blocks:[{type,text}],children:[...]}], figures:[], tables:[], sources:[], attachments:[] }
```

DATA: `{ caption, headers:[str], rows:[[any]], units:{col:unit}, notes }`
SIM: `{ solver, solver_version, inputs:{}, outputs:{}, runtime:{} }`
CAD: `{ cad_type, file_format, file_metadata:{}, components:[] }`
LOG / FORM / OTHER: free-form.

---

## 13. Quick reference card (한 화면 요약)

```
LOGIN     →  X-API-Key: <plaintext>
DISCOVER  →  GET  /api/discover                   [start here]
ASK       →  POST /api/ask {"query":"...","limit":5}
LIST      →  GET  /api/records?data_type=DOC&year=2026&limit=20
GET       →  GET  /api/records/{DOC-HE-CAE-2026-000001}
SEARCH    →  GET  /api/search?mode=fts&q=IGA      (or mode=tag&tags=...&tags=...)
SEMANTIC  →  GET  /api/search?mode=semantic&q=...
AGENT     →  GET  /api/data?agent=iga-analyst&query=...&limit=5
FILES     →  GET  /api/records/{id}/attachments
RELATED   →  GET  /api/records/{id}/lineage  +  /api/search?mode=tag&tags=<one>
INGEST    →  multipart POST /api/convert/ingest  (file + team + group + year + seq=0)
DIFF      →  GET  /api/records/{id}/diff?from=<other_id>
META      →  GET  /api/meta/options              (UI dropdown values)
SCHEMA    →  GET  /api/schema                    (machine-readable types)
HINTS     →  GET  /api/hints?context=getting_started
LLM DOC   →  GET  /api/docs/llm.txt              (5-10KB digest)
HEALTH    →  GET  /api/system/health
ERROR     →  body.error.code  +  body.error.details  +  X-Request-ID
```

End of reference. For step-by-step intro see `AGENT_ONBOARDING.md`. For internal architecture see `data_model.md`, `governance.md`, `mcp_integration_guide.md`.

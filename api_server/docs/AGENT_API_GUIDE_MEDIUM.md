# AGENT_API_GUIDE — Mobile eXperience AI Data Hub REST + MCP API (MEDIUM)

> 13B–70B 모델용 균형 가이드 (예: llama-3-70B, qwen-2.5-32B/72B, mixtral-8x22B).
> SMALL 가이드보다 더 깊은 설명 + 결정/폴백 흐름 + MCP 사용 패턴 포함.
> 보조 문서: `AGENT_ONBOARDING.md`, `AGENT_API_GUIDE_SMALL.md`, `data_model.md`.

---

## 1. 도메인 개요 (Domain context)

이 API 가 다루는 데이터의 도메인은 **팀(team)·그룹(group) 단위로 누적되는
산업 엔지니어링 산출물**이다. HE/EV/PT/DA/MX/VD 6 개 팀의 CAE 해석 보고서,
LS-DYNA / Abaqus 등 상용 솔버의 시뮬레이션 입력·출력, 인장·압축·소성 등 시험
측정 표 데이터, 3D CAD 모델 메타, 운영 로그·시계열, 공정 체크리스트, 강의자료
및 사내 코드(KooRemapper 같은 IGA-NURBS 도구) 문서가 모두 단일 `records`
테이블에 들어 있다. data_type 7 종(DOC/DATA/SIM/CAD/LOG/FORM/OTHER) 으로
1차 분류되고, 각 레코드는 사람이 읽을 수 있는 ID(`DOC-HE-CAE-2026-0000000001`)
와 자유 태그·에이전트 스코프·분류(approved/draft 등) 메타로 다시 슬라이스된다.

레코드는 단순 메타-목록이 아니다. DOC 변종은 섹션·블록 트리 + figure·table
첨부 + RAG 용 임베딩(`record_sections.embedding vector(384)`) 까지 포함한다.
DATA 변종은 headers + rows + 단위(unit) 까지, SIM 은 solver 입력·출력 dict 를
보존한다. 즉 허브는 “문서 검색 시스템 + 표 데이터 마트 + 시뮬레이션 카탈로그
+ 첨부 바이너리 저장소” 가 한 API 뒤에 합쳐진 형태다. 에이전트(iga-analyst /
cae-reporter / material-reviewer / process-checker / code-assistant)
다섯 종이 사전 정의돼 있고, 각 record 의 `agents[]` 배열이 어느 에이전트
시점에서 노출되는지를 결정한다.

---

## 2. 인증 + 환경 (Authentication & Environment)

| 항목 | 값 |
|---|---|
| Base URL (default) | `http://localhost:8000` |
| Auth header | `X-API-Key: <plaintext>` |
| Auth dep | `require_api_key` (FastAPI Depends) |
| Default mode | `AUTH_REQUIRED=false` — 헤더 없어도 통과 (anonymous principal) |
| Strict mode | `AUTH_REQUIRED=true` — 헤더 없거나 잘못되면 401 |
| Bootstrap | `BOOTSTRAP_API_KEY` env — 초기 키 발급 / hard delete 에 필요 |
| Hash storage | SHA-256 (plaintext 는 발급 시 1 회만 노출) |
| Verify | `POST /api/auth/keys/verify` → `{ok, key_name, agent_scopes}` |
| Service title | `사업부 문서 AI 데이터 API` (`/` root 에서는 `service: ai-data-api`) |

키 발급은 부트스트랩 키 보유자만 가능하다 — `POST /api/auth/keys` 에
`X-API-Key: <bootstrap>` 헤더를 붙이고 body 에 `{name, agent_scopes,
department?, expires_at?}` 를 보낸다. 응답의 `key` 필드(plaintext) 는 그
순간만 반환되므로 바로 보관해야 한다.

운영에 영향을 주는 핵심 환경 변수:

| env | 의미 | 기본값 |
|---|---|---|
| `DATABASE_URL` | PostgreSQL DSN (asyncpg) | `postgresql+asyncpg://postgres:postgres@localhost:5432/ai_data` |
| `AUTH_REQUIRED` | strict auth 모드 | `false` |
| `BOOTSTRAP_API_KEY` | 부트스트랩 키 plaintext | `""` (비활성) |
| `EMBEDDING_PROVIDER` | `hash` (default, deterministic) 또는 `openai` (text-embedding-3-small) | `hash` |
| `OPENAI_API_KEY` | `/api/ask` LLM 해석 + openai embedder 에 사용. 없으면 키워드 폴백 | (unset) |
| `OPENAI_ASK_MODEL` | `/api/ask` 의 LLM 모델명 | `gpt-4o-mini` |
| `AUTO_EMBED_ON_INSERT` | INSERT/UPDATE 후 임베딩 잡 자동 등록 | `false` |
| `JOBS_TTL_SECONDS` | in-memory job 보관 TTL | `3600` |
| `MAX_UPLOAD_MB` | `/api/convert` 업로드 한도 (MB) | `50` |
| `ATTACHMENTS_DIR` | 첨부 바이너리 루트 (정적 마운트) | `attachments` |
| `FIGURES_DIR` | 그림 바이너리 루트 (정적 마운트) | `figures` |
| `ENABLE_METRICS` | Prometheus `/metrics` 노출 | `true` |
| `BUILD_SHA` | `/api/system/health.build` 식별자 | `dev` |

표준 401 / 인증 실패 응답:

```json
{"error":{"code":"AUTHENTICATION_ERROR","message":"missing or invalid X-API-Key","details":{},"request_id":"<uuid>"}}
```

모든 응답에 `X-Request-ID` 헤더가 붙는다 — 에러 추적 시 이 값을 함께 보고한다.

---

## 3. 데이터 모델 (Data model)

허브는 단일 PostgreSQL 백엔드에 7 개 테이블만 둔다.
`records` 가 중심이고 나머지는 거의 1:N 또는 N:M 보조 테이블이다.

### 3.1 `records` — 모든 데이터의 단일 테이블

| 필드 | 타입 | NULL | 의미 / 빈도 높은 값 |
|---|---|---|---|
| `id` | str(80) PK | no | `DOC-HE-CAE-2026-0000000001` 형식 (§4) |
| `data_type` | str(20) | no | enum 7 종: DOC/DATA/SIM/CAD/LOG/FORM/OTHER |
| `team` | str(10) | no | HE/EV/PT/DA/MX/VD (§4.4) |
| `group` | str(20) | no | CAE/Test/Design/BMS/AI/MFG/QA/PLM/... |
| `year` | int | no | 2020..2099 |
| `seq` | int | no | (data_type, team, group, year) 단위 시퀀스 |
| `title` | text | no | 제목 (필수, 빈 문자열 금지) |
| `summary` | text | no | 요약. 비어 있으면 `""` |
| `tags` | text[] | no | 자유 태그 배열. AND/overlap 검색 모두 사용 |
| `agents` | text[] | no | 사용 가능 agent_type 배열 (iga-analyst 등) |
| `schema_version` | str(10) | no | 기본 `"1.0"` |
| `content` | jsonb | no | data_type 별 페이로드 (§12.13) |
| `content_hash` | str(64) | yes | SHA-256(content). diff 시 빠른 비교에 사용 |
| `source_file` | text | yes | 변환 시 원본 파일명 |
| `has_attachments`, `attachment_count` | bool, int | no | 첨부 요약 |
| `author`, `department` | str(100) | no | 기본 `""` |
| `project` | str(100) | yes | 프로젝트명 (PATCH 가 nullable 처리하는 유일 필드) |
| `version` | str(20) | no | 기본 `"1.0"` |
| `classification` | str(20) | no | enum 4 종 (§12.2). 기본 `internal` |
| `status` | str(20) | no | enum 4 종 (§12.3). 기본 `draft` |
| `domain` | str(100) | yes | 자유 도메인 문자열 (`thermal`, `cell` 등) |
| `subject_keywords` | text[] | no | 주제 키워드 배열 |
| `source_system` | str(50) | yes | 출처 시스템 (`PLM`, `Confluence`) |
| `language` | str(10) | no | 기본 `ko`. enum: ko/en/mixed (schema), ko/en/ja/zh (meta) |
| `parent_record_id` | str(80) FK→records.id | yes | 파생/번역/추출 관계 self-FK |
| `derivation` | str(20) | no | enum 4 종 (§12.4). 기본 `original` |
| `capabilities` | text[] | no | 자동 산출 라벨 13 종 (§12.8) |
| `quality_score` | smallint | yes | 0..100 |
| `valid_from`, `valid_until` | date | yes | 유효 기간 |
| `agent_hints` | text | yes | 에이전트가 이 record 를 어떻게 다뤄야 하는지 사람이 작성한 자유 텍스트 |
| `related_record_ids` | text[] | no | 수동 큐레이션 관계 |
| `query_examples` | text[] | no | 자연어 쿼리 예시 |
| `access_pattern` | str(20) | no | enum 3 종: frequent / occasional (default) / rare |
| `deleted_at` | timestamptz | yes | soft-delete. NULL = 활성 |
| `read_count` | int | no | 조회수. `_bump_usage` 가 GET 시 +1 |
| `last_accessed_at` | timestamptz | yes | 마지막 조회 시각 |
| `created_at`, `updated_at` | timestamptz | no | auto onupdate |

Unique constraint: `(data_type, team, group, year, seq)`. 같은 튜플로 두 번
INSERT 하면 409 `CONFLICT`. INSERT 시 `capabilities` 는 백엔드가 `content`
모양에서 자동 산출하므로 클라이언트가 직접 보내도 무시되거나 덮어써진다.

### 3.2 `record_sections` — RAG chunk

| 필드 | 타입 | NULL | 의미 |
|---|---|---|---|
| `id` | bigserial PK | no | 내부 PK |
| `record_id` | str(80) FK | no | 부모 |
| `section_id` | str(20) | no | 섹션 식별자 (예: `S001`) |
| `level` | smallint | no | 헤딩 레벨 (1=H1) |
| `title` | text | no | 섹션 제목 |
| `content_text` | text | no | 본문 평문 (RAG 검색의 핵심) |
| `figure_refs`, `table_refs` | text[] | no | 참조 ID |
| `embedding` | vector(384) | yes | pgvector. NULL 이면 시맨틱 검색 후보에서 제외 |
| `embedded_at`, `embedding_model` | timestamptz, str | yes | 백필 메타 |

Unique: `(record_id, section_id)`. DOC 변종에서만 의미 있고 다른 변종은 보통
빈 상태. `embedding` 차원은 마이그레이션 0004 의 `vector(384)` 와 정합 — 차원
변경은 마이그레이션과 함께 가야 한다.

### 3.3 `record_attachments` — 첨부 메타

| 필드 | 타입 | NULL | 의미 |
|---|---|---|---|
| `id` | str(80) PK | no | `{record_id}-A{nnn}` (레거시 `-F{nnn}` 도 허용) |
| `record_id` | str(80) FK | no | 부모 |
| `number` | int | no | 1 부터 |
| `kind` | str(20) | no | enum 9 종 (§12.7) |
| `caption` | text | no | 필수. 누락 시 `"(캡션 누락 — 검수 필요)"` placeholder |
| `file_name`, `file_path` | text | yes | 원본명 / 상대경로 |
| `mime_type`, `size_bytes`, `hash_sha256` | str/int/str | yes | 바이너리 메타 |
| `section_ref` | str(20) | yes | 참조 섹션 id |
| `extra` | jsonb | no | 자유 메타. 기본 `{}` |
| `created_at` | timestamptz | no | 생성 시각 |

바이너리는 `/attachments/{record_id}/A{nnn}.{ext}` 정적 마운트로 서빙된다.
그림 전용 마운트 `/figures/{record_id}/F{nnn}.{ext}` 도 있다.

### 3.4 `agents` / 3.5 `agent_records` / 3.6 `audit_log` / 3.7 `api_keys`

- `agents` (PK `agent_type`): `name`, `description`, `common_tags[]`, `data_types[]`,
  `created_at`. 표준 5 종이 시드된다 — `iga-analyst`, `cae-reporter`,
  `material-reviewer`, `process-checker`, `code-assistant`.
- `agent_records` (PK `(agent_type, record_id)`): `priority smallint default 1`
  (1..5 권장). `/api/data` 의 relevance 점수 계산에 priority 가 weight 로 들어간다.
- `audit_log` (PK `id bigserial`): `record_id`, `actor`, `action` (enum 6:
  INSERT/UPDATE/DELETE/RESTORE/VIEW/ACCESS), `field_changes jsonb {field:[old,new]}`,
  `request_id`, `created_at`. 직접 노출 엔드포인트 없음 — DB 기록 only.
- `api_keys` (PK `id`): `key_hash`, `name`, `agent_scopes[]`, `department`,
  `expires_at`, `revoked`, `last_used_at`, `created_at`.

---

## 4. ID 형식 + 명명 규칙 (ID format)

### 4.1 정식 패턴

```
{DATA_TYPE}-{TEAM}-{GROUP}-{YEAR}-{SEQ}
```

| 토큰 | 규칙 | 예 |
|---|---|---|
| DATA_TYPE | enum 7 종 | `DOC` |
| TEAM | 2~4 uppercase ASCII | `HE` |
| GROUP | 2~5 uppercase ASCII | `CAE` |
| YEAR | 4 자리, 2020..2099 | `2026` |
| SEQ | 6 자리 zero-pad | `000001` |

전체 예: `DOC-HE-CAE-2026-0000000001`. 정규식
`^(DOC|DATA|SIM|CAD|LOG|FORM|OTHER)-[A-Z]{2,4}-[A-Z]{2,5}-20[2-9][0-9]-[0-9]{6}$`
가 `/api/schema.properties.id.pattern` 에 노출돼 있다.

### 4.2 레거시 패턴

```
{TEAM}-{GROUP}-{YEAR}-{SEQ}
```

`data_type` 접두사가 빠진 형태. `parse_id()` / `normalize_id()` 가 자동으로
`DOC-` 접두를 붙여 정규화한다. 대량 일괄 적재 시 데이터를 강제 정규화하지
않더라도 ingest 단계에서 보강된다.

### 4.3 첨부 ID

```
{record_id}-A{number:03d}      예: DOC-HE-CAE-2026-0000000001-A001
{record_id}-F{number:03d}      (레거시 — figure 전용)
```

### 4.4 팀 / 그룹 시드 (`api/seed/teams.py`)

| TEAM | GROUPS |
|---|---|
| HE | CAE, Test, Design |
| EV | BMS, Battery, Motor |
| PT | Material, Process |
| DA | AI, Data |
| MX | MFG, QA |
| VD | DEV, PLM |

`/api/meta/options` 가 권위적으로 위 시드를 노출한다 (`allow_custom.group=true`,
`allow_custom.team=false`).

---

## 5. 핵심 엔드포인트 카탈로그 (Endpoint catalog)

| 메서드 | 경로 | 용도 | 주요 파라미터 | top-level 응답 키 |
|---|---|---|---|---|
| GET | `/` | 서비스 식별 | — | `service, version, status` |
| GET | `/health` | minimal liveness | — | `status` |
| GET | `/api/system/health` | health + 메타 | — | `status, version, auth_required, build` |
| GET | `/api/discover` | 전체 카탈로그 (60s 캐시) | `?no_cache=true` | `version, total_records, by_data_type, by_division, by_classification, agents[], data_types_explained, starting_points[], schema_url, hints_url, llm_doc_url, ask_url, generated_at` |
| GET | `/api/schema` | JSON Schema (draft-2020-12) | — | `$schema, $id, title, properties, oneOf, examples, x-relationships` |
| GET | `/api/hints` | 자연어 힌트 카탈로그 | `?context=getting_started/searching/...` | `context, available_contexts, hints[]` |
| GET | `/api/docs/llm.txt` | 5–10KB 통합 마크다운 (text/plain) | — | (raw text) |
| POST | `/api/ask` | 자연어 → interpreted_query + results | body `{query, limit}` | `interpreted_query, results, total_matched, follow_up_queries, raw_query` |
| GET | `/api/records` | 목록 + 필터 | `data_type, team, group, year, agent[], tag[], q, include_deleted, limit, offset` | `items[], total, limit, offset` |
| GET | `/api/records/{id}` | 단건 (read_count 자동 증가, VIEW 감사) | `?include_deleted` | full RecordOut |
| POST | `/api/records` | 직접 INSERT | body `RecordIn` | RecordOut (201) |
| PATCH | `/api/records/{id}` | 부분 수정 (`summary?, tags?, agents?, project?, version?` 등) | body `RecordPatch` | RecordOut |
| DELETE | `/api/records/{id}` | soft delete (멱등) | `?hard=true` (bootstrap 필요) | 204 |
| POST | `/api/records/{id}/restore` | soft delete 복원 | — | RecordOut |
| GET | `/api/records/{id}/lineage` | 조상/자손 BFS | — | `record_id, self, ancestors[], descendants[], ancestor_count, descendant_count` |
| GET | `/api/records/{id}/diff?from=<id>` | 두 record diff (메타 + 섹션 unified diff) | `?from=` (필수) | `from, to, meta_changes, section_changes[], block_changes` |
| GET | `/api/records/{id}/attachments` | 첨부 목록 | `?kind` | `[AttachmentOut]` |
| GET | `/api/records/{id}/attachments/{att_id}` | 첨부 단건 | — | `AttachmentOut` |
| GET | `/api/attachments` | 전역 첨부 검색 | `?kind, ?record_id, limit, offset` | `[AttachmentOut]` |
| GET | `/api/data` | agent-scoped 검색 (Cline SR) | `agent (req), query?, data_types[]?, limit` | `agent, query, results[], total_matched` |
| GET | `/api/search?mode=tag` | 태그 AND 검색 | `tags[] (req), limit, offset` | `mode, tags, items[], total, limit, offset` |
| GET | `/api/search?mode=fts` | FTS / ILIKE 텍스트 검색 | `q (req), limit, offset` | `mode, q, items[], total, limit, offset` |
| GET | `/api/search?mode=semantic` | pgvector cosine | `q (req), limit` | `mode, q, items[], total, limit, offset:0` |
| GET | `/api/agents` | 에이전트 목록 | — | `[AgentOut]` |
| GET | `/api/agents/{type}` | 에이전트 단건 | — | `AgentOut` |
| GET | `/api/agents/{type}/records` | 해당 에이전트의 record 목록 | — | `[RecordOut]` |
| POST | `/api/agents` | 에이전트 생성 | body `AgentIn` | `AgentOut` (201) |
| PATCH | `/api/agents/{type}` | 에이전트 수정 | body `AgentPatch` | `AgentOut` |
| DELETE | `/api/agents/{type}` | 에이전트 삭제 | — | 204 |
| GET | `/api/analytics/distribution` | 분포 통계 | — | `by_data_type, by_division, by_team, by_year` 등 |
| GET | `/api/analytics/common-tags` | agent 별 상위 태그 | `agent (req), limit` | `[{tag, count}]` |
| GET | `/api/analytics/cross-agent` | agent 간 공유 record | `agents[] (req)` | dict |
| GET | `/api/analytics/timeline` | 월별 카운트 | `year (req)` | dict |
| GET | `/api/analytics/usage` | 상위 read_count | `limit` | `items[], total, limit` |
| GET | `/api/meta/options` | UI 셀렉트박스 옵션 (5 분 캐시) | — | `version, teams, groups, agents, classifications, statuses, derivations, languages, data_types, supported_extensions, max_upload_mb, allow_custom` |
| GET | `/api/taxonomy/tags` | 태그 카탈로그 + 빈도 + data_type 분포 | `q?, min_count?, limit?` | `total, items[{tag,count,data_types,agents}]` |
| GET | `/api/taxonomy/tags/resolve` | 비공식 표현 → 정식 태그 매핑 (synonym 사전) | `q (req), limit?` | `query, normalized, candidates[{tag,score,method,count}]` |
| GET | `/api/taxonomy/data-types` | data_type 분포 + 추천 사용 패턴 | — | `items[{data_type,count,description,subtypes,schema_url,sample_query}]` |
| GET | `/api/taxonomy/domains` | domain 필드 분포 (CAE/lecture/test 등) | — | `items[{domain,count}]` |
| GET | `/api/taxonomy/agents` | agent 카탈로그 + record 수 + 주요 태그 5개 | — | `items[{agent_type,name,record_count,common_tags,data_types}]` |
| GET | `/api/taxonomy/classification` | classification enum + 의미 + 분포 | — | `field, items[{value,description,count}]` |
| GET | `/api/taxonomy/status` | status enum + 의미 + 분포 | — | `field, items[{value,description,count}]` |
| GET | `/api/taxonomy/access-pattern` | access_pattern enum + 의미 + 분포 | — | `field, items[{value,description,count}]` |
| POST | `/api/convert/` | 파일 → JSON (DB 적재 없음) | multipart `file, team, group, year, seq, tags, agents, classification, domain` | converter 결과 dict |
| POST | `/api/convert/ingest` | 파일 → JSON → DB INSERT/UPDATE | 위 + `status, language, subject_keywords, derivation, quality_score, valid_from, valid_until, title_override, summary_override, agent_hints, related_record_ids, query_examples, access_pattern, persist_attachments` | `record_id, status (inserted/updated), sections_written, assigned_seq, attachments_persisted, record` |
| POST | `/api/jobs/embed` | 임베딩 backfill | body `{record_id?, record_ids?[]}` | job dict (202) |
| GET | `/api/jobs/{job_id}` | 잡 상태/진행률/결과 | — | `{job_id, kind, status, progress, result, error}` |
| GET | `/api/jobs` | 잡 목록 | `?kind, ?limit` | `{jobs}` |
| POST | `/api/auth/keys` | 키 발급 (bootstrap) | body `ApiKeyIn` | `ApiKeyCreated` (incl. `key`) (201) |
| GET | `/api/auth/keys` | 키 목록 (bootstrap) | — | `[ApiKeyOut]` |
| POST | `/api/auth/keys/verify` | 현재 키 검증 (any key) | — | `{ok, key_name, agent_scopes}` |
| DELETE | `/api/auth/keys/{key_id}` | 키 폐기 (bootstrap) | — | 204 |
| GET | `/metrics` | Prometheus text | — | text/plain (`ENABLE_METRICS=true` 시) |

정적 마운트:

- `/figures/{record_id}/F{nnn}.{ext}` — figure 바이너리
- `/attachments/{record_id}/A{nnn}.{ext}` — attachment 바이너리

curl 예시:

```bash
curl -s http://localhost:8000/api/discover | jq '.total_records, .by_data_type'
curl -s -H "X-API-Key: $KEY" "http://localhost:8000/api/records?data_type=DOC&year=2026&limit=5"
curl -s -X POST -H "Content-Type: application/json" -H "X-API-Key: $KEY" \
     -d '{"query":"최근 1주일 IGA 시뮬","limit":5}' http://localhost:8000/api/ask
```

---

## 6. 검색 전략 — 결정 + 폴백 (Search strategy)

자연어 질의는 다섯 단계의 폴백 사다리를 따른다. 각 단계의 *조건*과 *언제
다음 단계로 내려가는지* 가 핵심이다.

```
사용자 질의
    │
    ▼
1. POST /api/ask                         (자연어. 항상 동작)
    │ results 0건 또는 매칭 모호?
    ▼
2. GET /api/search?mode=semantic&q=...   (의미 유사도, pgvector)
    │ 503 또는 결과 < expected?  (embedder 미준비 / EMBEDDING_PROVIDER=hash 의 신호 약함)
    ▼
3. GET /api/search?mode=fts&q=...        (한국어 형태소 약하지만 정확 매칭은 강함)
    │ q 가 합성어/조사 포함이라 정확 매칭 실패?
    ▼
4. GET /api/search?mode=tag&tags=...     (정확한 태그를 알 때)
    │ 태그 사전 모르면?
    ▼
5. GET /api/records?q=<title fragment>   (단순 ILIKE on title+summary, 마지막 폴백)
```

각 모드의 강점/약점은 다음과 같다.

| 모드 | 강점 | 약점 | 언제 쓰는지 |
|---|---|---|---|
| `ask` | 자연어 해석 + LLM 으로 필터 추정 + follow_up 제공 | LLM 키 없으면 키워드 룩업으로 폴백 (한국어/영어 매핑 사전 기반, 정확도 떨어짐) | 사용자 평문 질문이 들어왔을 때 첫 진입 |
| `semantic` | 동의어·재구성에 강함. 본문 단어와 질의어가 달라도 매칭 | pgvector 와 임베딩 backfill 필요. SQLite 폴백은 numpy 전체 로드 (소규모만) | 의미 유사도가 필요한 RAG, 본문이 아닌 요약 매칭 |
| `fts` | section content_text + record title/summary 모두 ILIKE/to_tsvector | 한국어 형태소 분석기 미적용 (PG 의 `simple` configuration). 동의어 약함 | 영어 키워드, 정확한 단어 검색 |
| `tag` | 정확한 카테고리 매칭. AND 의미 (`@>`) | 태그 사전을 미리 알아야 함 | UI 에서 사용자가 태그 체크박스 클릭 |
| `records?q=` | 가장 단순. ILIKE on title + summary | recall 낮음 | 마지막 폴백 |

**폴백 트리거 신호**: `ask` 응답의 `total_matched: 0` 또는
`interpreted_query.source: "keyword"` 이고 `filters` 가 비었을 때 → semantic
으로. semantic 이 503 (embedder 미준비) 이면 `mode=fts` 로. fts 가 0 건이면
질의어를 1~2 단어로 줄여서 다시 시도, 그래도 0 건이면 `mode=tag` 로 (사용자가
태그 후보를 알면). tag 도 실패면 `GET /api/records?q=...` 의 단순 ILIKE 가
최종 안전망이다.

응답 모양 예 (`mode=fts`):

```json
{
  "mode": "fts", "q": "IGA",
  "items": [{
    "record_id": "DOC-HE-CAE-2026-0000000001",
    "title": "IGA tensile test report",
    "data_type": "DOC",
    "section_id": "S003",
    "section_title": "3.1 시험 절차",
    "snippet": "…IGA NURBS shell 적용 후…",
    "tags": ["iga","tensile"]
  }],
  "total": 7, "limit": 20, "offset": 0
}
```

`mode=semantic` 의 `items[].score` 는 0..1 (1 = 동일). pgvector cosine
distance `<=>` 를 `1 - distance/2` 로 정규화한다.

### 6.1 의미 그룹 라우터 (Semantic Groups — NEW)

검색 결과를 의미 군집으로 자동 묶고, 한 그룹의 모든 record 를 한 번에 받는
패턴. 작은 AI 가 한 컨텍스트에서 여러 record 를 다발로 추론할 때 유용.

세 엔드포인트가 같이 동작한다:

| 엔드포인트 | 입력 | 동작 |
|---|---|---|
| `POST /api/groups/auto` | `{q, n_groups, limit_per_group, min_score, sim_threshold}` | (1) 시맨틱 검색 top-K → (2) 그리디 클러스터링 (cosine ≥ sim_threshold) → (3) 그룹 라벨 + 공통 태그/도메인 합성 |
| `GET /api/records/{id}/cluster?mode=semantic\|tag\|hybrid` | path id + mode | anchor 한 건과 같은 의미 그룹의 모든 record. `mode=hybrid` 가 기본 권장 (semantic 0.6 + tag 0.4 가중) |
| `POST /api/records/bulk` | `{ids:[...], include_sections}` | 여러 id 한 번에 fetch → 그룹 fetch 시 N+1 회피 |

`POST /api/groups/auto` 응답:

```json
{
  "query": "AI 도입 현황", "total_records": 23, "n_groups_requested": 3,
  "groups": [
    {
      "label": "AI 도입 현황 — AI·DigitalTwin·Strategy",
      "common_tags": ["AI","DigitalTwin","Strategy"],
      "common_agents": ["cae-reporter"],
      "common_domain": "strategy",
      "size": 7,
      "representative_record": {"id":"...","title":"...","score":0.92},
      "records": [{"id":"...","title":"...","score":0.92,"section_id":"...","snippet":"..."}]
    }
  ]
}
```

`GET /api/records/{id}/cluster?mode=hybrid` 응답:

```json
{
  "anchor_record": {"id":"DOC-HE-AI-2026-0000100001","title":"...","data_type":"DOC","tags":["AI","DigitalTwin"]},
  "mode": "hybrid", "cluster_size": 5,
  "items": [{"id":"...","score":0.91,"shared_tags":["AI","DigitalTwin"],"tag_jaccard":0.66,"semantic_sim":0.95}]
}
```

권장 워크플로우:

1. `POST /api/ask` 또는 `/api/groups/auto` 로 후보 그룹 발견
2. `groups[i].records[].id` 를 `/api/records/bulk` 에 한 번에 던져 풀 레코드 수신
3. `groups[i].label` + `common_tags` 로 그룹 의도 표시 후 LLM 처리

알고리즘 메모: 그리디 (정렬된 score 순서로 시드 선택, cosine ≥ threshold 인 것을 같은 그룹에 흡수). numpy 만 사용. K-means 미사용 — 외부 의존 없이 결정론적.

---

## 7. MCP 도구 카탈로그 + 사용 패턴 (MCP tools)

`api_server/src/mcp_server/server.py` 에 `@mcp.tool()` 로 등록된 9 개 도구.
MEDIUM 모델은 함수 호출 능력이 충분하므로 REST 호출 대신 MCP 도구를 쓰면
프롬프트 토큰을 절약하고 응답 정규화도 자동으로 받는다.

| 도구 | 인자 | 출력 | 용도 |
|---|---|---|---|
| `discover_schema()` | — | `{discover, schema}` | **항상 첫 호출.** `/api/discover` + `/api/schema` 합본 |
| `discover_capabilities(agent_type)` | `agent_type: str` | `{agent, record_count, sample_records, follow_up}` | 한 에이전트의 데이터 범위 |
| `ask(query, limit=5)` | `query`, `limit≤50` | `{interpreted_query, results, total_matched, follow_up_queries}` | 자연어 검색 |
| `find_related(record_id, mode='auto')` | `record_id`, `mode∈{tags,graph,semantic,auto}` | `{record_id, mode, related[], by_mode:{tags,graph,semantic}}` | 비슷한 record 찾기 (3 모드 통합) |
| `explain_field(field_name)` | `field_name` | `{field_name, spec, is_enum, allowed_values, type, description}` | 단일 필드 메타 |
| `explain_schema(field_name)` | (alias of `explain_field`) | 동일 | 명세 일관성용 alias |
| `query_data(agent, query="", limit=5)` | `agent`, `query`, `limit≤20` | `{results}` | `/api/data` 래퍼 |
| `list_agents()` | — | `{agents}` | 에이전트 enum |
| `get_record(record_id)` | `record_id` | full record dict | record 단건 |
| `search(mode, query="", tags=None)` | `mode∈{tag,fts,semantic}` | `{results}` 또는 mode별 응답 | `/api/search` 래퍼 |

자주 쓰는 도구 조합 (체인 패턴):

1. **콜드 스타트** — `discover_schema()` → `list_agents()` → `ask("...")` →
   결과 첫 번째 record 에 `get_record(id)` → 필요시 `find_related(id)`.
2. **에이전트 시점 조사** — `discover_capabilities("iga-analyst")` →
   `query_data("iga-analyst", query="...", limit=10)` → 흥미로운 record 마다
   `get_record`.
3. **필드 의미 의심** — `explain_field("classification")` 로 enum 확인 후
   `search(mode="tag", tags=[...])` 또는 `ask` 재시도.
4. **유사 record 추적** — `get_record(base_id)` → `find_related(base_id,
   mode="auto")` → `by_mode.tags` / `by_mode.graph` / `by_mode.semantic`
   각각 검사.
5. **이력 추적** — `get_record(id)` → 브랜치별 `/api/records/{id}/lineage`
   (MCP 미래 도구) 또는 직접 REST → `/api/records/{id}/diff?from=<ancestor>`.

도구 환경 변수: `API_URL` (기본 `http://localhost:8000`), `API_TIMEOUT`
(기본 30초), `MAX_LIMIT=20`. 도구가 401 등 오류를 반환하면 dict 의 `error` 키로
감싸서 돌려주기 때문에 (예외 raise 안 함) 호출 측에서 `if "error" in result`
로 분기하면 된다.

---

## 8. 8 가지 흔한 워크플로우 (Common workflows)

### 8.1 자연어 질의 → 답변 (RAG)

```
1. POST /api/ask  body {"query":"<user_text>","limit":5}
   → interpreted_query.source 가 "llm" 인지 "keyword" 인지 확인
2. results[].id 모음
3. 각 id 마다 GET /api/records/{id} 상세 (read_count 자동 증가)
4. DOC 인 경우 GET /api/records/{id}/sections 로 본문 청크
5. follow_up_queries 표시 — 사용자에게 다음 단계 추천
```
*예외*: `total_matched=0` → §6 폴백 사다리 (semantic → fts → tag → records?q=).

### 8.2 태그/분야로 record 카탈로그 조회

```
1. GET /api/discover                     by_data_type / by_division 분포 확인
2. GET /api/analytics/common-tags?agent=iga-analyst&limit=20  → 태그 후보
3. GET /api/search?mode=tag&tags=IGA&tags=NURBS&limit=20      AND 검색
4. items[].record_id 마다 GET /api/records/{id}
```
*예외*: 결과가 너무 많으면 `&limit=10&offset=10` 으로 페이지네이션, 너무
적으면 태그 1 개로 줄여서 다시.

### 8.3 한 record 의 첨부 그림 다운로드 + 캡션 확인

```
1. GET /api/records/{id}/attachments?kind=figure
2. 응답[i].id (= "{record_id}-A001") 와 file_path 확보
3. 바이너리: GET /attachments/{record_id}/A001.png  (정적 마운트)
4. caption / mime_type / size_bytes 는 응답[i] 에 들어 있음
```
*예외*: caption 이 `"(캡션 누락 — 검수 필요)"` 면 사용자에게 검수 필요 안내.
바이너리 대신 figure 마운트(`/figures/{...}/F{nnn}.{ext}`) 가 쓰일 수도 있음.

### 8.4 비슷한 record 찾기 (find_related)

```
1. GET /api/records/{id}                 base.tags, base.related_record_ids, base.parent_record_id
2. find_related(id, mode="auto")         (MCP)  또는 다음 3 개 합치기:
   2a. GET /api/search?mode=tag&tags=<base.tags[0]>
   2b. GET /api/search?mode=semantic&q=<base.title>
   2c. graph: parent_record_id + related_record_ids[] 각각 GET
3. 결과 dedup → by_mode 로 그룹화하여 반환
```
*예외*: pgvector 미준비 → semantic 단계 503 → tags + graph 만으로 동작.

### 8.5 분야별 통계 (data_type / team / agent 분포)

```
1. GET /api/analytics/distribution               by_data_type / by_division / by_team / by_year
2. GET /api/analytics/timeline?year=2026         월별 12 행
3. GET /api/analytics/cross-agent?agents=iga-analyst&agents=cae-reporter
   → 두 에이전트가 모두 사용하는 record 집합
4. GET /api/analytics/usage?limit=20             read_count 인기 record
```
*예외*: 빈 DB → 모든 카운트 0. UI 에서는 “데이터 없음” 안내가 필요.

### 8.6 새 record 적재 (file → /api/convert/ingest)

```
1. multipart POST /api/convert/ingest
   file=<doc.docx>, team=HE, group=CAE, year=2026, seq=0
   (seq=0 → backend 가 (DOC,HE,CAE,2026) 단위 MAX(seq)+1 자동 할당)
   tags="iga,tensile", agents="iga-analyst", status="draft"
2. 응답.record_id 와 응답.status ("inserted" or "updated") 확인
3. AUTO_EMBED_ON_INSERT=true 면 자동, 아니면:
   POST /api/jobs/embed  body {"record_id":"<id>"}
   GET  /api/jobs/{job_id}  → status: pending|running|done|failed
4. GET /api/records/{id}                         적재 확인
```
*예외*: 같은 (data_type, team, group, year, seq) 가 이미 있으면 409
`CONFLICT` — seq 를 비우거나 PATCH 로 업데이트. 파일 크기 > `MAX_UPLOAD_MB` 면
413 `PAYLOAD_TOO_LARGE`. 확장자가 화이트리스트에 없으면 `UnsupportedFormatError`.

### 8.7 record 변경 이력 추적 (version chain + diff)

```
1. GET /api/records/{id}/lineage
   → ancestors[] (parent → grandparent), descendants[] (BFS children)
2. lineage.ancestors[0].id 를 from 으로:
   GET /api/records/{id}/diff?from=<ancestor_id>
3. 응답.meta_changes = {field: [old, new]}  → 메타 변경 필드
   응답.section_changes[] = {section_id, kind, title_changes, content_diff}
   - kind: "added" | "removed" | "modified"
   - content_diff: unified diff text (n=2 context)
4. 응답.block_changes: "identical" 또는 "summary"
```
*예외*: from 과 record_id 가 같으면 400 `BAD_REQUEST`. 어느 한 쪽이 없으면
404. audit_log 는 직접 노출 엔드포인트가 없으므로 DB 직조회 필요.

### 8.8 자연어 질의에 LLM 키 없는 환경 → 폴백

```
1. POST /api/ask body {"query":"품질 80 이상 approved 문서"}
   → interpreted_query.source="keyword"  (OPENAI_API_KEY 없음)
2. 키워드 룩업이 잡은 필터 확인:
   filters.quality_score_gte=80, filters.status="approved"
3. 한국어 합성어/조사 때문에 매칭 약하면, 직접 필터링:
   GET /api/records?status=approved&limit=50  → 클라이언트에서 quality_score≥80
4. 또는 mode=semantic 으로:  GET /api/search?mode=semantic&q="품질 80 approved"
5. semantic embedder 가 hash 면 의미 신호 약함 → fts 로:
   GET /api/search?mode=fts&q=approved
```
*예외*: 모든 단계가 0 건 → 사용자에게 “결과 없음, 다른 키워드 시도” 안내 +
`GET /api/discover` 의 `by_data_type` 으로 분포 보여주기.

### 8.9 DATA 타입 워크플로우 — 시험·시뮬레이션 결과 분석

DATA 타입 record 는 `headers + rows` 표 데이터다 (Excel 변환 결과 등).
일반화된 데이터 위에서 작은 AI 가 직접 평균/최대/최소 계산 안 해도 되게 4 종 엔드포인트가 있다.

```
1. 카탈로그 (어떤 DATA 가 있나):
   GET /api/data?domain=material-test&min_rows=10&tags=Tensile
   → {total, items:[{id, title, domain, tags, rows, columns, units, context}]}

2. 컬럼 정의 (이 데이터가 뭔지):
   GET /api/data/{id}/columns
   → {items:[{column, description, unit, dtype}]}
   - dtype ∈ {int, float, str, enum, bool, mixed, null}
   - description 은 column_descriptions 또는 _GLOSSARY 시트에서 자동 추출

3. 행 페이징 + 컬럼=값 사전필터:
   GET /api/data/{id}/rows?limit=200&offset=0&where=Region:Yield
   → {headers, units, total_rows, rows:[[...]]}

4. 통계 집계:
   GET /api/data/{id}/aggregate?op=max&column=Stress
   → {result: 450.0, unit: "MPa"}

   group_by 모드:
   GET /api/data/{id}/aggregate?op=max&column=Stress&group_by=Region
   → result:[{Region:"UTS", max_Stress:450.0}, {Region:"Strain Hardening", ...}]
```

**op 별 동작**:
| op | column 필요 | 의미 |
|---|---|---|
| `count` | optional | non-null 행 개수 |
| `sum` | required | 숫자 합 |
| `avg` | required | 평균 (round 6자리) |
| `max` | required | 최댓값 |
| `min` | required | 최솟값 |

규칙: `op != count` 면 `column` 필수 → 누락 시 422. unknown column → 422.
group_by 컬럼은 dtype=enum 이어야 의미 있음. SS/시뮬 결과처럼 카테고리화된 컬럼.

### 8.10 다축 필터링 (faceted search) — 다음 query 좁힘 신호

기존 `/api/search` 는 단일 모드. faceted 는 **AND 다축 필터 + facet 카운트**.

```
GET /api/search/faceted?q=stress&mode=semantic&data_type=DOC,DATA
    &tags=Tensile&agent=cae-reporter&domain=material-test
    &classification=internal&status=approved
    &year_from=2025&year_to=2026&min_quality=80
    &limit=20&offset=0

응답:
{
  total: 14,
  items: [{id, data_type, title, tags, agents, domain, score?, ...}],
  facets: {
    data_type:    {DOC:11, DATA:3},
    tags:         {IGA:5, stress:4, 낙하시험:3, ...},
    domain:       {cae:8, lecture:4, material-test:2},
    agent:        {iga-analyst:7, cae-reporter:5},
    status:       {approved:9, review:5},
    classification:{internal:13, public:1},
    year:         {"2026":11, "2025":3}
  }
}
```

**facets 활용 패턴** — 작은 AI 가 다음 query 를 어떻게 좁힐지:

1. *큰 카운트 = 좁힐 후보*: `data_type.DOC=11` 이 압도적이면 다음 query 에 `data_type=DOC` 추가하면 결과 ½ 으로 감소.
2. *0 또는 1 = 무용*: `classification.public=1` 이면 그 축으로는 좁혀도 의미 없음.
3. *균등 분포 = 결정 보류*: `agent.iga-analyst=7, cae-reporter=5` 처럼 비슷하면 사용자에게 골라 달라고 묻기.

### 8.11 태그 매칭 (any/all)

```
GET /api/search/by-tags?tags=IGA,NURBS&match=all   # AND (default)
GET /api/search/by-tags?tags=IGA,NURBS&match=any   # OR (union)
```
`match=any` 는 `array_overlap` (PG: `&&`), `match=all` 은 `array_contains` (PG: `@>`).

---

## 9. 응답 스키마 — 모든 핵심 키 (Response schemas)

### 9.1 RecordOut (record 를 반환하는 모든 엔드포인트)

| 키 | 타입 | 의미 | 추출 (jq) | Python |
|---|---|---|---|---|
| `id` | str | record ID | `.id` | `r["id"]` |
| `data_type` | str | DOC/DATA/SIM/CAD/LOG/FORM/OTHER | `.data_type` | `r["data_type"]` |
| `team`, `group` | str | 분류 키 | `.team, .group` | `r["team"]` |
| `year`, `seq` | int | 분류 키 | `.year, .seq` | `r["year"]` |
| `title`, `summary` | str | 텍스트 메타 | `.title` | `r["title"]` |
| `tags`, `agents` | str[] | 배열 메타 | `.tags[]` | `r["tags"]` |
| `content` | obj | data_type 별 페이로드 | `.content` | `r["content"]` |
| `content_hash` | str/null | SHA-256 | `.content_hash` | `r.get("content_hash")` |
| `classification`, `status`, `derivation`, `access_pattern`, `language` | str | enum | `.classification` | `r["classification"]` |
| `domain`, `source_system`, `parent_record_id`, `agent_hints` | str/null | optional | `.parent_record_id // empty` | `r.get("parent_record_id")` |
| `subject_keywords`, `capabilities`, `related_record_ids`, `query_examples` | str[] | array | `.capabilities[]` | `r["capabilities"]` |
| `quality_score` | int/null | 0..100 | `.quality_score` | `r.get("quality_score")` |
| `valid_from`, `valid_until` | date/null | ISO date | `.valid_from` | — |
| `has_attachments`, `attachment_count` | bool, int | 첨부 요약 | `.has_attachments` | — |
| `created_at`, `updated_at` | datetime | ISO-8601 | `.updated_at` | — |

### 9.2 List 응답 (`GET /api/records`, `mode=tag`, `mode=fts`)

```json
{"items":[<RecordOut>...],"total":42,"limit":20,"offset":0}
```

### 9.3 `POST /api/ask` 응답

```json
{
  "interpreted_query": {
    "source": "llm" | "keyword",
    "explanation": "...",
    "data_type": "DOC", "year": 2026, "quality_score_gte": 80, ...
  },
  "results": [{
    "id":"...","data_type":"...","title":"...","summary":"...",
    "tags":[],"agents":[],"classification":"...","status":"...",
    "quality_score":85,"updated_at":"..."
  }],
  "total_matched": 7,
  "follow_up_queries": ["GET /api/records/{id} ...", "..."],
  "raw_query": "<원본 쿼리>"
}
```

### 9.4 Section diff (`/api/records/{id}/diff?from=`)

```json
{
  "from": "<id_a>", "to": "<id_b>",
  "meta_changes": {"title": ["old","new"], "tags": [["a"],["a","b"]]},
  "section_changes": [{
    "section_id": "S001",
    "kind": "modified" | "added" | "removed",
    "title_changes": ["old","new"] | null,
    "content_diff": "--- a/S001\n+++ b/S001\n@@ ...\n-old line\n+new line\n"
  }],
  "block_changes": "identical" | "summary"
}
```

### 9.5 AttachmentOut

```json
{
  "id":"DOC-HE-CAE-2026-0000000001-A001",
  "record_id":"DOC-HE-CAE-2026-0000000001",
  "number":1, "kind":"figure",
  "caption":"Stress curve of IGA NURBS shell",
  "file_name":"fig3.png",
  "file_path":"DOC-HE-CAE-2026-0000000001/A001.png",
  "mime_type":"image/png", "size_bytes":204800,
  "hash_sha256":"abcd...", "section_ref":"S003",
  "extra":{"page":7}, "created_at":"2026-01-15T..."
}
```

### 9.6 Discover payload (`GET /api/discover`)

```json
{
  "version":"1.0", "title":"Mobile eXperience AI Data Hub",
  "description":"사업부 문서·데이터 통합 허브 ...",
  "total_records":N,
  "by_data_type":{"DOC":x,"DATA":y},
  "by_division":{"HE":n},
  "by_classification":{"internal":n},
  "agents":[{"agent_type":"iga-analyst","name":"...","record_count":k,
            "common_tags":[],"data_types":[],"sample_query":"/api/data?agent=iga-analyst"}],
  "data_types_explained":{"DOC":"...","DATA":"...","SIM":"...","CAD":"...","LOG":"...","FORM":"...","OTHER":"..."},
  "starting_points":["GET /api/agents — ...", "POST /api/ask — ...", "..."],
  "schema_url":"/api/schema", "hints_url":"/api/hints",
  "llm_doc_url":"/api/docs/llm.txt", "ask_url":"/api/ask",
  "generated_at":"<ISO-8601>"
}
```

### 9.7 Job dict (`POST /api/jobs/embed`, `GET /api/jobs/{id}`)

```json
{"job_id":"<uuid>","kind":"embed","status":"pending|running|done|failed",
 "progress":0.0,"result":null,"error":null,"created_at":"...","updated_at":"..."}
```

---

## 10. 에러 처리 (Error handling)

표준 에러 응답:

```json
{"error":{"code":"<CODE>","message":"<msg>","details":{...},"request_id":"<uuid>"}}
```

| HTTP | code | 의미 | 회복 전략 |
|---|---|---|---|
| 400 | `BAD_REQUEST` | 잘못된 요청 (예: `mode=tag` without `tags`, diff 의 from==id) | `details.detail` 의 메시지로 파라미터 수정 |
| 401 | `AUTHENTICATION_ERROR` | 인증 실패 / 키 만료 / 폐기 | `X-API-Key` 재발급. bootstrap 필요한지 확인 |
| 403 | `AUTHORIZATION_ERROR` | 권한 부족 (hard delete 등) | bootstrap 키로 재시도 |
| 404 | `NOT_FOUND` | record / agent / job / key 없음 | id 재확인 — `GET /api/records?...` 으로 검색 |
| 405 | `METHOD_NOT_ALLOWED` | 잘못된 HTTP 메서드 | 표 §5 참조 |
| 409 | `CONFLICT` | 중복 (record id, agent_type, unique key 위반) | PATCH 로 변경, 또는 다른 seq |
| 413 | `PAYLOAD_TOO_LARGE` | 업로드 크기 초과 | `details.max_bytes` 미만으로 재시도 |
| 422 | `VALIDATION_ERROR` | Pydantic 검증 실패 | `details.errors[].loc` + `msg` 로 어떤 필드인지 식별 |
| 429 | `RATE_LIMIT` | 요청 과다 | 지수 백오프 후 재시도 |
| 500 | `INTERNAL_ERROR` | 서버 오류 | `details.type` 보고 + `request_id` 첨부 |
| 503 | `(http_error)` | semantic embedder 미준비 (예: openai 키 없음) | `mode=fts` 로 폴백 |

회복 패턴 예:

```python
resp = httpx.get(url, headers={"X-API-Key": key})
if resp.status_code == 401:
    # 키 재발급 필요
    new_key = issue_new_key(bootstrap_key)
    resp = httpx.get(url, headers={"X-API-Key": new_key})
elif resp.status_code == 503 and "semantic" in url:
    # semantic → fts 폴백
    fallback_url = url.replace("mode=semantic", "mode=fts")
    resp = httpx.get(fallback_url, headers={"X-API-Key": key})
elif resp.status_code == 409:
    # 중복 → PATCH 로 업데이트
    record_id = parse_id_from_url(url)
    resp = httpx.patch(f"{base}/api/records/{record_id}", json=patch, headers=...)
```

모든 응답에 `X-Request-ID` 헤더가 붙는다 — 디버그 시 첨부.

---

## 11. 페이지네이션 + 정렬 + 필터링 (Pagination, sort, filter)

| 엔드포인트 | limit 기본 | limit 최대 | offset | 기본 정렬 |
|---|---|---|---|---|
| `GET /api/records` | 20 | 100 | 0+ | `updated_at DESC, id DESC` |
| `GET /api/search?mode=tag` | 20 | 100 | 0+ | `updated_at DESC, id DESC` |
| `GET /api/search?mode=fts` | 20 | 100 | 0+ | section 매칭 우선, dedup 후 record fallback |
| `GET /api/search?mode=semantic` | 20 (= top_k) | 100 | n/a | cosine distance ASC |
| `GET /api/data` | 5 | 20 | n/a | relevance DESC (priority + hits) |
| `POST /api/ask` (limit) | 5 | 50 | n/a | `updated_at` DESC |
| `GET /api/attachments` | 50 | 500 | 0+ | `record_id ASC, number ASC` |
| `GET /api/analytics/common-tags`, `usage` | 20 | 100 | n/a | count DESC |

응답 모양:

```json
{"items":[...],"total":N,"limit":L,"offset":O}
```

마지막 페이지 판단: `offset + len(items) >= total`. 큰 결과셋은 1000 건 이상이면
태그/필터로 좁힌 뒤 페이지네이션을 권장한다.

`GET /api/records` 의 필터 조합 (모두 AND):

```
?data_type=DOC&team=HE&group=CAE&year=2026
&agent=iga-analyst&agent=cae-reporter           (overlap: 둘 중 하나라도 있으면)
&tag=IGA&tag=NURBS                              (contains: 모두 포함)
&q=tensile                                      (ILIKE on title + summary)
&include_deleted=false
&limit=20&offset=0
```

`agent` 와 `tag` 의 의미가 다르다 — agent 는 OR(overlap), tag 는 AND(contains).
혼동하기 쉬우니 주의.

---

## 12. 핵심 enum (전체)

### 12.1 `data_type` (7)

| 값 | 의미 |
|---|---|
| `DOC` | 문서·매뉴얼·보고서. sections + blocks 트리. 지원 source: docx/pdf/pptx/md/hwp |
| `DATA` | 측정·시험 표 데이터. headers + rows + units + notes |
| `SIM` | 시뮬레이션. solver + solver_version + inputs/outputs + runtime |
| `CAD` | 3D CAD 모델 메타. cad_type + file_format + components |
| `LOG` | 로그·시계열 (free-form) |
| `FORM` | 양식·체크리스트 (free-form) |
| `OTHER` | 기타 / 분류되지 않은 일반 레코드 |

### 12.2 `classification` (4)

`public` < `internal` (default) < `confidential` < `restricted`. UI 권한 게이팅의
주축.

### 12.3 `status` (4)

`draft` (default) → `review` → `approved` → `deprecated`.

- `draft`: 작성/임시. 보고서 초안.
- `review`: 검토 중. 일부 사용자에게만.
- `approved`: 공식 승인. 외부/대내 공유 가능.
- `deprecated`: 폐기. 검색에는 잡혀도 사용 자제.

### 12.4 `derivation` (4)

`original` (default), `extracted` (원본에서 추출, 예: PDF → DOC), `aggregated`
(여러 원본 합산), `translated` (언어 번역).

### 12.5 `access_pattern` (3)

`frequent` (캐시 강력 권장), `occasional` (default), `rare` (cold storage 후보).
캐싱 전략(§13)에 직결.

### 12.6 `language` (open enum)

- schema 노출: `ko`/`en`/`mixed`
- meta options: `ko`/`en`/`ja`/`zh`

### 12.7 `attachment.kind` (9)

| 값 | 대표 확장자 |
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

`sections`, `blocks`, `tables`, `figures`, `attachments`, `embeddings`, `rows`,
`headers`, `samples`, `files`, `components`, `inputs`, `outputs`. INSERT 시
backend 의 `compute_capabilities` 가 `content` 모양에서 산출 — 클라이언트 직접
지정 불필요.

### 12.9 `audit_log.action` (6)

`INSERT`, `UPDATE`, `DELETE`, `RESTORE`, `VIEW`, `ACCESS`. 직접 조회 엔드포인트
없음 — DB 만 기록.

### 12.10 `search.mode` (3)

`tag`, `fts`, `semantic`.

### 12.11 `find_related.mode` (4)

`tags`, `graph`, `semantic`, `auto` (셋 합치고 dedup).

### 12.12 Job kinds

현재 `embed` 1 종. 응답:
`{job_id, kind, status: pending|running|done|failed, progress, result, error}`.

### 12.13 DOC content shape

```json
{
  "meta": {},
  "toc": [{"id":"1","level":1,"title":"개요"}],
  "sections": [{
    "id":"1","level":1,"title":"개요",
    "blocks":[{"type":"paragraph","text":"..."}],
    "children":[...]
  }],
  "figures": [],
  "tables": [],
  "sources": [],
  "attachments": []
}
```

DATA: `{caption, headers, rows, units, notes}`
SIM: `{solver, solver_version, inputs, outputs, runtime}`
CAD: `{cad_type, file_format, file_metadata, components}`
LOG / FORM / OTHER: free-form.

---

## 13. 성능 / 캐싱 힌트 (Performance & caching)

`access_pattern` 필드는 캐싱 전략 결정의 핵심 신호다.

| `access_pattern` | 캐싱 전략 |
|---|---|
| `frequent` | 클라이언트 LRU 캐시 (TTL 5–10분). 임베딩 backfill 우선 |
| `occasional` (default) | TTL 60초 정도. on-demand 만 |
| `rare` | 캐시 안 함. 필요할 때만 fetch |

서버 측 캐시:

- `/api/discover` 는 **60초 in-process TTL** 캐시. `?no_cache=true` 로 bypass.
- `/api/meta/options` 는 응답에 `Cache-Control: public, max-age=300` 헤더.
  클라이언트는 5분 in-memory 캐시 권장.
- 그 외 엔드포인트는 캐시 없음 — 매 요청마다 SQL 실행.

임베딩 백필 비용:

- `EMBEDDING_PROVIDER=hash` (default): 외부 호출 없음, sha256(text) 시드 384
  차원 정규 분포. CPU 만 사용. **의미 신호 약함** → smoke / CI 용.
- `EMBEDDING_PROVIDER=openai`: `text-embedding-3-small`, `dimensions=384`. API
  호출 비용·레이턴시 발생. `OPENAI_API_KEY` 필요.
- `AUTO_EMBED_ON_INSERT=true` 면 INSERT/UPDATE 직후 자동 잡 등록. false (기본)
  면 `POST /api/jobs/embed` 로 수동 트리거.

쿼리 최적화 권장:

- `?capabilities=tables&data_type=DATA` 같은 더블 필터로 후보 좁히기.
- `agent[]` 와 `tag[]` 는 ARRAY 연산이라 PG 에서 GIN 인덱스 활용 (마이그레이션
  0003 에서 생성됨).
- semantic 검색은 ivfflat 인덱스 (마이그레이션 0004) 가 있어야 빠름. 인덱스
  없으면 풀 스캔.
- `read_count` / `last_accessed_at` 은 GET 시마다 fire-and-forget UPDATE
  (BackgroundTasks). 트랜잭션 안전하지만 best-effort — 실패해도 응답에는
  영향 없음.

---

## 14. AGENT 사용 패턴 — 도메인 전문가별

표준 5 에이전트와 그들이 자주 쓰는 record. seed 는 `api/seed/agents_data.py`
에 정의되어 있고, `/api/discover.agents[]` 와 `/api/agents` 에서 권위적으로
조회한다.

| `agent_type` | 한국어 이름 | 자주 다루는 record | 자주 쓰는 태그 | 다루는 data_type |
|---|---|---|---|---|
| `iga-analyst` | IGA 해석 분석가 | IGA 등기하해석, NURBS, LS-DYNA 입력, KooRemapper 변환 | IGA, NURBS, LS-DYNA, KooRemapper | DOC, SIM, DATA |
| `cae-reporter` | CAE 보고서 작성자 | 해석 결과 보고서, 그래프 / 표 동봉, 결과 기준 | 보고서, 해석, 결과, 기준 | DOC, SIM, DATA |
| `material-reviewer` | 재료 물성 검토자 | 재료 물성, 인장·압축 시험, 인증 기준 | 재료, 물성, 시험, 기준 | DOC, DATA |
| `process-checker` | 공정 절차 검증자 | 공정 절차, 체크리스트, 품질 기준 | 공정, 절차, 품질, 체크리스트 | DOC, FORM |
| `code-assistant` | 코드 어시스턴트 | KooRemapper 같은 사내 도구 코드 작업, API 참조 | 코드, API, KooRemapper, 변환기 | DOC |

각 에이전트의 시점별 권장 호출:

- `iga-analyst`: `query_data("iga-analyst", query="<keyword>", limit=10)` →
  결과 SIM 인 경우 `content.solver` 와 `content.inputs` 검사. DOC 인 경우
  `/api/records/{id}/sections` 로 본문 청크.
- `cae-reporter`: `query_data("cae-reporter", data_types=["SIM","DATA"])` 로
  최근 해석 결과 모은 뒤, `/api/records/{id}/figures` 로 그림, `/tables` 로
  표 추출.
- `material-reviewer`: `data_types=["DATA"]` 위주. headers/rows 의 단위
  (`content.units`) 와 `subject_keywords` 매칭이 중요.
- `process-checker`: `data_types=["FORM"]` + `capabilities=attachments` 조합.
  체크리스트 양식 추적.
- `code-assistant`: 거의 DOC. `query_examples` 와 `agent_hints` 가 코드 검색
  쿼리에 잘 활용된다.

여러 에이전트가 공유하는 record 를 찾으려면:

```
GET /api/analytics/cross-agent?agents=iga-analyst&agents=cae-reporter
```

단일 record 가 어떤 에이전트에서 노출되는지는 `record.agents[]` 배열을 보면
된다. 새 에이전트를 등록하려면 `POST /api/agents` 에 `AgentIn` 으로.

---

End of MEDIUM reference. SMALL 가이드는 더 응축된 형태,
LARGE 는 더 깊은 내부 구현 / migration 히스토리까지. 백엔드 구조는
`data_model.md`, `governance.md`, `mcp_integration_guide.md` 참조.

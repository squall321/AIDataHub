# AGENT_API_GUIDE_LARGE — AI Data Hub 깊이 있는 API 레퍼런스

> Frontier 모델 (Claude Opus, GPT-4/4o, Gemini 2.0 Pro 등 컨텍스트 200K+, 깊은 추론) 용 풀 레퍼런스.
> 이 한 문서가 백엔드 source 트리 전체를 대신한다 — 한 번 읽고 자율적으로 작업하라.
> 자매 문서: `AGENT_API_GUIDE_SMALL.md` (3-7B 빠른 참조), `AGENT_API_GUIDE_MEDIUM.md` (13-70B 균형). 본 LARGE 가이드는 그 중 가장 풍부하고 — "왜 이렇게 설계했는가" 를 함께 설명한다.

---

## 0. 이 문서를 어떻게 읽을 것인가

LARGE 모델은 한 컨텍스트에 8000-12000 단어를 흡수할 수 있다. 그러므로 본 가이드는 다음 두 가지를 동시에 추구한다.

1. **완전한 API 명세** — 모든 엔드포인트·파라미터·응답·에러 코드·enum 을 단일 위치에서 제공.
2. **설계 의도의 투명성** — 왜 7-key 스키마인가, 왜 group-shared id 인가, 왜 384-dim 임베딩인가, 왜 self-describing 한가. 단순 명세만 아는 에이전트는 함정에 빠진다 — 의도를 알면 회피 + 우회가 가능해진다.

읽는 순서 권장:

| 단계 | 섹션 | 목적 |
|---|---|---|
| 0 | §1 철학 | 무엇이 자기설명적·RAG 친화·agent-first 인가 |
| 1 | §2-§3 도메인·인증 | 어떤 데이터를 다루는가 + 어떻게 접속하는가 |
| 2 | §4-§5 데이터 모델·ID | 스키마와 ID 체계의 의미 |
| 3 | §6-§7 엔드포인트·검색 | 도구 카탈로그 + 다단계 검색 |
| 4 | §8-§9 MCP·워크플로우 | 실전 12개 패턴 |
| 5 | §10-§14 응답·에러·성능·enum·에이전트 협업 | 메타 평가와 운영 |
| 6 | §15-§16 디자인 결정·한계 | 의사결정 근거와 향후 |

각 섹션은 자족적(self-contained) 이지만 §1 의 7대 원칙은 후속 섹션의 모든 결정을 지배하므로 반드시 먼저 읽으라.

---

## 1. API 철학 + 설계 원칙

### 1.1 한 줄 요약

**AI Data Hub** 는 사업부 (Home Entertainment / EV / Powertrain / Data Analytics / Manufacturing / Vehicle Development 등) 의 문서·표·시뮬레이션·CAD·로그·양식을 단일 PostgreSQL `records` 테이블에 정규화 저장하고, REST + MCP 두 채널로 노출하는 RAG 친화 데이터 허브다. 현재 PoC 단계 (`contract_version=1.0`).

### 1.2 자기설명적 (self-describing) — 백엔드 코드를 읽지 않아도 동작

세 종류의 "자기 설명" 엔드포인트가 모든 정보를 노출한다.

| 엔드포인트 | 무엇을 알려주는가 | LARGE 모델의 활용 |
|---|---|---|
| `GET /api/discover` | 카탈로그 (총 record 수, data_type/team/classification 분포, 등록된 agent 목록 + 각 agent 의 record 수 + sample_query, starting_points URL 모음) | 첫 호출. 60초 in-memory 캐시 — `?no_cache=true` 로 신선도 강제. |
| `GET /api/schema` | JSON Schema (draft-2020-12). 모든 필드 + enum + oneOf (data_type 별 content 모양) | 정적·DB 비접근. 그대로 LLM tool-use 의 input schema 로 재활용 가능. |
| `GET /api/hints?context=<topic>` | 상황별 자연어 힌트 (`{hint, sample_endpoint, why_useful}` 트리플) | "어떤 토픽을 모를 때" 검색. 7개 컨텍스트: getting_started / searching / filtering_by_agent / tabular_data / time_bounded / attachments / cross_record_relations. |
| `GET /api/docs/llm.txt` | API 전체를 5-10KB 마크다운으로 압축 | LLM 컨텍스트에 한 번에 주입. |

**핵심 함의:** Frontier 모델은 처음 호출 시 `discover_schema` MCP 도구 (또는 `/api/discover` + `/api/schema` 2개) 만으로 전체 그림을 파악할 수 있다. 도메인을 모르면 `/api/hints?context=getting_started` 추가, 한 번에 다 보고 싶으면 `/api/docs/llm.txt` 한 번. 어느 경로든 작동한다.

### 1.3 RAG 친화 — 의미 단위 검색 + 출처 추적

데이터 적재 시 다음 7대 원칙이 일관 적용된다 (작성 표준 — `META_FORMAT_AUDIT.md` 와 정합).

| 원칙 | 적용 위치 | 검색에 미치는 영향 |
|---|---|---|
| **Heading Tree** | DOC `content.sections[]` 트리 (level 1-6) | section 단위 청크 → RAG 의 자연 단위. `record_sections` 테이블에 정규화. |
| **Semantic Chunk** | `record_sections.content_text` (한 섹션 = 한 청크) | pgvector embedding(384) 1:1. 너무 큰 섹션은 `large_section_warning` 으로 표시. |
| **Metadata** | `records.tags / agents / classification / domain / subject_keywords` | 검색 사전 필터 → 후보 좁히기 → 의미 검색. |
| **Claim-Evidence** | section 본문이 주장(claim) → 인접 figure/table 가 근거(evidence) | `figure_refs / table_refs` 로 cross-link. RAG 응답 시 "근거 figure 도 함께 인용". |
| **Entity** | `subject_keywords[]` (entity 배열, GIN index) | 용어 검색의 정확도 보강 ("NURBS", "IGA", "LS-DYNA" 등). |
| **Graph** | `parent_record_id` (self-FK), `related_record_ids[]` | 파생/번역/추출 트리 + 큐레이션 관계. `lineage` API. |
| **Version** | `version` (semver-ish), `derivation`, `parent_record_id`, `valid_from/until` | 같은 실험의 다른 버전 = 별도 record + parent 링크. `diff` API 로 변경 점 추적. |

LARGE 모델은 위 7원칙을 동시에 활용해야 한다. 단일 차원만 보면 — 예를 들어 tag 만 보면 — RAG 품질이 급격히 떨어진다. §7 (검색 전략) 에서 다단계 결합 패턴을 다룬다.

### 1.4 Agent-first — 도구 docstring 과 응답 키가 LLM 입력

MCP 도구의 docstring (`server.py`) 은 단순한 주석이 아니라 **LLM 의 도구 선택 입력**이다. 그래서:

- `discover_schema` docstring 첫 줄은 **"Call this FIRST."** — 도구 사용 순서를 강제.
- 모든 응답에 `follow_up_queries` 또는 `starting_points` 가 포함 — 다음 단계가 자명.
- 에러는 `{"error": "<code>", "detail": "...", ...}` 의 일관 dict — exception raise 안 함, LLM 이 텍스트로 즉시 인식.

이 디자인의 직접적 결과: **LARGE 모델은 "도구 시퀀스를 계획"하는 데 시간을 거의 쓰지 않는다.** 응답이 다음 도구를 가리키므로 자연스럽게 흐른다.

### 1.5 단일 진실 공급원 (Single Source of Truth)

| 진실 | 위치 |
|---|---|
| 데이터 형식 (record / section / attachment) | `src/api/db/models.py` (SQLAlchemy 2.0) |
| 입출력 모델 (Pydantic) | `src/api/schemas/common.py`, `routes/_schemas.py` |
| 메타 작성 표준 | `d:/Personal/AI_data/json_schema_rules.md` (룰 정의), `META_FORMAT_AUDIT.md` (변환기별 정합 감사) |
| API enum (data_type / classification / status / derivation / access_pattern) | `schemas/id_format.py` + `schemas/common.py` |
| MCP 도구 정의 | `src/mcp_server/server.py` |
| 마이그레이션 0001-0008 | `alembic/versions/` |

`/api/schema` 응답은 위 모든 enum 의 코드 정의와 sync — 코드가 단일 진실. 클라이언트는 hardcoded enum 을 가지지 말고 이 응답을 캐시하라.

---

## 2. 도메인 컨텍스트 — 이 허브가 다루는 데이터

### 2.1 팀 / 그룹 시드 (`api/seed/teams.py`)

| TEAM (팀) | 의미 | 주요 GROUP |
|---|---|---|
| HE | Home Entertainment (가전·디스플레이) | CAE, Test, Design |
| EV | Electric Vehicle (전기차) | BMS (Battery Management), Battery, Motor |
| PT | Powertrain (동력계) | Material, Process |
| DA | Data Analytics | AI, Data |
| MX | Manufacturing eXcellence | MFG, QA |
| VD | Vehicle Development | DEV, PLM |

이 시드는 실제 사업부와의 1:1 매핑이 아닌 **샘플 카탈로그**다 (운영 진입 시 확장). `/api/meta/options` 가 현재 등록된 team/group 목록을 반환한다 — hardcoded 하지 말고 이 응답을 받아라.

### 2.2 도메인별 데이터 특성

| 도메인 | 대표 data_type | 자주 등장하는 entity | 검색 시 주의 |
|---|---|---|---|
| **CAE / Simulation** | `SIM`, `DOC` | LS-DYNA, Abaqus, ANSYS, NASTRAN, IGA (Isogeometric Analysis), NURBS, FEM, mesh, solver | 같은 solver 가 여러 표기 (LS-DYNA / lsdyna / LSDYNA). `subject_keywords[]` 로 정규화. |
| **재료시험 / 측정** | `DATA` | tensile (인장), compression, fatigue (피로), stress-strain, modulus, yield, UTS, elongation | 단위(MPa, GPa, %) 가 `units` 키에 별도. row 값만 보고 단위 빠뜨리지 말 것. |
| **사업부 문서** | `DOC` | 업무 보고서, 가이드, 매뉴얼, 회의록, 기술문서, NDA, RFQ | `classification` 이 `internal/confidential/restricted` 다양 — 검색 결과 필터 필수. |
| **CAD 모델** | `CAD` | STEP, IGES, CATIA, SolidWorks, NX, Creo | `cad_type` (assembly/part/drawing) + `file_format` (확장자). 바이너리 자체는 첨부에 보관. |
| **강의자료 / 교육** | `DOC`, `OTHER` | curriculum, lecture, slide, exercise | PowerPoint 변환 시 슬라이드 → section 매핑. `doc_type=slide`. |
| **로그 / 시계열** | `LOG` | telemetry, sensor, time-series, event | content shape 자유. `valid_from/until` 로 시간 범위 표현. |
| **양식 / 체크리스트** | `FORM` | template, checklist, request form, approval | 자유 형식. 운영 적재가 가장 부족한 영역. |

### 2.3 약어 풀이 (frequently encountered)

| 약어 | 풀이 | 도메인 |
|---|---|---|
| IGA | Isogeometric Analysis (등기하 해석) | CAE |
| NURBS | Non-Uniform Rational B-Splines | CAE / CAD |
| FEM / FEA | Finite Element Method / Analysis | CAE |
| BMS | Battery Management System | EV |
| UTS | Ultimate Tensile Strength | 재료 |
| RFQ | Request For Quotation | 사업 |
| PLM | Product Lifecycle Management | 시스템 |
| BOM | Bill Of Materials | 제조 |
| HE / EV / PT / DA / MX / VD | (위 §2.1 참조) | 팀 코드 |

LARGE 모델은 이 도메인 지식을 활용해 **사용자가 약어로 질문할 때 풀이로 검색하거나, 거꾸로 도메인 표현으로 질의할 때 약어 form 도 함께 검색**할 수 있어야 한다. 예: "isogeometric" 으로 질문 → IGA 키워드도 추가.

---

## 3. 인증 + 보안 모델

### 3.1 X-API-Key — 단일 헤더, 두 가지 모드

| 항목 | 값 |
|---|---|
| 헤더 | `X-API-Key: <plaintext>` |
| 의존성 | `require_api_key` (FastAPI Depends, `auth.py`) |
| 기본 모드 | `AUTH_REQUIRED=false` (dev) — 헤더 없어도 호출 통과 (principal=anonymous) |
| Strict 모드 | `AUTH_REQUIRED=true` — 모든 호출 헤더 필수, 미존재/무효 시 401 |
| Bootstrap 키 | env `BOOTSTRAP_API_KEY` 로 1개 — `POST /api/auth/keys` 발급, `?hard=true` 삭제, key revoke 등 권한 작업에 사용 |
| 해시 | SHA-256. plaintext 는 발급 직후 한 번만 응답에 포함 (DB 미저장). |
| 활성 조회 | 부분 인덱스 `idx_api_keys_hash WHERE NOT revoked` (마이그레이션 0005). |

### 3.2 Scopes (`agent_scopes`)

`api_keys.agent_scopes text[]` 배열로 이 키가 어떤 agent_type 들을 다룰 수 있는지 제한. 빈 배열 = 모든 agent 허용 (현재 PoC 정책). 운영 진입 시 정책 enforcement 추가 예정.

### 3.3 Classification → 검색 노출 정책

`records.classification` 4단계 (오름차순 민감):

| 값 | 의미 | 검색 노출 정책 (현재 PoC) |
|---|---|---|
| `public` | 외부 공개 가능 | 모든 키에 노출 |
| `internal` (default) | 사내 일반 | 모든 키에 노출 (PoC) |
| `confidential` | 기밀 (NDA / 사업부 내부) | 모든 키에 노출 (PoC), 운영 시 scope 필요 |
| `restricted` | 극비 (소수 인원) | 모든 키에 노출 (PoC), 운영 시 scope 필요 |

**현재 PoC 한계:** classification 기반 자동 필터링은 미구현. 클라이언트가 응답 후 `meta.classification` 을 직접 확인해야 한다. 응답 활용 시 LARGE 모델은 항상 `classification` 을 메타 평가 차원에 포함하라 (§10).

### 3.4 키 발급 흐름

```
1. 운영자: env BOOTSTRAP_API_KEY=<bootstrap_secret> 설정.
2. POST /api/auth/keys
   header: X-API-Key: <bootstrap_secret>
   body:   {"name": "iga-group", "agent_scopes": ["iga-analyst"], "department": "EV/CAE", "expires_at": "2026-12-31T00:00:00Z"}
   resp:   {"id": 7, "name": "iga-group", "key": "<plaintext_returned_once>", ...}
3. 발급된 key 를 안전한 저장소(SecretStorage 등) 에 보관 — 다시 못 본다.
4. 일반 호출 시 X-API-Key: <plaintext_returned_once>
5. 검증: POST /api/auth/keys/verify (헤더만 보내면 200 + {ok, key_name, agent_scopes})
6. 폐기: DELETE /api/auth/keys/{id} (bootstrap 필요).
```

### 3.5 401/403 응답 구조

```json
{"error":{"code":"AUTHENTICATION_ERROR","message":"missing or invalid X-API-Key","details":{},"request_id":"<uuid>"}}
{"error":{"code":"AUTHORIZATION_ERROR","message":"hard delete requires bootstrap API key","details":{},"request_id":"<uuid>"}}
```

`X-Request-ID` 헤더는 모든 응답에 포함 — 디버깅 시 서버 로그와 cross-reference.

---

## 4. 데이터 모델 (full)

### 4.1 `records` — 모든 컬럼 (PostgreSQL)

| 컬럼 | 타입 | NULL | 의미 | 인덱스 |
|---|---|---|---|---|
| `id` | str(80) PK | no | 사람이 읽는 코드 (`DOC-HE-CAE-2026-000001`) | PK |
| `data_type` | str(20) | no | enum (§13) | `idx_records_type` |
| `team` | str(10) | no | 팀 코드 | `idx_records_div_team` |
| `group` | str(20) | no | 그룹 코드 | (위 복합) |
| `year` | smallint | no | 4자리 연도 | `idx_records_year` |
| `seq` | int | no | (data_type, team, group, year) 단위 시퀀스 | (uniq) |
| `title` | text | no | 제목 | — |
| `summary` | text | no | 요약 (기본 "") | — |
| `tags` | text[] | no | 태그 배열 (자유 키워드) | GIN `idx_records_tags` |
| `agents` | text[] | no | 사용 가능한 agent_type 배열 | GIN `idx_records_agents` |
| `schema_version` | str(10) | no | 기본 `"1.0"` | — |
| `content` | jsonb | no | data_type 별 페이로드 dict | GIN `idx_records_content` (jsonb_path_ops) |
| `content_hash` | str(64) | yes | SHA-256 of content (멱등성) | — |
| `source_file` | text | yes | 원본 파일명 | — |
| `has_attachments` | bool | no | 기본 false | — |
| `attachment_count` | int | no | 기본 0 | — |
| `author` | str(100) | no | 기본 "" | — |
| `department` | str(100) | no | 기본 "" (보통 "{div}-{group}") | — |
| `project` | str(100) | yes | 프로젝트명 | — |
| `version` | str(20) | no | 기본 `"1.0"` (semver-ish) | — |
| `classification` | str(20) | no | enum, 기본 `internal` | `idx_records_classification` |
| `status` | str(20) | no | enum, 기본 `draft` | `idx_records_status` |
| `domain` | str(100) | yes | 도메인 자유 문자열 | `idx_records_domain` |
| `subject_keywords` | text[] | no | 주제 키워드 배열 (entity) | GIN `idx_records_subject` |
| `source_system` | str(50) | yes | 출처 시스템 (`PLM`, `Confluence` 등) | — |
| `language` | str(10) | no | 기본 `ko`. enum: ko/en/mixed/ja/zh | — |
| `parent_record_id` | str(80) FK | yes | self-FK to `records.id` (파생 트리) | `idx_records_parent` |
| `derivation` | str(20) | no | enum, 기본 `original` | — |
| `capabilities` | text[] | no | 자동 산출 라벨 (§13.7) | GIN `idx_records_capabilities` |
| `quality_score` | smallint | yes | 0..100 | — |
| `valid_from` | date | yes | 유효 시작일 | — |
| `valid_until` | date | yes | 유효 종료일 | — |
| `agent_hints` | text | yes | 에이전트용 자유 힌트 텍스트 | — |
| `related_record_ids` | text[] | no | 수동 큐레이션 관계 ID | GIN `idx_records_related` |
| `query_examples` | text[] | no | 자연어 쿼리 예시 | — |
| `access_pattern` | str(20) | no | enum, 기본 `occasional` | `idx_records_access_pattern` |
| `deleted_at` | timestamptz | yes | soft-delete 시각 (NULL = 활성) | — |
| `read_count` | int | no | 조회수, 기본 0 | — |
| `last_accessed_at` | timestamptz | yes | 마지막 조회 시각 | — |
| `created_at` | timestamptz | no | 생성 시각 | — |
| `updated_at` | timestamptz | no | 갱신 시각 (auto onupdate) | — |

Unique: `(data_type, team, group, year, seq)` (자연 키 — 마이그레이션 0001 + 0006 진화). PK 는 `id` 문자열.

### 4.2 `record_sections` — RAG 청크 + 임베딩

| 컬럼 | 타입 | NULL | 의미 |
|---|---|---|---|
| `id` | bigserial PK | no | 내부 PK |
| `record_id` | str(80) FK | no | 부모 record.id (CASCADE) |
| `section_id` | str(20) | no | 섹션 식별자 (`"S001"`, `"3.1"`) |
| `level` | smallint | no | 헤딩 레벨 (1=H1) |
| `title` | text | no | 섹션 제목 |
| `content_text` | text | no | 본문 평문 (figure caption 포함, 표는 별도 record) |
| `figure_refs` | text[] | no | 이 섹션이 참조하는 figure id 배열 |
| `table_refs` | text[] | no | 이 섹션이 참조하는 table record id 배열 |
| `embedding` | vector(384) | yes | pgvector — 백필 후 채워짐 |
| `embedded_at` | timestamptz | yes | 임베딩 산출 시각 |
| `embedding_model` | str(100) | yes | `"all-MiniLM-L6-v2"` 등 |

Unique: `(record_id, section_id)`. CASCADE on parent delete.

**임베딩 백필 패턴:** 새 record INSERT 시 환경변수 `AUTO_EMBED_ON_INSERT=true` 면 자동 큐 등록. 수동: `POST /api/jobs/embed {"record_id": "..."}` 또는 `{"record_ids": [...]}`. 백필 안 된 레코드는 `embedding IS NULL` — semantic_search 에서 자동 제외 (§7.3 신뢰도 평가에 영향).

### 4.3 `record_attachments` — 파일 첨부

| 컬럼 | 타입 | NULL | 의미 |
|---|---|---|---|
| `id` | str(80) PK | no | `{record_id}-A{nnn}` (예: `DOC-HE-CAE-2026-000001-A001`) |
| `record_id` | str(80) FK | no | 부모 |
| `number` | int | no | 1부터 시작 |
| `kind` | str(20) | no | enum 9종 (figure/document/spreadsheet/media/archive/cad/drawing/data/other) |
| `caption` | text | no | **필수** — 누락 시 placeholder `"(캡션 누락 — 검수 필요)"` |
| `file_name` | text | yes | 원본 파일명 |
| `file_path` | text | yes | `attachments_dir` 기준 상대 경로 |
| `mime_type` | str(100) | yes | MIME |
| `size_bytes` | bigint | yes | 바이트 |
| `hash_sha256` | str(64) | yes | 파일 해시 |
| `section_ref` | str(20) | yes | 어느 섹션에서 참조됐는가 |
| `extra` | jsonb | no | 자유 메타 dict (`{"page": 7, "alt_text": "..."}`) |
| `created_at` | timestamptz | no | — |

바이너리 위치: `<attachments_dir>/{record_id}/A{nnn}.{ext}`. HTTP 정적 마운트: `/attachments/{record_id}/A{nnn}.{ext}`. Figure binary 도 `/figures/{record_id}/F{nnn}.{ext}` 로 별도 마운트.

**캡션 의무 — 왜?** RAG 검색에서 figure caption 은 그림의 의미 단위. 누락 시 검색 recall 직격타 → 적재 단계에서 placeholder 라도 채우게 강제 + 변환기 audit 에서 경고. LARGE 모델은 응답에서 placeholder caption 을 발견하면 사용자에게 "검수 필요" 로 노출.

### 4.4 `agents`, `agent_records` — 에이전트 메타 + N:M

`agents (agent_type PK, name, description, common_tags[], data_types[], created_at)`
`agent_records (agent_type FK, record_id FK, priority smallint default 1, PK 복합)`

priority 1-5 권장. `priority * 0.7 + hits * 0.1 + 0.05` 가 `/api/data` 의 relevance score 공식 (`search_svc._score`). 큐레이터가 강조하고 싶은 record 는 priority=5 로 핀.

### 4.5 `audit_log` — 거버넌스 (마이그레이션 0008)

`(id bigserial, record_id str?, actor str?, action str, field_changes jsonb {field: [old, new]}, request_id str?, created_at timestamptz)`

action enum: `INSERT / UPDATE / DELETE / RESTORE / VIEW / ACCESS`.

VIEW 이벤트는 모든 `GET /api/records/{id}` 호출마다 best-effort 로 기록. UPDATE 는 변경된 필드만 `field_changes` 에 차이 (`compute_diff` 헬퍼). 현재 audit_log 자체를 외부에 노출하는 엔드포인트는 없음 — DBA 가 직접 SELECT.

### 4.6 `api_keys` — 인증 키

(§3 참조). `(id, key_hash, name, agent_scopes[], department?, created_at, expires_at?, revoked, last_used_at?)`. `key_hash` SHA-256, plaintext 미저장.

### 4.7 Cross-reference 패턴 (모든 테이블 간 join)

```
records ─┬── record_sections (1:N, RAG 청크)
         ├── record_attachments (1:N, 파일)
         ├── agent_records (N:M with agents, priority)
         └── records (self-FK via parent_record_id, 파생 트리)

records.related_record_ids[] (graph traversal, no FK)
records.tags[]              (sharing → tag overlap = related)
records.subject_keywords[]  (entity overlap)
records.agents[]            (record.agents[] ⊆ agent_records 의 agent_type 집합)
```

LARGE 모델이 자주 쓰는 join 시나리오:

- "이 record 와 같은 figure 를 인용하는 record": section.figure_refs 검색 (현재 직접 API 없음 — 클라이언트 join 필요).
- "이 agent 가 사용하는 모든 record + record 의 figure caption": `/api/agents/{type}/records` → 각 record 의 `/attachments?kind=figure`.
- "한 사업부의 모든 approved 문서 + sections": `/api/records?team=HE&status=approved` → 각 record 의 `/sections`.

---

## 5. ID 시스템 — 왜 이렇게 디자인됐는가

### 5.1 정식 패턴

```
{DATA_TYPE}-{TEAM}-{GROUP}-{YEAR}-{SEQ}
예: DOC-HE-CAE-2026-000001
```

| 토큰 | 규칙 | 이유 |
|---|---|---|
| DATA_TYPE | enum (DOC/DATA/SIM/CAD/LOG/FORM/OTHER) | ID 만 보고 변종을 즉시 파악 |
| TEAM | 2-4 uppercase ASCII | 팀 식별, 그룹명 충돌 방지 |
| GROUP | 2-5 uppercase ASCII | 그룹 식별 |
| YEAR | 4 digits 2020-2099 | 연도별 슬라이스 즉시 가능 |
| SEQ | 6 digits zero-padded (000001-999999) | 자연 정렬 + 100만 record/year/group 가능 |

### 5.2 "왜 사람이 읽는 ID 인가" — UUID 가 아닌 이유

1. **로그/디버그 가독성** — `DOC-HE-CAE-2026-000001` 은 봐도 알지만 UUID 는 모른다.
2. **분류 키가 ID 안에 내장** — ID 만 보고 team/group/year 즉시 추출. ETL/리포팅 단순화.
3. **자연 정렬** — 단순 문자열 정렬이 의미적 정렬과 거의 일치.
4. **자기설명** — 외부 시스템(PLM 등) 과 cross-reference 시 ID 가 의미를 전달.

**트레이드오프:** ID 충돌 시 사람이 직접 다음 seq 결정. `seq=0` 으로 ingest 하면 backend 가 `MAX(seq)+1` 자동 할당 (`services/seq.next_seq`).

### 5.3 그룹 단위 동일 ID — `data_id`, `figure_id`, `table_id`

DOC content 안의 cross-reference 식별자는 다음 규칙을 따른다 (`json_schema_rules.md` 5장).

| 식별자 | 형태 | 의미 |
|---|---|---|
| `doc_id` | `{TEAM}-{GROUP}-{YEAR}-{SEQ}` (data_type prefix 없는 구버전) | DOC content 내부 self-reference. legacy. normalizer 가 수신 시 `DOC-` prefix 보강. |
| `data_id` | DATA record 의 ID (DOC 안에서 표 참조) | DOC 본문이 DATA-... ID 로 표 인용 |
| `figure_id` | `F{nnn}` 또는 `{record_id}-A{nnn}` | section 내부 단순 figure 참조는 F001 로컬, attachment 는 글로벌 ID |
| `table_id` | DATA record id 또는 DOC 내부 `T{nnn}` | DOC 안의 작은 표는 로컬, 큰 측정 표는 별도 DATA record 분리 |
| `attachment_id` | `{record_id}-A{nnn:03d}` | 첨부 글로벌 ID. 레거시 `-F{nnn}` 도 허용. |

**핵심 설계 의도 — "같은 H1 시리즈는 같은 doc_id":** 한 문서의 모든 섹션은 동일한 doc_id 를 공유한다. DOC content 안의 section[0..N] 은 모두 부모 record 의 ID 를 가리킨다. **이유:**

1. RAG 검색이 section 청크를 반환 → 클라이언트가 section_id + record_id 만으로 부모 문서를 즉시 fetch.
2. cross-document 검색에서 record 단위 dedup 가능 (한 문서의 여러 청크 hit 을 묶어서 보여줌).
3. 변경 추적 시 section 변경이 record 변경으로 자연스럽게 roll up.

**별도 DATA record 분리 기준:** 표가 (a) 단독으로 의미가 있거나, (b) row 수 > 50 이거나, (c) 측정 단위가 명확하면 별도 DATA record. 그 외 작은 in-line 표는 DOC content 안에 inline. 이 분리가 RAG 검색의 효율을 결정 — 측정 데이터는 표 단위 검색이 더 자연스럽기 때문.

### 5.4 Legacy ID — `{TEAM}-{GROUP}-{YEAR}-{SEQ}` (data_type 누락)

`parse_id()` 가 정식·레거시 둘 다 허용. 레거시 수신 시 `data_type="DOC"` 기본값 + `normalize_id()` 가 prefix 추가. 외부 시스템 (PLM, Confluence) 에서 가져온 데이터를 점진 마이그레이션할 때 유용.

---

## 6. 모든 엔드포인트 카탈로그

### 6.1 시스템 / 자기설명

| 메서드 | 경로 | 응답 |
|---|---|---|
| GET | `/` | `{service, version, status}` 식별자 |
| GET | `/health` | `{status: "ok"}` minimal liveness |
| GET | `/api/system/health` | `{status, version, auth_required, build}` |
| GET | `/api/discover` | 카탈로그 (60초 캐시, `?no_cache=true` 우회). §1.2 참조. |
| GET | `/api/schema` | JSON Schema (정적, draft-2020-12) |
| GET | `/api/hints?context=<topic>` | 자연어 힌트. context 생략 시 전체. |
| GET | `/api/docs/llm.txt` | text/plain 통합 마크다운 (5-10KB) |
| GET | `/metrics` | Prometheus text (only `ENABLE_METRICS=true`) |

### 6.2 자연어 검색

| 메서드 | 경로 | body / params |
|---|---|---|
| POST | `/api/ask` | `{"query": "...", "limit": 5}`. 응답: `{interpreted_query, results, total_matched, follow_up_queries, raw_query}`. LLM 키 있으면 source="llm", 없으면 키워드 폴백 source="keyword". |

### 6.3 records CRUD + 거버넌스

| 메서드 | 경로 | 용도 |
|---|---|---|
| GET | `/api/records` | 목록 + 필터 (`data_type, team, group, year, agent[], tag[], q, include_deleted, limit, offset`) |
| GET | `/api/records/{id}` | 단건 + VIEW audit + read_count++ |
| POST | `/api/records` | 직접 INSERT. 409 = 중복. |
| PATCH | `/api/records/{id}` | 부분 수정 (summary/tags/agents/project/version). |
| DELETE | `/api/records/{id}` | soft delete (`?hard=true` = 물리, bootstrap 필요) |
| POST | `/api/records/{id}/restore` | soft-delete 복원 |
| GET | `/api/records/{id}/lineage` | 조상 + 자손 BFS |
| GET | `/api/records/{id}/diff?from=<other>` | 두 record diff (meta + section unified diff) |

### 6.4 첨부

| 메서드 | 경로 | 용도 |
|---|---|---|
| GET | `/api/records/{id}/attachments?kind=` | record 의 첨부 목록 |
| GET | `/api/records/{id}/attachments/{att_id}` | 첨부 단건 |
| GET | `/api/attachments?kind=&record_id=&limit=&offset=` | 전역 첨부 검색 |
| GET (정적) | `/attachments/{rid}/A{nnn}.{ext}` | 바이너리 |
| GET (정적) | `/figures/{rid}/F{nnn}.{ext}` | figure 바이너리 |

### 6.5 검색

| 메서드 | 경로 | 파라미터 |
|---|---|---|
| GET | `/api/search?mode=tag&tags=A&tags=B` | AND 매칭, `limit/offset` (default 20/0, max 100) |
| GET | `/api/search?mode=fts&q=...` | 전문 검색 (PG: tsvector simple, SQLite: ILIKE), `limit/offset` |
| GET | `/api/search?mode=semantic&q=...` | pgvector cosine, `limit` (= top_k, max 100) |
| GET | `/api/data?agent=X&query=...&data_types=...&limit=` | agent-scoped (priority * matches relevance) |

### 6.6 에이전트

| 메서드 | 경로 | 용도 |
|---|---|---|
| GET | `/api/agents` | 목록 |
| GET | `/api/agents/{type}` | 단건 |
| GET | `/api/agents/{type}/records` | 해당 agent 의 모든 record |
| POST | `/api/agents` | 생성 (`AgentIn`) |
| PATCH | `/api/agents/{type}` | 수정 |
| DELETE | `/api/agents/{type}` | 삭제 (CASCADE on agent_records) |

### 6.7 분석

| 메서드 | 경로 | 응답 |
|---|---|---|
| GET | `/api/analytics/distribution` | counts (by_data_type/team/group/year) |
| GET | `/api/analytics/common-tags?agent=X&limit=20` | agent 별 상위 태그 |
| GET | `/api/analytics/cross-agent?agents=A&agents=B` | 두 agent 모두 사용 record 집합 |
| GET | `/api/analytics/timeline?year=2026` | 월별 카운트 12행 |
| GET | `/api/analytics/usage?limit=20` | read_count 상위 record |

### 6.8 메타 / 옵션

| 메서드 | 경로 | 응답 |
|---|---|---|
| GET | `/api/meta/options` | UI 셀렉트박스 (versions, teams, groups, agents, classifications, statuses, derivations, languages, data_types, supported_extensions, max_upload_mb, allow_custom) |

### 6.8.1 Taxonomy — 어휘 발견 / 동의어 매핑 (작은 모델 친화)

낮은 수준 AI 가 시스템 어휘를 한 번에 파악하고 비공식 표현을 정식 태그로 매핑할 수 있게 하는 read-only 엔드포인트군. 인증 면제.

| 메서드 | 경로 | 응답 (top-level) |
|---|---|---|
| GET | `/api/taxonomy/tags?q=&min_count=&limit=` | `total, items[{tag,count,data_types,agents}]` — 태그 + 빈도 + data_type 분포 + agent 매핑 |
| GET | `/api/taxonomy/tags/resolve?q=<expr>&limit=` | `query, normalized, candidates[{tag,score,method,count}]` — exact / synonym / prefix / substring 4단계 매칭 |
| GET | `/api/taxonomy/data-types` | `items[{data_type,count,description,subtypes,schema_url,sample_query}]` — data_type 분포 + 추천 사용 패턴 |
| GET | `/api/taxonomy/domains` | `items[{domain,count}]` — domain 필드 분포 (CAE/lecture/test/simulation 등 자동 집계) |
| GET | `/api/taxonomy/agents` | `items[{agent_type,name,record_count,common_tags(top5),data_types}]` — agent 분포 통계 (메타는 `/api/agents` 참조) |
| GET | `/api/taxonomy/classification` | `field, items[{value,description,count}]` — classification enum + 의미 + 분포 |
| GET | `/api/taxonomy/status` | `field, items[{value,description,count}]` — status enum + 의미 + 분포 |
| GET | `/api/taxonomy/access-pattern` | `field, items[{value,description,count}]` — access_pattern enum + 의미 + 분포 |

`tags/resolve` 의 `method` 값은 `exact`(score 1.0) / `synonym`(0.85, 사전 매칭) / `prefix`(0.7) / `substring`(0.55) 순. 후보가 비면 다음 단계로 넘어가지 말고 사용자에게 다른 표현을 요청하라.

### 6.9 변환 + 적재

| 메서드 | 경로 | 폼 |
|---|---|---|
| POST | `/api/convert/` | `file, team, group, year, seq, tags, agents, classification, domain` — 변환만 |
| POST | `/api/convert/ingest` | 위 + `status, language, subject_keywords, derivation, quality_score, valid_from, valid_until, title_override, summary_override, agent_hints, related_record_ids, query_examples, access_pattern, persist_attachments` — 변환 + DB |

`seq=0` (또는 음수) → backend 가 `(data_type, team, group, year)` 단위 `MAX(seq)+1` 자동 할당. data_type 은 확장자에서 추정 (DOCX/PPTX/MD/PDF→DOC, XLSX→DATA).

### 6.10 잡 (백필)

| 메서드 | 경로 | 용도 |
|---|---|---|
| POST | `/api/jobs/embed` | `{"record_id": "..."}` or `{"record_ids": [...]}`. 202 + job dict. |
| GET | `/api/jobs/{job_id}` | `{job_id, kind, status: pending|running|done|failed, progress, result, error}` |
| GET | `/api/jobs?kind=embed&limit=` | 잡 목록 |

### 6.11 인증

| 메서드 | 경로 | 권한 |
|---|---|---|
| POST | `/api/auth/keys` | bootstrap 만 |
| GET | `/api/auth/keys` | bootstrap 만 |
| POST | `/api/auth/keys/verify` | 모든 키 |
| DELETE | `/api/auth/keys/{key_id}` | bootstrap 만 |

### 6.12 사용 예시 (Python httpx + curl)

curl 자연어 질의:
```bash
curl -X POST http://localhost:8000/api/ask \
  -H "X-API-Key: <key>" -H "Content-Type: application/json" \
  -d '{"query":"2026년 IGA NURBS 인장 시뮬 quality 80 이상","limit":5}'
```

Python httpx — 다단계 검색 + record 풀 fetch:
```python
import httpx
api = httpx.Client(base_url="http://localhost:8000",
                   headers={"X-API-Key": KEY}, timeout=30)

# 1) ask
r = api.post("/api/ask", json={"query": "최근 IGA 시뮬", "limit": 5}).json()
hits = r["results"]
if not hits:
    # 2) semantic fallback
    r2 = api.get("/api/search", params={"mode": "semantic", "q": "isogeometric simulation"}).json()
    hits = r2.get("items", [])

# 3) 각 hit 의 풀 record
for h in hits:
    full = api.get(f"/api/records/{h['record_id'] if 'record_id' in h else h['id']}").json()
    print(full["title"], full["classification"], full["quality_score"])
```

---

## 7. 검색 전략 (고급)

### 7.1 5가지 모드 — 강점·약점·비용

| 모드 | 강점 | 약점 | 비용 | 언제 |
|---|---|---|---|---|
| `POST /api/ask` | 자연어 해석 + follow-up + interpreted_query 노출 | LLM 키 없으면 키워드 폴백, GPT-4o-mini 호출당 ~50ms + token | 中 (LLM 호출 또는 키워드 무료) | 사용자가 평문 질문, 의도 불명확 |
| `mode=semantic` | 동의어/재구성에 강함 (cosine) | embedding 백필 필수, 한국어 형태소 모델 의존, score 절대값 비교 어려움 | 中 (pgvector index + embedder) | 질의어가 본문 단어와 다를 때 |
| `mode=fts` | 정확한 단어 매칭, 빠름, 항상 동작 | 동의어 약함, 한국어 stemming 약함 | 低 (PG `to_tsvector('simple')` 또는 ILIKE) | 정확한 entity 검색 |
| `mode=tag` | 카테고리 정확, AND 조합 가능 | 태그 사전 필요, 미존재 태그면 0 hit | 极低 (GIN index) | 사용자가 태그를 알 때, 사전 필터로 |
| `GET /api/records?q=` | 가장 단순한 ILIKE | recall 매우 낮음, 본문 미검색 | 低 (title+summary 만) | 마지막 폴백, 단순 제목 검색 |

### 7.2 다단계 검색 — semantic → fts → tag → keyword

LARGE 모델 권장 의사결정 트리:

```
사용자 질의 q
   │
   ├─ q 가 명확한 entity 단어 (LS-DYNA, IGA 등)?
   │     YES → mode=fts 단독 (빠르고 정확)
   │
   ├─ q 가 자연어 문장?
   │     YES → POST /api/ask
   │           │
   │           ├─ results 충분 (>= limit*0.7)?
   │           │     YES → 종료
   │           │     NO  → mode=semantic 보강
   │           │
   │           └─ semantic 도 부족?
   │                 → interpreted_query.tags 로 mode=tag 보강
   │
   └─ q 가 다중 카테고리?
         YES → mode=tag (AND), zero hit 시 OR 로 완화
```

### 7.3 Score normalization — 다단계 결과 합치기

mode 마다 score 정의가 다르다. LARGE 모델은 **mode 별 정규화 후 합산**해야 한다.

| 모드 | score 정의 | 정규화 |
|---|---|---|
| `semantic` | cosine sim ∈ [0, 1] (PG: `1 - distance/2`, SQLite: `(sim+1)/2`) | 그대로 사용 |
| `fts` | snippet match 횟수 (응답 키 없음) | 클라이언트가 `q.lower() in snippet.lower()` count 후 `min(count*0.2, 1.0)` |
| `tag` | 매칭 태그 수 / 요청 태그 수 (응답 키 없음) | `len(matched_tags) / len(requested_tags)` |
| `/api/data` | `priority/5 * 0.7 + min(hits*0.1, 0.5) + 0.05` ∈ [0.05, 1.0] | 그대로 |
| `ask` (results) | 없음 — `updated_at desc` 정렬만 | 0.5 fallback |

권장 결합 공식 (LARGE 가 자체 ranker):
```
final_score = 0.5*semantic + 0.3*fts + 0.2*tag
            + 0.1*(quality_score/100 if quality_score else 0.5)
            + 0.1*(1.0 if status=='approved' else 0.5)
            - 0.2*(1.0 if classification in {'confidential','restricted'} else 0)
```

### 7.4 결과 신뢰도 평가 — 메타 종합

응답을 받으면 LARGE 모델은 다음 차원으로 신뢰도를 자체 평가하라.

| 차원 | 평가 키 | 해석 |
|---|---|---|
| **검색 품질** | score (semantic 0.7+ 신뢰), snippet match 횟수, hit 개수 | 0.7+ = 정확, 0.4-0.7 = 가능성, < 0.4 = 의심 |
| **데이터 품질** | quality_score (0-100), status (approved > review > draft > deprecated) | 큐레이터의 명시적 신뢰도 |
| **출처 명확성** | source_file, source_system, author, department | 출처 없으면 사용자에게 cross-check 권유 |
| **버전 정합성** | version, derivation, parent_record_id, valid_from/until | 옛 버전이면 최신 버전 추천 (lineage) |
| **민감도** | classification (`internal/confidential/restricted`) | 사용자 노출 가능 여부 판단 |
| **활성 상태** | deleted_at (NULL = 활성), deprecated 상태 | 죽은 데이터 노출 방지 |
| **사용 빈도** | read_count, last_accessed_at, access_pattern | frequent 한 record 가 일반적으로 신뢰성 高 |

**메타 평가 워크플로우 예:** 사용자가 "IGA 시뮬 quality 80 이상" 질의 → ask 응답 → 각 result 에 대해:
1. quality_score >= 80 확인
2. status == "approved" 인 것만 우선 인용
3. classification 이 internal/public 인 것만 사용자에게 노출
4. version == 최신 인지 lineage 로 확인 → 옛 버전이면 자손에서 더 최신 추천

### 7.5 한국어 형태소 + 영문 혼재

PostgreSQL FTS 는 `to_tsvector('simple', ...)` — 형태소 분석 없음. 단순 토큰화. 한국어 조사 ("을/를/이/가") 가 매칭에 직접 포함됨 → 검색어에 조사 포함 시 hit miss. **권장:** `q` 입력은 명사 stem 위주로 (조사 제거).

영문 + 한글 혼재 (예: "LS-DYNA 결과 보고서") 는 `mode=fts` 가 단어 분할로 잘 잡지만, 하이픈은 token break 가 됨 → "LS-DYNA" 가 "LS" + "DYNA" 두 토큰으로 분리. **회피:** 하이픈 변종 ("LS DYNA", "LSDYNA") 도 함께 시도하거나 mode=semantic 으로 우회.

### 7.6 zero-hit 회복 패턴

```
1차: mode=fts (정확)
2차: mode=semantic (의미)
3차: mode=tag (사용자 태그 추정)
4차: query 단순화 (문장 → 키워드)
5차: GET /api/records?team=X&data_type=Y (광역 슬라이스)
6차: GET /api/discover.starting_points (사용자에게 시작점 제안)
```

각 단계 실패 시 다음으로 자동 전이. 5/6 단계는 사용자 재질의 유도.

### 7.7 의미 그룹 라우터 (Semantic Groups — NEW)

#### 7.7.1 디자인 의도

기존 검색은 record 단건 (또는 section 단위) 결과만 반환한다. 그러나 사용자
질문 — "AI 도입 현황", "OGA 운영 정책 모음" — 은 보통 *비슷한 의미의
record 군*을 한 번에 보고 싶다. LARGE 모델은 답할 때 여러 record 를
인용하므로, **그룹 단위로 fetch + 그룹 단위로 인용** 이 자연스러운 단위.

이 라우터는 (1) 시맨틱 검색 → (2) 임베딩 cosine + 태그 jaccard 로 그리디
클러스터링 → (3) 그룹 라벨 / 공통 태그 / 도메인 합성을 한 번에 한다.

#### 7.7.2 엔드포인트 명세

| 엔드포인트 | 입력 | 출력 핵심 | 비용 |
|---|---|---|---|
| `POST /api/groups/auto` | `{q, n_groups (1..20), limit_per_group, min_score, sim_threshold (0..1, def 0.85), top_k (1..200, def 50)}` | `{query, total_records, groups:[{label, common_tags, common_agents, common_domain, size, representative_record, records:[{id,title,score,section_id,snippet}]}]}` | 中 (semantic 검색 1회 + numpy 클러스터링) |
| `GET /api/records/{id}/cluster?mode=semantic\|tag\|hybrid&sim_threshold=0.85&tag_threshold=0.6&limit=20` | path id + mode | `{anchor_record:{id,title,data_type,tags}, mode, cluster_size, items:[{id,title,score,shared_tags,tag_jaccard,semantic_sim}]}` | 低-中 (anchor 임베딩 + 후보 임베딩 일괄 조회) |
| `POST /api/records/bulk` | `{ids:[...max 200], include_sections}` | `{items:[{...full record meta + sections}], missing:[...]}` | 低 (단일 IN 쿼리) |

#### 7.7.3 알고리즘 세부

**그리디 클러스터링** (`services/cluster_svc.py:greedy_cluster`):

```
정규화 = vec / ||vec||  for each section embedding
정렬   = score 내림차 (semantic_search 가 이미 정렬)
for i, item in enumerate(items):
    if assigned[i]: continue
    if len(groups) >= n_groups:
        # 남은 항목은 가장 가까운 기존 그룹에 흡수
        best = argmax_g cos(item, group[g][0])
        groups[best].append(item); continue
    seed = item; new_group = [seed]
    for j > i where not assigned[j]:
        if cos(seed, items[j]) >= sim_threshold:
            new_group.append(items[j])
    groups.append(new_group)
```

K-means 미사용 — 외부 의존 없이 결정론적이고, top-K 작아 (≤50) 그리디 비용
미미. 결과는 시드 순서에 의존 (score 정렬 가정).

**hybrid 점수** (`/api/records/{id}/cluster?mode=hybrid`):
`combined = 0.6 * semantic_cos + 0.4 * tag_jaccard`,
컷오프 = `0.6 * sim_threshold + 0.4 * tag_threshold`.

#### 7.7.4 LARGE 모델 권장 워크플로우

S1. **자연어 → 그룹 단위 RAG:**
```
1. POST /api/groups/auto {q, n_groups: 3, limit_per_group: 5, min_score: 0.4}
2. groups[i].records[].id 모두 모아 unique 화 (최대 ~15 id)
3. POST /api/records/bulk {ids, include_sections: true}
4. 각 그룹별로 LLM 컨텍스트 1개 — label + common_tags 헤더로 의도 명시
5. 그룹별 답변 합성 → 최종 응답에 그룹 라벨 인용
```

S2. **anchor 확장 RAG:**
```
1. POST /api/ask → 첫 record id 채택
2. GET /api/records/{id}/cluster?mode=hybrid → 같은 의미 5~10건 확장
3. POST /api/records/bulk → 한 번에 fetch
4. 인용 시 anchor + cluster 멤버 모두 footnote
```

#### 7.7.5 신뢰도 / 운용 메모

- 임베딩 미백필 환경: `total_records=0` 빈 그룹으로 응답 (200 OK).
- pgvector 503 시: 라우터가 `503 semantic groups unavailable: ...` 응답.
- 그룹 라벨 (`label`) 은 결정론적이지만 인간 가독성 휴리스틱 — 사용자 노출
  시 그대로 쓰지 말고 LLM 으로 한 번 더 다듬는 것을 권장.
- `common_tags`/`common_domain` 은 그룹 내 모든 record 의 *교집합* — 빈
  배열이면 그룹 일관성이 약하다는 신호 → `sim_threshold` 를 더 높여서 재호출.

---

## 8. MCP 도구 카탈로그 + 고급 조합

### 8.1 9개 도구 명세 (`src/mcp_server/server.py`)

| 도구 | 입력 | 출력 | 핵심 역할 |
|---|---|---|---|
| `discover_schema` | (none) | `{discover, schema}` 합본 | **항상 먼저** — 스키마 + 카탈로그 |
| `discover_capabilities(agent_type)` | `agent_type: str` | `{agent, record_count, sample_records, follow_up}` | 한 agent 의 데이터 범위 |
| `ask(query, limit=5)` | `query: str, limit: int` | `{interpreted_query, results, total_matched, follow_up_queries}` | 자연어 검색 |
| `find_related(record_id, mode='auto')` | `record_id, mode ∈ {tags,graph,semantic,auto}` | `{related[], by_mode:{tags,graph,semantic}}` | 한 record 와 관련된 record |
| `explain_field(field_name)` | `field_name: str` | `{field_name, spec, is_enum, allowed_values, type, description}` | 필드 메타 (스키마 단일 추출) |
| `explain_schema(field_name)` | (alias) | (동일) | `explain_field` alias — 명세 호환 |
| `query_data(agent, query="", limit=5)` | `agent, query, limit` | `{results}` | agent-scoped (`/api/data`) |
| `list_agents()` | (none) | `{agents}` | 등록된 모든 agent |
| `get_record(record_id)` | `record_id: str` | full record dict | 단건 풀 fetch |
| `search(mode, query, tags=None)` | `mode, query, tags` | `{results}` 또는 mode별 응답 | tag/fts/semantic 통합 |

환경변수: `API_URL` (기본 `http://localhost:8000`), `API_TIMEOUT` (기본 30s), `MAX_LIMIT=20` (도구가 limit 클램프).

### 8.2 도구 호출 시나리오 (LARGE 모델)

**S1. 콜드 스타트 (처음 만난 허브):**
```
1. discover_schema()                           # 스키마 + 카탈로그
2. (옵션) explain_field('classification')      # 모르는 enum
3. ask(user_query, limit=5)                    # 자연어 검색
4. for r in results: get_record(r.id)          # 풀 detail
5. (옵션) find_related(r.id, mode='auto')      # 관련 record
```

**S2. 도메인 전문가 라우팅:**
```
1. list_agents()                               # 모든 agent
2. discover_capabilities(detected_agent)       # 그 agent 의 데이터
3. query_data(agent, query, limit=5)           # 좁힌 검색
4. get_record(top_result.record_id)
```

**S3. 스키마 워크플로우 자동 생성:**
```
1. discover_schema()
2. for field in schema.properties: explain_field(field)
3. LLM 이 field spec 으로 자체 input_schema 구축
4. 이 input_schema 로 자유롭게 record 필터
```

### 8.3 함정 (LARGE 가 자주 빠지는)

| 함정 | 증상 | 회피 |
|---|---|---|
| **도구 호출 폭주** | record 100건마다 `find_related` 호출 → 100*4 호출 | 상위 5건만 find_related; 나머지는 lineage 또는 tags 한 번 |
| **결과 합성 오류** | semantic + tag 결과를 단순 concat → 같은 record 중복 | record_id 로 dedup (§7.3 정규화 후) |
| **스키마 캐시 미사용** | 매 호출마다 discover_schema | 한 세션당 1회 + `?no_cache=true` 강제 갱신 시점만 |
| **explain_field 남용** | 매 응답마다 enum 값 explain | `/api/schema` 한 번으로 모든 enum 캐시 |
| **error dict 무시** | `{"error": ...}` 를 정상 응답처럼 처리 | 응답 첫 키가 "error" 면 분기 |
| **find_related mode='auto'** | tags + graph + semantic 3개 호출 → 응답 시간 급증 | 빠른 시 mode='tags' 단일 |

---

## 9. 12+ 흔한 워크플로우

### 9.1 자연어 질의 → RAG 응답 (생성 + 인용)

```
1. POST /api/ask {"query":"<user>", "limit":5}
2. interpreted_query 검토 (어떤 필터로 풀렸나)
3. results[].record_id 마다 GET /api/records/{id} → full
4. record.classification 검사 → public/internal 만 사용자 노출
5. record.content.sections (DOC) 또는 content (DATA/SIM) 에서 답변 근거 추출
6. RAG 응답 합성:
   - 각 인용에 [{record_id} §{section_id}] 형식 footnote
   - quality_score / status / version 함께 표시
7. follow_up_queries 사용자에게 제시
```

### 9.2 도메인 전문가 추천 (어떤 agent 에게 라우팅)

```
1. list_agents() → 모든 agent + common_tags + data_types
2. user query 의 토큰 ⊕ agent.common_tags 교집합 계산
   (또는 LLM 자체로 agent 매칭)
3. 매칭된 agent 별 query_data(agent, query, limit=3)
4. 결과 모두 합쳐 dedup → 상위 N
5. 응답에 "이 결과는 {agent_type} 의 데이터 — {agent.description}" 인용
```

### 9.3 다중 record 종합 분석 (5건 → 하나의 종합 답변)

```
1. ask(query, limit=10)
2. 상위 5 record 의 full content fetch
3. 5 record 의 sections 모두 모아 (DOC) 또는 rows (DATA) 통합
4. 충돌 검사: 같은 entity 에 대해 다른 값?
   YES → 충돌 보고 + 각 record 인용
   NO  → 종합 결론
5. 인용 footnote: [record_id1, record_id2, ...]
```

### 9.4 신뢰도 평가 + 출처 인용

```
1. 검색 결과 받음
2. 각 record 마다 신뢰도 차원 (§7.4) 평가:
   - quality_score >= 80 ? +0.3
   - status == 'approved' ? +0.3
   - source_file 존재 ? +0.1
   - 최신 version (lineage 의 leaf) ? +0.2
   - read_count 상위 20% ? +0.1
3. 신뢰도 합산 후 정렬
4. 응답에 신뢰도 + 인용 + 출처 명시
   "본 답변은 quality_score 87, approved, 작성자 홍길동 (HE/CAE) 의 DOC-HE-CAE-2026-000001 §S003 에 근거"
```

### 9.5 시간 흐름 분석 (version chain + 변경 점)

```
1. GET /api/records/{id}/lineage → ancestors + descendants
2. 시간순 정렬 (created_at)
3. 인접 페어마다 GET /api/records/{newer}/diff?from=<older>
4. diff.meta_changes / section_changes 로 변경점 요약
5. 변경 패턴:
   - 작은 patch (section_changes 1-2건) = 정정
   - 큰 변경 (>5 sections) = 개정
   - title 변경 = 의미 재정의
6. 사용자에게 timeline 으로 표시
```

### 9.6 분야별 메타 통계 + 인사이트

```
1. GET /api/analytics/distribution → by_data_type/team/group/year
2. GET /api/analytics/timeline?year=2026 → 월별
3. GET /api/analytics/usage?limit=20 → 인기 record
4. GET /api/analytics/common-tags?agent=X → agent 별 태그
5. GET /api/analytics/cross-agent?agents=A&agents=B → 교집합
6. LLM 이 인사이트 합성:
   - "EV 사업부의 SIM record 가 2026 년 3월 급증 (32% 증가)"
   - "iga-analyst 와 cae-reporter 가 공유 record 12건"
   - "quality_score < 50 인 draft 가 전체의 7%"
```

### 9.7 새 record 적재 + 변환 품질 평가

```
1. multipart POST /api/convert/ingest
   file=<doc.docx>, team=HE, group=CAE, year=2026, seq=0,
   classification=internal, status=draft,
   subject_keywords="iga,nurbs", agent_hints="figure 3 has key plot",
   query_examples="IGA 결과 보여줘"
2. 응답: {record_id, status: inserted|updated, sections_written, attachments_persisted}
3. 즉시 GET /api/records/{record_id} → 변환 품질 검증
   - sections 갯수 합리적?
   - figures/tables capabilities 채워졌나?
   - attachment.caption placeholder ("(캡션 누락 — 검수 필요)") 있나?
4. 품질 문제 시 PATCH 로 보완
5. POST /api/jobs/embed {"record_id": "..."} (수동 임베딩 백필)
6. GET /api/jobs/{job_id} 폴링 → status='done'
7. mode=semantic 으로 같은 record 검색 → 임베딩 정상 산출 확인
```

### 9.8 자기설명 활용한 동적 워크플로우 생성

LARGE 모델만의 고급 패턴 — 코드를 쓰지 않고 schema 로부터 자체 워크플로우 합성.

```
1. GET /api/discover → starting_points + agents
2. GET /api/schema → properties + oneOf + x-relationships
3. GET /api/hints (전체) → 7개 컨텍스트 모두
4. LLM 이 위 3개를 종합:
   - 어떤 필드로 필터 가능한지 (schema.properties)
   - 어떤 starting_points 있는지 (discover)
   - 어떤 토픽별 패턴 있는지 (hints)
5. 사용자 의도와 위 메타 매칭 후 자체적으로 호출 시퀀스 생성
6. 시퀀스 실행 + 응답마다 follow_up_queries 로 다음 단계 자연스러운 전이
```

### 9.9 첨부 그림 컨텍스트 분석 (figure caption + 주변 단락 + section 의미)

```
1. GET /api/records/{id}/attachments?kind=figure
2. 각 attachment 마다:
   a. caption 파싱
   b. section_ref 로 어느 섹션에서 참조됐나
   c. GET /api/records/{id}/sections (또는 record.content.sections)
   d. 해당 section 의 content_text 에서 caption 키워드 매칭
3. 합성:
   - figure caption + section.title + section.content_text 인접 단락
   = 그림의 "context window"
4. 그림 내용 LLM 추론 (멀티모달이면 binary 직접; 아니면 caption 만으로)
5. RAG 응답에 figure 인용 시 context window 도 함께 fetch
```

### 9.10 비슷한 record 찾기 + 차이점 자동 진단

```
1. find_related(record_id, mode='auto') → tags + graph + semantic 합본
2. 후보 5-10건 중 dedup
3. 각 후보 마다 GET /api/records/{base}/diff?from=<candidate>
   (diff API 는 from 의 메타·섹션 비교)
4. 차이 요약:
   - meta_changes 의 어떤 필드가 다른가
   - section_changes.kind 의 added/removed/modified 비율
5. 패턴 감지:
   - "후보 A 는 base 의 영문 번역 (derivation=translated)"
   - "후보 B 는 다른 quality_score 의 같은 실험 (재시험)"
   - "후보 C 는 다른 사업부의 유사 보고서"
```

### 9.11 검색 결과 zero-hit → fallback + 사용자 재질의 유도

```
1. ask(query) → results=[] AND total_matched=0
2. interpreted_query.filters 검토 — 너무 협소한 필터?
   YES → 필터 일부 제거 (year/team 우선 제거)
3. 단순화된 키워드만으로 mode=semantic 재시도
4. 그래도 0 → mode=tag (interpreted_query 의 첫 태그)
5. 그래도 0 → GET /api/discover.starting_points 표시
6. 사용자에게 자연어 응답:
   - "{user_query} 에 대한 결과를 찾지 못했습니다.
   - 시도해본 검색: ask, semantic '{simplified}', tag '{tag}'
   - 다음을 시도해보세요:
     1. {hints[0].sample_endpoint}
     2. {hints[1].sample_endpoint}
     3. 또는 다른 키워드 (예: '{discover.agents[0].sample_query}')"
```

### 9.12 agent 협업 — 여러 agent 가 같은 질의에 다른 관점

```
1. 단일 query 를 여러 agent 에 동시 라우팅:
   parallel: query_data(agent='iga-analyst', query, limit=5)
             query_data(agent='cae-reporter', query, limit=5)
             query_data(agent='material-reviewer', query, limit=5)
2. 각 agent 의 결과 모음
3. record_id 로 dedup → 어떤 record 가 여러 agent 가 동시 추천?
   = 합의도 高 → 신뢰도 boost
4. agent 별 분석 관점 노출:
   - iga-analyst: "IGA 알고리즘 측면 — section §3.1"
   - cae-reporter: "보고서 형식 측면 — section §1 요약"
   - material-reviewer: "재료 물성 측면 — section §4.2"
5. 사용자에게 다관점 답변 제공
```

### 9.13 DATA 타입 — 일반화 데이터 분석 (작은 AI 가 직접 계산 X)

DATA 타입 record 는 `headers + rows` 표 데이터. 시험 결과·시뮬레이션 출력·계측 raw 가 모두 들어온다.
LARGE 모델이라도 **수치 집계는 서버에 위임**하라 — 토큰 낭비 + 정확도 손실.

#### 4-step pipeline

```
Step 1. 카탈로그 (어떤 DATA 가 있나):
   GET /api/data?domain=material-test&min_rows=10&tags=Tensile&limit=20
   → items[] = [{id, title, domain, tags, rows, columns, units, context}, ...]
   - context.method/material/condition 같은 메타가 있으면 LLM 이 데이터 신뢰도
     평가에 활용 (예: "ASTM E8/E8M 가정 데이터" → 표준 시험법 준수).

Step 2. 컬럼 정의 (스키마 + 단위):
   GET /api/data/{id}/columns
   → items[] = [{column, description, unit, dtype}, ...]
   - description 은 column_descriptions 또는 _GLOSSARY 시트에서 자동 추출.
   - dtype ∈ {int, float, str, enum, bool, mixed, null}
   - 숫자 컬럼 + enum 컬럼 조합이면 group_by 분석 가능.

Step 3. 행 페이징 + 컬럼=값 사전필터:
   GET /api/data/{id}/rows?limit=200&offset=0&where=Region:Yield
   → {headers, units, total_rows, rows:[[...]]}
   - where 는 단일 조건. 다축 필터가 필요하면 클라이언트에서 한 번 가져온 후 후필터.
   - 큰 데이터셋이면 limit 을 100~500 정도로, offset 으로 페이징.

Step 4. 통계 집계 (서버측):
   GET /api/data/{id}/aggregate?op=avg&column=Stress
   GET /api/data/{id}/aggregate?op=max&column=Stress&group_by=Region
   - op ∈ {avg, max, min, sum, count}.
   - count: column 생략 가능 (전체 행 수).
   - sum/avg: 숫자만, 비숫자 자동 스킵.
   - group_by: 응답 result 가 [{group_col: value, "{op}_{column}": metric}, ...]
     예: result:[{Region:"UTS", max_Stress:450.0}, {Region:"Yield", max_Stress:270.0}]
```

#### 통계 활용 패턴

1. **재료 물성 추출** (engineering use):
   ```
   1) GET /api/data/{id}/columns                 # Stress, Strain, Region 확인
   2) GET /api/data/{id}/aggregate?op=max&column=Stress
      → UTS = 450 MPa
   3) GET /api/data/{id}/aggregate?op=max&column=Stress&group_by=Region
      → Region=UTS 그룹 → 450 MPa 검증
   4) GET /api/data/{id}/rows?where=Region:Yield&limit=10
      → 항복점 부근 raw 확인
   ```

2. **시뮬레이션 결과 비교** (cross-record):
   ```
   1) GET /api/data?domain=cae-simulation&tags=LS-DYNA  # 카탈로그
   2) for each id in items:
        GET /api/data/{id}/aggregate?op=max&column=peak_stress
   3) 각 record 의 peak_stress 비교 → 가장 큰/작은 시뮬 식별
   ```

3. **품질 게이트** (statistical control):
   ```
   1) GET /api/data/{id}/aggregate?op=avg&column=measurement
   2) GET /api/data/{id}/aggregate?op=max&column=measurement
   3) 평균 + 최대값으로 spec 위반 여부 판단
   ```

### 9.14 다층 필터링 (faceted search) — 다음 query 좁힘 전략

#### 왜 단일 `/api/search` 와 다른가

| 비교 축 | `/api/search` | `/api/search/faceted` |
|---|---|---|
| 필터 축 수 | 단일 (mode 하나) | 다축 (data_type/tags/agent/domain/classification/status/year/min_quality) |
| AND 조합 | 불가 (mode 단일) | 가능 (모든 축 AND) |
| facet 카운트 | 없음 | 응답 본문 `facets{}` |
| 키워드 매칭 | mode=fts/tag/semantic 중 하나 | mode=fts/semantic + 다축 |
| 정렬 | mode 기본 정렬 | semantic score → updated_at |

#### facets 활용 — 작은 AI 가 다음 query 어떻게 좁힐지

facets 응답은 **현재 결과 집합 위에서** 산출된다. 예시:
```
초기 query: GET /api/search/faceted?q=stress  (no filters)
응답:       total: 47, facets: { data_type: {DOC:32, DATA:11, SIM:4},
                                tags: {stress:47, IGA:18, 낙하시험:12, ...},
                                agent: {iga-analyst:24, cae-reporter:15, ...} }

분석 (LLM 추론):
1. data_type.DOC=32 가 67% 차지 → "DOC 위주" 라는 시그널.
   사용자가 원시 데이터를 원하면 data_type=DATA 추가하면 11건 으로 좁혀짐.
2. tags.IGA=18, 낙하시험=12 가 다음 좁힘 후보.
   "IGA 결과 중 stress" 라면 tags=IGA,stress → 다음 round 에서 facet 다시 보고 결정.
3. agent.iga-analyst=24 이면 agent=iga-analyst 로 한 번 더 좁힐 수 있음.

전략 (3-round narrowing):
  Round 1: q="stress" → facets 분석
  Round 2: q="stress" + 가장 큰 facet 축 추가 → 재 facets 분석
  Round 3: q="stress" + 두 축 추가 → items 비교 후 사용자 답변
```

#### 패턴 — facet 카운트 의미별 행동

| facet 분포 | LLM 행동 |
|---|---|
| 한 값이 90%+ | 그 값으로 **자동 좁힘** (사용자 묻지 말고) |
| 두 값이 비슷 (40-60% / 40-60%) | 사용자에게 **선택 질문** ("DOC 답변 vs DATA 표 ?") |
| 5+ 값으로 평탄 분포 | facet 무용. 다른 축으로 좁혀라 |
| 1개 값만 = total | 그 축은 좁힘 가치 없음 (이미 단일) |

#### `/api/search/by-tags` — any vs all

```
match=all  : array_contains (PG: @>) — 두 태그 모두 가진 record (intersection)
match=any  : array_overlap (PG: &&) — 둘 중 하나라도 (union)

전략:
  1) 먼저 match=all 시도 → 결과 ≥ 1 이면 정확 매치.
  2) 결과 = 0 이면 match=any 로 확장 (recall 우선).
  3) any 로도 0 이면 fts/semantic 으로 폴백.
```

---

## 10. 응답 스키마 + 메타 평가

### 10.1 RecordOut — 상세 응답

| 키 | 타입 | 신뢰도 평가 활용 |
|---|---|---|
| `id` | str | 인용 footnote |
| `data_type` | enum | content shape 분기 |
| `team/group/year/seq` | str/int | 출처 분류 |
| `title/summary` | text | 미리보기 |
| `tags/agents/subject_keywords` | str[] | 분류·entity |
| `content` | jsonb | 답변 본문 (variant 별) |
| `content_hash` | str | 멱등성·변경 감지 |
| `classification` | enum | **노출 정책 결정** |
| `status` | enum | **신뢰도 차원 1** |
| `quality_score` | int 0-100 | **신뢰도 차원 2** |
| `version / derivation / parent_record_id` | str/str/str | **버전 정합성** |
| `valid_from / valid_until` | date | **시간 유효성** |
| `read_count / last_accessed_at` | int / ts | **인기·신선도** |
| `access_pattern` | enum | **캐싱 우선순위** |
| `deleted_at` | ts | **활성 여부** |
| `agent_hints / query_examples` | text / str[] | **에이전트용 사용 힌트** |

### 10.2 List response 패턴

```json
{"items":[<RecordOut>...],"total":42,"limit":20,"offset":0}
```

페이지 진행: `offset += limit` 까지 `len(items)==0` 또는 `offset >= total`. 정렬: `updated_at DESC, id DESC` (records).

### 10.3 ask 응답 — interpreted_query 검토

```json
{"interpreted_query":{
   "data_type":"DOC", "year":2026, "agent":"iga-analyst",
   "explanation":"키워드로 agent=iga-analyst 추정; year=2026",
   "source":"keyword"|"llm"
 },
 "results":[{"id":"...","title":"...","quality_score":85,...}],
 "total_matched":7,
 "follow_up_queries":["GET /api/agents/iga-analyst/records ..."],
 "raw_query":"<원문>"
}
```

LARGE 모델 검증 체크리스트:
1. `interpreted_query.source == "llm"` 이면 LLM 해석 — 일반적으로 정확.
2. `source == "keyword"` 이면 단순 키워드 룩업 — 의도 누락 가능. `explanation` 검토 후 부족하면 직접 필터 보강.
3. `total_matched > limit` 이면 더 많은 결과 — 사용자에게 "n 건 더 있음" 노출.
4. `results=[]` && `total_matched=0` → §9.11 zero-hit 회복.

### 10.4 Discover 응답 — 카탈로그 스냅샷

```json
{"version":"1.0","title":"AI Data Hub","total_records":1234,
 "by_data_type":{"DOC":800,"DATA":300,"SIM":100,...},
 "by_division":{"HE":500,"EV":400,...},
 "by_classification":{"internal":900,"public":200,"confidential":100,"restricted":34},
 "agents":[{"agent_type":"iga-analyst","name":"IGA Analyst",
            "description":"...","record_count":42,
            "common_tags":["iga","nurbs"],"data_types":["DOC","SIM"],
            "sample_query":"/api/data?agent=iga-analyst"}],
 "data_types_explained":{"DOC":"문서·매뉴얼...", ...},
 "starting_points":["GET /api/agents",...],
 "schema_url":"/api/schema","hints_url":"/api/hints",
 "llm_doc_url":"/api/docs/llm.txt","ask_url":"/api/ask",
 "generated_at":"2026-05-09T..."}
```

### 10.5 Diff 응답

```json
{"from":"DOC-HE-CAE-2026-000001","to":"DOC-HE-CAE-2026-000002",
 "meta_changes":{"title":["old","new"],"version":["1.0","1.1"]},
 "section_changes":[
   {"section_id":"S001","kind":"modified",
    "title_changes":["old","new"]|null,
    "content_diff":"--- a/S001\n+++ b/S001\n@@ ... unified diff text"}],
 "block_changes":"identical"|"summary"}
```

`block_changes` 는 단순 마커. 깊은 block diff 는 미구현 — section content unified diff 가 대체.

---

## 11. 에러 처리 + 회복 패턴

### 11.1 표준 에러 응답

```json
{"error":{"code":"<CODE>","message":"<msg>","details":{...},"request_id":"<uuid>"}}
```

`X-Request-ID` 헤더에도 동일 uuid — 서버 로그 cross-reference.

### 11.2 코드 표

| HTTP | code | 의미 | 회복 전략 |
|---|---|---|---|
| 400 | `BAD_REQUEST` | mode=tag without tags 등 | `details.detail` 검토, 필수 파라미터 보강 |
| 401 | `AUTHENTICATION_ERROR` | 헤더 누락/무효 | `X-API-Key` 헤더 점검, env `BOOTSTRAP_API_KEY` 확인 |
| 403 | `AUTHORIZATION_ERROR` | hard delete 등 권한 부족 | bootstrap 키 사용 또는 작업 포기 |
| 404 | `NOT_FOUND` | record/agent/job/key 없음 | id 재확인. 검색으로 우회: `/api/records?q=<title>` |
| 405 | `METHOD_NOT_ALLOWED` | HTTP 메서드 잘못 | §6 표 재확인 |
| 409 | `CONFLICT` | 중복 (같은 id), 자연 키 위반 | seq 다르게 또는 PATCH |
| 413 | `PAYLOAD_TOO_LARGE` | 업로드 size 초과 | `details.max_bytes` 미만으로 분할 또는 `MAX_UPLOAD_MB` 조정 |
| 422 | `VALIDATION_ERROR` | Pydantic 검증 실패 | `details.errors[].loc` 확인 — 필드 fix |
| 429 | `RATE_LIMIT` | 요청 과다 | exponential backoff (1s, 2s, 4s, 8s) 후 재시도 |
| 500 | `INTERNAL_ERROR` | 서버 오류 | `details.type` 보고 + `request_id` 기록 |
| 503 | `(http_error)` | semantic search embedder 미준비 | `mode=fts` 또는 `mode=tag` 폴백 |

### 11.3 재시도 정책 + 멱등성

| 작업 | 멱등? | 재시도 권장 |
|---|---|---|
| GET (모든) | YES | 즉시 재시도 (3회, exponential backoff) |
| POST `/api/records` | NO (id 중복 시 409) | 409 시 PATCH 로 우회, 다른 에러는 NO 재시도 |
| POST `/api/convert/ingest` | YES (content_hash 비교) | 같은 파일 재업로드 → updated 또는 skipped |
| PATCH | NO 엄밀히 (race) | 단일 client 에서는 안전 |
| DELETE soft | YES (멱등 — 이미 deleted 면 noop) | 재시도 OK |
| DELETE hard | NO | 재시도 X — 이미 사라짐 |
| POST `/api/jobs/embed` | YES (같은 record_id 면 dedup) | 재시도 OK |

### 11.4 회복 시나리오

**회복 1. semantic search 503:**
```
try GET /api/search?mode=semantic&q=...
catch 503: GET /api/search?mode=fts&q=...
catch 0 hits: GET /api/search?mode=tag&tags=...
```

**회복 2. ingest 422 validation:**
```
422 → details.errors[]
for err in errors:
  fix payload[err.loc] per err.msg
retry POST /api/convert/ingest
```

**회복 3. 401 in middle of session:**
```
key 만료? → POST /api/auth/keys/verify
if revoked or expired: 새 키 발급 (bootstrap) 후 재시도
```

---

## 12. 성능 / 캐싱 / 백필 (deep)

### 12.1 캐시 계층

| 캐시 | 위치 | TTL | 용도 |
|---|---|---|---|
| Discover payload | `services/discover_svc._DISCOVER_CACHE` (in-process dict) | 60s | `/api/discover` 카운트 집계 회피. `?no_cache=true` 우회. |
| JSON Schema | 정적 (`build_json_schema`) | ∞ | 코드 변경 시만 갱신 |
| LLM doc | 정적 (`_LLM_DOC_TEMPLATE`) | ∞ | — |
| Hints | 정적 (`_ALL_HINTS`) | ∞ | — |
| Embedder | `services/embedding.get_embedder()` 싱글턴 | 프로세스 수명 | 모델 로드 1회 |
| FastAPI response | 미설정 | — | 운영 시 nginx/CDN 추가 권장 |

### 12.2 access_pattern 활용

`records.access_pattern ∈ {frequent, occasional, rare}` 는 단순 메타가 아니라 캐싱·우선순위 힌트.

| 값 | 의미 | LARGE 모델 활용 |
|---|---|---|
| `frequent` | 자주 조회 | 응답 캐시에 우선, embedding 우선 백필 |
| `occasional` (default) | 보통 | 표준 |
| `rare` | 드문 archive | embedding 백필 후순위, 검색 결과 노출 시 deprioritize |

### 12.3 임베딩 백필

**자동:** env `AUTO_EMBED_ON_INSERT=true` → `/api/convert/ingest` 시 `services/jobs.maybe_schedule_auto_embed(record_id)` 호출. 비동기 큐에 들어가 background worker 가 처리.

**수동:**
```
POST /api/jobs/embed {"record_id": "DOC-HE-CAE-2026-000001"}
→ 202 Accepted, body: {"job_id":"<uuid>","kind":"embed","status":"pending",...}

GET /api/jobs/{job_id} 폴링 → status: pending → running → done
done 시 result: {"embedded": 12, "skipped": 0, "failed": 0}
```

다중 record:
```
POST /api/jobs/embed {"record_ids": ["DOC-...", "DOC-..."]}
```

**임베딩 모델:** 기본 `sentence-transformers/all-MiniLM-L6-v2` (384-dim). `EMBEDDING_PROVIDER=openai` + `OPENAI_API_KEY` 시 OpenAI text-embedding-3-small (1536-dim → projection 384). DB 컬럼은 384 고정.

### 12.4 Async job queue 구조

`services/jobs.py` — in-memory dict + per-kind `asyncio.Semaphore` (동시성 제한, 기본 2). TTL 후 자동 GC. 클러스터 (멀티 워커) 환경에서는 워커 간 공유 X — Redis 등으로 확장 필요 (현재 PoC 단일 워커).

### 12.5 Batch CLI (`api/ingest/batch.py`)

대량 적재용 명령행 도구:
```
python -m api.ingest.batch --root <dir> --recurse --team HE --group CAE --year 2026
```

내부적으로 `convert_file → normalize → write_record` 동일 파이프라인. 멱등성 (`content_hash` 비교) — 같은 파일 재실행 시 skip 또는 update.

### 12.6 인덱스 현황 (records)

| 인덱스 | 컬럼 | 용도 |
|---|---|---|
| `idx_records_type` | data_type | filter |
| `idx_records_div_team` | team, group | 복합 filter |
| `idx_records_year` | year | filter |
| `idx_records_agents` (GIN) | agents[] | array overlap |
| `idx_records_tags` (GIN) | tags[] | array contains |
| `idx_records_content` (GIN, jsonb_path_ops) | content (jsonb) | jsonb path query |
| `idx_records_classification/status/domain` | scalar | filter |
| `idx_records_capabilities` (GIN) | capabilities[] | array contains |
| `idx_records_subject` (GIN) | subject_keywords[] | entity overlap |
| `idx_records_parent` | parent_record_id | lineage |
| `idx_records_related` (GIN) | related_record_ids[] | graph |
| `idx_records_access_pattern` | access_pattern | filter |

`record_sections.embedding` 은 마이그레이션 0004 에서 ivfflat (`USING ivfflat (embedding vector_cosine_ops)`) 시도 — pgvector 설치 시. 미설치 환경 (테스트 SQLite) 에서는 numpy 폴백.

---

## 13. 핵심 enum + semantic 매핑

### 13.1 `data_type` (7)

| 값 | 의미 | content shape |
|---|---|---|
| `DOC` | 문서 | `{ meta, toc, sections[{id,level,title,blocks,children}], figures, tables, sources, attachments }` |
| `DATA` | 표 데이터 | `{ caption, headers[], rows[][], units{col:unit}, notes }` |
| `SIM` | 시뮬레이션 | `{ solver, solver_version, inputs{}, outputs{}, runtime{} }` |
| `CAD` | 3D CAD 메타 | `{ cad_type, file_format, file_metadata{}, components[] }` |
| `LOG` | 로그·시계열 | free-form |
| `FORM` | 양식·체크리스트 | free-form |
| `OTHER` | 기타 | free-form |

### 13.2 `classification` (4) — 도메인 매핑

| 값 | 사업부 의미 | 외부 동의어 | 노출 정책 |
|---|---|---|---|
| `public` | 공개 (보도자료, 외부 발표) | unrestricted, open | 누구나 |
| `internal` (default) | 사내 일반 | company-only | 사내 모든 키 |
| `confidential` | 기밀 | NDA, restricted-circulation | scope 필요 (PoC 미강제) |
| `restricted` | 극비 | top-secret, need-to-know | scope 필요 (PoC 미강제) |

### 13.3 `status` (4)

`draft` (default) → `review` → `approved` → `deprecated` (단방향 전이 권장, 강제 안 함)

### 13.4 `derivation` (4)

| 값 | 의미 |
|---|---|
| `original` (default) | 원본 |
| `extracted` | 다른 record 에서 발췌 (parent_record_id 권장) |
| `aggregated` | 여러 record 합산 |
| `translated` | 번역 (parent_record_id 필수, language 다름) |

### 13.5 `access_pattern` (3)

`frequent` / `occasional` (default) / `rare`. §12.2 참조.

### 13.6 `language` (5)

`ko` (default), `en`, `ja`, `zh`, `mixed`. (스키마는 ko/en/mixed 만; 메타옵션은 ko/en/ja/zh — 운영 매핑 차이.)

### 13.7 `attachment.kind` (9)

| 값 | 확장자 (subset) |
|---|---|
| `figure` | png, jpg, jpeg, gif, bmp, wmf, emf, svg, tif, tiff, webp |
| `document` | pdf, doc, docx, hwp, hwpx, txt, rtf, odt |
| `spreadsheet` | xlsx, xls, xlsm, csv, tsv, ods |
| `media` | mp3, wav, ogg, flac, mp4, avi, mov, mkv, webm, m4a |
| `archive` | zip, tar, gz, 7z, rar, bz2, xz |
| `cad` | step, stp, iges, igs, catpart, sldprt, prt, x_t, stl |
| `drawing` | dwg, dxf |
| `data` | json, xml, yaml, yml, toml |
| `other` | (위 외 모든 것) |

### 13.8 `capabilities[]` (자동 산출, 13)

`sections, blocks, tables, figures, attachments, embeddings, rows, headers, samples, files, components, inputs, outputs`

INSERT 시 `services/capabilities.compute_capabilities` 가 content shape 으로부터 자동 산출. 클라이언트가 보낸 값은 무시되고 재산출.

### 13.9 `audit_log.action` (6)

`INSERT, UPDATE, DELETE, RESTORE, VIEW, ACCESS`

### 13.10 `search.mode` (3)

`tag, fts, semantic`. (LARGE 가 `find_related.mode` 와 혼동 주의 — 후자는 `tags, graph, semantic, auto` 4개.)

### 13.11 Job kinds (현재 1)

`embed`. 응답: `{job_id, kind, status: pending|running|done|failed, progress, result, error}`. 향후 `convert`, `reindex` 등 추가 예정.

---

## 14. agent 협업 패턴

### 14.1 agent 모델의 두 시점

1. **`agent_scopes` (api_keys.agent_scopes)** — 키 보안 차원: 이 키가 어떤 agent_type 들로 작업할 수 있나.
2. **`records.agents[] / agent_records junction`** — 데이터 차원: 이 record 가 어떤 agent_type 들에게 유의미한가.

두 시점은 독립이며 PoC 에서 enforcement 결합 미구현. 운영 진입 시 키의 scope 와 record 의 agents 교집합으로 access control.

### 14.2 `/api/data` relevance 공식

```
priority = agent_records.priority (1-5, 기본 1)
hits = q.lower() in (title + summary + section.content_text) 매칭 횟수
score = (priority/5) * 0.7 + min(hits*0.1, 0.5) + 0.05
      ∈ [0.05, 1.0], rounded 3자리
```

priority 5 는 큐레이터의 강한 추천 — score 0.75 부터 시작. priority 1 + hits 0 이면 0.19 floor. **활용:** 큐레이터가 "이 agent 의 핵심 record" 를 priority=5 로 핀하면 검색에서 항상 상위.

### 14.3 도메인 전문가 라우팅 알고리즘 (LARGE)

```python
def route_to_agents(query: str, agents: list[Agent]) -> list[str]:
    # 1) keyword overlap
    q_tokens = set(query.lower().split())
    candidates = []
    for ag in agents:
        score = 0
        score += len(q_tokens & set(t.lower() for t in ag.common_tags)) * 2
        score += len(q_tokens & set(dt.lower() for dt in ag.data_types))
        if any(k in query.lower() for k in ag.description.lower().split()):
            score += 1
        candidates.append((score, ag.agent_type))
    candidates.sort(reverse=True)
    return [a for s, a in candidates if s > 0][:3]
```

### 14.4 다중 agent 합의도

같은 query 에 여러 agent 호출 후 같은 record 가 여러 agent 결과에 등장 → 합의도 高.

```python
agent_results = {a: query_data(a, query) for a in routed_agents}
record_votes = Counter()
for a, results in agent_results.items():
    for r in results['results']:
        record_votes[r['record_id']] += 1
# 합의도 1 = 한 agent 만, 3 = 모든 agent 추천
```

### 14.5 agent 메타 운영

새 agent 등록:
```
POST /api/agents
{"agent_type":"battery-analyst","name":"Battery Analyst",
 "description":"BMS, cell chemistry, thermal","common_tags":["battery","bms","cell"],
 "data_types":["DATA","SIM","DOC"]}
```

agent ↔ record link:
```
PATCH /api/records/{id} {"agents":["battery-analyst"]}   (record.agents[] 에 추가)
```

priority 조정은 현재 `agent_records.priority` 직접 UPDATE 만 가능 (전용 API 미구현 — 운영 진입 전 추가 예정).

---

## 15. 디자인 결정 — "왜" 모음

### 15.1 왜 7-key JSON 스키마인가

`json_schema_rules.md` 의 7대 키: `meta`, `toc`, `sections`, `figures`, `tables`, `sources`, `attachments`.

**이유:**

1. **최소 충분 (Minimum Viable Schema)** — 실제 사업부 문서 (Word/PDF/PPT/MD/HTML) 의 90%+ 가 이 7개 안에 들어간다.
2. **RAG 친화** — 각 키가 검색 단위와 1:1 (sections=청크, figures=이미지 검색, tables=표 데이터 검색).
3. **변환기 일관성** — 6개 변환기 (Word/Excel/PPT/MD/HTML/PDF) 가 모두 동일 7키로 출력 → normalizer 가 단순.
4. **확장 여지** — variant 별 추가 필드는 `content` 안의 자유 dict 로 (예: `meta.pdf.creator`).

**대안의 거부 이유:**
- Block 단위 평면 트리 (Notion 식): RAG 청크 단위가 흐려짐.
- DOC/DATA 분리 스키마: 변환기 6개 곱하기 2 변종 = 12 출력 = 유지보수 부담.

### 15.2 왜 unique id 대신 같은 그룹은 같은 id 인가

DOC 한 개의 모든 sections 는 동일 `doc_id` 공유. (figure_id 도 figure 가 한 record 에 속하면 record_id 그대로 + A{nnn}.)

**이유:**

1. **RAG 검색 dedup** — section 청크 hit 100개 → record 단위 50개로 자연 그룹핑.
2. **인용 단순화** — `[DOC-HE-CAE-2026-000001 §3.1]` 한 형식으로 record + section 동시 표현.
3. **버전 관리** — record 단위 versioning, section 은 record 의 일부 → 버전 충돌 없음.
4. **외부 시스템 cross-ref** — 한 PLM 항목이 한 record. section 단위 ID 가 외부에 노출되지 않음.

**대안의 거부 이유:**
- section 마다 글로벌 unique ID: 인용이 길어지고 record 와의 관계가 흐려짐.
- UUID: 사람 읽기 어려움 + 자연 정렬 X (§5.2).

### 15.3 왜 embedding 384차원인가

기본 모델: `sentence-transformers/all-MiniLM-L6-v2` — 384 차원.

**이유:**

1. **저장 비용** — 1536-dim (OpenAI ada-002) 의 1/4. PG vector(384) 는 row 당 ~1.5KB.
2. **속도** — pgvector cosine distance 는 차원에 비례. ivfflat 인덱스도 384 가 fits in cache.
3. **품질 충분** — 사업부 문서 검색 도메인에서 384 vs 1536 의 recall 차이가 미미 (PoC 평가).
4. **한국어 + 영어 호환** — MiniLM 다국어 모델이 한국어도 어느 정도 처리 (perfect 아님 — §7.5 한계).
5. **OpenAI 백업** — `text-embedding-3-small` (1536) 사용 시도 projection 으로 384 정규화.

**한계:** 한국어 전용 도메인 모델 (KoSBERT 등) 보다는 떨어짐. 운영 진입 시 한국어 특화 모델 평가 예정.

### 15.4 왜 작성 표준 4원칙 (Heading + Claim-Evidence + Figure caption + 산문→표) 인가

`json_schema_rules.md` + `META_FORMAT_AUDIT.md` 정의. 변환기 + 큐레이터에게 공통 적용.

| 원칙 | 검색 영향 |
|---|---|
| **Heading Tree 명시** | section 청크 단위 정합 → RAG 정확도 |
| **Claim-Evidence 분리** | 주장과 근거가 같은 청크에 있어야 인용 시 함께 노출 |
| **Figure caption 의무** | 그림이 검색 가능해짐 (§4.3 placeholder 의무) |
| **산문 ↔ 표 표현 차이** | 측정 데이터는 표(DATA), 해석은 산문(DOC). 분리해야 표 단위 검색 가능 |

### 15.5 왜 self-describing endpoints (discover/schema/hints/llm.txt) 인가

REMAINING_JOBS 의 핵심 원칙 — "DB에는 매우 다양한 종류의 데이터가 추가될 것이다. 우수한 AI 에이전트뿐 아니라 단순한 AI 에이전트도 백엔드 코드를 읽지 않고 자기 힘으로 내부 구조를 이해하고 손쉽게 질의할 수 있어야 한다."

**구현 결과:**

- `discover` → 카탈로그 (현재 데이터 분포)
- `schema` → 형식 (필드/enum/관계)
- `hints` → 패턴 (자주 쓰는 endpoint)
- `llm.txt` → 통합 (한 페이지 압축)

이 4개를 받으면 백엔드 source 트리를 1줄도 읽지 않고도 모든 작업이 가능. LARGE 모델은 이 4개를 그대로 prompt 의 system context 에 주입 후 자율 작업.

**대안의 거부 이유:**
- OpenAPI/Swagger 만: enum 값이 나열되지만 의미 (data_types_explained, common_tags) 가 없음.
- 별도 docs site: 외부 의존 + drift.

---

## 16. 한계 + 향후 작업

### 16.1 PoC 단계 한계

| 영역 | 현재 상태 | 운영 진입 시 보강 |
|---|---|---|
| **HTTPS / TLS** | http://localhost:8000 only | 리버스 프록시 (nginx) + Let's Encrypt |
| **Rate limiting** | 미구현 (429 응답은 정의만) | nginx limit_req + Redis token bucket |
| **OCR** | 이미지 attachment 의 텍스트 미추출 | Tesseract / Azure Vision 통합 |
| **차트 데이터 추출** | PDF/PPT 차트는 figure 로만 | 차트 → DATA record 자동 변환 |
| **Classification enforcement** | 응답 시 필터링 없음 | scope 기반 자동 필터 (api_keys.agent_scopes ↔ records.classification) |
| **Multi-worker job queue** | in-memory dict | Redis / Celery / RQ |
| **Audit log 외부 노출 API** | DB SELECT 만 | `/api/audit?record_id=&actor=&action=&since=` |
| **한국어 형태소** | tsvector('simple') | mecab-ko 또는 elasticsearch 통합 |
| **Backup / DR** | 미구현 | pg_dump cron + WAL archive |
| **Secrets management** | env 변수 | Vault / AWS Secrets Manager |
| **Monitoring** | `/metrics` Prometheus 만 | Grafana 대시보드 + alert |
| **Deduplication** | content_hash 단순 비교 | near-duplicate 감지 (MinHash) |

### 16.2 변환기 잔여 작업 (`REMAINING_JOBS.md`)

- PPT/DOCX 첨부 캡션 자동 추정 — 현재 placeholder. 인접 텍스트박스 + alt-text + 슬라이드 제목 점수화 필요.
- 실데이터 회귀 검증 — pptx_pairs/, real_world_pptx/, xlsx_pairs/ 폴더로 batch CLI 실행.
- AUTO_EMBED_ON_INSERT 운영 토글 시 1000건 배치 수렴 검증.

### 16.3 메타 표준화 잔여 (`META_FORMAT_AUDIT.md`)

- 6개 변환기 간 키 정합 (P0): `agent_scope` → `agents`, `data_id` 와 `id` 충돌, classification/status 4/6 미지원.
- own-extras 컨테이너 표준화 (P1): `meta.{format}.*` 일관 키.

### 16.4 LARGE 모델 활용 시 인지해야 할 미래 변경 가능성

| 항목 | 현재 | 변경 가능성 |
|---|---|---|
| `contract_version` | "1.0" | 운영 진입 시 1.1 (breaking 가능) |
| Embedding 차원 | 384 | 한국어 모델 도입 시 768 또는 1024 |
| MCP 도구 갯수 | 9 | `audit_query`, `explain_relation` 추가 가능 |
| Discover 캐시 TTL | 60s | 운영 시 5min 조정 가능 |
| Classification 자동 필터 | 미구현 | 적용 시 응답 결과 갯수 변화 |

`/api/discover.version` 을 매번 확인하라. 1.0 → 1.x 변화 시 schema 도 함께 변경 가능 — 캐시 무효화.

---

## Appendix A. 한 화면 cheat sheet

```
LOGIN     →  X-API-Key: <plaintext>           (env BOOTSTRAP_API_KEY 로 발급)
DISCOVER  →  GET  /api/discover               (먼저, 60s 캐시)
SCHEMA    →  GET  /api/schema                 (정적 JSON Schema)
HINTS     →  GET  /api/hints?context=getting_started
LLM DOC   →  GET  /api/docs/llm.txt           (5-10KB 통합)
ASK       →  POST /api/ask {"query":"...","limit":5}
LIST      →  GET  /api/records?data_type=DOC&team=HE&year=2026&limit=20
GET       →  GET  /api/records/{DOC-HE-CAE-2026-000001}
SEARCH    →  GET  /api/search?mode=fts&q=IGA
                  (mode=tag&tags=...&tags=..., mode=semantic&q=...)
AGENT     →  GET  /api/data?agent=iga-analyst&query=...&limit=5
RELATED   →  MCP  find_related(record_id, mode='auto')
LINEAGE   →  GET  /api/records/{id}/lineage
DIFF      →  GET  /api/records/{id}/diff?from=<other>
ATTACH    →  GET  /api/records/{id}/attachments?kind=figure
              (binary: /attachments/{rid}/A{nnn}.{ext})
INGEST    →  multipart POST /api/convert/ingest
              (file + team + group + year + seq=0 자동)
EMBED     →  POST /api/jobs/embed {"record_id":"..."} → GET /api/jobs/{job_id}
ANALYTICS →  GET  /api/analytics/{distribution|common-tags|cross-agent|timeline|usage}
META      →  GET  /api/meta/options
ERROR     →  body.error.{code,message,details,request_id} + header X-Request-ID
```

## Appendix B. LARGE 모델 자율 작업 체크리스트

답변 합성 전 확인:

- [ ] 인용한 record 의 `classification` 이 사용자에게 노출 가능?
- [ ] `status == 'deprecated'` 또는 `deleted_at != null` 인가?
- [ ] `quality_score < 50` 이면 사용자에게 신뢰도 경고?
- [ ] `version` 이 lineage 의 leaf 인가? (옛 버전 인용 회피)
- [ ] `valid_until` 지난 record 인가?
- [ ] `caption == "(캡션 누락 — 검수 필요)"` 인 figure 인용 회피?
- [ ] 단일 source 만 인용? — 합의도 차원 (§9.12) 적용?
- [ ] `interpreted_query.source == 'keyword'` 폴백 시 검색 의도 검토?
- [ ] zero-hit 시 §9.11 회복 절차 거쳤나?
- [ ] follow_up_queries 사용자에게 노출?

---

End of LARGE reference. 이 문서는 자족적이다 — 다른 docs/ 파일을 참조하지 않아도 모든 작업 가능. 더 깊은 구현 디테일이 필요하면: `data_model.md` (테이블/마이그레이션), `governance.md` (audit/diff), `mcp_integration_guide.md` (MCP 등록), `api_reference.md` (OpenAPI), `META_FORMAT_AUDIT.md` (변환기 정합), `REMAINING_JOBS.md` (잔여 작업).

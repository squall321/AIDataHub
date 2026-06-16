# Claude Desktop MCP 이관 계획 — 데이터/Agent 정의를 대화로

> 작성: 2026-06-14 · 근거: 6관점 코드 조사 + 6렌즈 적대적 검증 (45 확정 문제)
> 상태: **MVP + 능동 흐름 구현 완료 (2026-06).** Phase 0~4·7 shipped, Phase 5·6 연기.

---

# 구현 현황 (shipped)

| Phase | 커밋 | 내용 | 검증 |
|-------|------|------|------|
| 0+1 | `9b0ea01` | `import_record` — drag&drop→되묻기→저장 + auth/서비스추출/에러봉투/actor | incomplete→ready→saved, auth 거부, 타입보존 |
| 2 | `1d4feb7` | Agent/DocType 정의 래퍼 (`draft_agent`/`create_agent`/`patch_agent`/`describe_agent_schema`/`list_doc_types`/`create_doc_type`) | create/patch/중복거부/list 38 |
| — | `227bcbd` | `convert_file` 브리지 (Path B) — inbox 파일 정밀변환 | xlsx 변환, traversal 차단 |
| 3 | `9042143` | `graph_type` 분석힌트 메타 + B4(_extract_data 유실) 수정 | DB 보존 확인 |
| 4 | `9119d5e` | `describe_data_capability` — 타입별 형태룰 + 적용도구 (런타임 조회) | DATA/SIM/미지타입 |
| 7 | `d8fdb16` | manifest `input_requirements` — graph_type DATA→도구 연결 완결 | describe(DATA,stress_strain)→stress_strain_plot |

**연기 (Phase 5·6 — data_profiles 룰 엔진):** "같은 타입 자동 인식·저장규칙"은
실데이터 헤더 패턴 20건+ 누적 후 착수 (과설계 방지, 적대검증 권고). 그 전까진
team/group 을 사용자가 확인.

## 실사용법 (Claude Desktop)

신규 MCP write 도구는 `api_key` 인자에 X-API-Key 를 넣어 호출 (AUTH_REQUIRED=true 시 필수).

1. **표/CSV 저장**: 사용자가 표를 붙임 → Claude 가 dict 파싱 →
   `import_record(record, dry_run=true)` → 부족필드 되물음 → `dry_run=false` 저장.
2. **파일 정밀변환** (docx/xlsx/pdf/pptx): 사용자가 서버 inbox(`~/aidh-inbox`,
   `AIDH_CONVERT_INBOX` 로 변경) 에 파일을 두고 → `convert_file("name.xlsx")` →
   record 초안 → `import_record` 로 저장. 경로/`..` 불가 (보안).
3. **Agent 정의**: `draft_agent(hint=...)` → 초안 → `create_agent(agent)`.
   빈 stub 채우기는 `patch_agent`.
4. **분석 흐름**: 데이터에 `graph_type` 담아 저장 →
   `describe_data_capability(DATA, graph_type)` 로 적합 도구 발견 → 그 도구 호출.

## MCP 파일 업로드의 현실 (사용자 질의 답)

**MCP 는 바이너리를 못 받는다** (프로토콜 제약 — 도구 인자는 JSON). 우회 3경로:
- **Path A (기본)**: Claude Desktop 이 첨부를 파싱 → `import_record(dict)`.
  작은/구조화 데이터(표·CSV). 한계: 큰 파일 잘림, 복잡한 xlsx/pdf 부정확.
- **Path B (`convert_file`)**: inbox 파일을 서버가 정밀변환(수식·병합셀). 동일 호스트.
- **Path C (대시보드)**: `POST /api/convert` 브라우저 업로드. 원격/경로없음.

바이너리는 절대 MCP 를 통과하지 않는다 — Claude 가 파싱하거나(A) 서버가 경로/업로드로 읽는다(B/C).

---

---

# v2 개정 — 적대적 검증 반영

6개 적대적 렌즈가 v1을 공격해 45개 문제(blocker 9·major 26·minor 10)를 확정. 핵심 변경:

## 판정: 계획 유지하되 **순서 재배열 + 5개 blocker 설계 수정 + Phase 0 신설**

v1의 전략 뼈대(in-process MCP, 선언적 manifest, DATA_TYPES 불변, 단일 진실원천)는
공격에 견뎠다. 그러나 **단계 순서가 가치를 뒤로 미뤘고, 5개 blocker가 그대로 가면
런타임에 터진다.** 폐기 아님 — 재배열 + 수정.

## 5개 Blocker와 설계 수정 (반드시 반영)

| # | Blocker (동의 렌즈 수) | v1의 문제 | v2 수정 |
|---|----------------------|-----------|---------|
| B1 | **stateless 멀티스텝** (3) | `validate_and_ask`→`import_record` 분리 → MCP stateless라 dict 유실/JSON 타입변질(float→str) silent 손상 | **단일 도구 `import_record(record, dry_run)`로 통합.** dry_run=true → `{status, ask_user, suggestions}`; Claude가 채워 dry_run=false로 **완전한 dict 재전송**. 각 호출 독립(stateless-clean). 별도 validate 도구 없음 |
| B2 | **base64 PNG 토큰 고갈** (1, 치명) | 인라인 이미지 = 응답당 ~50K 토큰, 대화 누적 시 Claude 컨텍스트 마비 | **이미지는 attachment URL 반환** (`capture_files` 경로 재사용). base64 인라인 금지. Claude/사용자가 URL로 조회 |
| B3 | **team/group silent error** (2) | 잘못된 프로파일 매칭 → 엉뚱한 team 저장 → 검색 누락, rollback 없음 | **추론된 team/group은 절대 자동확정 금지.** `suggestions`에 `{suggested, source, score, allow_override}` 넣어 Claude가 "팀 HE로 추론됨, 맞나요?" 반드시 확인 |
| B4 | **graph_type 유실** (1) | `_extract_data()`가 dict 명시 재구성으로 extra 필드 폐기 → `extra='allow'`가 무의미 | `_extract_data()`를 `DataContent.model_dump()` 기반으로 바꿔 검증된 전 필드 보존. `build_json_schema` oneOf에도 graph_type properties 추가 |
| B5 | **MCP 인증 공백** (1) | `mcp_runtime`이 X-API-Key 미검사 → write 도구가 `require_api_key` 우회 | **Phase 0에서 MCP write 도구에 auth + actor 주입.** AUTH_REQUIRED=true 시 키 검증, audit용 principal 기록 |

## Phase 0 (신설) — 공통 전제. 모든 write 도구의 토대

write 계열 MCP 도구(import/create/patch)를 만들기 전 1회 깔아야 하는 것:
- **MCP write-tool 인증** — X-API-Key 파라미터 + `get_principal` 동등 검증 (B5)
- **서비스 계층 추출** — FastAPI 핸들러는 `Depends()` 의존이라 MCP에서 직접 호출 불가.
  agent/record/doc_type write 로직을 service 함수로 분리 → REST·MCP 양쪽이 **같은 서비스 호출**
  (이게 단일 진실원천의 실제 구현. "핸들러 재사용"은 거짓이었음)
- **에러 봉투 표준** — `{error, code, recoverable, suggestion}` (예: `team_not_found` →
  "조직 관리자에게 HE 등록 요청"). Claude가 사용자에게 의미있게 설명 가능
- **actor 기록** — `Record`에 `changed_by/changed_at`(nullable) 추가, `_import_one`에 actor 전달

## 재배열 — 가치 빠른 순 (MVP-first)

v1은 Phase 1(describe_data_capability)이 **사용자 가치 0**(LLM 내부용)인데 맨 앞이었다.
사용자 핵심 요구(drag&drop→저장→그래프)가 Phase 3·7로 밀려 있었다. v2 순서:

| v2 | 내용 | (v1 대응) | 가치 | 난이도 |
|----|------|-----------|------|--------|
| **0** | 공통 전제 (auth/service/error/actor) | (신설) | 토대 | medium |
| **1** | `import_record(dry_run)` — drag&drop→되묻기→저장 | (v1 P3) | **첫 가시 가치** | medium |
| **2** | Agent/DocType 정의 래퍼 (draft/create/patch) | (v1 P5) | 독립 유용 | medium |
| **3** | graph_type 메타 + manifest input_requirements (+B4 수정) | (v1 P2) | 라우팅 기반 | medium |
| **4** | `describe_data_capability` — LLM 컨텍스트 | (v1 P1) | 폴리시 | easy |
| **5** | `data_profiles` classify — team/group 자동제안(항상 확인) | (v1 P4) | 자동화 | hard |
| **6** | 룰 엔진 폴백: 애매하면 되묻기 + 미지타입 승격 | (v1 P6) | 견고성 | hard |
| **7** | capability_tools 자동분석 (이미지=URL) | (v1 P7) | 능동화 완결 | hard |

**MVP 경계 = Phase 1~2.** 이것만으로 "drag&drop → 필드 되묻기 → 저장" + "Agent 정의를
대화로"가 동작. **data_profiles(Phase 5)는 후순위** — 실제 헤더 패턴이 20건+ 쌓인 뒤
빈도 보고 착수(과설계 방지). 그 전까지 team/group은 사용자 입력 또는 `recommend_agents` 추론.

## 룰 엔진 결정 — 신규 테이블, 단 MVP에서 제외

- doc_type 확장이 아니라 **신규 `data_profiles` 테이블** (관심사 분리: doc_type=의미분류,
  profile=signature→저장규칙). 단 `code` String(100), 동의어 `header_aliases`, 명시적
  `score_formula`(jaccard/precision), 추론 team/group **항상 사용자 확인**.
- **MVP에선 안 만든다.** Phase 5로 미루고, 실데이터 패턴 누적 후 착수.

## 데이터 무결성 방어 (major 다수)

- **CSV 잘림** — Claude가 큰 CSV를 truncate 파싱 → 부분 매칭/부분 저장 위험. `sample_rows`
  100행 상한 + "partial" 경고. 대용량은 파일 업로드 경로 안내(drag&drop의 한계 명시)
- **JSON 타입변질** — `DataContent`가 셀 타입 미검증 → float가 str로. 숫자 헤더 셀은
  타입 추론·강제(coerce) 또는 `content.schema` 명시
- **동시성** — VSCode↔MCP 같은 id 경합. `Record.version` optimistic lock → 409 Conflict
- **render_template PII** — `{input.X}` 화이트리스트(`input.title/headers`만) + 256자 한도
- **부팅 캐시** — `register_all_uploads` 1회 로드. describe/classify는 **런타임 DB 조회**

## 견고해서 유지 (공격 실패)

- in-process FastMCP (별도 서버 X), 선언적 manifest/content, DATA_TYPES 불변,
  단일 진실원천 원칙(단 "서비스 계층 공유"로 구현 수정), 3단계 룰 엔진 개념(단 시점 연기)

---

# v1 원안 (결정 이력 — 위 v2가 유효)

---

## North Star (한 문장 + 완성 UX)

**"Claude Desktop에서 자연어로 데이터/Agent를 검색·조사하고, 표/그래프 데이터를
drag&drop하면 타입이 자동 인식되어 우리 DB 규격으로 저장되며, 도구가 데이터 타입에
따라 자동으로 적합한 분석(예: 그래프)을 수행한다."**

완성 시 시나리오: 사용자가 "SUS304 인장시험 표 올릴게"라며 표를 붙이면 →
Claude가 이 표가 `DATA`(stress-strain signature)임을 인식 → 빠진 필드(title)만 되묻고
→ 저장 후 곧바로 `stress_strain_plot`을 호출해 곡선을 인라인 렌더. 사용자는 표를 붙이고
제목만 답했을 뿐인데 **저장 + 그래프까지 완료**. VSCode extension에서 하던 데이터·Agent
정의가 전부 Claude 대화로 가능해진다.

---

## 핵심 아키텍처 결정 (왜 이렇게 가는가)

| # | 결정 | 이유 | 버린 대안 |
|---|------|------|-----------|
| 1 | **MCP는 현행 in-process FastMCP 유지** (별도 서버 X) | `main.py`가 이미 FastMCP를 `/mcp`로 mount + `register_all_uploads`로 동적 도구 등록 중. ReportArchive가 서버를 분리한 이유(의존성 충돌)가 AIDH엔 없음 — 한 프로세스에서 이미 공존. | ReportArchive식 별도 `mcp_server/` 프로세스 + REST 프록시 (배포 유닛/헤더포워딩 추가 부담) |
| 2 | **신규 능력은 전부 "선언적 manifest 확장 + content 메타필드"로** (코드 `if data_type==X` 분기 금지) | `DataContent`가 `extra="allow"`라 `graph_type` 메타필드를 마이그레이션 0으로 추가 가능. 능력을 코드가 아니라 DB(manifest/content)에 두면 VSCode와 Claude Desktop이 **같은 단일 소스**를 봄. | 도구별 파이썬 핸들러에 `data_type`별 if 분기 — 새 타입마다 코드 수정/재배포, 두 인터페이스 동기화 불가 |
| 3 | **데이터 타입 룰 엔진은 신규 `data_profiles` 테이블 1개로** (`DATA_TYPES`/`doc_type` enum 무변경) | `DATA_TYPES` 7-enum은 id 포맷·정규식·DB 컬럼에 박혀있어 확장 시 **모든 레코드 ID가 깨짐**. `data_profiles`를 옆에 두고 (signature→저장규칙)만 담당시키면 기존 인제스트 무변경 점진 도입. | enum 확장 / doc_type에 inference 우겨넣기 |
| 4 | **drag&drop = "Claude가 첨부를 dict로 파싱 → MCP가 import dry_run 호출"** 멀티스텝 (바이너리 업로드 후순위) | MCP는 바이너리 미지원. 하지만 Claude Desktop은 첨부 표/텍스트를 이미 읽어 컨텍스트로 가짐 — 그 파싱 결과를 dict 인자로 넘기면 충분. import 라우트가 이미 dry_run+auto_seq+normalize 지원. | base64 파일 업로드 도구 (payload 한계 + xlsx/pdf 재추출 중복) |
| 5 | **도구의 "입력 요구"와 "타입별 분기"는 manifest `input_requirements` + 신규 `describe_data_capability` 도구로 노출** | 현재 `llm_hints`는 자연어뿐이라 LLM이 "어떤 record를 찾아야 하는지" 구조적으로 모름. ReportArchive `describe_template`이 "작성 룰을 미리 주고 LLM이 따르게" 하는 패턴 차용. | get_record 후 LLM이 매번 content를 눈으로 추론 (비결정적, 토큰 낭비) |

---

## 단계별 구현 (하나씩 진행)

각 단계는 독립 검증 가능. `depends_on` 순서 준수.

### Phase 1 — `describe_data_capability` (읽기전용, 무위험 진입점) · 난이도: easy

**목표:** data_type(+doc_type)별로 "content 형태 룰 + 적용 가능 도구"를 반환하는 MCP
도구 추가. LLM이 검색 전에 "이 타입 데이터는 이렇게 생겼고 이 도구로 분석한다"를 미리 학습.

- `discover_svc`의 `CONTENT_SHAPE_HINTS` + `DATA_TYPE_DESCRIPTIONS`를 묶는
  `build_data_capability(session, data_type, doc_type=None)` 작성
- 등록된 `mcp_uploads` manifest를 스캔해 `persist_output.data_type`이 매칭되는
  도구명·`llm_hints`를 `capability_tools`로 수집
- `mcp_runtime.py`에 `@mcp.tool describe_data_capability` 등록
- `ingest_guide_svc.build_guide`를 재사용해 필수/권장 필드 표를 합침 (중복 구현 금지)

**파일:** `services/discover_svc.py`, `mcp_runtime.py`, `services/ingest_guide_svc.py`
**검증:** `describe_data_capability(data_type='DATA')` → `{required:[headers,rows],
optional:[...], capability_tools:[...]}` 반환. 기존 도구 무영향.
**의존:** 없음

### Phase 2 — manifest `input_requirements` + content `graph_type` 메타필드 · 난이도: medium

**목표:** 도구가 "내가 받을 입력 데이터 모양"을 선언하고, DATA content가 `graph_type`을
담게 함. 코드 분기 없이 선언으로 타입별 도구 라우팅 기반 마련.

- `mcp_upload_svc.py`에 `InputRequirements` dataclass 추가 + `UploadManifest`에 필드
- `validate_manifest`에 `input_requirements` 파싱·검증 (persist_output 패턴 그대로)
- `data.py DataContent`에 `graph_type/x_axis/y_axis/scale` 옵셔널 (마이그레이션 0)
- `stress_strain_plot` manifest에 `input_requirements` 예시 추가 (레퍼런스)
- Phase 1 도구가 `graph_type`별로 도구를 매칭하도록 확장

**파일:** `services/mcp_upload_svc.py`, `schemas/data.py`, `examples/wave-5/stress_strain_plot/manifest.yaml`
**검증:** `input_requirements` 포함 manifest 통과(단위테스트). graph_type 보존 확인.
**의존:** Phase 1

### Phase 3 — `validate_and_ask` + `import_record` MCP 래퍼 (drag&drop 자동채움·되묻기) · 난이도: medium

**목표:** Claude가 파싱한 dict를 dry_run 검증하고, 빠진 필수 필드만 구조화해 되묻게 함.
최종 저장도 MCP에서. ReportArchive `create_report_draft`의 "느슨한 입력→정규화→warnings" 동형.

- `records.py`의 dry_run 분기를 재사용하는 `validate_interactive(record, agent_type)`
  → `missing_required`·`warnings`·`suggestions`(team/group 추론) 반환
- title 자동 **제안** (content.caption/첫 헤더/파일명 기반 — 확정 금지, 제안만)
- `@mcp.tool validate_and_ask(record, agent_type=None)` → `{status, ask_user, suggestions}`
- `@mcp.tool import_record(record, dry_run=true)` → 기존 `_import_one` 경로 호출
- `_INSTRUCTIONS`에 "drag&drop → dict 파싱 → validate_and_ask → 되물은 뒤 import_record" 흐름

**파일:** `routes/records.py`, `mcp_runtime.py`
**검증:** title 누락으로 `validate_and_ask` → `status=incomplete, ask_user=['title']`.
title 채워 `import_record(dry_run=false)` → auto_seq 채번·저장. REST 무영향.
**의존:** Phase 1

### Phase 4 — `data_profiles` 룰 엔진 1단계 (가장 어려운 핵심) · 난이도: hard

**목표:** 신규 데이터의 헤더/구조로 기존 profile을 점수 매칭하고, 매칭되면 저장규칙
(team/group/tags/graph_type/dedup/capability_tools)을 자동 채움. **무ML 결정적 1단계.**

- alembic 신규 마이그레이션 → `data_profiles` 테이블 (스키마는 아래 룰 엔진 섹션)
- `data_profile_svc.classify(headers, sample_rows, data_type)` — `required_headers`
  교집합 + `header_regex` + `units_required`로 0~1 점수화 (임베딩 미사용, 결정적)
- `apply_storage_rules(record, profile)` — storage_rules를 record에 머지
  (사용자 입력 있으면 덮어쓰지 않음)
- `@mcp.tool classify_data_profile(headers, sample_rows, data_type='DATA')`
  → `matches[{code, score, storage_rules, capability_tools}]`
- Phase 3 `validate_and_ask`가 classify 결과로 suggestions를 채우도록 연결
- `stress_strain_table` 시드 프로파일 1건 등록 (레퍼런스)

**파일:** `db/models.py`, `services/data_profile_svc.py`, `mcp_runtime.py`, alembic
**검증:** strain/stress 헤더로 `classify_data_profile` → `stress_strain_table` 최고점.
apply 후 team=HE·group=CAE·graph_type=stress_strain 자동 채움. 무관 헤더면 저점→폴백.
**의존:** Phase 2, 3

### Phase 5 — Agent/DocType 정의를 Claude에서 (draft/create/doc_type 래퍼) · 난이도: medium

**목표:** VSCode extension의 Agents/doc_type CRUD를 Claude 대화로 이관. REST는 전부
이미 존재(`/api/agents` POST/PATCH, `/api/agents/draft`, `/api/doc-types`) → 얇은 래퍼만.

- `@mcp.tool draft_agent(record_ids?, filter_tags?, filter_data_types?, hint?)`
- `@mcp.tool create_agent(...)` / `patch_agent` 래퍼 (REST 핸들러 재사용)
- `@mcp.tool list_doc_types()` / `create_doc_type(...)` 래퍼
- `@mcp.tool describe_agent_schema()` — agent 폼 구조 반환 (VSCode form 대체 introspection)
- `_INSTRUCTIONS`에 "agent/doc_type 정의도 대화로 가능" 흐름

**파일:** `mcp_runtime.py`, `routes/agents.py`, `routes/doc_types.py`
**검증:** `draft_agent(hint='배터리 시험보고서')` → 초안 반환. `create_agent` 저장 후
`list_agents` 노출.
**의존:** Phase 1

### Phase 6 — 룰 엔진 2·3단계: 애매 매칭 되묻기 + 미지 타입 승격 · 난이도: hard

**목표:** 1단계 매칭이 낮을 때 graceful 폴백. Claude가 확인하거나 새 종류를 unofficial
프로파일로 제안·등록. ReportArchive `report_types`의 official/unofficial crowd-source 차용.

- classify 점수 임계값(예: 0.6) 미만 → `status='ambiguous'` + top-k 후보. Claude가
  "이거 X 타입 맞나요?" 되묻도록 `_INSTRUCTIONS` 규약
- `@mcp.tool propose_data_profile(name, data_type, required_headers, storage_rules?, description)`
  → `status='unofficial'`로 INSERT (승인 전 사용 가능, 검색 노출)
- 운영자 승격: `PATCH /api/data-profiles/{code} status=official`
- classify가 unofficial도 후보에 포함하되 score 페널티 가중

**파일:** `services/data_profile_svc.py`, `mcp_runtime.py`, `routes/records.py`
**검증:** 미등록 헤더 → `ambiguous`. propose 후 재classify → 그 unofficial 매칭. official 승격 후 페널티 소거.
**의존:** Phase 4

### Phase 7 — `persist_output {input.X}` 템플릿 + capability_tools 자동 연계 · 난이도: hard

**목표:** 도구가 입력 record content를 참조해 title/dedup 동적 생성, 데이터→도구 호출이
capability_tools로 자동 제안. **수동 챗봇 → 능동 분석 에이전트 전환 완결.**

- `render_template`의 ctx에 `input` 스코프 추가 — `{input.headers}`, `{input.title}` 등
- `_persist_record_insert` 호출부에서 입력 record content를 `ctx['input']`에 주입
- describe/classify의 capability_tools를 `recommend_agents`의 relevant_tools와 합침
- `_INSTRUCTIONS`에 "graph_type 있는 record를 보면 capability_tools로 자동 분석 제안"

**파일:** `services/mcp_upload_svc.py`, `mcp_runtime.py`
**검증:** `{input.title}` 반영 저장 확인. graph_type=stress_strain record 검색 →
Claude가 stress_strain_plot 자동 제안 → 호출 → PNG 렌더의 **end-to-end 1회 성공** (North Star 재현).
**의존:** Phase 4, 2

---

## 가장 어려운 핵심 — 데이터 타입 룰 엔진

### 문제

"같은 데이터 타입을 나중에 찾고 저장하는 룰." 현재 타입 결정은 `detect_variant()`의 구조 키
휴리스틱뿐 (`headers+rows`면 무조건 `DATA`). 신규 표가 들어올 때 "기존 stress-strain 표들과
같은 종류인가"를 판별하는 신호가 없고, 타입별 저장규칙은 SyncSource당 1개 `mapping_rules`로
외부시스템에만 묶여 있음. `persist_output`의 data_type/team/group/dedup도 manifest에 완전
하드코딩이라 도구가 입력에 적응 못 함.

### 접근 — 완벽한 ML 추론을 처음부터 만들지 않는다. 3단계 점진.

1. **선언적 signature 매칭** — `data_profiles`에 "이 프로파일은 어떤 content 모양인가
   (required_headers, 정규식, units 필요 여부)"와 "매칭되면 어떤 저장규칙(team/group/
   default_tags/suggest_agents/dedup/doc_type)을 쓰나"를 사람이 1회 정의. 신규 표가
   들어오면 헤더 집합 교집합/정규식으로 best-match를 점수화 (임베딩 불필요, 결정적).
2. **LLM 보조** — 점수가 낮으면 Claude가 후보 프로파일을 받아 "이거 stress-strain 표
   맞나요?"를 되묻고, 답을 받아 프로파일 확정.
3. **crowd-sourced 등록** — 매칭 프로파일이 없으면 Claude가 `propose_data_profile`로
   초안을 만들어 unofficial 저장. 운영자가 나중에 승격.

핵심: 1단계만으로 알려진 타입은 즉시 동작, 미지 타입은 2·3단계가 graceful하게 받음.

### 스키마 제안

```
-- 신규 테이블 data_profiles (alembic)
code             String(60) PK            -- 'stress_strain_table'
name             String(120)              -- '응력-변형률 측정 표'
data_type        String(10)               -- 'DATA' (7-enum 중 1)
doc_type         String(40) nullable      -- doc_types.code FK
description      Text                     -- ★Claude가 인식에 쓰는 자연어
                                          --  (사용자가 "그래프 타입을 Description에
                                          --   정의"하려던 바로 그 슬롯)
match_rules      JSONB  -- {required_headers:[], header_regex:[], units_required:bool,
                        --  min_rows:int, signal_keys:[]}
storage_rules    JSONB  -- {team, group, default_tags:[], suggest_agents:[],
                        --  dedup_template:'{h0}_{h1}',
                        --  content_meta:{graph_type:'stress_strain', x_axis, y_axis}}
capability_tools JSONB  -- ['stress_strain_plot']  -- 이 프로파일에 붙는 분석 도구
status           String(12) default 'unofficial'  -- official|unofficial
created_by, approved_by, created_at
```

```
-- DATA content 메타필드 (data.py DataContent, extra="allow" → 마이그레이션 0)
graph_type: str|None   -- 'stress_strain'|'time_series'|'scatter'|'table'
x_axis, y_axis, scale: str|None
-- 저장 시 storage_rules.content_meta를 record.content에 머지.
-- 이후 stress_strain_plot 등 도구가 get_record로 graph_type을 보고 분기.
```

```
-- UploadManifest 확장 (mcp_upload_svc.py)
input_requirements: {data_type:'DATA', required_headers:[], units_required:bool,
                     graph_type:'stress_strain'}
-- describe_data_capability가 읽어 "stress-strain graph_type DATA를 가진 도구는
--  stress_strain_plot"을 LLM에 노출.
```

### 풀 시나리오 — 그래프 데이터 1건의 정의→인식→저장

1. **[정의·1회]** 운영자(또는 Claude `propose_data_profile`)가 `data_profiles`에
   `code='stress_strain_table'` 등록: `match_rules={required_headers:['strain','stress'],
   units_required:true, min_rows:5}`, `storage_rules={team:'HE', group:'CAE',
   default_tags:['stress-strain','material'], content_meta:{graph_type:'stress_strain',
   x_axis:'strain', y_axis:'stress'}}`, `capability_tools=['stress_strain_plot']`.
2. **[인식·런타임]** 사용자가 SUS304 표(headers=[strain, stress(MPa)], 12행)를 붙이며
   "저장하고 곡선 그려줘". Claude가 headers를 `classify_data_profile`에 넘김 → 서버가
   모든 프로파일의 match_rules와 교집합 점수화 → strain·stress 일치 + units 있음 →
   `stress_strain_table` 0.95 매칭 (저장규칙·capability_tools 동봉).
3. **[저장]** Claude가 import dry_run으로 초안 구성: data_type=DATA, team=HE, group=CAE
   (자동), content.graph_type='stress_strain'(머지), tags 자동. title만 비어 warnings →
   Claude가 "제목을 SUS304 인장시험으로 할까요?"만 되물음 → 사용자 OK → import(dry_run=false)
   → auto_seq로 `DATA-HE-CAE-2026-...` 채번·저장.
4. **[자동 분석]** capability_tools에 stress_strain_plot이 있으므로 Claude가 곧바로 호출
   → PNG 인라인 렌더. **사용자는 표를 붙이고 제목만 답했을 뿐.**

---

## Claude Desktop 대화 시나리오

1. **검색→조사→안내:** "CAE 시뮬 분석 에이전트랑 맞는 데이터 뭐 있어?" → `recommend_agents`
   → `get_agent_session`(persona 채택) → `describe_data_capability(agent_type)`로 "이 agent는
   SIM/DATA, graph_type=stress_strain 데이터를 다루고 도구는 stress_strain_plot" 안내 →
   `agent_search`로 실제 레코드를 출처 id와 제시. *(신규는 describe_data_capability 1개)*
2. **drag&drop→자동채움→되묻기:** 인장시험 CSV 붙이며 "DB에 넣어줘" → Claude가 표 파싱 →
   `classify_data_profile` 0.9 매칭 → import dry_run, team/group/tags/graph_type 자동 →
   "제목만 정해주세요. 나머지는 자동 설정" → "SUS304 인장시험" → 저장 완료.
3. **타입별 자동 분기(그래프):** "방금 넣은 SUS304로 곡선 보여줘" → `get_record`로
   graph_type 확인 → capability_tools의 stress_strain_plot 자동 호출 → PNG 렌더 + 결과가
   SIM 레코드로 자동 적재되어 다음 검색에 노출.
4. **Agent 정의를 Claude에서:** "배터리 셀 시험보고서만 찾는 에이전트 만들어줘" →
   `draft_agent` → system_prompt/sample_queries 초안 → "필수 doc_type을 test_report로
   할까요?"만 되물음 → `create_agent` 저장. *(VSCode Agents 탭을 대화로 대체)*

---

## 리스크 (반드시 지킬 것)

1. **`DATA_TYPES` 7-enum 절대 확장 금지** — id 포맷·정규식·records.data_type 컬럼에
   박혀있어 확장 시 모든 레코드 ID가 깨짐. 세부타입은 `data_profiles`/`doc_type`/
   `content.graph_type` 층에만.
2. **MCP stateless** — 대화 상태는 전적으로 Claude(클라이언트)가 보유. validate_and_ask
   →되묻기→import_record의 멀티스텝 상태를 서버에 두면 안 됨 (매 호출 독립 전제).
3. **drag&drop ≠ 파일 업로드** — Claude Desktop은 로컬 바이너리 접근 차단. 실체는
   "첨부를 텍스트/표로 파싱해 dict 전달". xlsx/pdf 원본은 여전히 VSCode convert.py 경로
   필요 — 사용자 기대와 갭, 명시 안내 필요.
4. **결정적 헤더매칭의 한계** — strain vs 변형률 vs eps 동의어를 1단계는 놓침. 의도적으로
   단순하게 두고, 동의어/임베딩 유사도는 점수 낮을 때만 2단계(되묻기)로 보완. 처음부터
   임베딩 스키마 유사도 넣으려다 과설계 경계.
5. **`{input.X}` 템플릿 주입 위험** — 큰 content/PII가 title·dedup에 샐 수 있음.
   `_lookup`에 길이 제한·허용 키 화이트리스트.
6. **부팅 1회 캐시 의존 금지** — `register_all_uploads`는 부팅 시 동기 로드. classify/
   describe는 런타임 DB 조회로 (describe_template처럼 매 호출 조회).
7. **단일 진실원천 유지** — MCP 래퍼는 반드시 기존 REST 핸들러/서비스 함수를 **재사용**.
   별도 쓰기 경로를 만들면 VSCode와 Claude Desktop이 다시 갈라짐.

---

## 지금 당장 시작 — Phase 1

`discover_svc.py`에 `build_data_capability(session, data_type, doc_type=None)` 추가:
- 기존 `CONTENT_SHAPE_HINTS[data_type]` + `DATA_TYPE_DESCRIPTIONS[data_type]` 읽기
- `ingest_guide_svc.build_guide(session)`에서 해당 타입 필드 표 끌어옴 (중복 구현 금지)
- `mcp_uploads` manifest를 읽어 `persist_output.data_type` 일치 도구를 capability_tools로
- `mcp_runtime.py`에 `@mcp.tool describe_data_capability` 등록 (get_record 패턴 그대로)

읽기전용·신규 도구 1개라 기존 동작 무영향. `curl /mcp`로 응답 JSON만 확인하면 검증 끝.

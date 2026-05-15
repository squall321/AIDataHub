# Mobile eXperience AI Data Hub — 표준 운영 절차서

문서/측정/시뮬레이션 데이터를 적재하고, 그 데이터를 담당할 agent를 정의하고,
LLM 클라이언트(Claude / Codex / Gemini / Cline / Cursor / Copilot)가 그 agent의
성향대로 데이터를 검색·응답하게 만드는 전체 절차의 표준 설명이다.

본 문서는 실제 구현·검증된 동작만 기술한다.

---

## 0. 전체 라이프사이클

```
[1] 데이터 적재        파일 업로드 + 메타데이터 부여 → DB(record) + RAG 청크(record_sections) 생성
        │
[2] Agent 정의         수동 입력 또는 LLM 초안 → retrieval_config / system_prompt / response_config
        │
[3] 데이터 ↔ Agent 연결  업로드 시 agents= 직접 지정  +  저장 후 bind-matching 자동 연결
        │
[4] Agent 활성화        Console 탭 → 자동 설치 → AI 클라이언트에 MCP 등록 + system_prompt 주입
        │
[5] 런타임 사용         LLM이 MCP 도구로 agent 세션 초기화 → 검색 → 인용 응답
        │
[6] 운영 루프           새 데이터 추가 시 재바인딩 / sample resync / agent 튜닝
```

[1]~[3]이 비어 있으면 DB는 잠자는 데이터다. [4]가 "DB → LLM이 실제 사용"의 다리다.

---

## 1. 데이터 적재

### 경로
- Extension **Upload 탭**: 원본 파일 드래그-드롭 (`.docx .pdf .pptx .xlsx .md`)
- Extension **Bundle 탭**: 사전 변환된 JSON+리소스 zip
- API 직접: `POST /api/convert/ingest` (multipart)

### 필수 메타데이터
| 필드 | 설명 |
|---|---|
| `team` | 팀 코드. 대문자 2~4자. org_teams 마스터에 등록돼 있어야 함 (strict). |
| `group` | 그룹 코드. 대문자 **2~5자** (예: CAE, MFG, DOCS). 초과 시 거부. |
| `year` | 4자리 연도 (2020~2099). |
| `file` | 업로드 파일. |

### 선택 메타데이터
`seq`, `tags`, `agents`, `classification`, `status`, `domain`, `subject_keywords`,
`title_override`, `summary_override`, `agent_hints`, 등.

### 동작 규칙 (반드시 숙지)
- **record id 형식**: `{DATA_TYPE}-{TEAM}-{GROUP}-{YEAR}-{SEQ:010d}`
  예: `DOC-HE-CAE-2026-0000000005`
- **seq 자동 할당**: `seq`를 비우면(또는 0) 백엔드가 `(data_type, team, group, year)`
  튜플 단위로 `MAX(seq)+1`을 자동 부여한다. 명시하면 그 값으로 upsert.
  → seq를 명시하지 않아도 기존 레코드를 덮어쓰지 않는다.
- **태그/agents 병합**: 사용자가 폼에서 넘긴 `tags`/`agents`는 컨버터가 본문에서
  추출한 태그와 **합집합(union)**으로 보존된다. 둘 중 하나가 사라지지 않는다.
  → bind-matching이 핵심 문서를 놓치지 않게 하는 핵심 동작.
- **data_type**: 파일/내용으로 추론 (DOC / DATA / SIM / CAD / LOG / FORM / OTHER).
- 적재 결과로 `record` 1건 + `record_sections` N건(RAG 청크, 임베딩 포함)이 생성된다.

### 확인
- 대시보드 **카탈로그 탭** 또는 `GET /api/discover`로 `total_records` / `by_data_type` 확인.

---

## 2. Agent 정의

Agent는 단순 태그가 아니라 **검색·응답 레시피**다.

| 구성 | 의미 |
|---|---|
| `agent_type` | PK. kebab-case 권장. |
| `name` / `description` | 카탈로그 표시. |
| `common_tags` | 이 agent가 다루는 주제 태그. bind-matching 기준. |
| `data_types` | 소비하는 record 타입. agent_search 범위 필터로도 사용. |
| `required_doc_type` / `required_tags` / `excluded_tags` | 기대 스키마. bind-matching 매칭 규칙. |
| `retrieval_config` | `top_k`, `score_threshold`, `data_type_filter`, `tag_boost`. 검색 시 자동 적용. |
| `system_prompt` | LLM에 주입될 페르소나. `{base_url}/{agent_type}/{agent_name}` 치환, 도구 가이드 자동 append (`<!-- no-tool-guide -->`로 끄기). |
| `response_config` | `max_tokens`, `citation_required`, `refusal_message`, `refuse_below_score`. |
| `sample_queries` | 라우팅 정확도용 예시 질문. 임베딩되어 recommend_agents에 반영. |

### 생성 방법
1. **수동**: Extension Agents 탭 → "New agent" → 폼 입력.
2. **LLM 초안 (권장 시작점)**: Agents 탭 → "LLM 초안 생성"
   - 의도 힌트 입력 (선택)
   - **데이터 군 한정 (선택)**: 태그 칩 / data_type CSV → 그 데이터군만 분석
   - `OPENAI_API_KEY`(또는 `OPENAI_BASE_URL` 사내 호환 백엔드) 있으면 LLM 초안,
     없으면 빈도 기반 휴리스틱 초안
   - 결과가 폼 전체에 채워짐 → **검토·수정 필수** → 저장
3. **수정**: Agents 탭 목록 → 해당 agent **Edit** → `agent_type` 외 전부 수정 가능 (PATCH).
   변경 이력은 `agents_history`에 append-only로 남는다.

### 저장 전 테스트
폼의 "Test preview" → 현재 레시피로 실제 검색 + (LLM 키 있으면) 답변 미리보기.

---

## 3. 데이터 ↔ Agent 연결

두 경로가 있고, 둘 다 `agent_records` junction을 채운다.

1. **적재 시 직접 지정**: 업로드 폼 `agents=iga-analyst,cae-analyst`.
2. **저장 후 자동 바인딩 (bind-matching)**:
   - Agents 폼의 "저장 후 매칭 레코드 자동 바인딩" 체크 → 생성 직후 실행
   - 또는 `POST /api/agents/{agent_type}/bind-matching`
   - 매칭 규칙 (AND): `required_doc_type` 일치 + `data_types` 포함 +
     (`required_tags` 모두 포함 / 없으면 `common_tags` 1개 이상 겹침)
   - 이미 바인딩된 레코드는 건너뜀. 응답에 `scanned` / `bound_count` 반환.

> 데이터 → LLM이 agent 제안 → 저장 → 자동 바인딩으로 루프가 닫힌다.

---

## 3-1. 계층 데이터 (campaign ↔ specimen) — Migration 0017

여러 시료 시험처럼 "집단(campaign) + 개별 시료(specimen)" 구조는 record ID가
아니라 `parent_record_id` 참조 + `depth` 컬럼으로 표현한다. ID는 영구 불변
(인용 키)이라 ID에 계층을 인코딩하지 않는다 — 재부모화 시 ID가 깨지기 때문.

- **depth**: 0 = campaign/root, 1 = specimen, ... `parent_record_id` 설정/변경
  시 자동으로 `parent.depth + 1` 재계산 (ingest + PATCH 양쪽).
- **연결 경로**:
  1. 적재 시 — Upload 폼의 `parent_record_id` 입력칸 (= campaign id)
  2. 사후 — `PATCH /api/records/{id}` 의 `parent_record_id`
- **포맷 유사 부모 추천**: `GET /api/records/{id}/suggest-parent` →
  doc_type/team-group/data_type/섹션구조/태그 유사도로 후보를 점수화,
  "기존 자식 보유"는 가점. Extension 업로드 결과 화면의
  "유사 부모(campaign) 연결" → 후보 표 → 사람이 확인 후 한 클릭 연결.
- **lineage 조회**: `GET /api/records/{id}/lineage` (조상/자손 체인).
- **깊이로 검색 제어**: agent `retrieval_config.max_depth` 지정 시
  `agent_search` 가 `depth <= max_depth` 인 record 만 검색
  (예: `max_depth=0` = campaign 요약만, 미지정 = 전체).

판단 기준: 시료 수십 + "시험군 요약" 위주 → campaign 1 record로 충분.
시료 수백 또는 "특정 시료/시료 비교" 잦음 → specimen별 record + parent.

---

## 4. Agent 활성화 (가장 중요한 다음 액션)

DB만 쌓여 있으면 LLM은 모른다. **연결 액션**:

1. Extension **Console 탭**에서 대상 agent 선택
2. AI 클라이언트 드롭다운 선택 (Cline / Claude Desktop / Claude Code / Cursor / Copilot / Gemini / **Codex**)
3. **"1, 2, 3 불러오기"** → MCP 등록 정보 + context-bundle + system_prompt 로드
4. **"자동 설치 (MCP + Prompt)"** 클릭:
   - 클라이언트별 MCP 설정 파일에 `aidatahub` HTTP 서버 등록
   - 그 agent의 `system_prompt`를 클라이언트 룰 파일에 주입
     (Claude Code → `CLAUDE.md`, Codex → `AGENTS.md`, Cursor → `.cursorrules`,
      Cline → `cline.customInstructions`, Copilot → 설정, 그 외 수동 안내)
   - 멱등(marker block 기반) — 재실행 시 해당 블록만 교체

클라이언트별 설정 경로는 OS 자동 판별 (Windows AppData / macOS Library / Linux .config).

---

## 5. 런타임 사용 (LLM 클라이언트 측)

활성화된 클라이언트의 새 세션 흐름:

```
1. (주입된 system_prompt를 페르소나로 채택)
2. get_agent_session(agent_type)
     → system_prompt / retrieval_config / response_config / scope 수신
3. 사용자 질문
     → agent_search(agent_type, q, mode="semantic"|"fts"|"tag")
       · retrieval_config 자동 적용 (top_k / score_threshold / data_type_filter / tag_boost)
       · agent 소속 record로 범위 제한
       · required/excluded_tags 후필터
       · refuse_below_score 미달 시 refused=true + refusal_message
4. get_record_sections(record_id)   ← 상세 RAG 청크
5. 답변 — 사실마다 (source: <record_id> §<section_id>) 인용
```

여러 agent를 오갈 때는 재설치 없이 LLM이 `recommend_agents(q)` → `get_agent_session()`으로
런타임 전환 (MCP 도구라 클라이언트가 자율 호출).

### MCP 도구 일람
| 도구 | 용도 |
|---|---|
| `discover` / `list_agents` | 카탈로그·agent 목록 |
| `recommend_agents(q)` | 자연어 → 적합 agent 추천 |
| `get_agent_session(agent_type)` | persona+설정 초기화 (세션 첫 호출) |
| `agent_search(agent_type, q, mode)` | retrieval_config 자동 적용 검색 (주 도구) |
| `semantic_search` / `fts_search` / `tag_search` | 범용 검색 (agent_type 옵션) |
| `get_record` / `get_record_sections` | 레코드 상세 / 청크 |
| `get_context_bundle` | agent records+sections 묶음 (sample_queries anchor로 threshold 필터) |

---

## 6. 운영 루프

새 데이터가 들어오거나 agent를 다듬을 때 (현재 수동 액션):

- 새 레코드 적재 → 해당 agent `bind-matching` 재실행
- `sample_queries` 변경 → `POST /api/agents/{agent_type}/resync-samples`
  (또는 `resync-samples-all` — EMBEDDING_DIM 변경 후 백필)
- `retrieval_config` / `system_prompt` 튜닝 → Agents Edit → 필요 시 클라이언트 재-자동설치
- 이력 누적 정리 → `POST /api/agents/history/prune`

---

## 7. 검증 / 진단

| 도구 | 용도 |
|---|---|
| `bash status.sh` | 빠른 상태 (instance/port/health) |
| `bash diag.sh [--tail-logs]` | 계층별 상세 진단 |
| 대시보드 **상태 탭** | API/임베더/레코드 수 + Extension 다운로드 링크 |
| 대시보드 **분석 탭** | 분포·타임라인·교차 분석 |
| `GET /api/discover` | 전체 카탈로그 한눈에 |

---

## 8. 알려진 제약·주의

- **GROUP 코드는 대문자 2~5자**. 초과 시 record id 검증에서 거부 (정상 동작).
- **LLM 키 미설정 시**: `/api/ask`, agent draft, preview는 키워드/휴리스틱 폴백으로
  동작한다 (기능은 됨, 품질만 낮음). `OPENAI_BASE_URL`로 사내 Qwen/Ollama 연결 가능.
- **recommend_agents 점수 정책**: record/sample 항을 각각 [0,1] 평균으로 정규화 후
  `record_mean + W*sample_mean` (W 기본 1.0, `AGENT_SAMPLE_WEIGHT`로 조정).
  평균 기반이라 정밀도(최적 적중) 우선 — 적중 소수 agent가 다수 섹션 평균보다
  앞설 수 있다 (의도된 트레이드오프).
- **MCP 동적 instructions**: FastMCP stateless 특성상 서버가 세션 instructions를
  자동 교체하지 못한다. `get_agent_session`의 system_prompt를 클라이언트 LLM이
  세션 지배 규칙으로 채택하는 방식이 표준 (instructions에 명시).
- **외부 접근**: `API_HOST=0.0.0.0`, CORS `*`. 방화벽에서 API 포트만 열면 됨.
  `setup.sh`/`install_all.sh`가 HOST_IP 자동 감지 후 `.env` 기록.

---

## 9. 새 서버 한 줄 셋업

```
bash setup.sh
```
→ PG+API 기동 → Extension 빌드·`/downloads` 게시 → (code CLI 있으면) 자동 설치 →
대시보드 자동 오픈. 이후 누구나 `http://HOST_IP:8001/dashboard`의
**VSCode Extension** 카드에서 `.vsix` 직링크로 받을 수 있다.

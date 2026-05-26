# Wave-7 — Agent ↔ Tool 통합 (recommend_agents 가 도구 추천)

작성일: 2026-05-26
선행: wave-5 (P1.9 완료, v0.5.0) — 동적 도구 등록 + persist_output + recursive RAG
관련 정책 기반: wave-5 plan §10 "Agent ↔ Tool 연결 정책" (정책만 정의, 미구현)
목표: **wave-5 도구를 자연어로 발견 가능하게 만든다.** `recommend_agents(q)` 응답에 `relevant_tools` 동봉 → LLM 이 agent 선택 직후 도구를 1-call 호출 가능.

---

## 1. 동기 + 사용 시나리오

| 시나리오 | 현재 (v0.5.0) | wave-7 후 |
|---|---|---|
| 사용자: "SUS304 stress-strain 그려줘" | discover() → list_agents → 사용자가 도구 이름 알아야 호출 | recommend_agents("SUS304 stress-strain") 가 cae_engineer agent + stress_strain_plot 도구 동시 추천 |
| 도구 노출 범위 통제 | 모든 도구가 모든 클라이언트에 노출 | restrict_agents / require_agent_tag 매니페스트 키로 필터 |
| 도구가 어느 agent 컨텍스트에 적합한가 | 사용자 추측 | tool description embedding + agent context embedding cosine 유사도 |

---

## 2. 현재 상태 — 무엇이 이미 있는가

| 컴포넌트 | 상태 | 비고 |
|---|---|---|
| `agents` 테이블 (Agent 모델) | ✓ — `common_tags`, `data_types`, `sample_queries`, `retrieval_config` 보유 | [models.py:340](api_server/src/api/db/models.py#L340) |
| `mcp_uploads` 테이블 (wave-5 동적 도구) | ✓ — `manifest` JSONB 컬럼 보유 | alembic 0021 |
| `recommend_agents` MCP tool | ✓ — sample_queries embedding cosine 검색 | [recommend_svc.py](api_server/src/api/services/recommend_svc.py) |
| `manifest.restrict_agents` 정책 정의 | ✓ (docs only) | wave-5 plan §10 |
| 도구 description embedding | ✗ — 아직 없음 | wave-7 P1 필요 |
| `recommend_agents.relevant_tools` 응답 필드 | ✗ — 아직 없음 | wave-7 P1 필요 |
| `list_tools` 의 agent-context 필터링 | ✗ — 모든 도구 무조건 노출 | wave-7 P2 필요 |

---

## 3. 결정 (확정)

| 항목 | 결정 |
|---|---|
| recommend_agents 응답에 도구 동봉 | 최상위 응답 객체에 `relevant_tools: [{name, score, description}, ...]` 키 추가 (기존 필드 호환) |
| 도구 description embedding | sentence-transformers-multilingual-e5-base 768d (sections 와 동일 모델). `mcp_uploads.description_embedding` Vector(768) 컬럼 추가 |
| 매니페스트 정책 키 | `restrict_agents`, `require_agent_tag`, `exclude_agent_tag` 3종 (wave-5 plan §10 그대로 채택) |
| 검색 알고리즘 | 1단계: q embedding ↔ agent sample_queries (기존). 2단계: q embedding ↔ tool description. 두 결과를 score 기준 통합 정렬 (현재 흐름 보존) |
| top_k_tools | recommend_agents 응답에 기본 3개. `?top_k_tools=N` 쿼리로 조정 (1~10) |
| 동적 도구 변경 시 embedding 재계산 | 업로드 파이프라인 마지막 단계에 `_embed_tool_description()` 추가 (sample_embedding_svc 패턴 재사용) |

---

## 4. Phase 분할

### Phase 1 — 도구 description embedding + recommend_agents 통합

**산출물**:

- `alembic/versions/0017_mcp_uploads_embedding.py` — `description_embedding Vector(768)` 컬럼 + HNSW 인덱스
- `api/services/tool_embedding_svc.py` — 도구별 embedding 생성/업데이트
- `api/services/mcp_upload_svc.py` 확장 — 업로드 마지막 단계에 `_embed_tool_description()` 호출
- `api/services/recommend_svc.py` 확장 — tool 검색 함수 + 응답 통합
- `api/routes/agents.py` — `?top_k_tools=N` 쿼리 파라미터
- `tests/test_tool_embedding.py` — 단위 테스트 5+
- `tests/test_recommend_with_tools.py` — 통합 테스트 3+

**완료 조건**:

1. wave-5 도구 업로드 직후 `mcp_uploads.description_embedding` 채워짐 (NOT NULL)
2. `recommend_agents(q="SUS304 stress-strain")` 응답에 `relevant_tools` 키 있음, stress_strain_plot 가 score >= 0.5 로 포함
3. 도구 description 변경 (`version +1`) 시 embedding 재계산
4. 기존 recommend_agents 클라이언트 호환 (필드 추가만, 제거 없음)

### Phase 2 — 매니페스트 정책 (restrict_agents / require_agent_tag / exclude_agent_tag) 강제

**산출물**:

- `api/services/tool_visibility_svc.py` — agent context 별 도구 필터 함수
- `api/mcp_runtime.py` 확장 — `list_tools()` 호출 시 client 가 보유한 agent context 기반 필터
- `api/routes/mcp_tools.py` — 매니페스트 validate 단계에 agent_type 존재 검증
- 매니페스트 reload 훅 — 도구 업데이트 시 즉시 반영
- `tests/test_tool_visibility.py` — restrict_agents / require_agent_tag / exclude_agent_tag 각 케이스

**완료 조건**:

1. 매니페스트에 `restrict_agents: [cae_engineer]` 명시한 도구는 `cae_engineer` context 외에서 list_tools 응답에 안 보임
2. `require_agent_tag: [structural]` 도구는 agent.common_tags 가 `structural` 포함 시만 노출
3. agent context 식별 — MCP tools/list 의 `clientInfo.name` 매핑 또는 쿼리 파라미터 `?agent_type=`
4. 정책 충돌 시 (restrict + require) AND 로 평가

### Phase 3 — Dashboard UI 통합

**산출물**:

- VSCode extension 의 Agents 탭 — 각 agent 카드에 "사용 가능 도구" subsection
- Dashboard `/agents/<type>` 페이지 — 해당 agent 의 sample_queries + 도구 매칭 점수 시각화

**완료 조건**:

1. agent 카드 클릭 시 그 agent 가 호출 가능한 도구 목록 표시
2. 각 도구 카드에 "이 agent 와의 유사도 score" 표시

---

## 5. 데이터베이스 변경

```sql
-- alembic 0017 (wave-7 P1)
ALTER TABLE mcp_uploads
  ADD COLUMN description_embedding vector(768),
  ADD COLUMN description_text TEXT;  -- embedding 의 원본 텍스트 (capture description 변경 추적)

CREATE INDEX idx_mcp_uploads_desc_embedding_hnsw
  ON mcp_uploads
  USING hnsw (description_embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
```

`description_text` 는 매니페스트의 `description` + `llm_hints.when_to_use` + `llm_hints.example_calls[*].natural_language` 를 join 한 텍스트.

---

## 6. API 설계 — recommend_agents 응답 확장

### 기존 응답 (v0.5.0)

```json
{
  "query": "SUS304 stress-strain",
  "matches": [
    { "agent_type": "cae_engineer", "score": 0.92, "name": "CAE Engineer", ... }
  ]
}
```

### Wave-7 P1 응답

```json
{
  "query": "SUS304 stress-strain",
  "matches": [
    { "agent_type": "cae_engineer", "score": 0.92, "name": "CAE Engineer", ... }
  ],
  "relevant_tools": [
    {
      "name": "stress_strain_plot",
      "score": 0.88,
      "description": "Plot stress-strain curve for given material",
      "manifest_url": "/api/mcp_tools/stress_strain_plot",
      "compatible_agents": ["cae_engineer", "materials_engineer"]
    }
  ]
}
```

신규 필드만 추가, 기존 필드 변경 없음 → 기존 클라이언트 호환.

---

## 7. 위험 / 미해결

| 위험 | 완화 |
|---|---|
| 도구 description 이 너무 짧아 embedding 품질 낮음 | `description_text` 합성 — description + when_to_use + example_calls.natural_language join |
| 도구 수 증가 시 recommend 응답 폭주 | top_k_tools 기본 3, 최대 10. 응답 크기 모니터링 (`/api/metrics/recommend`) |
| restrict_agents 정책 우회 (MCP 클라이언트가 agent_type 위조) | Phase 2 단계에서 인증 게이트 연결 (wave-5 C-4 보류 항목 — 보강 필요) |
| description 변경 시 embedding 재계산 비용 | 비동기 처리 (jobs 큐) — UI 는 "embedding pending" 상태 표시 |

---

## 8. 수락 테스트 (Phase 1 완료 기준)

| 시나리오 | 기대 |
|---|---|
| 새 도구 (csv_summary) 업로드 후 30초 내 `description_embedding IS NOT NULL` | PASS |
| `recommend_agents(q="csv 통계 요약")` → relevant_tools 에 csv_summary 포함 (score >= 0.5) | PASS |
| `recommend_agents(q="고무공 낙하 시뮬레이션")` → relevant_tools 에 stress_strain_plot 제외 또는 score < 0.3 | PASS |
| 도구 version +1 업로드 후 `description_embedding` 새 값으로 교체 | PASS |
| 기존 recommend_agents 호출 (예전 클라이언트) → 응답 200, 기존 필드 그대로 + relevant_tools 추가만 | PASS |

---

## 9. 일정 (estimate)

| Phase | 작업 | 기간 |
|---|---|---|
| P1 | DB 마이그레이션 + embedding svc + recommend_agents 통합 + 테스트 8+ | 2~3 일 |
| P2 | restrict/require/exclude 매니페스트 정책 강제 + agent_type context 식별 | 2 일 |
| P3 | Dashboard UI (VSCode ext + dashboard) | 1~2 일 |

**총 5~7 일** (v0.6.0 minor 마일스톤).

---

## 10. 완료 정의 (DoD)

**Phase 1** 가 다음을 만족할 때 완료:

1. alembic 0017 적용 + HNSW 인덱스 존재
2. 신규 도구 업로드 → `description_embedding` 자동 채워짐
3. recommend_agents 응답에 `relevant_tools` 필드 (기본 top 3, `?top_k_tools=N` 조정)
4. 기존 테스트 58 PASS 유지 + 신규 8+ 테스트 PASS
5. 운영 검증 (target 서버) — 신규 도구 1개 업로드 후 자연어 질의로 발견 확인

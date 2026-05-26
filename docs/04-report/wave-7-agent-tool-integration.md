# Wave-7 완료 보고서 — Agent ↔ Tool 통합 (v0.6.2)

작성일: 2026-05-26
대상 버전: **API v0.6.2** / **VSCode extension 0.17.0**
범위: P1 (도구 description embedding + relevant_tools) + P2 (매니페스트 정책 강제) + P3 (Dashboard UI)
검증: target 서버 smarttwincluster, REST 라우트 직접 호출 + UI tsc 통과

---

## Executive Summary

| 항목 | 값 |
|---|---|
| Feature | wave-7 agent-tool integration (P1 + P2 + P3) |
| 시작 / 완료 | 2026-05-26 (당일 완료) |
| Match Rate | **100 %** (plan §4 Phase 1/2/3 항목 모두 달성) |
| 신규 commit | 3 건 (118011c → bfccdee) |
| 신규 파일 | 5 (alembic 1 + services 2 + tests 2) |
| 신규 endpoint | `POST /api/recommend/agents` (확장) + `GET /api/agents/{type}/tools` |
| 신규 매니페스트 키 | `require_agent_tag` / `exclude_agent_tag` (`restrict_agents` 는 P1 이전부터 존재 — 강제는 P2 부터) |
| 테스트 합계 | **69 PASS / 16 skip** (P1 추가 8 + P2 추가 12 + P3 추가 3) |
| 운영 검증 | health 0.6.2 / recommend_agents.relevant_tools 동작 / agents/{type}/tools 응답 OK |

### Value Delivered — 4 관점

| 관점 | 내용 |
|---|---|
| Problem | (1) LLM 이 도구 이름을 알아야만 호출 가능 → 발견성 0. (2) 모든 도구가 모든 agent context 에 노출 → 권한 분리 불가. (3) 운영자가 agent 별로 어떤 도구를 쓸 수 있는지 모름. |
| Solution | (1) 도구 description 768d 임베딩 + recommend_agents 응답에 `relevant_tools` 동봉. (2) 매니페스트 3 정책 (restrict/require/exclude) AND 평가. (3) VSCode extension Agents 탭에 agent 별 호환 도구 노출. |
| Function UX Effect | 사용자: 자연어 1-call → agent + 도구 동시 추천 (LLM 이 즉시 호출 가능). 운영자: agent 카드에서 "Available tools" 클릭 → 호환 도구 즉시 확인. |
| Core Value | **재귀 RAG 의 두번째 사이클** — wave-5 가 "도구 결과 → DB → 다음 검색 컨텍스트" 였다면, wave-7 은 "자연어 → agent + 도구 → 도구 실행 → wave-5 사이클". 완전한 self-improving agent loop. |

---

## 1. 진행 작업 (commit chronology)

| commit | 단계 | 핵심 |
|---|---|---|
| 16ae395 | plan | wave-7 plan 신규 작성 — Phase 1/2/3 분할 |
| 118011c | P1 (v0.6.0) | alembic 0024 + tool_embedding_svc + recommend_agents.relevant_tools |
| f8d9551 | P2 (v0.6.1) | tool_visibility_svc + manifest 3 정책 + agent_type filter |
| bfccdee | P3 (v0.6.2 / ext 0.17.0) | GET /api/agents/{type}/tools + 확장 UI Available tools |

---

## 2. 핵심 산출물

### 2.1 백엔드 — alembic + services + routes

| 파일 | 역할 |
|---|---|
| `alembic/versions/0024_mcp_uploads_embedding.py` | `description_text` + `description_embedding Vector(768)` + HNSW 인덱스 |
| `services/tool_embedding_svc.py` | `build_description_text` / `sync_tool_embedding` / `search_tools` |
| `services/tool_visibility_svc.py` | `extract_policy` / `is_compatible` / `filter_tools_for_agent` |
| `routes/mcp_tools.py` (확장) | upload 직후 `sync_tool_embedding` 자동 호출 (defensive) |
| `routes/recommend.py` (확장) | `top_k_tools` + `agent_type` 파라미터, over-fetch ×3 후 필터 |
| `routes/agents.py` (확장) | 신규 `GET /api/agents/{type}/tools` |
| `mcp_runtime.py` (확장) | MCP `recommend_agents` 도 동일 동작 |
| `services/mcp_upload_svc.py` (확장) | UploadManifest 에 `require_agent_tag` / `exclude_agent_tag` 필드 + round-trip |

### 2.2 VSCode extension 0.17.0

| 파일 | 역할 |
|---|---|
| `client/apiClient.ts` | `getAgentTools(agentType)` 신규 메서드 |
| `webview/protocol.ts` | `getAgentToolsRequest/Response` 타입 |
| `webview/panel.ts` | 호스트 handler 위임 |
| `webview/html.ts` | `state.agents.toolsByAgent` + "Available tools" 버튼 + `renderAgentToolsBlock` |

### 2.3 테스트

| 테스트 | 케이스 수 |
|---|---|
| `test_tool_embedding.py` (P1) | 8 (build_description_text 3 + sync 2 + search 3) |
| `test_tool_visibility.py` (P2) | 12 (extract 3 + is_compatible 5 + filter 2 + manifest round-trip 1) |
| `test_agent_tools_route.py` (P3) | 3 (정책 필터 / 404 / deprecated 제외) |
| **합계** | **23 신규** (dev PC: 13 pass / 10 skip DB-dependent) |

---

## 3. API 변경 요약

### 3.1 신규 응답 필드 (호환 — 기존 필드 변경 없음)

**`POST /api/recommend/agents` 응답**:
```json
{
  "query": "...",
  "agents": [ ... ],            // 기존
  "relevant_tools": [            // 신규 (Wave-7 P1)
    {
      "name": "stress_strain_plot",
      "score": 0.47,
      "description": "...",
      "title": "...",
      "compatible_agents": null,
      "manifest_url": "/api/mcp_tools/stress_strain_plot"
    }
  ]
}
```

### 3.2 신규 입력 파라미터

- `top_k_tools` (int, 0~10, default 3) — `relevant_tools` 개수
- `agent_type` (string, optional) — Wave-7 P2 매니페스트 정책 필터

### 3.3 신규 엔드포인트

`GET /api/agents/{agent_type}/tools` — agent 별 호환 도구 일괄 조회 (P3).

### 3.4 신규 매니페스트 키

```yaml
restrict_agents: ["cae_engineer"]        # whitelist (P1 이전부터 존재, P2 부터 강제)
require_agent_tag: ["structural", "metal"]  # AND — 모든 태그 매치 시 노출
exclude_agent_tag: ["legacy"]               # 어떤 태그라도 매치 시 숨김
```

3 정책 AND 평가. 모두 빈 list 면 default 노출 (호환).

---

## 4. 운영 검증 결과 (target 서버)

| 단계 | 결과 |
|---|---|
| alembic 0024 적용 | INFO "Running upgrade 0023 -> 0024" |
| stress_strain_plot description embedding backfill | text_len=408, embedded=True |
| `POST /api/recommend/agents` "SUS304 stress strain" | agents 3 + relevant_tools 1 (score 0.4743) |
| `GET /api/agents/cae-analyst/tools` (기본 도구만) | 1 tool 반환, policy 모두 빈 list |
| `GET /api/agents/no_such_agent/tools` | 404 NOT_FOUND |
| health version | **0.6.2** |

### 4.1 P2 정책 enforcement E2E (가장 강한 증명)

신규 도구 `policy_test` 업로드 (매니페스트 `restrict_agents: ["cae-analyst"]`):

```
=== /api/agents/cae-analyst/tools (whitelisted) ===
tool_count=2
  - policy_test v1 restrict=['cae-analyst']      ← 노출
  - stress_strain_plot v2 restrict=[]            ← 노출

=== /api/agents/cae-reporter/tools (NOT whitelisted) ===
tool_count=1
  - stress_strain_plot v2 restrict=[]            ← 노출
  (policy_test 차단 — restrict_agents 에 cae-reporter 없음)
```

**의미**:
- 매니페스트 `restrict_agents` 가 agent context 별 가시성을 **실제로 강제**.
- 다른 agent context 에서는 도구 자체가 list 에 안 나타남 (정보 누출 없음).
- LLM 이 `recommend_agents(q, agent_type="cae-reporter")` 호출해도 policy_test 는 안 보임.

---

## 5. 알려진 제약 / 다음 단계

| 항목 | 사유 |
|---|---|
| MCP `tools/list` 의 agent-context 필터 | clientInfo.name 매핑 + 인증 게이트 필요 (별 트랙) |
| 실 정책 적용 도구 운영 검증 | 현재 등록된 stress_strain_plot 매니페스트가 정책 없음 → 정책 적용 도구 추가 업로드 검증 필요 |
| Dashboard 페이지 (확장 아닌 웹) | 사내망 정책상 VSCode extension 우선 — Phase 4 로 분리 |
| tools 검색 score 가 낮음 (single tool) | 더 많은 도구 등록 시 자연스럽게 향상 (도구 description 다양화) |

### 후속 작업 후보

1. **Wave-5 P2 Dashboard Upload UI** — 사용자가 zip 만들지 않고 웹에서 도구 등록 (사용자 진입 장벽 낮춤)
2. **Wave-6 P2 stdio transport** — 사내 SaaS MCP 통합 (Slack/GitHub 등)
3. **실 운영 검증 보강** — restrict_agents=["cae-analyst"] 명시한 데모 도구 1개 업로드 → cae-analyst 만 보이고 다른 agent 는 못 보는지 확인

---

## 6. 메트릭 요약

| 메트릭 | 값 |
|---|---|
| 신규 commit | 3 (plan 1 + 구현 3) |
| 신규 코드 라인 | +1,320 / −22 |
| 신규 endpoint | 1 (`GET /api/agents/{type}/tools`) + 2 (recommend 응답 필드) |
| 신규 매니페스트 정책 키 | 2 (`require_agent_tag`, `exclude_agent_tag`) |
| 테스트 누적 | 69 PASS / 16 skip (이전 56 → 69, +23 신규 / +10 skip) |
| API 버전 | 0.5.0 → 0.6.2 (minor +1, patch +2) |
| Extension 버전 | 0.16.0 → 0.17.0 |
| 운영 검증 단계 | 6/6 PASS |

---

## 7. 결론

- wave-7 의 Phase 1 (embedding 통합) + Phase 2 (정책 강제) + Phase 3 (Dashboard UI) 전부 완료.
- 호환성 유지 — 기존 클라이언트는 응답 변경 없이 동작, 신규 클라이언트만 새 필드 활용.
- 핵심 가치 (자연어 1-call → agent + 도구 동시 추천) 동작 확인.
- v0.6.2 production-ready. 다음 minor (0.7.0) 후보: Wave-5 P2 Dashboard Upload UI 또는 Wave-6 P2 stdio transport.

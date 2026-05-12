# Report — MCP HTTP Server (`mcp-http-server`)

**Date**: 2026-05-11
**Plan**: [docs/01-plan/mcp-http-server.md](../01-plan/mcp-http-server.md)
**Predecessor**: agent-discovery-console

## Executive Summary

| Field | Value |
|-------|-------|
| Feature | FastAPI 안에 MCP (Model Context Protocol) Streamable HTTP server 마운트 |
| 효과 | Cline / Claude Desktop 등이 우리 7 tools + 2 resources 를 자동 발견 |
| 코드 변경 | 백엔드 2 파일 (mcp_runtime.py NEW + main.py lifespan 통합), Extension 1 파일 |
| VSIX | v0.9.0 → v0.10.0 (46 KB) |
| 검증 | initialize / tools/list / tools/call / resources/list 모두 정상 |

### Value Delivered

| 관점 | 내용 |
|------|------|
| **Problem** | Console 탭에서 system prompt + 2 bundle 복붙 3회. 매 새 대화마다 LLM에 base_url 재안내. |
| **Solution** | 우리 서버가 표준 MCP server 노출. Cline은 등록 JSON 한 줄(URL)만 받아 도구를 자동 발견. |
| **Function/UX Effect** | 사용자: Console 탭 "📋 Cline 등록 JSON 복사" → Cline MCP Servers에 붙여넣기 → 끝. 이후 채팅만 하면 LLM이 7개 도구를 자동 호출. |
| **Core Value** | 클라이언트 LLM 셋업 시간 분 단위 → 초 단위. 본 허브가 LLM 호스팅 없이 표준 프로토콜로 모든 MCP 클라이언트와 호환. |

## 노출된 도구·리소스

### Tools (7)
| Name | 매핑 | 용도 |
|------|------|------|
| `discover` | `discover_svc.build_discover_payload` | 시스템 한눈에 |
| `recommend_agents(q, top_k=5)` | `recommend_svc.recommend_agents` | 자연어 → ranked agents |
| `get_context_bundle(agent_type, format, ...)` | `recommend_svc.build_context_bundle` | RAG payload (md/json) |
| `semantic_search(q, top_k=10, data_types)` | `search_svc.semantic_search` | 의미 검색 |
| `get_record(record_id)` | `Record` ORM | 단일 record 풀 |
| `get_record_sections(record_id, limit)` | `RecordSection` ORM | RAG section chunks |
| `list_agents` | `Agent` ORM | agent 카탈로그 |

### Resources (2)
| URI | 출처 |
|------|------|
| `aidh://llm-guide` | `discover_svc.build_llm_doc()` (markdown 한 페이지) |
| `aidh://discover` | live `discover_svc.build_discover_payload()` (JSON) |

## 변경 산출물

### 백엔드
- [api_server/src/api/mcp_runtime.py](../../api_server/src/api/mcp_runtime.py) **(NEW)** — FastMCP 인스턴스, 7 tools, 2 resources, `stateless_http=True`, `streamable_http_path="/"` (외부에서 `/mcp` mount).
- [api_server/src/api/main.py](../../api_server/src/api/main.py) — lifespan 통합 (MCP task group enter/exit). `app.mount("/mcp", _mcp_app)`.

### Extension (v0.9.0 → v0.10.0)
- [vscode_extension/src/webview/html.ts](../../vscode_extension/src/webview/html.ts) — Console 탭에:
  - `🚀 MCP 자동 등록 (권장)` 카드 + 라이브 JSON 프리뷰 + "Cline 등록 JSON 복사" 버튼
  - 기존 system prompt / context bundle 복붙 UI는 "대안 (수동 모드)" 로 강등 표시
- VSIX: [ai-data-hub-uploader-0.10.0.vsix](../../vscode_extension/ai-data-hub-uploader-0.10.0.vsix) (46.12 KB)

## 검증 결과 (curl JSON-RPC)

```text
POST /mcp/  method=initialize
  → 200 SSE event, serverInfo.name="aidatahub", serverInfo.version="1.27.1"
     capabilities: {tools, resources, prompts}

POST /mcp/  method=tools/list
  → 7 tools (discover, recommend_agents, get_context_bundle, semantic_search,
            get_record, get_record_sections, list_agents)

POST /mcp/  method=tools/call  name=recommend_agents  args={"q":"LS-DYNA 메시 매핑"}
  → result.content[0].text + result.structuredContent
     → {"query":"LS-DYNA 메시 매핑",
        "agents":[{"agent_type":"lsdyna-automation","score":46.38,
                   "matched_records":1,"matched_sections":50, ...}]}

POST /mcp/  method=resources/list
  → 2 resources (aidh://llm-guide, aidh://discover)
```

## 발견한 통합 이슈 (해결됨)

- FastAPI 의 `app.mount()` 는 sub-app 의 lifespan 을 자동 전파 안 함.
- MCP `streamable_http_app` 의 task group 이 미초기화 → `RuntimeError: Task group is not initialized`.
- 해결: FastAPI lifespan 안에서 `_mcp_app.router.lifespan_context(_mcp_app)` 명시 enter/exit.

## 사용자 흐름 (최종 형태)

```
1. (1회) Console 탭 → [📋 Cline 등록 JSON 복사]
   → 클립보드: {"mcpServers":{"aidatahub":{"url":"http://110.15.177.120:8001/mcp/"}}}
2. Cline 설정 → MCP Servers → 붙여넣기 → 저장 → Cline 재시작
3. Cline UI 에 7 tools 자동 표시
4. 사용자가 "LS-DYNA 메시 매핑 알려줘" → LLM 이 자동으로:
     recommend_agents(q="...")  → lsdyna-automation 발견
     get_context_bundle("lsdyna-automation", format="markdown") → RAG context
     get_record_sections("DOC-HE-CAE-2026-0000000001") → 구체 본문
   사용자는 복붙 0번, 답변만 받음.
```

## Acceptance

- [x] `mcp` SDK 이미 requirements 에 포함 (별도 설치 단계 없음)
- [x] `GET /mcp/` 와 `POST /mcp/` 정상 응답
- [x] `tools/list` → 7개 표시
- [x] `tools/call recommend_agents` → 정상 호출 + structured content 반환
- [x] `resources/list` → 2개 표시
- [x] Extension Console 탭 v0.10.0 에 MCP 등록 카드 + 복사 버튼

## Out-of-scope (다음 사이클 후보)

- stdio MCP wrapper (npm 패키지 형태)
- Cline 실제 환경 E2E 검증 (사용자 환경 의존)
- 인증 통합 (`AUTH_REQUIRED=true` 환경에서 MCP transport 헤더에 API key)
- MCP Prompts (3rd primitive — 미리 정의된 프롬프트 템플릿)
- `notifications/initialized` 이후 세션 ID 헤더(`Mcp-Session-Id`) 표준 흐름 점검

## 결론

본 사이클로 사용자 워크플로의 마지막 마찰 (복붙 3번) 이 표준 프로토콜 한 줄 등록으로 대체됨. AIDataHub 는 이제 **LLM-agnostic 메타 카탈로그 + RAG context provider + MCP server** 세 역할을 동시에 수행. Cline / Claude Desktop / Claude Code 등 어느 MCP 클라이언트라도 즉시 본 시스템을 챗봇 자원으로 활용 가능.

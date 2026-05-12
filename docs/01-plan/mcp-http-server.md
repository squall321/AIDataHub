# Plan — MCP HTTP Server (Streamable)

**Feature**: `mcp-http-server`
**Date**: 2026-05-11
**Predecessor**: [agent-discovery-console](./agent-discovery-console.md)

## Executive Summary

| Field | Value |
|-------|-------|
| Feature | FastAPI 안에 MCP (Model Context Protocol) HTTP 서버 마운트 → Cline / Claude Desktop이 우리 도구를 자동 발견 |
| 동기 | Console 탭 "복붙 3번" UX 를 "Cline에 URL 한 줄 등록 → 영구" 로 단순화 |
| 범위 | 백엔드: `mcp` Python SDK 추가, `/mcp` 라우터, 7 tools + 2 resources. Extension: "MCP 등록 JSON 복사" 버튼 |

### Value Delivered

| 관점 | 내용 |
|------|------|
| **Problem** | 현재 Cline 사용자가 ① system prompt 복붙 ② context bundle 복붙 ③ 매 새 대화마다 base_url 인지를 함. 도구 호출도 LLM의 자유의지에 의존. |
| **Solution** | 우리 서버가 MCP-compliant 도구를 노출 → Cline 시작 시 자동으로 `tools/list` 받아 UI에 표시 → LLM이 표준 `tools/call` 로 호출. 사용자는 그냥 채팅. |
| **Function/UX Effect** | Cline 설정에 `mcpServers: {"aidatahub": {"url":"http://...:8001/mcp"}}` 한 줄 → 영구. Console 탭은 그 한 줄을 복사 버튼으로 제공. |
| **Core Value** | "사용 설명서 (system prompt)" → "표준 프로토콜 (MCP)". 클라이언트 의존성 0, 도구 호출 결정성 ↑. |

## 결정 (확정)

1. **Transport**: Streamable HTTP (SSE-based, MCP 표준). Cline 0.x 가 stdio 우선이지만 HTTP 도 지원. Cline 비호환이면 stdio wrapper 추가는 별도 사이클.
2. **SDK**: 공식 `mcp` Python 패키지 (`mcp.server.fastmcp.FastMCP`).
3. **마운트 위치**: `app.mount("/mcp", ...)` — `main.py` 안.
4. **도구 호출**: in-process 함수 직접 호출 (REST 우회). 인증/세션은 FastAPI 의존성으로 처리.
5. **인증**: PoC 는 anonymous. 운영 시엔 MCP 헤더에 X-API-Key 전달.
6. **자동 의존성**: `start_api.sh` 가 `EMBEDDING_PROVIDER` 처럼 mcp 도 자동 설치 — `pip install "mcp>=1.0"`.

## 노출할 도구 (7개) + 리소스 (2개)

### Tools

| Tool | 인자 | 매핑 |
|------|------|------|
| `discover` | - | `discover_svc.discover()` |
| `recommend_agents` | `q`, `top_k=5` | `recommend_svc.recommend_agents` |
| `get_context_bundle` | `agent_type`, `format="markdown"` | `recommend_svc.build_context_bundle` |
| `semantic_search` | `q`, `agent_type=None`, `top_k=10` | `search_svc.semantic_search` |
| `get_record` | `id` | `records_svc.get_record` |
| `get_record_sections` | `id`, `limit=50` | `records_svc.get_sections` |
| `list_agents` | - | `agent_svc.list_agents` |

### Resources

| URI | 내용 |
|------|------|
| `aidh://discover` | `/api/discover` 응답 (한 번에 카탈로그) |
| `aidh://llm-guide` | `/api/docs/llm.txt` 의 LLM 사용 안내 |

## 영향 파일

| 파일 | 변경 |
|------|------|
| `api_server/requirements.txt` | `mcp>=1.0` 추가 (또는 start_api.sh 자동 설치만) |
| `api_server/src/api/mcp_server.py` | NEW — FastMCP 인스턴스 + 도구 정의 |
| `api_server/src/api/main.py` | mcp app mount |
| `deploy/apptainer/start_api.sh` | mcp 자동 설치 (옵션 임베더 패턴 재사용) |
| `vscode_extension/src/webview/html.ts` | Console 탭에 "MCP 등록 JSON 복사" 버튼 |
| `vscode_extension/src/webview/panel.ts` | base_url 만 받아 JSON 생성 (copyToClipboardRequest 재사용) |
| `package.json` | v0.9.0 → v0.10.0 |

## Acceptance

- [ ] `GET /mcp` 가 200 응답 (또는 MCP handshake 시작)
- [ ] MCP JSON-RPC `initialize` → 200
- [ ] MCP `tools/list` → 7개 도구 일람
- [ ] `recommend_agents(q="LS-DYNA 메시 매핑")` 호출 → 동일 결과
- [ ] Console 탭 "MCP 등록 JSON 복사" → 클립보드에 `{"mcpServers":{"aidatahub":{"url":"http://110.15.177.120:8001/mcp"}}}` 들어감
- [ ] Cline 등록 검증은 사용자가 실제 환경에서 (사이클 외)

## Out-of-scope

- stdio MCP wrapper 패키지
- Cline 실제 동작 E2E (사용자 수동 검증)
- 인증 통합 (PoC 익명 그대로)
- MCP Prompts (3rd primitive) — 필요해지면 별도

## Risks

- `mcp` Python SDK 의존성 무거울 가능성 → `start_api.sh` 자동 설치라 무겁지 않게
- Streamable HTTP transport API 가 SDK 버전마다 다를 수 있음 → `mcp>=1.0` 고정
- FastAPI mount 충돌 (다른 라우터와 path conflict) → `/mcp` 단일 prefix

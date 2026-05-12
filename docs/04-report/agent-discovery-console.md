# Report — Agent Discovery Console (MVP)

**Feature**: `agent-discovery-console`
**Date**: 2026-05-11
**Predecessors**: team-group-mgmt, embedding-dim-upgrade, ask-keywords-from-db
**Plan**: [docs/01-plan/agent-discovery-console.md](../01-plan/agent-discovery-console.md)

## Executive Summary

| Field | Value |
|-------|-------|
| Feature | "뭐 하고싶다" → agent 추천 → LLM-ready context bundle + system prompt → Cline/Qwen 챗봇 셋업 |
| 범위 | 백엔드 3 endpoint + VSCode extension Console 탭 |
| 코드 변경 | 백엔드 4 파일 / extension 4 파일, vsix v0.8.2→0.9.0 |
| 검증 | curl 3개 endpoint 정상, vsix 빌드 OK |

### Value Delivered

| 관점 | 내용 |
|------|------|
| **Problem** | 사용자가 "뭐 하고싶다"고 입력해도 어느 agent를 골라야 할지 불명. Cline은 본 서버를 처음 보고 어떤 도구·메타가 있는지 모름. 챗봇 셋업이 비-자명. |
| **Solution** | `POST /api/recommend/agents` (의미검색 집계 ranking), `GET /api/agents/{type}/context-bundle` (Markdown + JSON), `GET /api/agents/{type}/system-prompt` (Cline custom instructions용). Extension Console 탭이 입력 → 추천 → 3가지 복사로 한 화면 완결. |
| **Function/UX Effect** | 사용자가 한 줄 입력 → 3 클릭 → 챗봇 셋업 완료. 본 시스템은 LLM을 호스팅하지 않고 "사용 설명서"만 제공. |
| **Core Value** | 어떤 LLM(Qwen/Claude/GPT/Ollama)이든 본 허브를 즉시 활용 가능. "코드 상수 → 운영 가능 객체" 정신을 최종 사용자 UX로 확장. |

## 변경 산출물

### 백엔드

| 파일 | 종류 | 내용 |
|------|------|------|
| [services/recommend_svc.py](../../api_server/src/api/services/recommend_svc.py) | NEW | `recommend_agents`, `build_context_bundle`, `render_context_bundle_markdown`, `build_system_prompt` |
| [routes/recommend.py](../../api_server/src/api/routes/recommend.py) | NEW | `POST /api/recommend/agents` |
| [routes/agents.py](../../api_server/src/api/routes/agents.py) | EDIT | `GET /{type}/context-bundle` (Accept md/json), `GET /{type}/system-prompt` |
| [routes/__init__.py](../../api_server/src/api/routes/__init__.py) | EDIT | recommend 라우터 등록 |

### Extension (v0.8.2 → v0.9.0)

| 파일 | 변경 |
|------|------|
| [client/apiClient.ts](../../vscode_extension/src/client/apiClient.ts) | `recommendAgents` / `getContextBundle` / `getSystemPrompt` 3 메서드 |
| [webview/protocol.ts](../../vscode_extension/src/webview/protocol.ts) | 8개 message type (4 request + 4 response) |
| [webview/panel.ts](../../vscode_extension/src/webview/panel.ts) | 4개 케이스 (recommend/bundle/prompt/copyToClipboard) |
| [webview/html.ts](../../vscode_extension/src/webview/html.ts) | Console 탭 마크업 + state + renderConsoleTab + 이벤트 핸들러 + 메시지 dispatch |
| [package.json](../../vscode_extension/package.json) | version 0.8.2 → 0.9.0 |
| [vscode_extension/ai-data-hub-uploader-0.9.0.vsix](../../vscode_extension/ai-data-hub-uploader-0.9.0.vsix) | 45.82 KB |

## API 동작 (검증 결과)

```
POST /api/recommend/agents {"q":"LS-DYNA 메시 매핑 자동화"}
→ agents:[{
    agent_type:"lsdyna-automation", name:"LSDyna 자동화 Agent",
    score:46.35, matched_records:1, matched_sections:50,
    why:"의미검색 top-50 결과 중 50 sections / 1 records 가 이 agent 소속"
  }]

GET /api/agents/lsdyna-automation/context-bundle (Accept: text/markdown)
→ "# Agent: LSDyna 자동화 Agent ...
   ## Records (1)
   ### DOC-HE-CAE-2026-0000000001 — KooRemapper_Manual
   - tags: 메시, 자동, ls-dyna, ...
   - summary: ...
   **Key sections:**
   - §1 _본문_ — KooRemapper 기능 설명서 버전 1.8.0 ..."

GET /api/agents/lsdyna-automation/context-bundle (Accept: application/json)
→ {agent:{...}, records:[{id,title,summary,tags,key_sections:[...]}], endpoints:{...}}

GET /api/agents/lsdyna-automation/system-prompt?base_url=http://110.15.177.120:8001
→ "You are an assistant for "LSDyna 자동화 Agent" inside the Mobile eXperience AI Data Hub.
   ## First step on every conversation
   1. GET http://110.15.177.120:8001/api/agents/lsdyna-automation/context-bundle ..."
```

## Extension Console 탭 UX

```
┌─ [Upload] [Bundle] [Search] [Agents] [Console] ────────────────────┐
│                                                                    │
│  자연어로 할 일 시작하기                                            │
│  [예: LS-DYNA 메시 매핑 자동화 도구 사용법 알려줘    ] [추천 받기]   │
│                                                                    │
│  ── 추천 agents ──                                                 │
│  ┌─────────────────────────────────────┐                          │
│  │ LSDyna 자동화 Agent (lsdyna-automation)                          │
│  │ LS-DYNA 전·후처리 자동화...                                       │
│  │ score: 46.35 · 1 records / 50 sections · tags: LS-DYNA, ...      │
│  │                                          [이 agent 선택]         │
│  └─────────────────────────────────────┘                          │
│                                                                    │
│  ── 선택된 agent: lsdyna-automation ──                            │
│  ┌─ 1️⃣ System prompt ────[불러오기] [📋 복사]──┐                  │
│  ├─ 2️⃣ Context bundle (Markdown) ──[불러오기] [📋 복사]──┐       │
│  ├─ 3️⃣ Context bundle (JSON) ──[불러오기] [📋 복사]──┐           │
│                                                                    │
│  💡 Cline 사용법: 설정 → Custom Instructions → ① 붙여넣기 ...      │
└────────────────────────────────────────────────────────────────────┘
```

## 사용자 흐름 (한 줄로)

```
사용자 입력 → [추천 받기] → agent 선택 → [불러오기] × 3 → [📋 복사] × 3
                                                    → Cline 설정에 붙여넣기 → 챗봇 동작
```

## Acceptance

- [x] `POST /api/recommend/agents` → lsdyna-automation top1
- [x] `context-bundle` markdown / json 둘 다 정상
- [x] `system-prompt` 정상
- [x] Console 탭 마크업 + 핸들러 + 메시지 dispatch
- [x] tsc 컴파일 + vsix 패키징 (v0.9.0, 45.82 KB)
- [x] 기존 탭 회귀 없음 (tsc 통과)

## 후속

- MCP 서버 (Cline 자동 도구 발견) — 별도 사이클
- 추천 알고리즘 가중치 (common_tags 정확매치, quality_score, freshness) — record 증가 후 평가
- "Open in Cline" 버튼 (URI scheme 가능 시 한 번에 띄움) — Cline 측 지원 필요
- Console 탭에 history (최근 추천 결과 캐시)
- context-bundle streaming + 토큰 한도 컨트롤

## 결론

본 사이클로 사용자 의도 — "사용자는 한 마디만 하면, 시스템이 추천 + LLM-ready 컨텍스트를 내주고, 그걸 받아 Cline/Qwen이 즉시 챗봇이 됨" — 의 **MVP가 완성**됨. 본 허브는 LLM을 호스팅하지 않으면서도 모든 클라이언트 LLM에 대해 "사용 설명서"를 발급하는 메타 카탈로그로 자리잡음.

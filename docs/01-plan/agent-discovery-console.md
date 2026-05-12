# Plan — Agent Discovery Console (MVP)

**Feature**: `agent-discovery-console`
**Date**: 2026-05-11
**Predecessors**: [team-group-mgmt](./team-group-mgmt.md), [embedding-dim-upgrade](./embedding-dim-upgrade.md), [ask-keywords-from-db](./ask-keywords-from-db.md)

## Executive Summary

| Field | Value |
|-------|-------|
| Feature | "뭐 하고싶다" → agent 추천 → LLM-ready context bundle + system prompt → Cline/Qwen 챗봇 셋업 |
| 범위 | 백엔드 3 endpoint + VSCode extension Console 탭 (선택 시 클립보드 복사) |
| 의도 | 본 시스템이 직접 LLM을 띄우지 않고, **Cline/로컬 LLM이 "이 시스템에서 뭘 할 수 있는지" 자동 발견**하도록 라이선스 셋업 |

### Value Delivered

| 관점 | 내용 |
|------|------|
| **Problem** | 사용자가 "뭐 하고싶다"고 말해도 어떤 agent를 골라야 할지 모름. 골랐어도 그 agent에 어떤 record를 보내야 LLM이 RAG 답변하는지 모름. Cline은 우리 서버를 처음 보고 어디서 시작할지 모름. |
| **Solution** | (a) 추천 endpoint 가 자연어 → 의미검색 → agents 집계, (b) bundle endpoint 가 agent별 모든 records + sections를 LLM 친화 markdown/JSON으로 묶음, (c) system-prompt endpoint 가 Cline에 그대로 붙여넣는 시스템 프롬프트 + 도구 일람 생성. Extension Console 탭이 이 세 단계를 한 화면에서 안내. |
| **Function/UX Effect** | 사용자 입력 한 줄 → 추천 agent → 클립보드 복사 → Cline custom instruction 붙여넣기 → 챗봇 즉시 동작. VSCode extension은 단순 안내자 역할. |
| **Core Value** | 본 허브가 LLM 백엔드를 호스팅하지 않으면서도 **클라이언트 LLM에게 "사용 설명서"** 를 제공. 사내 어떤 LLM(Qwen / Claude / GPT / Ollama)이든 본 허브의 능력을 즉시 사용. |

## 결정 (확정)

1. **추천 신호**: 단순 의미검색 집계 (e5_base score 가중 합산)
2. **bundle 포맷**: Accept 헤더로 분기 (`text/markdown` default, `application/json` 옵션)
3. **system-prompt 출처**: 현재 [llm.txt](../../api_server/static/dashboard/) + agent별 메타 + 도구 호출 가이드 결합
4. **Extension Console 탭**: Uploader 옆 신규 탭, 단일 입력 + 추천 카드 + 3개 복사 버튼 (system-prompt / markdown bundle / json bundle)
5. **MCP**: 이번 사이클 out-of-scope

## API Spec (요약)

### POST /api/recommend/agents

```http
POST /api/recommend/agents
Content-Type: application/json
{"q":"LS-DYNA 메시 매핑 자동화 도구 사용법", "top_k": 5}

200 OK
{
  "query": "...",
  "agents": [
    {
      "agent_type": "lsdyna-automation",
      "name": "LSDyna 자동화 Agent",
      "description": "...",
      "score": 0.94,
      "matched_records": 1,
      "matched_sections": 18,
      "common_tags": ["LS-DYNA", "KooRemapper", ...],
      "why": "의미검색 top-18 sections 모두 이 agent 소속"
    }
  ]
}
```

알고리즘:
1. `/api/search?mode=semantic` 로 top-50 sections 가져옴
2. 각 section.record.agents[] 카운트 + score 가중 합산
3. ranked desc → top_k 반환

### GET /api/agents/{type}/context-bundle

```http
GET /api/agents/lsdyna-automation/context-bundle?max_records=10
Accept: text/markdown    # default
# 또는: Accept: application/json
```

Markdown 응답 (LLM-friendly):
```
# Agent: LSDyna 자동화 Agent (lsdyna-automation)
## Description
...
## Common tags / data_types
...
## Records (3)
### DOC-HE-CAE-2026-0000000001 — KooRemapper_Manual
- tags: ...
- summary: ...
- key sections:
  - § 4. map — HEX8 구조화 메시 매핑 [...]
  - § 5. shellmap — QUAD4 셸 ...
...
## How to query for more
- GET /api/records/{id}                       # 본문 풀
- GET /api/records/{id}/sections              # 섹션 청크
- GET /api/search?mode=semantic&q=&agent=...  # 의미검색
```

JSON 응답 (구조화):
```json
{
  "agent": {"agent_type":"...", "name":"...", "common_tags":[...], ...},
  "records": [
    {
      "id": "...", "title": "...", "summary": "...",
      "tags": [...], "doc_type": "...",
      "key_sections": [{"section_id":"4","title":"map — HEX8...","excerpt":"..."}]
    }
  ],
  "endpoints": {
    "record_detail": "/api/records/{id}",
    "sections": "/api/records/{id}/sections",
    "semantic_search": "/api/search?mode=semantic&q={q}&agent=lsdyna-automation"
  }
}
```

### GET /api/agents/{type}/system-prompt

Plain text. Cline custom instructions 또는 LLM system message에 그대로 사용.

```
You are an assistant for "LSDyna 자동화 Agent" inside the Mobile eXperience AI Data Hub.

## Your role
- Help the user with: LS-DYNA 전·후처리 자동화, KooRemapper 메시 매핑/리매핑/생성/변형률.
- Authoritative data: 1 record(s) registered under team=HE/group=CAE.

## When the user asks something
1. Call GET http://<host>:8001/api/agents/lsdyna-automation/context-bundle to load your knowledge.
2. For follow-up specifics: GET /api/search?mode=semantic&q=<term>&agent=lsdyna-automation
3. For full document content: GET /api/records/{id}/sections

## Available tools (REST)
- GET /api/discover                  — system catalog
- GET /api/records/{id}              — record detail
- GET /api/records/{id}/sections     — RAG chunks
- GET /api/search?mode=semantic      — semantic search
- POST /api/ask                      — natural language query

## Conventions
- IDs follow {DATA_TYPE}-{TEAM}-{GROUP}-{YEAR}-{SEQ:010d}.
- Korean and English both supported.
- Always cite source: include the record id in your reply.
```

## 영향 파일

| 파일 | 변경 |
|------|------|
| `api_server/src/api/routes/recommend.py` | NEW |
| `api_server/src/api/services/recommend_svc.py` | NEW |
| `api_server/src/api/routes/agents.py` | EDIT — context-bundle / system-prompt 라우트 추가 (또는 별도 라우터로 분리) |
| `api_server/src/api/routes/__init__.py` | EDIT — recommend 등록 |
| `vscode_extension/src/webview/panel.ts`, `html.ts` | EDIT — 새 Console 탭 마크업 + 핸들러 |
| `vscode_extension/src/client/apiClient.ts` | EDIT — recommend / context-bundle / system-prompt 호출 |
| `docs/01-plan/agent-discovery-console.md` | 이 문서 |

## Acceptance

- [ ] `POST /api/recommend/agents {"q":"LS-DYNA 자동화"}` → lsdyna-automation top1, score>0
- [ ] `GET /api/agents/lsdyna-automation/context-bundle` (Accept md) → KooRemapper_Manual 포함 markdown
- [ ] `GET /api/agents/lsdyna-automation/context-bundle` (Accept json) → 구조화 JSON
- [ ] `GET /api/agents/lsdyna-automation/system-prompt` → text/plain 프롬프트
- [ ] Extension Console 탭에서 자연어 입력 → 추천 카드 → 3가지 복사 버튼 동작
- [ ] `bash setup.sh` 멱등 재실행 후 모두 정상

## Out-of-scope

- MCP 서버 구현 (Cline 자동 발견)
- 추천 알고리즘 고도화 (가중치 / RBAC / 신선도)
- streaming context-bundle
- agent별 RAG 인덱스 사전 빌드

# AIDataHub 자체 Chat 기능 — Checklist

## 백엔드
- [x] `services/chat_svc.py`
  - [x] `TOOL_SPECS` — 6도구 OpenAI function 스키마
  - [x] `TOOL_EXECUTORS` — name→async 실행기 (MCP 래퍼 복제, 같은 서비스 호출)
  - [x] `_vllm_chat()` — httpx OpenAI 호환 `/chat/completions` (non-stream, tools)
  - [x] `stream_chat()` — tool-calling 루프(≤8) + SSE 이벤트 async generator
  - [x] graceful degrade (LLM 미설정 → 안내) + `mode="echo"` dev 경로
  - [x] 시스템 프롬프트 — 수집 규약(유사확인→제안채움→team/group은 사람, 출처 인용)
- [x] `routes/chat.py` — `POST /api/chat` StreamingResponse(text/event-stream) + X-API-Key
- [x] `routes/__init__.py` — include_router(chat)

## 프론트 (dashboard SPA)
- [x] `index.html` — Chat 탭을 **첫 탭·기본 active**(tab-main), 기존 가이드는 뒤로
- [x] `dashboard.js` — chatReadSSE(fetch+ReadableStream), 메시지/도구스텝/trace 렌더, drag&drop
- [x] `dashboard.css` — 챗 레이아웃 스타일 (버블/상태/trace/드롭존)

## 설정
- [x] `.env.example` — ReportArchive 규칙(LLM_BACKEND/LLM_BASE_URL/LLM_MODEL/LLM_API_KEY/
  LLM_TIMEOUT_S/LLM_NO_PROXY) 문서화, 기본=상암. 하위호환 OPENAI_* 유지 (§8 config-only)
- [x] 설정 UI (GET/PUT/DELETE /api/chat/config + test) — 기본 상암 + 런타임 override

## 테스트/검증
- [x] `tests/test_chat_svc.py` — mock vLLM 툴루프 + echo + degrade + 스펙 + 라우트 SSE (7 tests)
- [x] pytest 통과 (전체 252 passed, 217 skipped — 회귀 없음)
- [x] SSE echo/degrade curl 확인 (실 uvicorn 스모크)
- [x] 프론트 playwright 렌더 + 실제 전송→SSE→에러버블 end-to-end 확인
- [x] 커밋

## 남은 것 (별도 작업)
- [ ] 실 vLLM 연결 검증 (타겟 서버, §8 — dev box에선 라이브 테스트 안 함)
- [ ] 포털 MCP Gateway 에 AIDataHub MCP 등록 (포털 글로벌 챗 연동)
- [ ] 토큰 스트리밍(event: token) / 대화 히스토리 영속화

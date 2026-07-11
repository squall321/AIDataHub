# AIDataHub 자체 Chat 기능 — Context Notes

> 결정과 근거를 계속 덧붙인다. 다음 세션이 재유도 없이 이어가도록.

## 목표
챗으로 데이터를 올리는 것을 **메인 페이지**로. 기존 대시보드 탭·사용 가이드는 보조.
LLM(vLLM, OpenAI 호환)이 AIDataHub의 로컬 도구를 호출해 수집·검색을 대화로 수행한다.

## 확정된 결정 (사용자 승인)
- **오케스트레이션 = 자체 완결.** AIDataHub 백엔드가 vLLM을 직접 호출 + 자기 도구를 로컬 tool-calling.
  외부 Agent Server 불필요. 챗이 단독 동작. (포털 MCP Gateway엔 AIDataHub MCP를 등록만 해두면
  포털 글로벌 챗도 같은 도구 접근 — 그건 별개 작업.)
- **도구 범위 = 수집+검색 6종.** find_similar_data · describe_record_schema · list_doc_types ·
  import_record · list_records · semantic_search.
- **챗 = 새 랜딩 탭(기본).** 기존 10탭·가이드는 보조로 유지.
- **vLLM = config/env 주도** (플레이북 §8). 라이브 테스트 안 함. 미연결 시 graceful degrade +
  `mode=echo` dev 경로로 SSE 자체를 검증.

## 기술 결정 + 근거
- **vLLM 클라이언트 = httpx 직접** (OpenAI 호환 `POST {base}/chat/completions`).
  이유: 이 venv에 openai SDK 미설치(EMBEDDING_PROVIDER=e5_base라 안 깔림). httpx 0.28.1은 있음.
  새 의존성 0 + 항상 동작 → "자체 완결" 원칙에 부합. env: OPENAI_BASE_URL / OPENAI_API_KEY /
  CHAT_MODEL(없으면 OPENAI_ASK_MODEL).
- **단일 진실원.** 챗 도구 실행기는 MCP 도구와 **같은 서비스 함수**를 호출한다
  (suggest_by_similarity · run_import · query_records · semantic_search · build_guide · list_doc_types).
  MCP 래퍼(mcp_runtime.py)의 SessionLocal 패턴을 그대로 복제.
- **스트리밍 = status+result SSE** (토큰 스트리밍 아님, v1). vLLM 호출은 non-stream,
  도구 라운드마다 `event: status`, 종료 시 `event: result`+`event: done`. 토큰 델타/tool-call 델타
  파싱의 취약함을 피해 견고성 우선. (플레이북 §5 SSE 계약 준수.)
- **SSE 수신 = fetch+ReadableStream** (EventSource 아님). POST+헤더 필요 → EventSource 불가
  (플레이북 §2-A ★ 동일 이유).
- **도구 루프 상한** = 8라운드. 무한 tool-call 방지.

## 검증 제약 (정직)
- vLLM 라이브 테스트 불가(외부 GPU 호스트, §8) + 이 dev box PG는 좀비.
- 그래서: chat_svc 도구루프는 **mock vLLM 클라이언트**로 유닛테스트(오케스트레이션 = 위험지점).
  echo 모드 SSE는 curl로. 프론트는 playwright 렌더. 실 vLLM+PG는 타겟 서버에서.

## SSE 이벤트 계약 (플레이북 §5 준수)
```
event: status  data: {"step":"유사 데이터 확인 중","tool":"find_similar_data"}
event: result  data: {"role":"assistant","content":"…","tool_trace":[…]}
event: error   data: {"code":"llm_unconfigured|vllm_down|timeout|bad_input","message":"…"}
event: done    data: {}
```

## LLM 연결 설정 (2차 — 상암 기본 + 설정 UI)
- **기본 = 상암 프로덕션 LLM(B300).** `http://10.198.143.137:10000/v1`, 모델 `GLM-5-2`
  (ReportArchive backend/app/config.py 의 B300 OpenAI 호환 예시 = 상암. 사용자 "137" 확인).
- **.env 규칙 = ReportArchive 동일.** `LLM_BACKEND`(openai|off) · `LLM_BASE_URL`(/v1 포함) ·
  `LLM_MODEL` · `LLM_API_KEY` · `LLM_TIMEOUT_S` · `LLM_NO_PROXY`(폐쇄망 직결, 기본 true).
  하위호환으로 OPENAI_BASE_URL/CHAT_MODEL/OPENAI_ASK_MODEL 도 읽음.
- **우선순위:** 런타임 override(설정 UI) → env → 상암 기본. `_llm_config()` 가 매 요청 계산.
- **설정 UI** = 대시보드 '데이터 챗 > LLM 연결 설정'(접이식). base_url/model/backend 편집 +
  저장/연결 테스트/기본복귀. `GET/PUT/DELETE /api/chat/config`, `POST /api/chat/config/test`.
  override 는 `{DATA_DIR}/chat_llm_config.json` 에 저장(마이그레이션 없음). **api_key 는 UI 미노출**
  (env 전용, dev↔prod 는 base_url·model 만 스왑 — §3-1).
- **연결 테스트**는 사용자 버튼 트리거(GET {base}/models). §8 자동 프로빙 아님.

## 미결/후속 (이번 범위 밖)
- 포털 MCP Gateway 등록(AIDataHub MCP를 gateway에), 포털 글로벌 챗 연동.
- 대화 히스토리 영속화(현재 요청 바디에 messages 전량 왕복 = 세션 메모리).
- 토큰 스트리밍(event: token) 고도화.
- 파일 바이너리(docx/xlsx) → convert_file 경로 챗 연동(현재는 텍스트/표 붙여넣기).

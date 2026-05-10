# AI Data Hub — 남은 작업 정리 (재구성)

> 2026-05-09 갱신. 직전 갭 분석 (`REMAINING_JOBS_GAP_2026-05-09.md`) 결과 5개 트랙 중
> A·B·C·D 완료, E 부분 완료. **남은 작업은 약 1.5 인일 분량의 polish 만 존재.**
>
> 사용자 지시 반영: **보안·운영은 제외**, **AI 에이전트 RAG 친화 설계가 최우선**.

---

## 🎯 핵심 원칙 (불변)

DB에는 매우 다양한 종류의 데이터가 추가될 것이다. 우수한 AI 에이전트뿐 아니라 **단순한 AI 에이전트**도 백엔드 코드를 읽지 않고 **자기 힘으로 내부 구조를 이해하고 손쉽게 질의**할 수 있어야 한다. 이를 위해 API 자체가 자기설명적이고 RAG-친화적이어야 한다.

---

## 📊 트랙별 상태 요약

| Track | 영역 | 상태 | 비고 |
|-------|------|------|------|
| A | VS Code 확장 (P4~P7) | ✅ 완료 | vsix 빌드됨 + USER_GUIDE 186줄 |
| B | AI Agent Discovery / RAG ★ | ✅ 완료 | 9 MCP 도구 + AGENT_ONBOARDING 188줄 |
| C | 거버넌스 | ✅ 완료 | 마이그레이션 0008 + audit/diff/soft-delete |
| D | 확장성 | ✅ 완료 | async queue + batch CLI + auto-seq + 첨부 영구화 |
| E | 변환기 보강 | 🟡 부분 | 첨부 캡션 자동 추정만 미구현 (1/4) |

---

## 🚀 즉시 진행할 것 (남은 polish — 약 1.5 인일)

| 우선 | 작업 | 추정 | 담당 |
|------|------|------|------|
| 1 | **PPT/DOCX 첨부 캡션 자동 추정** — 인접 텍스트박스 + alt-text + 슬라이드 제목 점수화. 현재 `ppt_converter/core.py` 가 모든 그림/표/차트에 generic placeholder 캡션을 붙여 RAG 검색 품질에 직접 영향. (Track E 잔여) | 1일 | Agent X |
| 2 | **실데이터 회귀 검증** — `pptx_pairs/`, `real_world_pptx/`, `xlsx_pairs/` 폴더로 batch CLI 회귀. 1번과 묶어 진행 효율적. | 0.3일 | Agent X |
| 3 | **USER_GUIDE 스크린샷 보강** — vsix 설치 → DropZone → ingest → 결과 확인 flow 1회 캡처. (Track A polish — 사용자가 직접 캡처) | 0.2일 | 사용자 |
| 4 | **`AUTO_EMBED_ON_INSERT` end-to-end smoke** — 운영 토글 시 임베딩 잡 수렴 검증 (배치 1000건 기준). | 0.2일 | Agent Y |

> ✅ **MCP 도구 네이밍 정합성** (`explain_schema` alias) — 2026-05-09 처리 완료. `mcp_server/server.py` 에 `explain_schema` 가 `explain_field` alias 로 등록됨.

---

## ✅ 완료된 트랙 (archive)

상세 증거는 `REMAINING_JOBS_GAP_2026-05-09.md` 참조.

### Track A — VS Code 확장 (Agent 28)

- P4 DropZone Webview · P5 메타데이터 폼 · P6 ingest + SecretStorage · P7 vsix
- 산출물: `vscode_extension/ai-data-hub-uploader-0.4.0.vsix`, `USER_GUIDE.md`

### Track B — AI Agent Discovery / RAG ★ (Agent 30)

- `GET /api/discover` · `GET /api/schema` · `GET /api/hints?context=...` · `GET /api/docs/llm.txt` · `POST /api/ask`
- MCP 9개 도구: `discover_schema`, `discover_capabilities`, `ask`, `find_related`, `explain_field` (+`explain_schema` alias), `query_data`, `list_agents`, `get_record`, `search`
- record 메타 확장 (마이그레이션 0007): `agent_hints`, `related_record_ids`, `query_examples`, `access_pattern`
- `docs/AGENT_ONBOARDING.md` (188 lines)

### Track C — 거버넌스 (Agent 31)

- 마이그레이션 0008: `audit_log` 테이블 + `records.deleted_at` + `read_count` / `last_accessed_at`
- API: soft delete (`DELETE /api/records/{id}`), restore (`POST /{id}/restore`), diff (`GET /{id}/diff?from=...`)
- `services/audit.py` (170 lines)

### Track D — 확장성 (Agent 32 일부)

- Async job queue (`services/jobs.py`, in-memory + per-kind Semaphore + TTL)
- 배치 적재 CLI (`api/ingest/batch.py`, 420 lines, 재귀 + 멱등성)
- 자동 임베딩 트리거 (`AUTO_EMBED_ON_INSERT` env)
- auto-seq 발급 (`services/seq.py`)
- 첨부 바이너리 영구화 (`routes/convert.py:354 persist_attachments=True`)

---

## 제외 항목 (사용자 명시)

- ❌ HTTPS/TLS, rate limiting, antivirus 스캔
- ❌ Windows 서비스 등록, NSSM, 백업 자동화
- ❌ Grafana 대시보드, 로그 집계
- ❌ 보안 키 로테이션 (단순 발급만 유지)

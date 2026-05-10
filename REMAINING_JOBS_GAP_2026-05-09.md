# 백엔드/Extension 남은 작업 갭 분석 (2026-05-09)

> 기준: `REMAINING_JOBS.md` (2026-05-08) — 그 후 1일 동안 4개 에이전트(28/30/31/32)가
> 병렬 진행. 본 문서는 **실제 산출물의 존재**를 코드/마이그레이션/파일로 직접 검증한 결과.

---

## 요약 (1줄)

5개 트랙 중 **4개 트랙 완료, 1개 트랙(E)은 부분 완료 (1/4 항목 미흡)** — 신규 기능 미완은 사실상 1건(첨부 캡션 자동 추정)뿐.

---

## Track A — VS Code Extension (Agent 28)

| Phase | 상태 | 증거 | 남은 작업 |
|-------|------|------|----------|
| P4 DropZone Webview | **완료** | `vscode_extension/src/webview/html.ts` (793 lines, drag/drop + preview + 확장자 자동 인식 포함) · `panel.ts` (222 lines) | 없음 |
| P5 메타데이터 폼 | **완료** | `html.ts` 단일 파일에 division/team/year/seq/classification/status/domain/language/tags/agents/quality/valid_range 폼 + 검증 + paintErrors. `OptionsCache` 로 옵션 API 연동 | 없음 |
| P6 ingest + SecretStorage | **완료** | `html.ts:624` `POST /api/convert/ingest` (FormData + XHR progress 90%/100%) · `configStore.ts` `context.secrets.store/get/delete` 로 SecretStorage 사용 · X-API-Key 헤더 | 없음 |
| P7 vsix + 가이드 | **완료** | `vscode_extension/ai-data-hub-uploader-0.4.0.vsix` 빌드됨 · `USER_GUIDE.md` 186 lines · `README.md` 존재 | (선택) e2e 시나리오 자동화 — 명세상 unit 테스트는 요구되지 않음 |

**판정:** Track A 전체 **완료**.

---

## Track B — Discovery / RAG API (Agent 30) ★

| 항목 | 상태 | 증거 |
|------|------|------|
| `GET /api/discover` | **완료** | `api/routes/discover.py:31` + `services/discover_svc.py` (1061 lines) · 60s in-memory 캐시 |
| `GET /api/schema` | **완료** | `discover.py:48` `build_json_schema()` draft-2020-12 |
| `GET /api/hints?context=...` | **완료** | `discover.py:57` 7가지 컨텍스트 (getting_started/searching/filtering_by_agent/tabular_data/time_bounded/attachments/cross_record_relations) |
| `GET /api/docs/llm.txt` | **완료** | `discover.py:79` PlainTextResponse |
| `POST /api/ask` | **완료** | `discover.py:99` LLM(OpenAI) → 키워드 폴백 + `interpreted_query.source` 노출 + `follow_up_queries` |
| MCP `discover_schema` | **완료** | `mcp_server/server.py:117` (discover + schema 합본) |
| MCP `discover_capabilities` | **완료** | `server.py:136` |
| MCP `find_related` (tags/graph/semantic/auto) | **완료** | `server.py:187` |
| MCP `ask` | **완료** | `server.py:166` |
| MCP `explain_field` | **완료** (≈ explain_schema) | `server.py:291` — REMAINING_JOBS 의 `explain_schema` 명세를 `explain_field` 로 구현 |
| `docs/AGENT_ONBOARDING.md` | **완료** | 188 lines (mental model + workflow + agent type별 패턴 + 5가지 예시) |
| 마이그레이션 0007 (agent_hints/related_record_ids/query_examples/access_pattern) | **완료** | `alembic/versions/0007_record_agent_hints.py` |

**판정:** Track B 전체 **완료** (네이밍 `explain_schema` → `explain_field` 1건 차이 있으나 기능적으로 동일).

---

## Track C — 거버넌스 (Agent 31)

| 항목 | 상태 | 증거 |
|------|------|------|
| 마이그레이션 0008 audit_log 테이블 | **완료** | `0008_governance.py:39` (record_id/actor/action/field_changes JSONB/request_id/created_at + 4 인덱스) |
| `records.deleted_at` + 부분 인덱스 | **완료** | `0008:71` `idx_records_deleted_at WHERE deleted_at IS NULL` |
| `read_count / last_accessed_at` | **완료** | `0008:87/96` |
| Soft delete API | **완료** | `routes/records.py:299` (DELETE = soft) · `:319` `POST /{id}/restore` · 기본 list 는 `deleted_at IS NULL` 필터 (`:138, :185`) · `?include_deleted=true` (`:115`) |
| `GET /api/records/{id}/diff?from=...` | **완료** | `routes/records.py:498` |
| audit 서비스 | **완료** | `services/audit.py` (170 lines, `log_action`, `compute_diff`, `record_snapshot`) |
| 사용 통계 갱신 | **완료** | `routes/records.py:61` `_bump_read_count` (fire-and-forget 별도 세션) |

**판정:** Track C 전체 **완료**.

---

## Track D — 확장성 (Agent 32 일부)

| 항목 | 상태 | 증거 |
|------|------|------|
| Async job queue | **완료** | `services/jobs.py` (279 lines) — in-memory `asyncio.create_task` + per-kind Semaphore + TTL pruning. RQ/Arq 미사용 (의도적 — 0009 마이그레이션이 placeholder no-op로 명시) |
| 배치 적재 CLI | **완료** | `api/ingest/batch.py` (420 lines) — `python -m api.ingest.batch <dir> [--workers N] [--dry-run] [--no-attachments]` · 재귀 + 멱등성 |
| 자동 임베딩 트리거 | **완료** | `config.py:49` `AUTO_EMBED_ON_INSERT` env · `ingest/db_writer.py:286` 인서트 후 enqueue · `services/jobs.py:256` |
| auto-seq 발급 | **완료** | `services/seq.py` `next_seq()` `MAX(seq)+1` (UNIQUE 제약 + IntegrityError retry 안내) |
| 첨부 바이너리 영구화 | **완료** | `routes/convert.py:354` `persist_attachments=True` (default) → `:463` `copy_attachments(..., attachments_dir=settings.attachments_dir)` |

**판정:** Track D 전체 **완료**.

---

## Track E — 변환기 보강 (Agent 32 일부)

| 항목 | 상태 | 증거 / 갭 |
|------|------|----------|
| PDF OCR | **완료 (옵션)** | `pdf_converter/ocr.py` — pytesseract 기반, optional import, 시스템 가이드 docstring 포함 |
| PPT 차트 데이터 | **완료** | `ppt_converter/charts.py` `ChartTable` (Bar/Column/Line/Pie/Doughnut/Scatter) — `chart.plots[*].series[*]` → `tables[]` |
| Excel 다중 표 분리 | **완료 (opt-in)** | `excel_converter/detect_multi.py` — contiguous block 휴리스틱, `--detect-multi-tables` 플래그 |
| 첨부 캡션 자동 추정 (인접 텍스트박스) | **부분 / 미흡** | `ppt_converter/core.py:326,343,448,482` 캡션이 모두 placeholder ("슬라이드 {id} 이미지", "Figure N: 차트") 로만 생성됨 — **인접 텍스트박스(alt-text) 활용 로직 미구현**. DOCX/MD 의 `parse_caption`은 명시적 `Figure N:` 패턴만 파싱. |
| (참고) 실데이터 검증 | 명시 없음 — `xlsx_pairs/`, `pptx_pairs/`, `word_pairs/`, `pdf_pairs/`, `real_world_pptx/` 디렉토리 존재 |

**판정:** Track E **부분 완료** — 핵심 기능 3/4 완료, 첨부 캡션 자동 추정만 미구현.

---

## 우선순위 (즉시 다음 작업 5개)

1. **★ PPT/DOCX 첨부 캡션 자동 추정** — 현재 `ppt_converter/core.py` 가 모든 그림/표/차트에 generic placeholder 캡션을 붙이고 있어 RAG 검색 품질에 직접 영향. 인접 텍스트박스 + alt-text + 슬라이드 제목을 점수화해 추정. (Track E 잔여)
2. **MCP 도구 네이밍 정합성** — `explain_field` → `explain_schema` alias 추가 (REMAINING_JOBS 명세와 1:1 매칭). 1줄 작업. (Track B cosmetic)
3. **e2e 시나리오 + USER_GUIDE 스크린샷 보강** — vsix 가 있고 사용자 가이드는 텍스트 186줄. 사업부 배포 직전 단계로, 실제 .vsix 설치 → DropZone → ingest → 결과 확인 flow 1회 캡처. (Track A polish)
4. **실데이터 검증** (Track E 명세 항목) — `pptx_pairs/`, `real_world_pptx/`, `xlsx_pairs/` 폴더로 batch CLI 회귀 돌리기 — 캡션 추정 기능과 함께 묶어 진행 효율적.
5. **`AUTO_EMBED_ON_INSERT` end-to-end smoke** — `db_writer.py:286` 가 인서트 후 enqueue 하지만, 실제 production 토글 시 임베딩 잡 수렴 검증 (배치 1000건 기준). 운영 전환 직전에 필요.

---

## 분담 제안

- **Agent X (1명)**: ★우선순위 #1 + #4 묶어서 진행 (Track E 잔여 — 캡션 추정 + 실데이터 회귀). 가장 임팩트 큼.
- **Agent Y (1명)**: 우선순위 #2 + #3 + #5 (정합성·polish·smoke). 각각 작은 작업이라 1명이 하루에 처리 가능.
- 나머지 28/30/31/32 에이전트는 **해산** — 담당 트랙 완료. Track A/B/C/D 는 추가 작업 없음.

> 종합: 28/30/31 트랙은 명세 100% 충족. 32 는 D 100% / E 75%. 1.5 인일 정도면 모든 명시 항목이 닫힘.

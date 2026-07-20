# Mobile eXperience AI Data Hub — Conversion Rules · Master Index

> 다른 AI 가 이 저장소에 진입할 때 가장 먼저 읽어야 할 단일 진입점.
> 모든 변환 규칙·스키마·구현·운영 산출물의 **위치 지도**.
>
> 작성일: 2026-05-10  ·  형식 버전: 1.0  ·  상위 스키마: [`json_schema_rules.md`](./json_schema_rules.md) v1.1

---

## 0. 30초 요약 (TL;DR)

이 저장소는 **사업부 문서 → AI-친화 JSON → PostgreSQL + pgvector → REST API + 대시보드** 라는 한 줄짜리 파이프라인이다.

```text
[Word | Excel | PPT | MD | PDF | HTML]
        │ (6 converter packages)
        ▼
   [JSON 스키마 v1.0]   ← json_schema_rules.md (단일 계약)
        │ (api_server/src/api/ingest/normalizer.py)
        ▼
[records / record_sections / record_attachments]
        │ (+ pgvector embedding(384) for semantic search)
        ▼
[REST API 56 endpoints + /dashboard SPA + MCP server]
```

작은 AI 가 이 시스템을 사용해야 한다면 [`/api/discover`](http://localhost:8000/api/discover) 와 [`/api/docs/agent-guide?size=small`](http://localhost:8000/api/docs/agent-guide?size=small) 를 먼저 호출하면 된다.

> **다른 AI agent 가 직결할 거라면 → [`agent_pack/`](./agent_pack/) 폴더부터.** API URL 하드코딩된 자체 완결형 가이드 (README + CONFIG + api_reference_compact + schema_reference + patterns + 6 examples). 이 폴더만 들고 가도 즉시 사용 가능.

---

## 1. 변환 규칙 — 6개 포맷 한 번에 보기

| 포맷 | 룰 문서 (MD) | 변환기 패키지 | 핵심 라이브러리 | data_type | 특수 기능 |
|---|---|---|---|---|---|
| **Word** (.docx) | [`word_to_json_conversion_rules.md`](./word_to_json_conversion_rules.md) v1.1 | [`api_server/src/converter/`](./api_server/src/converter/) | `python-docx` | DOC | Heading 휴리스틱, OLE 첨부 9종 분류 |
| **Excel** (.xlsx) | [`excel_to_json_conversion_rules.md`](./excel_to_json_conversion_rules.md) v1.1 | [`api_server/src/excel_converter/`](./api_server/src/excel_converter/) | `openpyxl` | DATA | `_META`/`_GLOSSARY` 시트, **multi-table opt-in** (`detect_multi.py`) |
| **PPT** (.pptx) | [`ppt_to_json_conversion_rules.md`](./ppt_to_json_conversion_rules.md) v1.1 | [`api_server/src/ppt_converter/`](./api_server/src/ppt_converter/) | `python-pptx` | DOC | **차트 데이터 추출** (`charts.py` — bar/line/pie/scatter), 동일 제목 그룹화 휴리스틱 |
| **Markdown** (.md) | [`md_to_json_conversion_rules.md`](./md_to_json_conversion_rules.md) v1.0 | [`api_server/src/md_converter/`](./api_server/src/md_converter/) | `markdown-it-py` | DOC | YAML frontmatter 메타 |
| **PDF** (.pdf) | [`pdf_to_json_conversion_rules.md`](./pdf_to_json_conversion_rules.md) v1.1 | [`api_server/src/pdf_converter/`](./api_server/src/pdf_converter/) | `pdfplumber` + `pypdf` | DOC | **OCR opt-in** (`ocr.py` — Tesseract + pdf2image) |
| **HTML** (.html) | [`html_to_json_conversion_rules.md`](./html_to_json_conversion_rules.md) v1.0 | [`api_server/src/html_converter/`](./api_server/src/html_converter/) | `lxml.html` | DOC | head meta (og:*, dc:*), figcaption |

> **공통 출력 키 (8개)**: `schema_version` · `meta` · `toc` · `sections` · `figures` (deprecated) · `tables` · `sources` · `attachments`. 자세한 매핑은 [`json_schema_rules.md`](./json_schema_rules.md) §13 표.

> **CAD/CAE (변환기 없음, 메타 규칙만)**: MCAD(Parasolid)·ECAD(ODB++)·솔버 덱(LS-DYNA k 등)의
> 레코드 구성·첨부 kind(`cad`/`cae`)·과제/개발단계(dv1…pra)/DOE/BOM 연계 컨벤션은
> [`cad_cae_metadata_rules.md`](./cad_cae_metadata_rules.md) v1.0. 파생 포맷(ECAD-JSON, MCAD-STEP)은 예약만.

### 1-1. 어떤 변환기를 언제 쓰나

| 작성자가 가지고 있는 것 | 권장 입력 포맷 | 사유 |
|---|---|---|
| 보고서/매뉴얼 (구조화 본문) | Word | 가장 풍부한 메타·표·그림 추출 |
| 측정·시험 데이터 표 | Excel | 표 그대로 + `_META` 분리로 의미 보존 |
| 발표 자료 | PPT | 슬라이드 단위 섹션, 차트 데이터까지 추출 |
| 기술 노트 / README | Markdown | 가장 단순한 양식, 100% 본문 보존 |
| 외부 발간 / 스캔 자료 | PDF | OCR opt-in 으로 스캔도 처리 |
| 웹 페이지 / Confluence export | HTML | head meta 자동 매핑 |

---

## 2. 룰 문서 작성 표준 vs 휴리스틱 자동 보정

각 변환기는 **두 모드** 로 동작한다:

1. **작성 표준 모드** — 작성자가 룰 문서대로 작성한 입력. 룰 그대로 적용 → 100 % 정확.
2. **레거시/휴리스틱 모드** — 작성자가 표준을 모르거나 따르지 못한 입력. 변환기가 자동 보정 (default ON / opt-in 분기).

**Default ON 휴리스틱 (모든 변환기 공통):**
- Heading 누락 자동 복구 (텍스트 패턴 → level 추정)
- 캡션 자동 생성 (`Figure N: ...`)
- 첨부 자동 분류 (확장자 + 컨텐츠 → `kind` 11종)

**포맷별 추가 휴리스틱:**

| 포맷 | 휴리스틱 | 끄기 플래그 |
|---|---|---|
| Word | 헤딩 폰트 추론 | `--no-heading-font-heuristic` |
| Excel | multi-table 자동 탐지 | `--detect-multi-tables` (opt-in) |
| PPT | 동일 제목 그룹화 | `--no-group-consecutive-duplicates` |
| PPT | 본문 번호 패턴 → 서브섹션 | `--no-extract-body-headings` |
| PPT | 차트 series·category 추출 | `--no-extract-charts` |
| PDF | outline → 패턴 → 폰트 크기 폴백 | `--fontsize-ratio` 임계값 |
| PDF | 스캔 페이지 OCR | `--ocr` (opt-in) |
| HTML | head meta → `meta.*` 매핑 | (항상 ON) |

---

## 3. 어떤 파일이 어디 사는가 — Layer Map

```text
d:/Personal/AI_data/
│
├─ CONVERSION_RULES_INDEX.md       ← 본 문서 (시작점)
├─ json_schema_rules.md            ← 마스터 JSON 스키마 (모든 변환기 계약)
├─ word_to_json_conversion_rules.md
├─ excel_to_json_conversion_rules.md
├─ ppt_to_json_conversion_rules.md
├─ md_to_json_conversion_rules.md
├─ pdf_to_json_conversion_rules.md
├─ html_to_json_conversion_rules.md
├─ META_FORMAT_AUDIT.md            ← 메타 작성 표준 감사 (P0/P1/P2 이슈)
│
├─ api_server/                     ← FastAPI + SQLAlchemy 서버
│   ├─ src/
│   │   ├─ api/                    ← REST 라우터 + 서비스 + DB 모델
│   │   │   ├─ routes/             ← /api/{records,search,groups,taxonomy,...}
│   │   │   ├─ services/           ← embedding, search_svc, cluster_svc, ...
│   │   │   ├─ ingest/             ← normalizer.py = JSON → DB 단일 진입
│   │   │   ├─ db/models.py        ← SQLAlchemy 모델 (Record, RecordSection, ...)
│   │   │   └─ main.py             ← FastAPI 앱 + /dashboard 마운트
│   │   ├─ converter/              ← Word
│   │   ├─ excel_converter/        ← Excel (+ detect_multi.py)
│   │   ├─ ppt_converter/          ← PPT (+ charts.py)
│   │   ├─ md_converter/           ← Markdown
│   │   ├─ pdf_converter/          ← PDF (+ ocr.py)
│   │   ├─ html_converter/         ← HTML
│   │   └─ mcp_server/             ← MCP 프로토콜 서버 (AI agent 통합)
│   ├─ static/dashboard/           ← /dashboard SPA (5탭 + API explorer)
│   ├─ alembic/versions/           ← DB 마이그레이션 (현재 0009 까지)
│   ├─ tests/                      ← pytest (regression + per-feature)
│   ├─ docs/                       ← 운영자/에이전트용 부가 문서
│   │   ├─ AGENT_API_GUIDE_TINY.md       ← /api/docs/agent-guide?size=tiny
│   │   ├─ AGENT_API_GUIDE_SMALL.md      ← (1B-3B / 3B-7B / 13B-70B / frontier)
│   │   ├─ AGENT_API_GUIDE_MEDIUM.md
│   │   ├─ AGENT_API_GUIDE_LARGE.md
│   │   ├─ AGENT_ONBOARDING.md     ← AI agent 가 처음 만나는 가이드
│   │   ├─ FAQ.md
│   │   ├─ governance.md           ← 보안 / 권한 / 감사 로그
│   │   ├─ observability.md        ← 메트릭 / 로깅 구조
│   │   └─ mcp_integration_guide.md
│   ├─ setup.bat / run.bat / ingest.bat
│   └─ requirements.txt
│
├─ vscode_extension/               ← VS Code 비개발자 GUI 클라이언트
│   ├─ ai-data-hub-uploader-0.6.0.vsix  (산출물)
│   ├─ USER_GUIDE.md
│   └─ src/                        ← TypeScript (webview)
│
├─ deploy/                         ← 서버 셋업 자동화
│   ├─ SERVER_QUICK_SETUP.bat                    ← Windows 4-step
│   ├─ install_postgres_windows.ps1
│   ├─ install_pgvector_windows.ps1
│   ├─ install_postgres_linux.sh                 ← Linux apt/dnf + source build
│   ├─ install_pgvector_linux.sh
│   ├─ vendor/pgvector-pg18-windows-x64.zip      ← 사전 빌드 binary
│   └─ README_LINUX.md
│
├─ distribution/                   ← 외부 배포용 패키지 (production-ready)
│   ├─ api_server/                 ← .venv/.env 제거된 클린 카피
│   ├─ deploy/                     ← 위 deploy/ 의 미러
│   ├─ client_setup/               ← CLI 클라이언트 (.bat 6종)
│   ├─ vscode_extension/           ← .vsix + 가이드
│   ├─ word_pair_KooRemapper/      ← 변환 전후 비교 페어
│   ├─ ppt_pair_AI_DigitalTwin/
│   ├─ xlsx_pair_StressStrain/
│   ├─ ai_data_strategy_deck.html  ← 25슬라이드 발표자료
│   ├─ SERVER_SETUP_GUIDE.md
│   ├─ CLIENT_SETUP_GUIDE.md
│   ├─ META_FORMAT_AUDIT.md
│   └─ README.md
│
├─ examples/standard/              ← 작성 표준 예시 (룰 그대로 작성된 입력)
│
└─ AI_Data_Distribution.zip        ← distribution/ 을 압축한 배포 산출물 (gitignore)
```

---

## 4. AI agent 진입 시나리오

### 4.1 "이 시스템에 무슨 데이터가 있나?" — 질의 모드

| 단계 | 호출 | 응답 핵심 |
|---|---|---|
| 1 | `GET /api/discover` | total_records, by_data_type, agents, tags top-N |
| 2 | `GET /api/taxonomy/tags?limit=50` | tag cloud + usage_count |
| 3 | `GET /api/search/faceted?q=<자연어>` | items + facets (다음 좁힘 후보) |
| 4 | `POST /api/groups/auto` body=`{q, n_groups, top_k}` | 시맨틱 클러스터 |
| 5 | `GET /api/records/{id}` | 단일 record 전체 본문 |

### 4.2 "이 문서를 적재하려면?" — ingest 모드 3가지

| 단계 | 호출 | 비고 |
|---|---|---|
| **A** (원본 → 서버 변환) | `POST /api/convert/ingest` (multipart, .docx/.pptx 등) | 가장 간단 — 서버가 변환기 실행 |
| **B** (검수만) | `POST /api/convert` | JSON 만 반환, DB 적재 안 함 |
| **C** (사전 변환된 번들) | `POST /api/ingest/bundle` (multipart, .zip) | **JSON + 자원 폴더 통째 zip 업로드** — 변환 skip + figures/attachments 자동 배치 |
| 검증 | `GET /api/records/{id}` | 적재 결과 확인 |

대안 (CLI): `cd api_server && ingest.bat <파일경로>` (서버 머신에서) — A 와 같은 파이프라인.

자세한 패턴: [`agent_pack/patterns.md`](./agent_pack/patterns.md) §13.

### 4.3 "그룹 / 분류된 문서들만 가져오려면?"

자세한 룰은 [`META_FORMAT_AUDIT.md`](./META_FORMAT_AUDIT.md) — 권장 패턴:

- `tags = ["group:<코드>"]` (그룹 식별)
- `tags = ["checklist", "scope:<범위>"]` (체크리스트 종류)
- `classification = "internal|restricted-<group>"` (권한)

발췌 호출: `GET /api/records?tag=group:CAE&tag=checklist`

---

## 5. JSON 스키마 핵심만 (참조용)

전체는 [`json_schema_rules.md`](./json_schema_rules.md) 참조. 가장 자주 묻는 키만:

```jsonc
{
  "schema_version": "1.0",
  "meta": {
    "id": "DOC-HE-CAE-2026-0000000001",   // {prefix}-{team}-{group}-{year}-{seq6}
    "data_type": "DOC",                // DOC | DATA | SIM | CAD | LOG | FORM | OTHER
    "title": "...",
    "summary": "...",
    "tags": [...],
    "agents": [...],                    // 이 record 를 소비하는 agent_type[]
    "domain": "CAE",
    "classification": "internal",
    "language": "ko",
    "schema_version": "1.0",
    // ... (json_schema_rules §4 참조)
    "agent_hints": "...",               // free-text (markdown)
    "related_record_ids": [...],
    "query_examples": [...],
    "access_pattern": "frequent|occasional|rare"
  },
  "toc": [...],
  "sections": [
    {
      "id": "1.2",
      "level": 2,
      "title": "...",
      "blocks": [...],                  // paragraph | code | list_item | table_ref | figure_ref
      "figure_refs": ["F001"],
      "table_refs": ["T001"]
    }
  ],
  "tables": [...],
  "attachments": [...],                  // kind: figure | document | spreadsheet | media | archive | cad | cae | drawing | data | chart | other (11종, api/schemas/attachment.py)
  "sources": [...]
}
```

DB 측: `record_sections.embedding (vector(384))` 가 `EMBEDDING_PROVIDER=e5_small` 사용 시 `multilingual-e5-small` 로 채워지며 `embedding <=> qvec` (코사인 거리) 으로 시맨틱 검색.

---

## 6. 변경 추적

### v1.3 (2026-05-10) — 코드 사이드 P0 닫힘 + 자동 메타 채움

코드 패치 3건으로 v1.2 의 KNOWN GAP / 잔여 자동화 갭 해소. 룰 MD 도 새 동작 반영.

| 변경 | 위치 |
|---|---|
| **A-1** normalizer 가 0006 10필드 (classification/status/domain/...) 흡수 | `normalizer.py:103-153, 240-274, 280-359` |
| **A-2** 6 변환기 모두 0007 필드 (`agent_hints`/`query_examples`/`access_pattern`) 자동 채움 | `_build_meta` × 6 + `_apply_agent_discovery_defaults` |
| **A-3** Word `summary` (extractive lead-3) / `tags` (RAKE+stopword) 자동 추출 | `converter/core.py:132-339, 1119-1142` |
| **doc** `json_schema_rules.md` §4.4 KNOWN GAP 박스 → "v1.3 닫힘" 박스로 교체 | `json_schema_rules.md` §4.4 |
| **doc** 6 per-converter 룰 MD §0 노트 새 동작 반영 | 각 룰 §0 |
| **doc** `META_FORMAT_AUDIT.md` v1.3 — P0 모두 닫힘, A-1/A-2/A-3 done 표기, 잔여 A-4~A-10 명시 | `META_FORMAT_AUDIT.md` |
| **검증** pytest 318 + E2E (normalize → write_record → DB read-back) 통과 | `d:/tmp/e2e_full2.py` |

이전과의 호환성: **JSON 출력 스키마는 v1.0 그대로**. 새 동작은 모두 작성자가 메타 출력 안 하면 자동 채움 (`agent_hints` 등) 또는 DB 까지 정상 흐름 (`classification` 등).

### v1.2 (2026-05-10) — 코드 정합

룰 MD 들을 **변환기 소스 코드를 단일 진실 공급원으로** 정렬. [`META_FORMAT_AUDIT.md`](./META_FORMAT_AUDIT.md) P0 8건 중 6건 doc-fixed, 2건 code-TODO 식별.

| 문서 | 변경점 |
|---|---|
| `json_schema_rules.md` | §4 meta — `doc_id` / `agent_scope` 1차 표기 + `derivation` enum 정정 + KNOWN GAP 박스 (classification 등 미흡수) + own-extras 표 §4.6 |
| `*_to_json_conversion_rules.md` (6종) | §0 "코드 정합 노트" 박스 추가 — 변환기별 식별자/agent/own-extras/KNOWN GAP 위치 명시 |
| `META_FORMAT_AUDIT.md` | P0 8건에 Status 라벨 (doc-fixed/code-TODO) + §5 잔여 액션 A/B/C 분류 |

### v1.1 (2026-05-10) — 6변환기 정식화

이 인덱스 v1.0 발행 + 6 변환기 cross-ref + 새 기능 정식화:

| 문서 | 변경점 |
|---|---|
| `json_schema_rules.md` | 5→6 변환기 (PDF·HTML 추가), embedding/embedded_at/embedding_model 명세, `attachments` 정식화, 변환기 차이 표 6열 |
| `pdf_to_json_conversion_rules.md` | OCR opt-in 정식 (`--ocr`, `pdf_converter/ocr.py`, Tesseract+poppler 의존성 표) |
| `ppt_to_json_conversion_rules.md` | 차트 데이터 추출 정식 (`charts.py`, bar/line/pie/scatter, `--no-extract-charts`) |
| `excel_to_json_conversion_rules.md` | multi-table opt-in (`--detect-multi-tables`, `detect_multi.py`) |
| `word_to_json_conversion_rules.md` | 8-키 최상위 출력 키 표 (attachments 정식 포함), 6 변환기 cross-ref |
| `md_to_json_conversion_rules.md` | 6 변환기 cross-ref |
| `html_to_json_conversion_rules.md` | 6 변환기 cross-ref + 시작점 안내 |

이전 버전과의 호환성: **JSON 출력 스키마는 v1.0 그대로** — 기존 ingest 파이프라인 그대로 동작. 신규 기능은 모두 opt-in 플래그.

---

## 7. 자주 묻는 운영 질문

| 질문 | 답 / 위치 |
|---|---|
| 새 PC 에 셋업하려면? | Python 3.12 설치 → `distribution/deploy/SERVER_QUICK_SETUP.bat` 더블클릭. [`distribution/SERVER_SETUP_GUIDE.md`](./distribution/SERVER_SETUP_GUIDE.md) |
| API 키 발급은? | `cd api_server && python -m api.cli issue-key --name <name>` |
| 시맨틱 검색 임베더 변경? | `api_server/.env` 의 `EMBEDDING_PROVIDER=hash\|openai\|e5_small` |
| HTTPS / 인증 운영? | nginx 앞단 종단 + `AUTH_REQUIRED=true`. [`SERVER_SETUP_GUIDE.md`](./distribution/SERVER_SETUP_GUIDE.md) §"다음 단계" |
| MCP 통합? | [`api_server/docs/mcp_integration_guide.md`](./api_server/docs/mcp_integration_guide.md) |
| 작은 모델용 가이드? | `GET /api/docs/agent-guide?size={tiny,small,medium,large}` |
| 대시보드는? | <http://localhost:8000/dashboard/> (5탭: 상태/카탈로그/검색/그룹/API) |
| 로그·메트릭? | [`api_server/docs/observability.md`](./api_server/docs/observability.md) |
| 권한·감사? | [`api_server/docs/governance.md`](./api_server/docs/governance.md) |

---

## 8. 다음 우선순위 (open items)

| 항목 | 위치 |
|---|---|
| GPU 임베딩 (sentence-transformers + CUDA) | `api_server/src/api/services/embedding.py` |
| 자동 백업 (pg_dump 스케줄러) | `deploy/` 신규 |
| OCR paddleocr 백엔드 옵션 | `pdf_converter/ocr.py` |
| HTTPS 앞단 (nginx/Caddy) 자동화 | `deploy/` 신규 |
| 그룹·분류 메타 자동 부여 (P0 8건) | [`META_FORMAT_AUDIT.md`](./META_FORMAT_AUDIT.md) |

---

*이 인덱스는 시작점이지 마침표가 아니다. 각 영역의 권위 있는 룰은 해당 MD 문서이며, 본 인덱스는 길안내용 메타-문서다.*

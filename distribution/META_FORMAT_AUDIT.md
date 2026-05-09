# 공용 메타데이터 포맷 감사 보고서

> 작성일: 2026-05-09
> 대상: `schema_v1` 기반 6개 변환기(Word·Excel·PPT·MD·HTML·PDF) 의 `meta` 객체 + `records` DB 스키마
> 단일 진실 공급원: `d:/Personal/AI_data/json_schema_rules.md`

---

## 1. 현재 공용 meta 필드 카탈로그

명세(`json_schema_rules.md` 4장, `lines 105-196`) 가 정의한 필드와, 6개 변환기가 실제 출력하는 필드를 대조한다.
`O` = 채움(또는 채울 수 있음), `-` = 출력하지 않음, `~` = 옵션·CLI override 로만 가능.

| 필드 | 타입 | 명세 필수 | Word | Excel | PPT | MD | HTML | PDF | 비고 |
|------|------|----------|------|-------|-----|----|----|-----|------|
| `id` | string | 필수 | - | - | - | - | - | - | 모든 변환기가 `doc_id` 키로 출력. 명세는 `id`. (P0 키 충돌) |
| `doc_id` | string | (명세에 없음) | O | O (`data_id` 로 별도) | O | O | O | O | 명세에 `doc_id` 키 없음. |
| `data_type` | enum | 필수 | - | - (`schema_version=data.v1`) | - | - | - | - | 변환기 어디도 `meta.data_type` 안 씀 → ID prefix 로만 판단. |
| `division` / `team` / `year` / `seq` | — | 필수 | - | - | - | - | - | - | 변환기 meta 에 출력 X. ID 파싱으로만 도출 |
| `title` | string | 필수 | O `core.title` 또는 파일명 | O `_META.title` 또는 빌트인 `wb.properties.title` | O `core.title` 또는 stem | O `front_matter.title` 또는 첫 h1 | O `<title>` 또는 첫 h1 | O `/Info.Title` 또는 첫 헤딩 | 일관성 양호 |
| `summary` | string | 필수 | ~ override 만 | O `_META.summary` / `description` 빌트인 | ~ `_extract_summary_fallback` (slide note) | O `front_matter.summary` | O `<meta name="description">` | O `/Info.Subject` | Word 만 자동 추출 없음 |
| `tags` | string[] | 필수 | ~ override 만 | O `_META.tags` / `keywords` 빌트인 | ~ CLI tags | O `front_matter.tags` | O `<meta name="keywords">` | O `/Info.Keywords` 분할 | Word 자동 추출 없음 |
| `agents` | string[] | 권장 | ~ `agent_scope` override | O `_META.agents` | ~ CLI agents | O `front_matter.agents` | O `<meta name="agents">` | ~ CLI agents | **명세는 `agents`, 변환기는 `agent_scope`** (P0) |
| `author` | string | 권장 | O `core.author` | O `creator` 빌트인 | O `core.author` | O `front_matter.author` | O `<meta name="author">` | O `/Info.Author` | 일관성 양호 |
| `department` | string | 권장 | O `{div}-{team}` 합성 | - (워크북 키 없음) | O 합성 | O 합성 | O 합성 | O 합성 | Excel 만 자동 채움 없음 |
| `project` | string | 선택 | - | O `_META.project` | - | ~ front_matter | ~ head_meta | - | 5/6 누락 |
| `source_file` | string | 권장 | O | - (DATA payload 의 `source.sheet` 만) | O | O | O | O | Excel 의 source 키가 별도 |
| `source_format` | string | (명세에 없음) | O `"docx"` | - | O `"pptx"` | O `"md"` | O `"html"` | O `"pdf"` | 명세에 정식으로 없음 — 사실상 표준이지만 미정의 (P1) |
| `doc_type` | string | (명세에 없음) | O `"manual"`/override | - | O `"slide"` | O `"manual"` | O `"manual"` | O `"manual"` | 명세에 없음. data_type 와 의미 충돌 (P1) |
| `created` / `modified` | date | (명세에 없음) | O `YYYY-MM-DD` | - (빌트인 `created`/`modified` 사용 안 함) | O `YYYY-MM-DD` | O front_matter > now | O head_meta > now | O `/Info.CreationDate` 10자리 | 명세에 정식 키 없음. DB 컬럼 `records.created_at` 은 ingest 시각 (충돌 위험) (P1) |
| `version` | string | 권장 | O `"1.0"` 고정 | O `_META.version` | O `"1.0"` 고정 | O front_matter / `"1.0"` | O `"1.0"` | O `"1.0"` 고정 | Word/PPT/HTML/PDF 는 항상 1.0 |
| `classification` | enum | 기본 internal | - | O `_META.classification` | - | ~ `front_matter_extra` 잔존 | O head meta name | - | 4/6 미지원. 일관성 X (P0) |
| `status` | enum | 기본 draft | - | O `_META.status` | - | ~ `front_matter_extra` | O head meta name | - | 동상 (P0) |
| `domain` | string | 선택 | - | O `_META.domain`/빌트인 `subject` | - | ~ FM extra | O head meta name | - | 자동 추출 거의 없음 (P1) |
| `subject_keywords` | string[] | 선택 | - | O `_META.subject_keywords` | - | ~ FM | - | - | 5/6 미지원 |
| `source_system` | string | 선택 | - | O `_META.source_system` | - | ~ FM | - | - | 5/6 미지원 |
| `language` | string | 기본 ko | - | O `_META.language` | - | ~ FM | O head meta | - | Word/PPT/PDF 는 모름 (P1) |
| `parent_record_id` | string | 선택 | - | - | - | ~ FM | - | - | 모두 미지원 |
| `derivation` | enum | 기본 original | - | - | - | ~ FM | - | - | 모두 미지원 + **명세 enum vs 코드 enum 불일치** (P0) |
| `quality_score` | int 0~100 | 선택 | - | - | - | ~ FM | - | - | 모두 미지원 |
| `valid_from` / `valid_until` | date | 선택 | - | - | - | ~ FM | - | - | 모두 미지원 |
| `agent_hints` | text | 선택 (Mig 0007) | - | - | - | ~ FM | - | - | 모두 미지원 |
| `related_record_ids` | string[] | 선택 | - | - | - | ~ FM | - | - | 모두 미지원 |
| `query_examples` | string[] | 선택 | - | - | - | ~ FM | - | - | 모두 미지원 |
| `access_pattern` | enum | 기본 occasional | - | - | - | ~ FM | - | - | 모두 미지원 |

증빙 (코드 라인):

- Word `_build_meta`: `api_server/src/converter/core.py:749-778`
- Excel `_parse_meta_sheet` 화이트리스트: `api_server/src/excel_converter/core.py:337-363`, `_extract_workbook_properties`: `api_server/src/excel_converter/core.py:503-545`, payload: `api_server/src/excel_converter/core.py:125-159`
- PPT `_build_meta`: `api_server/src/ppt_converter/core.py:741-796`
- MD `_build_meta`: `api_server/src/md_converter/core.py:608-667`
- HTML `_build_meta`: `api_server/src/html_converter/core.py:603-660`, `_extract_head_meta` 화이트리스트: `api_server/src/html_converter/core.py:238-273`
- PDF `_build_meta`: `api_server/src/pdf_converter/core.py:596-677`, `extract_pdf_metadata`: `api_server/src/pdf_converter/parser.py:118-163`
- Normalizer 흡수 경로: `api_server/src/api/ingest/normalizer.py:97-144`, `205-235`
- RecordIn 정의: `api_server/src/api/schemas/common.py:61-103`
- Migration 0006: `api_server/alembic/versions/0006_record_metadata_extension.py:46-151`
- Migration 0007: `api_server/alembic/versions/0007_record_agent_hints.py:37-78`

---

## 2. 변환기별 고유 meta (own extras)

| 변환기 | 키 | 위치 | 내용 |
|--------|----|------|------|
| Word | `agent_scope` | `meta.agent_scope` | `meta_overrides` 통해 주입. (명세는 `agents`) `core.py:770-771` |
| Excel | `tables[].context` | payload 의 `context` | 시트 레벨 `_META: sheet:<name>.{description,method,condition,equipment,operator,date,notes,caveats}`. `core.py:354-363` |
| Excel | `tables[].column_descriptions` | payload 의 `column_descriptions` | `_GLOSSARY` description. `core.py:495` |
| Excel | `tables[].units_map` | payload 의 `units_map` | `_GLOSSARY` unit (객체) — 명세 `tables[].units` (객체) 와 키명 다름 (P0) |
| Excel | DATA 페이로드 자체 | top-level `data_id`, `schema_version="data.v1"`, `caption`, `division`, `team`, `year`, `headers`, `rows`, `row_count`, `column_count`, `source.{sheet,kind}`, `generated_at` | 다른 변환기와 schema_version 자체가 다름. `core.py:125-159` |
| PPT | `agent_scope` | top-level meta | Word 와 동일 비표준 |
| MD | `front_matter_extra` | `meta.front_matter_extra` | `title/tags/agents/summary/author/doc_type/created/modified/version` 외 모든 front matter 잔존 키. `core.py:649-656` |
| HTML | `head_meta_extra` | `meta.head_meta_extra` | 표준 매핑되지 않은 `<meta name=...>` 모두 + `_extra`. `core.py:641-650` |
| PDF | `meta.pdf` | top-level meta 의 `pdf` 객체 | `{page_count, heading_strategy, creator, producer, creation_date, modification_date}`. `core.py:651-664` |
| PDF | `creator` / `producer` | `meta.pdf.creator` 등 | PDF 작성 SW 식별 (Adobe, MS Word 등) |
| Excel/Word/PPT | `_META`/외부에서 받는 `extra_meta` | 통째로 meta 에 머지 (MD/HTML/PDF) | classification·status 등 명세 필드를 CLI 에서 강제 주입할 수 있는 유일한 통로 |

**문제점:**

- 6개 변환기 모두가 다른 own-extras 컨테이너 키를 사용한다 (`pdf` / `head_meta_extra` / `front_matter_extra` / `context` / 합쳐서 top-level). 명세는 own-extras 의 표준 컨테이너(예: `meta.{format}.*`) 를 정의하지 않음.
- Excel 의 `units_map` 은 명세 11.2.1 (`tables[].units` 객체) 와 키명이 다르다.

---

## 3. 발견된 불일치 / 갭 (우선순위별)

### P0 — 즉시 정정 필요 (이름 충돌·필수 필드 누락)

| # | 항목 | 증거 | 영향 |
|---|------|------|------|
| P0-1 | **`meta.id` vs `meta.doc_id` 키 충돌** | 명세 `json_schema_rules.md:114, 142, 697` 는 `meta.id` 를 PK 로 명시. 6개 변환기 모두 `meta.doc_id` 출력 (`converter/core.py:757`, `ppt_converter/core.py:775`, `md_converter/core.py:633`, `html_converter/core.py:625`, `pdf_converter/core.py:635`, Excel 은 `data_id`). normalizer 가 `meta.doc_id` 폴백을 가지고 있어(`normalizer.py:104`) 통과는 함. | 명세 위반. 검증 단계에서 `meta.id` 직접 체크하면 즉시 실패. |
| P0-2 | **`meta.agents` vs `meta.agent_scope` 키 충돌** | 명세 `json_schema_rules.md:158` 는 `agents`. 변환기는 모두 `agent_scope` 로 출력 (Word `core.py:771`, PPT `core.py:789`, MD `core.py:647`, HTML `core.py:639`, PDF `core.py:649`). normalizer 가 폴백 (`normalizer.py:108`). | 동상 |
| P0-3 | **명세 필수 필드 `data_type`/`division`/`team`/`year`/`seq` 어느 변환기도 출력 안 함** | 명세 `json_schema_rules.md:140-148`. 변환기 _build_meta 어디에도 없음. ID prefix 에서만 도출됨. | 명세 4.1 검증 위배. ingester 가 ID 파싱으로 보충하나 `meta` 자체 검증은 실패. |
| P0-4 | **`derivation` enum 값 불일치** | 명세 `json_schema_rules.md:181, 715`: `original/revision/translation/extract`. 코드 `api_server/src/api/schemas/common.py:26, 36-41`: `original/extracted/aggregated/translated`. | 4개 enum 중 `original` 1개만 일치. 명세대로 `revision`/`extract` 입력 시 RecordIn 검증 실패. |
| P0-5 | **classification/status/domain/subject_keywords/source_system/language/derivation/quality_score/valid_from/valid_until 가 normalizer 에서 추출되지 않음** | `api_server/src/api/ingest/normalizer.py:103-130, 205-235` 의 `_extract_doc` / `_common_fields` 어디에도 위 필드를 읽는 코드 없음. RecordIn 은 정의돼 있으나 (`schemas/common.py:85-97`) 항상 기본값 사용. | 변환기가 출력해도 (Excel/HTML/MD) DB 까지 가지 못함. 0006 컬럼 사실상 dead. |
| P0-6 | **0007 `agent_hints/related_record_ids/query_examples/access_pattern` 어떤 변환기도 채우지 않음** | 6개 `_build_meta` grep 결과 0건. MD/HTML 의 front_matter_extra/head_meta_extra 로만 들어갈 수 있고, normalizer 는 `meta.agent_hints` 를 읽기는 하지만 변환기가 안 채움. | RAG-친화 메타가 0% coverage. |
| P0-7 | **Excel 의 `tables[].units` 객체 키명 불일치** | 명세 `json_schema_rules.md:407, 776`: `units` 객체 `{column_name: 단위}`. 코드 출력 키는 `units_map` (`excel_converter/core.py:122, 158, 758`). | 명세 검색·필터 인덱스가 못 찾음. |
| P0-8 | **Excel payload 가 schema_v1 자체를 따르지 않음** | `excel_converter/core.py:125-159` payload top-level 은 `data_id`/`schema_version="data.v1"`/`headers`/`rows`/`source.kind` 등. 명세 3장의 표준 7개 키(`schema_version="1.0"`, `meta`, `toc`, `sections`, `tables`, `attachments`, `sources`, `figures`) 가 아님. `meta` 는 옵션 추가 필드. | normalizer 가 `_extract_data` 로 흡수하지만 (`normalizer.py:147-160`) 명세 16장 표와 충돌. Excel JSON 만 단독 검증 시 실패. |

### P1 — RAG 친화도 큰 영향 (다음 사이클)

| # | 항목 | 증거 | 영향 |
|---|------|------|------|
| P1-1 | **`source_format` / `doc_type` / `created` / `modified` 명세에 정식으로 없음** | 변환기 모두 출력 (`source_format`, `doc_type`) 하지만 명세 4장 표에 없음. `doc_type` 은 `data_type` 과 의미 충돌. `created`/`modified` 도 명세 미정의 (RAG 시간 필터에 핵심). | 검증 통과해도 의미 없는 프리포맷 필드. RAG 시간 슬라이스에 못 씀. |
| P1-2 | **`language` 자동 감지 부재** | 명세 기본 `ko` (`json_schema_rules.md:179`). HTML 은 `<html lang>` 안 봄. PDF/PPT/Word 모두 본문 언어 분석 안 함. `pdf_converter/core.py`/`html_converter/core.py:238-273` 미참조. | en/ko 혼합 코퍼스에서 언어 필터 무력화. |
| P1-3 | **`domain` 자동 분류 없음** | 6개 변환기 어디에서도 본문 분석 기반 도메인 분류 안 함. Excel `_META.domain` 만 수동 기재. | 작은 모델이 도메인별 슬라이스를 못 함. |
| P1-4 | **`summary` 자동 추출 부재 (Word 한정 심각)** | Word `_build_meta` 는 `meta_overrides` 만 사용 (`converter/core.py:768`) — 본문 요약 추출 로직 없음. PDF 는 `/Info.Subject` 가 있어야만 채움. | summary 가 RAG 1차 필터 (명세 4.2) 인데 빈 문자열 다수. |
| P1-5 | **`tags` 자동 추출 부재 (Word/PPT 한정)** | Word `tags` 는 override 만, PPT 는 CLI tags 만. 본문 키워드 추출 없음. | tags 검색이 변환자 책임으로 떠넘겨짐. |
| P1-6 | **own-extras 컨테이너 명명 표준 부재** | PDF `meta.pdf`, HTML `meta.head_meta_extra`, MD `meta.front_matter_extra`, Word/PPT 는 그냥 top-level. 명세에 표준 컨테이너 키 정의 없음. | 작은 모델이 변환기별 키를 따로 외워야 함. |
| P1-7 | **`agents` enum/agents 테이블 부재** | 명세 `json_schema_rules.md:786-790` 는 `meta.agents[]` 가 `agents.agent_type` 에 등록돼야 한다고 명시. 변환기는 검증 없이 자유 문자열 입력. | 오타·고아 agent 식별자가 그대로 통과. |
| P1-8 | **PDF `creation_date` 와 명세 `meta.created` 의 정규화 충돌 가능성** | PDF 는 `meta.pdf.creation_date` 에 ISO8601 (시간 포함) 둠. `meta.created` 는 10자리 절단. 다른 변환기는 timezone 정보 손실 (`docx core.py:753`, `ppt core.py:759-760`). | 시간 비교 RAG 쿼리에서 정밀도 손실. |

### P2 — 장기 개선 (장기 로드맵)

| # | 항목 | 증거 | 영향 |
|---|------|------|------|
| P2-1 | DB 컬럼 `records.capabilities` 는 normalizer 가 채움 (명세 `json_schema_rules.md:723`) 으로 정의됐지만 실제 매핑 코드는 본 normalizer 에 없음. | normalizer.py 어디에도 `compute_capabilities` 호출 없음. | capability 슬라이스 dead. |
| P2-2 | `meta.created`/`modified` 가 string vs DB 의 `records.created_at` (ingest 시각) 키 충돌 | 명세 13장은 `records.created_at` 만 정의. 변환기 출력 `meta.created` 는 어느 컬럼으로도 매핑 없음. | 작성일이 검색 가능 컬럼이 안 됨. |
| P2-3 | `attachments[].file_path` POSIX 슬래시 강제 (명세 16장 마지막) — PDF 는 백슬래시→슬래시 변환만 있음 (`pdf_converter/core.py:691`). Word/PPT 본문 코드 미확인. | Windows 경로 누출 가능. |
| P2-4 | `figures` legacy 키와 `attachments` 동시 출력 시 우선순위 (명세 7장) — 변환기 코드에 분기 없음. | 중복 ingestion 가능. |
| P2-5 | `meta.id` 의 6자리 seq 와 `meta.seq` 일치 검증 코드 부재 | RecordIn `parse_id` 만 호출 (`schemas/common.py:107-110`). seq 일치 검증 없음. | 손상된 ID 통과. |

---

## 4. 보강 제안 — 추가하면 좋을 메타 필드

작은 모델(3B~7B) 이 추가 추론 없이 검색·요약·QA 하기 위한 메타. 자동 추출 가능한 것 중심으로 제안.

| 필드 | 타입 | 자동 추출 방법 | 6개 변환기에서 채우는 방법 | 기대 효과 |
|------|------|-----------------|------------------------------|-----------|
| `key_phrases` | string[] (10~30개) | TF-IDF 또는 yake / KR-WordRank. 본문 평탄화 후 길이 2~6 어절 후보 추출 | normalizer 단계에서 `record_sections.content_text` 평탄화 직후 1회 계산 → records 컬럼 추가 (Mig 0010) | 작은 모델이 tags 부족할 때 fallback 검색 키. **RAG hit-rate 직접 향상** |
| `entity_list` | string[] | spaCy NER (한국어 KoNLPy) 또는 정규식 (대문자 약자 `[A-Z]{2,8}`, 한글 고유명사 후보) | 동상. 본문 1회 스캔 | 약어/제품명 검색 향상 (예: `IGA`, `KooRemapper`) |
| `language_detected` | enum (`ko`/`en`/`ja`/`mixed`) | langdetect 또는 char-ratio 휴리스틱 (한글:영문:기타 비율) | 모든 변환기에서 본문 평탄화 후 1회 (Excel 은 시트별 합산) | 명세 P1-2 해결. 다국어 코퍼스 슬라이스 가능 |
| `domain_auto` | enum + score | 도메인 분류 모델 (HE-CAE/HE-MFG/... 사전 라벨) 또는 tags + 사전 매핑 | normalizer 단계에서 1회. 변환기는 raw `tags`/`title` 만 제공 | 명세 P1-3 해결. 작은 모델의 도메인 우선 검색 |
| `claim_evidence_count` | object `{claims, evidence_pairs, code_blocks, tables_count, figures_count}` | sections 트리 순회 후 카운트. claim = `paragraph` 끝이 `.이다`/`.한다`, evidence = `figure_ref`/`table_ref` 가 인접한 paragraph | normalizer 가 sections 으로부터 도출. 변환기 변경 불필요 | 7대 원칙 적합도 자동 측정 |
| `structure_score` | int 0~100 | 7대 원칙 (제목 명확성, summary 존재, tags 2개+, sections 트리 균형, 캡션 존재율, 표/그림 ref 존재율, agents 등록 여부) 가중합 | normalizer 가 도출 → records.quality_score 에 저장 가능 (현 컬럼 재활용) | quality_score 가 자동화됨. 작은 모델이 "신뢰 가능한 문서만" 슬라이스 가능 |
| `meta.format_extras` | object | 변환기별 own-extras 의 표준 컨테이너. 안에 `{word/excel/ppt/md/html/pdf}` 키 | 6개 변환기 모두 자기 키만 채움. 기존 `pdf`/`head_meta_extra`/`front_matter_extra`/`context`/`agent_scope` 를 이 컨테이너로 재배치 | own-extras 명명 표준화. 명세 5번 갭 해결 |
| `summary_auto` | string | 본문 첫 N 문장 추출 (extractive) 또는 lead-3 — 내장 모델 호출 없이 가능 | 변환기 _build_meta 에서 sections[0].blocks[0].text 추출 폴백 | 명세 P1-4 해결. summary 빈 문자열 0건 |
| `tags_auto` | string[] | RAKE / yake / 빈도 기반 fallback | 변환기에서 자동 추출. 명시적 tags 가 있으면 보강 (union, 중복 제거) | 명세 P1-5 해결 |
| `embedding_anchor_text` | string | RAG 임베딩 1차 컨텐츠로 사용할 짧은 자기-소개 문장 — `title + summary + key_phrases[:5]` | normalizer 가 도출 | 작은 임베딩 모델의 일관된 entry-point. record-level retrieval 안정화 |
| `created_iso` / `modified_iso` | string (ISO 8601 with TZ) | 변환기 원본 날짜를 timezone 포함 ISO 로 보존 | 6개 변환기 모두 timezone 정보 보존 (현재는 strftime 으로 절단) | P1-8 해결. 시간 RAG 쿼리 정확도 |

---

## 5. 다음 액션 우선순위

작은 모델 RAG 친화도 향상 임팩트 = (적용 변환기 수) × (RAG 쿼리 빈도) × (자동화 가능 여부) 기준 정렬.

1. **`meta.id`/`meta.agents` 표준화 + 명세 필수 5필드(`data_type`/`division`/`team`/`year`/`seq`) 변환기 출력 추가** (P0-1, P0-2, P0-3). 명세 단일 진실 공급원 회복. 모든 후속 자동화 검증의 전제. 변환기 6개 _build_meta 에 8줄씩 추가하면 됨.

2. **Normalizer 의 0006/0007 메타 흡수 경로 보강** (P0-5, P0-6). `_extract_doc` / `_common_fields` 가 `meta.classification`·`status`·`domain`·`subject_keywords`·`source_system`·`language`·`derivation`·`quality_score`·`valid_from`·`valid_until` 를 읽도록 10줄 추가. RAG-친화 메타가 0% → 100% (out-of-the-box).

3. **`derivation` enum 명세-코드 동기화** (P0-4). 명세 `revision/translation/extract` 또는 코드 `extracted/aggregated/translated` 중 하나로 일원화. 정합성 핵심.

4. **Word `summary`/`tags` 자동 추출 + 6개 변환기 `language_detected`** (P1-2, P1-4, P1-5). 모든 신규 record 에 RAG 1차 필터 자동 채움. 본문 한 번 평탄화하면 yake + langdetect 2개 호출로 끝.

5. **`structure_score` (= `quality_score` 자동화) + `key_phrases` + `entity_list`** 를 normalizer 단계에서 도출. 변환기 변경 0줄. RAG 검색 hit-rate 정량 향상 즉시 측정 가능.

6. **own-extras 표준 컨테이너 `meta.format_extras.{word/excel/ppt/md/html/pdf}` 도입** (P1-6, 보강 제안). 변환기별 자기 키만 채움. 작은 모델이 외워야 할 키 6 → 1.

7. **Excel payload schema_v1 합치** (P0-8). `data.v1` → `1.0` + `data_type="DATA"` + `meta` top-level + `tables[]` 본체. 검증 단일화.

8. **`agents` 등록 검증** (P1-7). `meta.agents[]` 의 모든 값을 `agents.agent_type` 과 대조. ingest 시 미등록 agent 는 경고 후 통과 → 추후 strict.

위 1-3 만 처리해도 변환기 ↔ DB 계약이 명세대로 회복된다. 4-6 은 RAG hit-rate 향상의 자동화 토대.

---

*본 보고서는 d:/Personal/AI_data 의 2026-05-09 시점 코드를 근거로 작성. 모든 라인 번호는 검증 가능하다.*

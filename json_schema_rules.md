# 범용 문서 JSON 스키마 규칙서

## AI 친화적 문서 데이터 표준 v1.0 (DB-precision)

> 작성일: 2026-05-07 (개정: 2026-05-08)
> 적용 대상: Word·Excel·PPT·MD에서 변환된 모든 사업부 문서 JSON
> 소비자: `src/api/ingest/normalizer.py` → PostgreSQL `records` / `record_sections` / `record_attachments`

---

## 목차

1. [목적과 범위](#1-목적과-범위)
2. [schema_version](#2-schema_version)
3. [최상위 구조](#3-최상위-구조)
4. [meta — 문서 신원 정보](#4-meta)
5. [toc — 목차](#5-toc)
6. [sections — 본문 계층 트리](#6-sections)
7. [figures — 그림 정보 (deprecated)](#7-figures-deprecated)
8. [tables — 표 데이터](#8-tables)
9. [attachments — 첨부 객체 (NEW)](#9-attachments)
10. [sources — 외부 파일 참조](#10-sources)
11. [data_type별 content 페이로드 변종](#11-data_type별-content-페이로드-변종)
12. [ID 포맷](#12-id-포맷)
13. [DB 컬럼 매핑 표](#13-db-컬럼-매핑-표)
14. [content_hash 산출](#14-content_hash-산출)
15. [검증 체크리스트](#15-검증-체크리스트)
16. [변환기별 차이점 요약 표](#16-변환기별-차이점-요약-표)
17. [완전한 예시](#17-완전한-예시)

---

## 1. 목적과 범위

본 규칙서는 변환기(Word/Excel/PPT/MD)와 데이터베이스(PostgreSQL) 사이의 **단일 계약(contract)** 이다.

- 모든 변환기는 본 스키마를 따르는 JSON을 생성한다.
- `src/api/ingest/normalizer.py`는 본 스키마에 정의된 경로만 신뢰한다.
- 동일 JSON은 `records`(1행) + `record_sections`(N행) + `record_attachments`(M행)으로 정규화된다.
- 변환기 간 차이는 `data_type`과 `content` 페이로드 형태로만 표현한다 (11장 참조).
- 본 규칙을 위반하는 JSON은 ingest 단계에서 거부된다.

---

## 2. schema_version

```json
"schema_version": "1.0"
```

| 항목 | 내용 |
|------|------|
| 타입 | string |
| 필수 | 필수 |
| 형식 | `"주버전.부버전"` |
| 현재값 | `"1.0"` |

**규칙:**

- 키 삭제·타입 변경·의미 변경은 주버전을 올린다 (예: `1.0` → `2.0`).
- 키 추가·enum 값 추가 등 하위 호환 변경은 부버전을 올린다 (예: `1.0` → `1.1`).
- ingester는 `schema_version` 첫 자리가 다르면 거부한다.

---

## 3. 최상위 구조

모든 변환 JSON은 다음 키만 가진다.

```json
{
  "schema_version": "1.0",
  "meta":        { ... },
  "toc":         [ ... ],
  "sections":    [ ... ],
  "tables":      [ ... ],
  "attachments": [ ... ],
  "sources":     [ ... ],
  "figures":     [ ... ]
}
```

**규칙:**

- 키 순서는 위 순서를 따른다.
- 빈 배열도 키를 명시한다 (`"tables": []`).
- 위에 정의되지 않은 최상위 키를 추가하지 않는다.
- `figures`는 v1.0에서 deprecated이다 (7장 참조). 신규 변환기는 `attachments`만 사용한다.

| 키 | 타입 | 필수 | 비고 |
|----|------|------|------|
| `schema_version` | string | 필수 | "1.0" |
| `meta` | object | 필수 | 4장 |
| `toc` | array | 필수 (빈 배열 허용) | 5장 |
| `sections` | array | 필수 | 6장 |
| `tables` | array | 필수 (빈 배열 허용) | 8장 |
| `attachments` | array | 필수 (빈 배열 허용) | 9장 |
| `sources` | array | 필수 (빈 배열 허용) | 10장 |
| `figures` | array | 선택 (하위 호환) | 7장 |

---

## 4. meta

문서의 신원증명서. AI가 식별·분류·인용·필터링하는 데 사용한다. `meta`의 모든 필드는 `records` 테이블의 컬럼으로 매핑된다.

```json
"meta": {
  "id":               "DOC-HE-CAE-2026-000001",
  "data_type":        "DOC",
  "division":         "HE",
  "team":             "CAE",
  "year":             2026,
  "seq":              1,
  "title":            "IGA (Isogeometric Analysis) 가이드",
  "summary":          "KooRemapper v1.3.0의 IGA 기능 사용법을 설명...",
  "tags":             ["IGA", "LS-DYNA", "NURBS", "FEM"],
  "agents":           ["iga-analyst", "code-assistant"],
  "source_file":      "iga_guide.docx",
  "author":           "홍길동",
  "department":       "CAE팀",
  "project":          "KooRemapper",
  "version":          "1.0",
  "classification":   "internal",
  "status":           "approved",
  "domain":           "구조해석",
  "subject_keywords": ["등기하해석", "솔리드해석"],
  "source_system":    "manual_authoring",
  "language":         "ko",
  "parent_record_id": null,
  "derivation":       "original",
  "quality_score":    85,
  "valid_from":       "2026-05-07",
  "valid_until":      null
}
```

### 4.1 식별 필드

| 필드 | 타입 | 필수 | 규칙 |
|------|------|------|------|
| `id` | string | 필수 | 12장 형식. 전사 고유. `records.id`. |
| `data_type` | string (enum) | 필수 | DOC/DATA/SIM/CAD/LOG/FORM/OTHER. `records.data_type`. |
| `division` | string | 필수 | 팀 코드 대문자. id에서 파싱 가능. |
| `team` | string | 필수 | 그룹 코드 대문자. id에서 파싱 가능. |
| `year` | integer | 필수 | 4자리. id에서 파싱 가능. |
| `seq` | integer | 필수 | 1~999999. id의 6자리 순번에 대응. |

`id`만으로 division/team/year/seq를 추론할 수 있어야 하며, 별도 필드와 일치해야 한다 (검증 항목).

### 4.2 RAG 필드

| 필드 | 타입 | 필수 | 규칙 |
|------|------|------|------|
| `title` | string | 필수 | 원본 제목. 버전 표기 금지 (별도 `version`). |
| `summary` | string | 필수 | 1~3문장, 최대 500자. RAG 1차 필터. |
| `tags` | string[] | 필수 | 2~20개. 짧은 키워드. 부서·작성자·날짜 금지. |
| `agents` | string[] | 권장 | 참조해야 하는 에이전트 식별자. `agents` 테이블에 등록된 값. |
| `domain` | string | 선택 | 도메인 분류 (구조해석/제어/품질 등). |
| `subject_keywords` | string[] | 선택 | tags 보조. 한국어 정규 키워드. |

### 4.3 출처/저자 필드

| 필드 | 타입 | 필수 | 규칙 |
|------|------|------|------|
| `source_file` | string | 권장 | 원본 파일명만. 경로 불포함. |
| `source_system` | string | 선택 | PLM/Confluence/SharePoint 등 발생지 식별자. |
| `author` | string | 권장 | `"홍길동"` 또는 `"홍길동, 김철수"`. |
| `department` | string | 권장 | 작성 부서명. |
| `project` | string | 선택 | 과제명. |
| `version` | string | 권장 | 원본 표기 그대로 (`"1.0"`, `"r3"`). |

### 4.4 분류/생애주기 필드

| 필드 | 타입 | 필수 | 허용값 / 규칙 |
|------|------|------|---------------|
| `classification` | enum | 기본 `"internal"` | `public` / `internal` / `confidential` / `restricted` |
| `status` | enum | 기본 `"draft"` | `draft` / `review` / `approved` / `deprecated` |
| `language` | string | 기본 `"ko"` | ISO 639-1 (`ko`, `en`, ...) |
| `parent_record_id` | string | 선택 | 다른 record의 `id`. FK. |
| `derivation` | enum | 기본 `"original"` | `original` / `revision` / `translation` / `extract` |
| `quality_score` | integer | 선택 | 0~100. SMALLINT. |
| `valid_from` | date | 선택 | `"YYYY-MM-DD"`. |
| `valid_until` | date | 선택 | `"YYYY-MM-DD"`. |

### 4.4-bis Agent discovery 필드 (Migration 0007)

AI 에이전트가 record 자체에서 사용 힌트를 얻을 수 있도록 하는 확장 필드.
`meta.*` 또는 raw top-level 어디에 둬도 normalizer 가 인식한다 (`meta` 우선).

| 필드 | 타입 | 필수 | 규칙 |
|------|------|------|------|
| `agent_hints` | string | 선택 | 자유 텍스트(마크다운). 에이전트가 이 record 를 어떻게 사용해야 하는지 사람이 작성한 힌트. |
| `related_record_ids` | string[] | 선택 | 다른 record id 의 배열. 수동 큐레이션 관계 그래프. |
| `query_examples` | string[] | 선택 | 이 record 를 다루기 위한 자연어 쿼리 예시 배열. `/api/ask` few-shot 학습 자료. |
| `access_pattern` | enum | 기본 `"occasional"` | `frequent` / `occasional` / `rare`. UI/캐싱 전략 힌트. |

### 4.5 enum 정의

**data_type:**

| 값 | 의미 | 주 변환기 |
|----|------|-----------|
| `DOC` | 문서·매뉴얼·보고서·슬라이드 | Word, PPT, MD |
| `DATA` | 데이터 표·측정값 시트 | Excel |
| `SIM` | 시뮬레이션 입력/결과 메타 | Word(보고서)+sources |
| `CAD` | CAD 메타·도면 인덱스 | Word + sources |
| `LOG` | 로그·로우 텍스트 기록 | MD, plain text |
| `FORM` | 양식·체크리스트 | Word, Excel |
| `OTHER` | 그 외 | 모든 변환기 |

**classification / status / derivation:** 위 표 참조.

---

## 5. toc

목차. AI가 문서 전체를 읽지 않고 구조만 빠르게 파악할 때 사용한다.

```json
"toc": [
  { "id": "1",   "level": 1, "title": "개요" },
  { "id": "1.1", "level": 2, "title": "IGA란 무엇인가" },
  { "id": "2",   "level": 1, "title": "작동 원리" }
]
```

| 필드 | 타입 | 필수 | 규칙 |
|------|------|------|------|
| `id` | string | 필수 | sections의 id와 정확히 일치. |
| `level` | integer | 필수 | 1, 2, 3 중 하나. |
| `title` | string | 필수 | sections의 title과 정확히 일치. |

**규칙:**

- 평탄(flat) 리스트로 표현한다. 트리 구조 금지.
- level 1~3만 포함. level 4 이상은 toc에서 생략한다.
- 문서 출현 순서대로 나열한다.
- `record_sections` 테이블에 직접 매핑되지 않으며, ingester는 `sections`만 정규화한다 (toc는 검증 보조용).

---

## 6. sections

문서 본문을 계층 트리로 표현한다. 각 노드는 `record_sections` 1행으로 정규화된다 (트리는 평탄화하여 저장하되, `level`과 `section_id`로 트리 복원이 가능해야 한다).

### 6.1 구조

```json
"sections": [
  {
    "id":          "1.1",
    "level":       2,
    "title":       "IGA란 무엇인가",
    "blocks": [
      { "type": "paragraph", "text": "IGA(Isogeometric Analysis)는 ..." },
      { "type": "code",      "lang": "yaml", "text": "iga:\n  bbox_scale: 1.5" },
      { "type": "list_item", "level": 1, "text": "NURBS 기저함수 사용" }
    ],
    "figure_refs": ["DOC-HE-CAE-2026-000001-A001"],
    "table_refs":  ["DOC-HE-CAE-2026-000001-T001"],
    "children":    []
  }
]
```

### 6.2 섹션 노드 필드

| 필드 | 타입 | 필수 | 규칙 |
|------|------|------|------|
| `id` | string | 필수 | `"1"`, `"1.2"`, `"2.3.1"`. `record_sections.section_id`. |
| `level` | integer | 필수 | 1~6 (Word는 1~3, MD는 1~6). `record_sections.level`. |
| `title` | string | 필수 | 번호 제외. `record_sections.title`. |
| `blocks` | object[] | 필수 | 본문 블록 배열. JSONB로 보존. |
| `figure_refs` | string[] | 필수 (빈 배열 허용) | 첨부(이미지) id 목록. |
| `table_refs` | string[] | 필수 (빈 배열 허용) | 표 id 목록. |
| `children` | object[] | 필수 (빈 배열 허용) | 하위 섹션 트리. |

### 6.3 blocks 형식

`blocks`는 변환기마다 일부 type이 다르며 (16장 참조), 공통 type은 다음과 같다.

| type | 필드 | 의미 |
|------|------|------|
| `paragraph` | `text` | 단락 텍스트 |
| `code` | `lang`, `text` | 코드 블록 |
| `list_item` | `level`, `ordered`, `text` | 리스트 항목 |
| `table_ref` | `id` | tables 배열의 id 참조 (인라인 위치 표기) |
| `figure_ref` | `id` | attachments 배열의 id 참조 (인라인 위치 표기) |
| `quote` | `text` | 인용 |
| `heading_inline` | `level`, `text` | level 4+ 헤딩(트리에 포함 안 됨) |

**규칙:**

- `blocks`는 출현 순서를 보존한다.
- 표·그림의 본문은 `tables` / `attachments` 배열에 저장하고, blocks에는 참조만 둔다.
- 변환기는 자체 type을 추가할 수 있으나 (`type` 필드는 항상 string), normalizer는 알 수 없는 type을 그대로 JSONB에 보존한다.

### 6.4 content_text 도출

`record_sections.content_text` 컬럼은 변환기가 생성하지 않고 normalizer가 `blocks`로부터 다음과 같이 도출한다.

1. 각 block의 `text`를 평탄화하여 이어 붙임.
2. block 사이에는 `\n\n` 삽입.
3. `code` 블록은 ``````lang\n...\n`````` 형태로 보존.
4. `figure_ref` / `table_ref`는 `[Figure A001]`, `[Table T001]` 토큰으로 치환.

(변환기는 `content_text`를 직접 채우지 않는다.)

### 6.5 트리 깊이 규칙

- Word: 최대 깊이 3 (Heading 1/2/3). Heading 4+는 `blocks[].type = heading_inline`으로 강등.
- MD: 최대 깊이 6 (h1~h6).
- PPT: 최대 깊이 2 (1=섹션, 2=슬라이드).
- Excel: 최대 깊이 1 (1=시트).

### 6.6 섹션 ID 일관성

- toc의 모든 `id`가 sections에 존재해야 한다.
- `figure_refs` / `table_refs`의 모든 id가 `attachments` / `tables` 배열에 존재해야 한다.
- `children`은 비어있어도 키를 생략하지 않는다.

---

## 7. figures (deprecated)

v0.x에서 사용한 `figures` 배열은 v1.0부터 `attachments`로 통합되었다.

**하위 호환:**

- `figures` 키가 존재하면 ingester는 `kind="figure"`인 `attachments`로 변환하여 받아들인다.
- 신규 변환기는 `figures`를 사용하지 않는다 (`attachments` 직접 사용).
- 변환기가 `figures`와 `attachments` 둘 다 출력하면 `attachments`가 우선한다.
- `sections[].figure_refs`는 v1.0에서도 유지되며, 값은 attachment의 id를 참조한다 (필드명만 레거시).

`figures` 항목 필드(레거시):

| 필드 | 매핑 |
|------|------|
| `id` | → `attachments[].id` |
| `number` | → `attachments[].number` |
| `caption` | → `attachments[].caption` |
| `section_ref` | → `attachments[].section_ref` |
| `image_path` | → `attachments[].file_path` |
| `source_ref` | → `attachments[].source_ref` |

---

## 8. tables

표는 `headers + rows`로 구조화하여 저장한다. 평문 문자열로 표를 표현하면 안 된다.

### 8.1 구조

```json
"tables": [
  {
    "id":          "DOC-HE-CAE-2026-000001-T001",
    "number":      1,
    "caption":     "Table 1: FEM과 IGA의 형상 함수·연속성 비교",
    "section_ref": "1.1",
    "headers":     ["항목", "FEM", "IGA"],
    "rows": [
      ["형상 함수", "Lagrange 다항식", "NURBS"],
      ["연속성",    "C0",              "Cp-1"]
    ]
  }
]
```

### 8.2 필드 규칙

| 필드 | 타입 | 필수 | 규칙 |
|------|------|------|------|
| `id` | string | 필수 | `"{record_id}-T{3자리}"` |
| `number` | integer | 필수 | 1부터 연속 번호 |
| `caption` | string | 필수 | `"Table N: ..."` 시작 |
| `section_ref` | string | 필수 | 등장 섹션의 id |
| `source_ref` | string | 선택 | sources의 id (BOM/netlist 등) |
| `headers` | string[] | 필수 | 첫 행, 길이 = 모든 row 길이 |
| `rows` | array[][] | 필수 | 각 행은 `headers`와 동일 길이 |

### 8.3 셀 값 타입

| 상황 | 타입 | 예시 |
|------|------|------|
| 텍스트 | string | `"Lagrange"` |
| 숫자 | number | `1000`, `3.14` |
| 빈 셀 | `null` | `null` |
| 불리언 의미 | string | `"Y"` / `"N"` |

**규칙:**

- 빈 셀은 빈 문자열 `""`이 아닌 `null`로 표현.
- 병합 셀은 각 셀에 동일 값을 반복 기재.
- `headers`에는 단위를 포함 (`"무게(kg)"`).

### 8.5 Excel 전용 확장 필드 (선택)

원칙 6 (`_META` / `_GLOSSARY`) 가 적용된 Excel 변환 결과는 `tables[]` 항목에 다음
선택 필드가 추가된다 (자세한 내용은 11.2.1 참조).

| 필드 | 타입 | 의미 |
|------|------|------|
| `context` | object | `{description, method, condition, equipment, operator, date, notes, caveats}` |
| `column_descriptions` | object | `{column_name: "이 컬럼의 의미"}` |
| `units` | object | `{column_name: "단위"}` — Excel 한정. (다른 변환기는 `headers`에 인라인) |

### 8.4 DB 저장

`tables` 배열 전체는 `records.content` JSONB의 일부로 보존된다 (별도 테이블 없음). 표 텍스트는 RAG 임베딩 시 평탄화된다.

---

## 9. attachments

문서에 포함된 모든 첨부 객체(이미지·차트·OLE·외부 링크 등). 각 항목은 `record_attachments` 1행으로 정규화된다. **캡션은 필수**이다 — AI는 첨부의 바이너리를 직접 보지 못하기에 캡션이 유일한 정보 통로이다.

### 9.1 구조

```json
"attachments": [
  {
    "id":          "DOC-HE-CAE-2026-000001-A001",
    "number":      1,
    "kind":        "image",
    "caption":     "Figure 1: NURBS 박스와 FE mesh의 공간 관계 — 파란색 박스가 회색 mesh를 감싼 형태",
    "file_name":   "F001.png",
    "file_path":   "DOC-HE-CAE-2026-000001/A001.png",
    "mime_type":   "image/png",
    "size_bytes":  482917,
    "hash_sha256": "9f3b2e8a47c1...",
    "section_ref": "1.2",
    "source_ref":  "DOC-HE-CAE-2026-000001-S001",
    "extra":       { "width": 1920, "height": 1080, "alt": "NURBS box" }
  }
]
```

### 9.2 필드 규칙

| 필드 | 타입 | 필수 | 규칙 |
|------|------|------|------|
| `id` | string | 필수 | `"{record_id}-A{3자리}"` |
| `number` | integer | 필수 | 1부터 연속 |
| `kind` | enum | 필수 | 9개 값 중 하나 (아래) |
| `caption` | string | 필수 | 의미 있는 설명. 단순 번호 금지. |
| `file_name` | string | 권장 | 파일명만 |
| `file_path` | string | 선택 | POSIX 슬래시 상대경로. 정적 마운트 기준. |
| `mime_type` | string | 권장 | `image/png`, `application/pdf` 등 |
| `size_bytes` | integer | 권장 | BIGINT |
| `hash_sha256` | string | 권장 | 64자 hex |
| `section_ref` | string | 필수 | 등장 섹션 id |
| `source_ref` | string | 선택 | sources의 id (외부 CAD 등) |
| `extra` | object | 선택 | kind별 부가정보 (width/height/alt/slide_index 등). JSONB. |

### 9.3 kind 9종

| 값 | 의미 | 대표 mime_type |
|----|------|----------------|
| `image` | 비트맵/벡터 이미지 (Word/PPT 인라인) | image/png, image/jpeg, image/svg+xml |
| `chart` | PPT/Excel 차트 이미지 | image/png |
| `ole` | Word 임베디드 OLE (Excel·PPT 등) | application/vnd.openxmlformats-* |
| `equation` | 수식 객체 | application/mathml+xml |
| `audio` | 오디오 클립 | audio/mpeg |
| `video` | 비디오 클립 | video/mp4 |
| `external_link` | 외부 URL/UNC 경로 (MD의 외부 그림 링크 포함) | (없음 또는 inode 기반) |
| `archive` | zip/tar 등 묶음 첨부 | application/zip |
| `other` | 위에 속하지 않는 모든 첨부 | (자유) |

### 9.4 caption 작성 규칙

캡션은 AI가 바이너리 없이 첨부 내용을 이해할 수 있는 수준이어야 한다.

**필수 포함:**

- 번호: `"Figure N: "` / `"그림 N: "` / `"Chart N: "` 등
- 무엇: 대상 부품·공정·데이터의 이름
- 조건: 해석 조건, 측정 조건, 버전 등

**권장 포함:**

- 핵심 수치 (최대값·범위·특이점)
- 색상·마킹 의미

**나쁜 예:**

```text
"caption": "그림 1"
"caption": "Figure 2: 해석 결과"
```

**좋은 예:**

```text
"caption": "Figure 1: 브라켓 von Mises 응력 분포 (하중 1,000N, 최대 250MPa, 빨간색=응력 집중부)"
```

### 9.5 변환기별 캡션 매칭 (요약)

| 변환기 | 캡션 매칭 방법 |
|--------|---------------|
| Word | 인라인 이미지 직후의 `"Figure N: ..."` 또는 `"그림 N: ..."` 패턴 단락 |
| Excel | 시트 이름을 캡션으로 사용 |
| PPT | 슬라이드 노트 첫 줄 + 이미지의 alt-text 결합 |
| MD | `![alt](path)`의 `alt` 텍스트 |

---

## 10. sources

CAD·외부 도면·외부 시뮬레이션 입력 등 **본 문서가 참조하는 외부 원본 파일**. `attachments`와 달리 이 객체는 외부에 존재하며, 본 문서는 메타데이터만 보관한다.

### 10.1 구조

```json
"sources": [
  {
    "id":          "DOC-HE-CAE-2026-000001-S001",
    "type":        "MCAD",
    "format":      "CATPart",
    "file_name":   "bracket_v3.CATPart",
    "file_path":   "//file-server/PLM/HE/CAE/2026/bracket/bracket_v3.CATPart",
    "modified":    "2026-04-15T14:32:00",
    "size_bytes":  8429472,
    "hash_sha256": "9f3b2e8a...",
    "description": "브라켓 v3 형상 (재료 변경 후 두께 조정)"
  }
]
```

### 10.2 필드 규칙

| 필드 | 타입 | 필수 | 규칙 |
|------|------|------|------|
| `id` | string | 필수 | `"{record_id}-S{3자리}"` |
| `type` | enum | 필수 | MCAD/ECAD/DRAWING/SIM/DOC/OTHER |
| `format` | string | 필수 | `CATPart`, `STEP`, `ODB++`, `k`, `dxf`, ... |
| `file_name` | string | 필수 | 파일명만 |
| `file_path` | string | 필수 | UNC 또는 PLM 절대경로 |
| `modified` | string | 필수 | ISO 8601 (`YYYY-MM-DDTHH:MM:SS`) |
| `size_bytes` | integer | 필수 | BIGINT |
| `hash_sha256` | string | 권장 | 64자 hex (동일성 정밀 판정) |
| `description` | string | 권장 | 한 줄 설명 |

### 10.3 type 값

| 값 | 대표 포맷 |
|----|-----------|
| `MCAD` | CATPart, CATProduct, STEP, IGES, sldprt, prt |
| `ECAD` | ODB++, IPC-2581, brd, sch, gerber |
| `DRAWING` | dxf, dwg, pdf 도면 |
| `SIM` | k (LS-DYNA), inp (Abaqus), cdb (Ansys) |
| `DOC` | 다른 부서의 외부 참조 문서 |
| `OTHER` | 그 외 |

### 10.4 동일성 판정

1. `hash_sha256` 일치가 최우선.
2. 해시가 없으면 `file_name + size_bytes + modified` 조합.
3. `file_path`만으로는 불충분(이동/복사 가능).

### 10.5 attachments / tables와의 연결

`attachments[].source_ref` 또는 `tables[].source_ref`에 sources의 id를 명시할 수 있다. 명시된 경우 sources에 실제 존재해야 한다.

---

## 11. data_type별 content 페이로드 변종

`records.content` JSONB는 변환기와 무관하게 동일 키 집합(meta·toc·sections·tables·attachments·sources·figures)을 가지지만, 의미가 변형되는 부분이 있다.

### 11.1 DOC (Word·PPT·MD)

- 정상적인 본문 트리. `sections[].blocks`에 paragraph/code/list_item 등이 모두 등장.
- `tables`, `attachments` 모두 사용.

### 11.2 DATA (Excel)

- `sections`는 시트 단위 1-depth. `level=1`, `title=시트명`.
- `blocks`는 비어있거나 시트 설명 paragraph 1개.
- `tables`가 본 페이로드의 핵심. 시트 1개 → table 1개.
- `attachments`는 일반적으로 빈 배열.

#### 11.2.1 데이터 의미 컨텍스트 (원칙 6)

Excel 변환기는 **표 자체(데이터)** 와 **표의 의미(메타)** 를 분리해서 추출한다.
의미 정보는 작성자가 `_META` / `_GLOSSARY` 시트, 또는 워크북 빌트인 속성에 기술하며,
변환기는 이를 다음 위치에 보존한다.

| JSON path                                | 의미                                                    | 출처                              |
|------------------------------------------|--------------------------------------------------------|----------------------------------|
| `meta.title`, `meta.summary`, `meta.tags`, `meta.agents`, `meta.domain`, `meta.classification`, `meta.status`, `meta.language`, `meta.source_system`, `meta.author`, `meta.department`, `meta.project`, `meta.version`, `meta.subject_keywords` | 워크북 단위 메타 — 모든 시트가 공유 | `_META` 워크북 키 / 빌트인 속성 |
| `tables[].context.description`           | 이 표(시트)가 무엇을 측정/기록한 것인가                  | `_META: sheet:<name>.description` |
| `tables[].context.method`                | 측정 방법·시험 표준                                     | `_META: sheet:<name>.method`     |
| `tables[].context.condition`             | 환경·시험 조건                                          | `_META: sheet:<name>.condition`  |
| `tables[].context.equipment`             | 사용 장비                                              | `_META: sheet:<name>.equipment`  |
| `tables[].context.operator`              | 시험 수행자                                            | `_META: sheet:<name>.operator`   |
| `tables[].context.date`                  | 측정/시험 날짜                                         | `_META: sheet:<name>.date`       |
| `tables[].context.notes`                 | 특이사항                                               | `_META: sheet:<name>.notes`      |
| `tables[].context.caveats`               | 데이터 한계·주의사항                                    | `_META: sheet:<name>.caveats`    |
| `tables[].column_descriptions`           | `{column_name: "이 컬럼의 의미"}`                       | `_GLOSSARY` 의 description       |
| `tables[].units`                         | `{column_name: "단위"}` (객체 형식 — 컬럼 → 단위 매핑)   | `_GLOSSARY` 의 unit / 헤더 인라인 |

위 키는 **선택**(optional) 이며, 작성자가 의미 컨텍스트를 기술하지 않은 워크북에서는
키 자체가 생략된다 (빈 객체로 출력하지 않는다).

자세한 작성 표준은 `excel_to_json_conversion_rules.md` 의 10~12장을 참조한다.

### 11.3 SIM (Word 보고서 + sources)

- DOC와 동일한 본문 구조.
- `sources`에 시뮬레이션 입력/출력 파일이 다수 등장.
- `meta.domain`은 `구조해석` / `유체` / `열` 등으로 채워짐.

### 11.4 CAD

- 본문은 비어있거나 매우 짧음 (`sections`는 1~2개 노드).
- `sources`가 핵심. CAD 파일들의 인덱스 역할.
- `attachments`에 CAD 뷰 캡쳐 이미지가 포함될 수 있음.

### 11.5 LOG

- `sections`는 단일 1-depth, `blocks`는 paragraph/code 위주.
- `tables` / `attachments`는 거의 비어있음.

### 11.6 FORM

- `sections`는 form 항목 그룹.
- `tables`에 체크리스트 행이 들어감 (`headers=["항목","기준","결과"]` 형태).
- `attachments`는 양식 원본 PDF가 들어갈 수 있음.

### 11.7 OTHER

- 위 분류에 속하지 않는 모든 경우. 본문 구조는 자유롭되 본 스키마는 따른다.

---

## 12. ID 포맷

### 12.1 record id

```text
{DATA_TYPE}-{DIV}-{TEAM}-{YYYY}-{6digits}
```

| 자리 | 규칙 |
|------|------|
| `DATA_TYPE` | 4.5절 enum 값 (DOC/DATA/SIM/CAD/LOG/FORM/OTHER) |
| `DIV` | 팀 대문자 (HE/DA/MX/VD ...) |
| `TEAM` | 그룹 대문자 (CAE/MFG/QA/DEV/PLM ...) |
| `YYYY` | 4자리 연도 |
| `6digits` | 6자리 제로패딩 순번 (000001~999999) |

**예시:**

- `DOC-HE-CAE-2026-000001`
- `DATA-HE-MFG-2026-000034`
- `SIM-HE-CAE-2025-000007`

### 12.2 sub-id (sections 내부)

| 종류 | 형식 | 예시 |
|------|------|------|
| 섹션 | `{n}` / `{n}.{m}` / `{n}.{m}.{k}` | `1`, `1.2`, `2.3.1` |
| Attachment | `{record_id}-A{3자리}` | `DOC-HE-CAE-2026-000001-A001` |
| Table | `{record_id}-T{3자리}` | `DOC-HE-CAE-2026-000001-T001` |
| Source | `{record_id}-S{3자리}` | `DOC-HE-CAE-2026-000001-S001` |
| Figure (legacy) | `{record_id}-F{3자리}` | `DOC-HE-CAE-2026-000001-F001` |

**규칙:**

- 구분자(A/T/S/F)는 반드시 대문자.
- 순번은 3자리 제로패딩.
- 문서 전체에서 연속. 섹션별 초기화 금지.
- `id` 파싱 시 마지막 `-`로 분할하여 prefix와 suffix 분리.

### 12.3 일관성

- `meta.id`의 `DATA_TYPE`은 `meta.data_type`과 일치.
- `meta.id`의 `DIV`는 `meta.division`과 일치.
- `meta.id`의 `TEAM`은 `meta.team`과 일치.
- `meta.id`의 `YYYY`는 `meta.year`와 일치.
- `meta.id`의 6자리 순번은 `meta.seq`와 일치 (제로패딩 제거 후).
- attachments/tables/sources의 모든 sub-id는 `meta.id`로 시작한다.

---

## 13. DB 컬럼 매핑 표

본 표는 JSON 경로와 PostgreSQL 컬럼의 1:1 매핑을 명시한다. ingester는 본 표만 신뢰한다.

### 13.1 records 테이블

| JSON path | type | required | DB column | DB type | notes |
|-----------|------|----------|-----------|---------|-------|
| `meta.id` | string | required | `records.id` | VARCHAR(80) PK | 12장 형식. 파싱하여 division/team/year/seq 도출 |
| `meta.data_type` | enum | required | `records.data_type` | VARCHAR(10) | DOC/DATA/SIM/CAD/LOG/FORM/OTHER |
| `meta.division` | string | required | `records.division` | VARCHAR(10) | 대문자 |
| `meta.team` | string | required | `records.team` | VARCHAR(10) | 대문자 |
| `meta.year` | integer | required | `records.year` | SMALLINT | 4자리 |
| `meta.seq` | integer | required | `records.seq` | INTEGER | 1~999999 |
| `meta.title` | string | required | `records.title` | TEXT | 원본 제목 |
| `meta.summary` | string | required | `records.summary` | TEXT | RAG 1차 필터, 최대 500자 |
| `meta.tags[]` | array<string> | required | `records.tags` | TEXT[] | GIN 인덱스 |
| `meta.agents[]` | array<string> | optional | `records.agents` | TEXT[] | GIN 인덱스 |
| `schema_version` | string | required | `records.schema_version` | VARCHAR(10) | "1.0" |
| (전체 JSON) | object | required | `records.content` | JSONB | 본 JSON 원본 보관 (sections/tables/attachments 등 모두 포함) |
| (computed) | string | computed | `records.content_hash` | VARCHAR(64) | 14장 산출 |
| `meta.source_file` | string | optional | `records.source_file` | TEXT | 파일명만 |
| `meta.author` | string | optional | `records.author` | VARCHAR(200) | 복수는 `, ` 구분 |
| `meta.department` | string | optional | `records.department` | VARCHAR(100) | |
| `meta.project` | string | optional | `records.project` | VARCHAR(200) | |
| `meta.version` | string | optional | `records.version` | VARCHAR(40) | 원본 표기 |
| `meta.classification` | enum | default `internal` | `records.classification` | VARCHAR(20) | public/internal/confidential/restricted |
| `meta.status` | enum | default `draft` | `records.status` | VARCHAR(20) | draft/review/approved/deprecated |
| `meta.domain` | string | optional | `records.domain` | VARCHAR(100) | |
| `meta.subject_keywords[]` | array<string> | optional | `records.subject_keywords` | TEXT[] | |
| `meta.source_system` | string | optional | `records.source_system` | VARCHAR(50) | |
| `meta.language` | string | default `ko` | `records.language` | VARCHAR(10) | ISO 639-1 |
| `meta.parent_record_id` | string | optional | `records.parent_record_id` | VARCHAR(80) FK | records.id 참조 |
| `meta.derivation` | enum | default `original` | `records.derivation` | VARCHAR(20) | original/revision/translation/extract |
| (computed) | string[] | computed | `records.capabilities` | TEXT[] | normalizer가 data_type/blocks 분석으로 채움 |
| `meta.quality_score` | integer | optional | `records.quality_score` | SMALLINT | 0~100 |
| `meta.valid_from` | date | optional | `records.valid_from` | DATE | YYYY-MM-DD |
| `meta.valid_until` | date | optional | `records.valid_until` | DATE | YYYY-MM-DD |
| (computed) | bool | computed | `records.has_attachments` | BOOLEAN | `len(attachments) > 0` |
| (computed) | int | computed | `records.attachment_count` | INTEGER | `len(attachments)` |
| (auto) | timestamp | auto | `records.created_at` | TIMESTAMPTZ | ingest 시각 |
| (auto) | timestamp | auto | `records.updated_at` | TIMESTAMPTZ | 갱신 시각 |

### 13.2 record_sections 테이블

| JSON path | type | required | DB column | DB type | notes |
|-----------|------|----------|-----------|---------|-------|
| (auto) | uuid | auto | `record_sections.id` | UUID PK | gen_random_uuid() |
| `meta.id` | string | required | `record_sections.record_id` | VARCHAR(80) FK | records.id |
| `sections[].id` | string | required | `record_sections.section_id` | VARCHAR(20) | "1.2.3" |
| `sections[].level` | integer | required | `record_sections.level` | SMALLINT | 1~6 |
| `sections[].title` | string | required | `record_sections.title` | TEXT | |
| (computed from `blocks`) | string | computed | `record_sections.content_text` | TEXT | 6.4절 도출 규칙 |
| `sections[].figure_refs[]` | array<string> | optional | `record_sections.figure_refs` | TEXT[] | |
| `sections[].table_refs[]` | array<string> | optional | `record_sections.table_refs` | TEXT[] | |
| `sections[].blocks[]` | array<object> | required | (없음 — `records.content` JSONB에만 저장) | — | 임베딩은 content_text 사용 |
| (later) | vector | later | `record_sections.embedding` | VECTOR(1536) | 추후 |

**평탄화 규칙:** `sections` 트리는 깊이 우선 순회로 행렬화. `children`은 별도 컬럼 없이 `level`+`section_id` 조합으로 트리 복원.

### 13.3 record_attachments 테이블

| JSON path | type | required | DB column | DB type | notes |
|-----------|------|----------|-----------|---------|-------|
| `attachments[].id` | string | required | `record_attachments.id` | VARCHAR(80) PK | "{record_id}-A001" |
| `meta.id` | string | required | `record_attachments.record_id` | VARCHAR(80) FK | records.id |
| `attachments[].number` | integer | required | `record_attachments.number` | INTEGER | 1부터 |
| `attachments[].kind` | enum | required | `record_attachments.kind` | VARCHAR(20) | 9.3절 9종 |
| `attachments[].caption` | string | required | `record_attachments.caption` | TEXT | mandatory |
| `attachments[].file_name` | string | optional | `record_attachments.file_name` | TEXT | |
| `attachments[].file_path` | string | optional | `record_attachments.file_path` | TEXT | POSIX 슬래시 |
| `attachments[].mime_type` | string | optional | `record_attachments.mime_type` | VARCHAR(100) | |
| `attachments[].size_bytes` | integer | optional | `record_attachments.size_bytes` | BIGINT | |
| `attachments[].hash_sha256` | string | optional | `record_attachments.hash_sha256` | VARCHAR(64) | hex |
| `attachments[].section_ref` | string | optional | `record_attachments.section_ref` | VARCHAR(20) | section_id 값 |
| `attachments[].extra` | object | optional | `record_attachments.extra` | JSONB | width/height/alt 등 |

### 13.4 tables / sources

`tables[]`와 `sources[]`는 별도 테이블 없이 `records.content` JSONB 내에 보존된다. 다만 향후 검색을 위해 다음 가상 컬럼으로 인덱스를 둘 수 있다.

| JSON path | DB 표현 |
|-----------|---------|
| `tables[].id` | `records.content -> 'tables'` JSONB GIN |
| `tables[].caption` | (전문 검색 인덱스 후보) |
| `tables[].context.*` (Excel) | `records.content -> 'tables' -> N -> 'context'` JSONB — 검색·필터에 사용 |
| `tables[].column_descriptions.*` (Excel) | `records.content -> 'tables' -> N -> 'column_descriptions'` JSONB |
| `tables[].units.*` (Excel) | `records.content -> 'tables' -> N -> 'units'` JSONB (객체 형식) |
| `sources[].file_path` | `records.content -> 'sources'` JSONB GIN |
| `sources[].hash_sha256` | (해시 GIN 인덱스 후보) |

### 13.5 agents 테이블 (참조)

| 컬럼 | 의미 |
|------|------|
| `agents.agent_type` | `meta.agents[]` 의 각 항목과 매칭되는 식별자 |
| `agents.name` | 사람용 이름 |
| `agents.description` | 역할 설명 |
| `agents.common_tags` | 이 에이전트가 자주 다루는 태그 |
| `agents.data_types` | 이 에이전트가 다루는 data_type 배열 |

`meta.agents[]` 의 모든 값은 `agents.agent_type`에 등록되어 있어야 한다.

---

## 14. content_hash 산출

`records.content_hash`는 동일 문서의 재인입을 탐지하기 위한 안정 해시이다.

**산출 절차:**

1. JSON 객체에서 다음 키를 제거: `meta.created_at`, `meta.updated_at` (있다면).
2. 모든 객체의 키를 **사전순(lexicographic)** 으로 정렬.
3. 모든 공백을 제거 (`json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False)`).
4. UTF-8로 인코딩.
5. SHA-256 적용. 결과는 64자 hex 소문자.

**불포함:**

- `records.id`, `records.created_at` 등 DB 측 필드는 해시에 영향 주지 않음.
- 단, `meta.id`는 포함된다 (id 변경 = 다른 문서로 간주).

**용도:**

- 동일 `content_hash`인 record가 이미 존재하면 ingest는 skip.
- `content_hash`가 다르고 `id`만 같으면 update.

---

## 15. 검증 체크리스트

ingest 전에 변환기 또는 normalizer가 다음을 검증한다.

### 15.1 구조

- [ ] 최상위 키가 정의된 7개(+legacy figures) 외에 없는가
- [ ] `schema_version`이 `"1.0"`인가
- [ ] 빈 배열도 키가 명시되어 있는가

### 15.2 meta

- [ ] `meta.id`가 `{DATA_TYPE}-{DIV}-{TEAM}-{YYYY}-{6digits}` 형식인가
- [ ] `meta.id`에서 파싱한 값이 `data_type`/`division`/`team`/`year`/`seq`와 모두 일치하는가
- [ ] `data_type`이 enum 값(DOC/DATA/SIM/CAD/LOG/FORM/OTHER) 중 하나인가
- [ ] `classification`/`status`/`derivation`이 enum 값 중 하나인가 (기본값 적용 후)
- [ ] `tags`가 2개 이상, 20개 이하인가
- [ ] `summary`가 비어있지 않고 제목 단순 반복이 아닌가
- [ ] `language`가 ISO 639-1 코드인가
- [ ] `parent_record_id`가 명시된 경우 해당 record가 존재하는가
- [ ] `quality_score`가 0~100 범위인가
- [ ] `valid_from <= valid_until` 인가 (둘 다 명시된 경우)

### 15.3 sections

- [ ] toc의 모든 `id`가 sections에 존재하는가
- [ ] 모든 section에 `id`/`level`/`title`/`blocks`/`figure_refs`/`table_refs`/`children` 7개 키가 있는가
- [ ] `figure_refs`의 모든 id가 `attachments` 배열(또는 legacy `figures`)에 실제 존재하는가
- [ ] `table_refs`의 모든 id가 `tables`에 실제 존재하는가
- [ ] `level`이 변환기별 허용 범위 내인가 (Word 1~3, MD 1~6, PPT 1~2, Excel 1)

### 15.4 tables

- [ ] 모든 표의 `id`가 어느 section의 `table_refs`에 포함되어 있는가
- [ ] `headers` 길이와 모든 `rows` 행의 길이가 일치하는가
- [ ] 빈 셀이 `null`로 표현되어 있는가
- [ ] `caption`이 `"Table N: ..."`로 시작하며 의미 있는 설명을 포함하는가

### 15.5 attachments

- [ ] 모든 attachment의 `id`가 `{record_id}-A{3자리}` 형식인가
- [ ] `kind`가 9개 enum 값 중 하나인가
- [ ] `caption`이 비어있지 않고 단순 번호만이 아닌가
- [ ] `section_ref`가 실제 sections에 존재하는 id인가
- [ ] `source_ref`가 명시된 경우 sources에 실제 존재하는가

### 15.6 sources

- [ ] `id`가 `{record_id}-S{3자리}` 형식인가
- [ ] `type`이 enum 값(MCAD/ECAD/DRAWING/SIM/DOC/OTHER) 중 하나인가
- [ ] `modified`가 ISO 8601 형식인가

### 15.7 ID 일관성

- [ ] 모든 sub-id (A/T/S/F)가 `meta.id`로 시작하는가
- [ ] 동일 prefix 내 number가 중복되지 않는가
- [ ] number가 1부터 빈자리 없이 연속하는가

---

## 16. 변환기별 차이점 요약 표

본 표는 변환기 구현 시 참조용이다. 4개 변환기는 동일 스키마를 출력하되 아래 항목에서만 차이가 있다.

| 항목 | Word | Excel | PPT | MD | PDF |
|------|------|-------|-----|-----|-----|
| `data_type` | DOC | DATA | DOC (slide) | DOC | DOC |
| `sections` 깊이 | 1~3 (Heading 1/2/3) | 1 (시트별) | 1~2 (섹션/슬라이드별) | 1~6 (h1~h6) | 1~3 (outline / 패턴 / 폰트 크기) |
| `blocks` types | paragraph / code / table_ref / figure_ref / list_item / quote | (없음, table 단일) | paragraph / figure_ref | paragraph / code / list_item / table_ref / figure_ref / quote | paragraph / table_ref / figure_ref |
| `tables` | Word 표 그대로 | 시트 1개 = table 1개 | 슬라이드 내 표 | MD `\|...\|` 표 | `pdfplumber.extract_tables()` (그리드 표) |
| `attachments` | 인라인 image + OLE + chart | (없음) | 인라인 image + 차트 이미지 + 오디오/비디오 | external_link (외부 그림) | 페이지 image placeholder (바이너리 미추출) |
| 캡션 매칭 규칙 | `"Figure N:"` / `"그림 N:"` 텍스트 패턴 매칭 | 시트 이름 = 캡션 | 슬라이드 노트 + alt-text 결합 | `![alt](path)`의 `alt` | (자동 감지 불가 — placeholder + 검수) |
| `meta.language` 기본 | `ko` | `ko` | `ko` | 파일 frontmatter 우선, 없으면 `ko` | `ko` |
| 파싱 라이브러리 | `python-docx` | `openpyxl` | `python-pptx` | `markdown` + `markdown-it-py` | `pdfplumber` + `pypdf` |
| heading 4+ 처리 | `blocks[].type=heading_inline`으로 강등 | (해당 없음) | (해당 없음) | 트리에 그대로 포함 (level 4~6) | level 3 으로 collapse |
| 코드 블록 출처 | `Code` 스타일 단락 | (해당 없음) | 텍스트 박스에 monospace 폰트 | ` ``` ` fence | (감지 안됨 — 일반 paragraph) |
| 헤딩 추론 우선순위 | Heading 스타일 (정확) | (시트 이름 = section title) | 섹션/슬라이드 타이틀 | `#` `##` 명시 | outline > 패턴 > 폰트 크기 (휴리스틱) |

**변환기 공통 의무:**

- `meta.id`는 변환기가 직접 생성하지 않는다 (사전 발급 시스템에서 받아옴).
- `content_hash`는 변환기가 채우지 않는다 (normalizer가 14장에 따라 산출).
- `attachments[].file_path`는 POSIX 슬래시(`/`)만 사용한다 (Windows 백슬래시 금지).
- 캡션 추출 실패 시 `caption`을 임의로 채우지 않는다 — 변환기는 `caption: "(MISSING)"`로 표시하고 ingest 단계에서 거부되도록 한다.

---

## 17. 완전한 예시

`iga_guide.docx` (Word, DOC)을 변환한 전체 JSON.

```json
{
  "schema_version": "1.0",

  "meta": {
    "id":               "DOC-HE-CAE-2026-000001",
    "data_type":        "DOC",
    "division":         "HE",
    "team":             "CAE",
    "year":             2026,
    "seq":              1,
    "title":            "IGA (Isogeometric Analysis) 가이드",
    "summary":          "KooRemapper v1.3.0의 IGA(등기하해석) 기능 사용 가이드. NURBS 기반 Trimmed Volume 방식으로 FE solid mesh를 IGA로 자동 변환하는 절차, YAML 설정 문법, 생성 파일 구조를 설명한다. LS-DYNA R12 이상 환경 전용.",
    "tags":             ["IGA", "LS-DYNA", "NURBS", "KooRemapper", "FEM", "솔리드해석"],
    "agents":           ["iga-analyst", "code-assistant"],
    "source_file":      "iga_guide.docx",
    "author":           "홍길동",
    "department":       "CAE팀",
    "project":          "KooRemapper",
    "version":          "1.0",
    "classification":   "internal",
    "status":           "approved",
    "domain":           "구조해석",
    "subject_keywords": ["등기하해석", "솔리드해석"],
    "source_system":    "manual_authoring",
    "language":         "ko",
    "parent_record_id": null,
    "derivation":       "original",
    "quality_score":    85,
    "valid_from":       "2026-05-07",
    "valid_until":      null
  },

  "toc": [
    { "id": "1",   "level": 1, "title": "개요" },
    { "id": "1.1", "level": 2, "title": "IGA란 무엇인가" },
    { "id": "1.2", "level": 2, "title": "LS-DYNA에서의 IGA" },
    { "id": "2",   "level": 1, "title": "작동 원리" },
    { "id": "2.1", "level": 2, "title": "Trimmed NURBS Volume 개념" }
  ],

  "sections": [
    {
      "id":          "1",
      "level":       1,
      "title":       "개요",
      "blocks":      [],
      "figure_refs": [],
      "table_refs":  [],
      "children": [
        {
          "id":    "1.1",
          "level": 2,
          "title": "IGA란 무엇인가",
          "blocks": [
            { "type": "paragraph", "text": "IGA(Isogeometric Analysis, 등기하해석)는 CAD와 CAE를 통합하는 수치해석 방법론이다." },
            { "type": "table_ref", "id": "DOC-HE-CAE-2026-000001-T001" }
          ],
          "figure_refs": [],
          "table_refs":  ["DOC-HE-CAE-2026-000001-T001"],
          "children":    []
        },
        {
          "id":    "1.2",
          "level": 2,
          "title": "LS-DYNA에서의 IGA",
          "blocks": [
            { "type": "paragraph", "text": "LS-DYNA는 R12 버전부터 IGA solid 해석을 지원한다." },
            { "type": "figure_ref", "id": "DOC-HE-CAE-2026-000001-A001" }
          ],
          "figure_refs": ["DOC-HE-CAE-2026-000001-A001"],
          "table_refs":  [],
          "children":    []
        }
      ]
    }
  ],

  "tables": [
    {
      "id":          "DOC-HE-CAE-2026-000001-T001",
      "number":      1,
      "caption":     "Table 1: FEM(HEX8/TET4)과 IGA(NURBS)의 형상 함수·연속성 비교",
      "section_ref": "1.1",
      "headers":     ["항목", "FEM (HEX8/TET4)", "IGA (NURBS)"],
      "rows": [
        ["형상 함수", "Lagrange 다항식",       "NURBS 기저함수"],
        ["연속성",    "C0 (요소 경계)",        "Cp-1 (p차 기준)"]
      ]
    }
  ],

  "attachments": [
    {
      "id":          "DOC-HE-CAE-2026-000001-A001",
      "number":      1,
      "kind":        "image",
      "caption":     "Figure 1: LS-DYNA R12의 IGA Trimmed NURBS Volume 구조 — NURBS 직육면체 박스가 FE solid mesh를 완전히 감싸며, FE mesh 외면이 trim 경계로 사용됨",
      "file_name":   "A001.png",
      "file_path":   "DOC-HE-CAE-2026-000001/A001.png",
      "mime_type":   "image/png",
      "size_bytes":  482917,
      "hash_sha256": "9f3b2e8a47c1d9e0b5a6f8d2c4e1b3a7d9f0e2c5b8a4d6f1e3c7b9a2d5f8e0c4",
      "section_ref": "1.2",
      "source_ref":  "DOC-HE-CAE-2026-000001-S001",
      "extra":       { "width": 1920, "height": 1080, "alt": "NURBS volume" }
    }
  ],

  "sources": [
    {
      "id":          "DOC-HE-CAE-2026-000001-S001",
      "type":        "SIM",
      "format":      "k",
      "file_name":   "block_2x2x1.k",
      "file_path":   "//file-server/PLM/HE/CAE/2026/iga_examples/block_2x2x1.k",
      "modified":    "2026-04-15T14:32:00",
      "size_bytes":  4521,
      "hash_sha256": "9f3b2e8a47c1d9e0b5a6f8d2c4e1b3a7d9f0e2c5b8a4d6f1e3c7b9a2d5f8e0c4",
      "description": "IGA 변환 예제용 베이스 FE 모델 (HEX8 4요소, 2층)"
    }
  ],

  "figures": []
}
```

---

*본 규칙서는 v1.0 기준이며, 스키마 변경 시 함께 업데이트된다. 변환기와 normalizer는 본 문서를 단일 진실 공급원(single source of truth)으로 삼는다.*

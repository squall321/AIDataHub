 범용 문서 JSON 스키마 규칙서
## AI 친화적 문서 데이터 표준 v1.0

> 작성일: 2026-05-07  
> 적용 대상: Word·Excel·PPT·PDF에서 변환된 모든 사업부 문서 JSON

---

## 목차

1. [스키마 전체 구조](#1-스키마-전체-구조)
2. [schema_version](#2-schema_version)
3. [meta — 문서 신원 정보](#3-meta)
4. [toc — 목차](#4-toc)
5. [sections — 본문 계층 트리](#5-sections)
6. [figures — 그림 정보](#6-figures)
7. [tables — 표 데이터](#7-tables)
8. [sources — CAD 소스 파일](#8-sources)
9. [ID 명명 규칙](#9-id-명명-규칙)
10. [필드 필수/선택 요약표](#10-필드-필수선택-요약표)
11. [금지 사항](#11-금지-사항)
12. [완전한 예시](#12-완전한-예시)
13. [검증 체크리스트](#13-검증-체크리스트)

---

## 1. 스키마 전체 구조

모든 변환 JSON은 다음 7개 최상위 키를 가진다.

```json
{
  "schema_version": "1.0",
  "meta":     { ... },
  "toc":      [ ... ],
  "sections": [ ... ],
  "figures":  [ ... ],
  "tables":   [ ... ],
  "sources":  [ ... ]
}
```

**규칙:**

- 키 순서는 위 순서를 따른다 (가독성 및 일관성).
- 문서에 그림이 없으면 `"figures": []` — 키 자체를 생략하지 않는다.
- 문서에 표가 없으면 `"tables": []` — 키 자체를 생략하지 않는다.
- 문서에 참조한 CAD/외부 소스가 없으면 `"sources": []`.
- 최상위에 위 7개 이외의 키를 임의로 추가하지 않는다.

---

## 2. schema_version

```json
"schema_version": "1.0"
```

| 항목 | 내용 |
|------|------|
| 타입 | string |
| 필수 여부 | **필수** |
| 형식 | `"주버전.부버전"` (예: `"1.0"`, `"1.1"`, `"2.0"`) |

**규칙:**
- 현재 버전은 `"1.0"`.
- 스키마 구조가 변경될 경우에만 버전을 올린다.
- 하위 호환을 깨는 변경 (키 삭제, 타입 변경)은 주버전(첫 번째 숫자)을 올린다.
- 필드 추가 등 하위 호환 변경은 부버전(두 번째 숫자)을 올린다.

---

## 3. meta

문서의 신원증명서. AI가 문서를 식별·분류·인용하는 데 사용한다.

```json
"meta": {
  "doc_id":      "HE-CAE-2026-000001",
  "title":       "IGA (Isogeometric Analysis) 가이드",
  "source_format": "docx",
  "source_file": "iga_guide.docx",
  "doc_type":    "manual",
  "created":     "2026-05-07",
  "modified":    "2026-05-07",
  "author":      "홍길동",
  "department":  "CAE팀",
  "project":     "KooRemapper",
  "version":     "1.0",
  "tags":        ["IGA", "LS-DYNA", "NURBS", "FEM"],
  "summary":     "KooRemapper v1.3.0의 IGA 기능 사용법을 설명하는 기술 매뉴얼...",
  "agent_scope": ["iga-analyst", "code-assistant"]
}
```

### 3.1 각 필드 상세 규칙

#### doc_id

| 항목 | 내용 |
|------|------|
| 타입 | string |
| 필수 여부 | **필수** |
| 형식 | `{사업부}-{팀}-{연도}-{순번}` |
| 예시 | `"HE-CAE-2026-000001"`, `"HE-MFG-2026-000034"` |

**형식 상세:**

```text
HE  -  CAE  -  2026  -  000001
 ↑      ↑       ↑         ↑
사업부  팀코드  연도    6자리 순번
```

- **사업부 코드**: 대문자 영문 약어 (HE, DA, MX, VD 등 사업부 단위)
- **팀 코드**: 대문자 영문 약어 (CAE, MFG, QA, DEV, PLM 등 팀 단위)
- **연도**: 문서 최초 등록 연도 4자리
- **순번**: 6자리 제로패딩 (000001 ~ 999999). 팀+연도 조합 내에서 고유

**규칙:**

- 전사 고유값. 동일한 doc_id가 두 개 존재해서는 안 된다.
- 시작값: 각 팀·연도 조합의 첫 문서는 `000001`부터 시작.
- 문서를 수정해도 doc_id는 바꾸지 않는다. 버전 정보는 `version` 필드로 관리.
- 팀이 통합·분리되어도 기존 doc_id는 변경하지 않는다.

#### title

| 타입 | string | 필수 | **필수** |
|------|--------|------|----------|

- 원본 문서의 제목을 그대로 기재.
- 파일명이 아닌 문서 내 제목을 사용.
- 버전 정보를 제목에 포함시키지 않는다 (`version` 필드로 분리).

#### source_format

| 타입 | string (enum) | 필수 | **필수** |
|------|---------------|------|----------|

허용값:

| 값 | 원본 포맷 |
|----|-----------|
| `"docx"` | Microsoft Word |
| `"xlsx"` | Microsoft Excel |
| `"pptx"` | Microsoft PowerPoint |
| `"pdf"` | PDF (텍스트 또는 스캔) |
| `"manual"` | 수동 작성 (원본 파일 없음) |

#### source_file

| 타입 | string | 필수 | 권장 |
|------|--------|------|------|

- 원본 파일명만 기재 (경로 없이). 예: `"iga_guide.docx"`
- 파일이 없는 경우 `null`.

#### doc_type

| 타입 | string (enum) | 필수 | **필수** |
|------|---------------|------|----------|

허용값:

| 값 | 설명 | 주로 담는 내용 |
|----|------|--------------|
| `"report"` | 분석·검토·결과 보고서 | 목적, 방법, 결과, 결론 |
| `"manual"` | 사용법·절차·가이드 | 단계별 지침, 파라미터 설명 |
| `"spec"` | 규격·사양·요구사항 | 수치, 허용 범위, 조건 |
| `"slide"` | 발표·교육·회의 자료 | PPT에서 변환된 내용 |
| `"data"` | 측정값·파라미터 데이터 표 | 주로 Excel에서 변환 |
| `"form"` | 양식·체크리스트 | 체크 항목, 기입 양식 |

#### created / modified

| 타입 | string (ISO 8601) | 필수 | **필수** |
|------|-------------------|------|----------|

- 형식: `"YYYY-MM-DD"` (예: `"2026-05-07"`)
- `created`: 원본 문서 최초 작성일.
- `modified`: JSON 변환 시점의 최종 수정일.

#### author / department

| 타입 | string | 필수 | 권장 |
|------|--------|------|------|

- `author`: 문서 작성자 이름. 복수 저자는 `"홍길동, 김철수"`.
- `department`: 소속 팀 또는 부서명.

#### project

| 타입 | string | 필수 | 선택 |
|------|--------|------|------|

- 해당 문서가 속한 프로젝트·과제명. 없으면 `null`.

#### version

| 타입 | string | 필수 | 권장 |
|------|--------|------|------|

- 문서 자체의 버전. 형식: `"1.0"`, `"2.1"`, `"r3"` 등 원본 문서 표기 방식 그대로.

#### tags

| 타입 | string[] (배열) | 필수 | **필수** |
|------|-----------------|------|----------|

**규칙:**
- 최소 2개, 최대 20개.
- 각 태그는 짧은 키워드 (1~4단어). 문장 금지.
- 대소문자는 영어 고유명사는 원래 표기 유지 (예: `"IGA"`, `"NURBS"`, `"LS-DYNA"`).
- 한국어 태그와 영어 태그를 혼용 가능.
- 부서명·작성자명·날짜는 태그에 넣지 않는다 (별도 필드가 있음).

```json
"tags": ["IGA", "NURBS", "LS-DYNA", "KooRemapper", "FEM", "해석", "솔리드"]
```

#### summary

| 타입 | string | 필수 | **필수** |
|------|--------|------|----------|

**규칙:**
- 문서 전체 내용을 1~3문장으로 요약. 최대 500자.
- RAG 시스템이 관련 문서를 선별할 때 summary를 먼저 읽는다. 이것이 없으면 RAG가 제대로 작동하지 않는다.
- 작성자가 직접 쓰거나, 변환 후 AI가 자동 생성해도 된다.
- 단순히 제목을 반복하지 않는다. 핵심 내용·용도·대상 독자를 담는다.

```json
"summary": "KooRemapper v1.3.0의 IGA(등기하해석) 기능 사용 가이드. NURBS 기반 Trimmed Volume 방식으로 기존 FE solid mesh를 IGA로 자동 변환하는 절차, YAML 설정 문법, 생성 파일 구조를 설명한다. LS-DYNA R12 이상 환경에서 단독 및 assemble 모드로 활용 가능."
```

#### agent_scope

| 타입 | string[] (배열) | 필수 | 권장 |
|------|-----------------|------|------|

**규칙:**
- 이 문서를 참조해야 하는 Cline SR 에이전트 유형 목록.
- 에이전트 유형 식별자는 `agent_scope` 테이블에 등록된 값과 일치해야 한다.
- 복수의 에이전트 유형에 걸치는 문서일 경우 모두 나열.

```json
"agent_scope": ["iga-analyst", "code-assistant"]
```

표준 에이전트 유형:

| 식별자 | 역할 |
|--------|------|
| `iga-analyst` | IGA 해석 설정·검토 |
| `cae-reporter` | 해석 결과 보고서 작성 |
| `material-reviewer` | 재료 물성 검토 |
| `process-checker` | 공정 절차 검증 |
| `code-assistant` | KooRemapper 코드 작업 |

---

## 4. toc

목차. AI가 문서 전체를 읽지 않고 구조를 파악할 때 사용한다.

```json
"toc": [
  { "id": "1",     "level": 1, "title": "개요" },
  { "id": "1.1",   "level": 2, "title": "IGA란 무엇인가" },
  { "id": "1.2",   "level": 2, "title": "LS-DYNA에서의 IGA" },
  { "id": "2",     "level": 1, "title": "작동 원리" },
  { "id": "2.1",   "level": 2, "title": "Trimmed NURBS Volume 개념" }
]
```

### 4.1 toc 항목 필드

| 필드 | 타입 | 필수 | 규칙 |
|------|------|------|------|
| `id` | string | **필수** | sections의 id와 완전히 일치해야 함 |
| `level` | integer | **필수** | 1, 2, 3 중 하나 |
| `title` | string | **필수** | 해당 섹션의 제목 텍스트 |

**규칙:**
- toc의 `id`와 `title`은 sections의 해당 노드와 **반드시 일치**해야 한다.
- toc에는 level 1~3 섹션만 포함. 그 이상 깊이는 생략.
- toc는 문서 순서대로 나열 (깊이 우선 탐색 순서가 아닌 출현 순서).

---

## 5. sections

문서 본문을 계층 트리로 표현한다.

### 5.1 구조

```json
"sections": [
  {
    "id":          "1",
    "level":       1,
    "title":       "개요",
    "content":     "",
    "figure_refs": [],
    "table_refs":  [],
    "children": [
      {
        "id":          "1.1",
        "level":       2,
        "title":       "IGA란 무엇인가",
        "content":     "IGA(Isogeometric Analysis, 등기하해석)는 CAD와 CAE를 통합하는 수치해석 방법론이다...",
        "figure_refs": ["HE-CAE-2026-000001-F001"],
        "table_refs":  ["HE-CAE-2026-000001-T001"],
        "children":    []
      }
    ]
  }
]
```

### 5.2 섹션 노드 필드

| 필드 | 타입 | 필수 | 규칙 |
|------|------|------|------|
| `id` | string | **필수** | 섹션 번호 문자열. 규칙은 9장 참조 |
| `level` | integer | **필수** | 1, 2, 3 중 하나 |
| `title` | string | **필수** | 섹션 제목 텍스트 (번호 제외) |
| `content` | string | **필수** | 해당 섹션의 단락 텍스트. 없으면 `""` |
| `figure_refs` | string[] | **필수** | 이 섹션에 등장하는 그림 id 목록. 없으면 `[]` |
| `table_refs` | string[] | **필수** | 이 섹션에 등장하는 표 id 목록. 없으면 `[]` |
| `children` | object[] | **필수** | 하위 섹션 배열. 없으면 `[]` |

### 5.3 content 작성 규칙

- `content`는 해당 섹션 제목 직후부터 다음 같은 레벨 제목이 나오기 전까지의 단락 텍스트를 이어 붙인다.
- 단락 구분은 줄바꿈 두 개(`\n\n`)로 표현한다.
- 표·그림의 텍스트 내용은 `content`에 넣지 않는다. 각각 `tables`, `figures` 배열로 분리한다.
- 단, 표나 그림을 설명하는 **주변 설명 단락**은 `content`에 포함한다.
- 코드 블록이나 수식은 그대로 텍스트로 포함. 포맷팅 마크업 없이 순수 텍스트.

### 5.4 트리 깊이 규칙

- 최대 깊이 3 (level 1 → 2 → 3).
- level 4 이상은 해당 내용을 level 3의 `content`에 단락으로 포함.
- Word Heading 4 이하는 별도 섹션으로 분리하지 않는다.

### 5.5 children이 없는 섹션

```json
{
  "id": "1.2",
  "level": 2,
  "title": "LS-DYNA에서의 IGA",
  "content": "LS-DYNA는 R12 버전부터 IGA solid 해석을 지원한다...",
  "figure_refs": [],
  "table_refs": [],
  "children": []
}
```

`children`이 없더라도 키를 생략하지 않고 빈 배열 `[]`을 명시한다.

---

## 6. figures

그림 정보. AI는 이미지를 직접 볼 수 없으므로 캡션이 유일한 정보 전달 수단이다.

### 6.1 구조

```json
"figures": [
  {
    "id":          "HE-CAE-2026-000001-F001",
    "number":      1,
    "caption":     "Figure 1: FEM 메시(TET4)와 IGA NURBS 박스의 관계 — NURBS 직육면체가 FE mesh를 완전히 감싸고 있으며, FE mesh는 trim 경계로 사용됨",
    "section_ref": "1.1"
  }
]
```

### 6.2 필드 규칙

| 필드 | 타입 | 필수 | 규칙 |
|------|------|------|------|
| `id` | string | **필수** | `"{doc_id}-F{3자리}"` 형식. 전역 고유. (예: `HE-CAE-2026-000001-F001`) |
| `number` | integer | **필수** | 그림 번호 (정수). 1부터 시작, 문서 전체에서 연속 번호. |
| `caption` | string | **필수** | 전체 캡션 텍스트. 번호 포함. 규칙은 아래 참조. |
| `section_ref` | string | **필수** | 이 그림이 등장하는 섹션의 id. |
| `image_path` | string | 권장 | 그림 바이너리 상대 경로. `"{doc_id}/F{3자리}.{ext}"` 형식. 예: `"HE-CAE-2026-000001/F001.png"`. 정적 마운트 `/figures` 의 직하 경로와 동일. 그림이 텍스트 전용이거나 추출 실패 시 생략 또는 `null`. |
| `source_ref` | string | 선택 | 그림이 외부 CAD 파일에서 유래된 경우 sources의 id. 없으면 생략 또는 `null`. |

### 6.3 caption 작성 규칙

캡션은 AI가 이미지 없이 그림 내용을 이해할 수 있는 수준이어야 한다.

**필수 포함 요소:**
1. 번호: `"Figure N: "` 또는 `"그림 N: "` 형식으로 시작
2. **무엇**: 대상 부품·공정·데이터의 이름
3. **조건**: 해석 조건, 측정 조건, 버전 등

**권장 포함 요소:**
4. **핵심 수치**: 최대값, 범위, 특이점
5. **색상·마킹 의미**: "빨간색 = 응력 집중부" 같은 시각 코드 해석

**나쁜 예:**
```
"caption": "그림 1"
"caption": "Figure 2: 해석 결과"
```

**좋은 예:**
```
"caption": "Figure 1: 브라켓 부품의 von Mises 응력 분포 해석 결과 (하중 조건 1,000N, 최대 응력 250MPa, 빨간색 영역이 응력 집중부)"
"caption": "Figure 3: KooRemapper IGA 변환 후 생성된 NURBS 박스와 FE mesh 관계도 (bbox_scale=1.5 적용, 파란색 = NURBS 박스, 회색 = FE mesh)"
```

### 6.4 section_ref 규칙

- 그림 캡션이 위치하는 섹션의 `id`를 기재.
- 섹션 경계에 걸쳐 있는 경우 캡션이 속한 섹션 기준.

---

## 7. tables

표 데이터. 평문이 아닌 `headers + rows` 구조로 저장해야 AI가 열을 구분하고 값을 검색할 수 있다.

### 7.1 구조

```json
"tables": [
  {
    "id":          "HE-CAE-2026-000001-T001",
    "number":      1,
    "caption":     "Table 1: FEM(HEX8/TET4)과 IGA(NURBS)의 주요 특성 비교",
    "section_ref": "1.1",
    "headers":     ["항목", "FEM (HEX8/TET4)", "IGA (NURBS)"],
    "rows": [
      ["형상 함수",   "Lagrange 다항식",     "NURBS 기저함수"],
      ["형상 표현",   "근사 (절점 보간)",     "정확 (CAD와 동일)"],
      ["연속성",      "C0 (요소 경계)",       "Cp-1 (p차 기준)"],
      ["메시 세분화", "h-refinement (분할)", "k-refinement (차수+세분화 동시)"]
    ]
  }
]
```

### 7.2 필드 규칙

| 필드 | 타입 | 필수 | 규칙 |
|------|------|------|------|
| `id` | string | **필수** | `"{doc_id}-T{3자리}"` 형식. 전역 고유. (예: `HE-CAE-2026-000001-T001`) |
| `number` | integer | **필수** | 표 번호 (정수). 1부터 시작, 문서 전체에서 연속 번호. |
| `caption` | string | **필수** | 표 제목 텍스트. 번호 포함. |
| `section_ref` | string | **필수** | 이 표가 등장하는 섹션의 id. |
| `source_ref` | string | 선택 | 표가 외부 파일(BOM, ECAD netlist 등)에서 유래된 경우 sources의 id. |
| `headers` | string[] | **필수** | 헤더 행. 각 열 이름. |
| `rows` | string[][] | **필수** | 데이터 행 배열. 각 행은 셀 값의 배열. |

### 7.3 headers 규칙

- 표의 첫 번째 행 (헤더 행)이어야 한다.
- 각 헤더는 열의 의미를 명확히 나타내는 문자열.
- 단위가 있는 경우 헤더에 포함: `"무게(kg)"`, `"응력(MPa)"`.
- 헤더를 생략하거나 빈 문자열로 두지 않는다.

### 7.4 rows 규칙

- 각 행은 `headers`와 같은 길이의 배열이어야 한다.
- 셀 값의 타입:

| 상황 | 타입 | 예시 |
|------|------|------|
| 일반 텍스트 | string | `"Lagrange 다항식"` |
| 숫자 | number | `1000`, `3.14`, `-1.0` |
| 빈 셀 | `null` | `null` |
| 불리언 의미 | string | `"Y"` / `"N"` (true/false 대신 문자열 권장) |

- 병합된 셀(merged cell)이 있었던 경우, 병합된 값을 각 셀에 개별적으로 반복 기재한다.
  ```json
  ["구분A", "값1", "설명"],
  ["구분A", "값2", "설명"]   ← "구분A"를 반복 기재
  ```

### 7.5 caption 작성 규칙

- `"Table N: [표가 담고 있는 내용 설명]"` 형식.
- 무엇을 비교하거나 나열하는지 명확히 기재.

**나쁜 예:**
```
"caption": "표 1"
"caption": "Table 2: 파라미터"
```

**좋은 예:**
```
"caption": "Table 1: FEM과 IGA의 형상 함수·연속성·메시 세분화 특성 비교"
"caption": "Table 3: IGA target YAML 설정 파라미터 전체 목록 (KooRemapper v1.3.0)"
```

---

## 8. sources

CAD 등 **외부 원본 파일**을 가리키는 참조 정보. 같은 CAD 파일이 여러 문서에서 참조되는 경우, 동일성을 판정하기 위해 파일 메타데이터(이름·수정일·용량·해시)를 함께 저장한다.

### 8.1 사용 목적

- 보고서·매뉴얼이 특정 CAD 모델·도면·기구를 인용할 때, **단순히 파일명만 적으면** 시간이 흐른 후 어떤 파일인지 추적하기 어렵다.
- `sources`에 ID·경로·메타데이터를 함께 기록하면, 나중에 다른 문서가 같은 파일을 가리키는지 **메타데이터 비교로 정확히 판정**할 수 있다.
- AI가 "이 보고서가 참조한 ECAD 파일이 무엇인가?"라는 질문에 답할 때 `sources` 배열을 직접 활용한다.

### 8.2 구조

```json
"sources": [
  {
    "id":            "HE-CAE-2026-000001-S001",
    "type":          "MCAD",
    "format":        "CATPart",
    "file_name":     "bracket_v3.CATPart",
    "file_path":     "//file-server/PLM/HE/CAE/2026/bracket/bracket_v3.CATPart",
    "modified":      "2026-04-15T14:32:00",
    "size_bytes":    8429472,
    "hash_sha256":   "9f3b2e8a...c4d12",
    "description":   "브라켓 v3 형상 모델 (재료 변경 후 두께 조정 반영)"
  },
  {
    "id":            "HE-CAE-2026-000001-S002",
    "type":          "ECAD",
    "format":        "ODB++",
    "file_name":     "mainboard_rev2.tgz",
    "file_path":     "//file-server/PLM/HE/EDA/2026/mainboard/mainboard_rev2.tgz",
    "modified":      "2026-03-22T09:15:00",
    "size_bytes":    142387200,
    "hash_sha256":   "a7e1f3...b8902",
    "description":   "메인 PCB rev2 (8층, 임피던스 매칭 반영)"
  }
]
```

### 8.3 필드 규칙

| 필드 | 타입 | 필수 | 규칙 |
| ---- | ---- | ---- | ---- |
| `id` | string | **필수** | `"{doc_id}-S{3자리}"` 형식. 전역 고유. |
| `type` | string (enum) | **필수** | `"MCAD"`, `"ECAD"`, `"DRAWING"`, `"SIM"`, `"DOC"`, `"OTHER"` |
| `format` | string | **필수** | 파일 포맷명 (예: `CATPart`, `STEP`, `ODB++`, `dxf`, `k`, `pdf`) |
| `file_name` | string | **필수** | 파일명만 (경로 없이) |
| `file_path` | string | **필수** | 전체 경로 (UNC 또는 PLM 경로) |
| `modified` | string (ISO 8601) | **필수** | 파일의 최종 수정 시각. `"YYYY-MM-DDTHH:MM:SS"` |
| `size_bytes` | integer | **필수** | 파일 용량 (바이트 단위) |
| `hash_sha256` | string | 권장 | SHA-256 해시 (동일성 정밀 판정용) |
| `description` | string | 권장 | 이 파일이 무엇인지 한 줄 설명 |

### 8.4 type 값 정의

| 값 | 의미 | 대표 포맷 |
| -- | ---- | --------- |
| `MCAD` | 기구 CAD (3D 모델) | CATPart, CATProduct, STEP, IGES, sldprt, prt |
| `ECAD` | 전자 CAD (PCB) | ODB++, IPC-2581, brd, sch, gerber |
| `DRAWING` | 2D 도면 | dxf, dwg, pdf 도면 |
| `SIM` | 시뮬레이션 입력/결과 | k (LS-DYNA), inp (Abaqus), cdb (Ansys) |
| `DOC` | 외부 참조 문서 | pdf, docx, xlsx (다른 부서 자료) |
| `OTHER` | 그 외 | 기타 모든 포맷 |

### 8.5 동일성 판정 규칙

같은 파일을 가리키는지 판정할 때 다음 우선순위를 따른다.

1. **`hash_sha256`이 같으면 동일 파일** (가장 정확).
2. 해시가 없으면 `file_name + size_bytes + modified` 조합으로 판정.
3. 단순 `file_path` 일치만으로는 충분하지 않다 (파일이 이동·복사될 수 있음).

### 8.6 figures/tables와의 연결

그림이나 표가 특정 CAD 소스에서 추출된 것이라면, 해당 객체에 `source_ref` 필드로 source의 id를 명시한다.

```json
{
  "id":          "HE-CAE-2026-000001-F001",
  "number":      1,
  "caption":     "Figure 1: 브라켓 v3 형상 (CATPart 뷰)",
  "section_ref": "1.2",
  "source_ref":  "HE-CAE-2026-000001-S001"
}
```

`source_ref`는 선택 필드. 해당 객체가 외부 CAD 파일에서 유래된 경우에만 기재한다.

---

## 9. ID 명명 규칙

### 9.1 doc_id

```text
{사업부}-{팀}-{연도}-{순번}

형식: HE-CAE-2026-000001
      ↑   ↑    ↑      ↑
   사업부 팀  연도  6자리 순번

예:   HE-CAE-2026-000001   (HE사업부 CAE팀, 2026년 1번째 문서)
      HE-MFG-2026-000034   (HE사업부 MFG팀, 2026년 34번째 문서)
      HE-QA-2025-000007    (HE사업부 QA팀,  2025년 7번째 문서)
```

- **사업부**: 대문자 영문 약어 (HE, DA, MX, VD 등)
- **팀**: 대문자 영문 약어 (CAE, MFG, QA, DEV, PLM 등)
- **연도**: 4자리 숫자
- **순번**: 6자리 제로패딩 (000001 ~ 999999). 팀+연도 조합 내에서 고유하면 됨.
- 첫 문서는 `000001`부터 시작.

### 9.2 section id

```text
{n}           1레벨 섹션  → "1", "2", "3"
{n}.{m}       2레벨 섹션  → "1.1", "1.2", "2.1"
{n}.{m}.{k}   3레벨 섹션  → "1.1.1", "2.3.2"
```

- 숫자만 사용. 문자·기호 금지.
- 섹션 번호는 Word 문서의 Heading 번호와 일치해야 한다.
- doc_id를 포함하지 않는다. 섹션 id는 문서 내부 범위에서만 사용.

### 9.3 figure id

문서 전체에서 전역 고유값이 되도록 **doc_id를 접두어**로 사용한다.

```text
{doc_id}-F{순번}

형식: HE-CAE-2026-000001-F001
      ↑ doc_id 전체 ↑    ↑ ↑
                         F  3자리 순번

예:   HE-CAE-2026-000001-F001   (1번 그림)
      HE-CAE-2026-000001-F002   (2번 그림)
      HE-CAE-2026-000001-F015   (15번 그림)
```

- `F` 는 Figure 구분자. 반드시 대문자.
- 순번은 3자리 제로패딩 (001 ~ 999).
- 문서 전체에서 연속 번호. 섹션별로 초기화하지 않는다.

### 9.4 table id

figure id와 동일한 규칙. 구분자만 `T`로 다르다.

```text
{doc_id}-T{순번}

형식: HE-CAE-2026-000001-T001
      ↑ doc_id 전체 ↑    ↑ ↑
                         T  3자리 순번

예:   HE-CAE-2026-000001-T001   (1번 표)
      HE-CAE-2026-000001-T002   (2번 표)
      HE-CAE-2026-000001-T008   (8번 표)
```

- `T` 는 Table 구분자. 반드시 대문자.
- 순번은 3자리 제로패딩 (001 ~ 999).
- 문서 전체에서 연속 번호. 섹션별로 초기화하지 않는다.

### 9.5 source id

CAD 등 외부 소스 파일을 가리키는 ID. 구분자는 `S`.

```text
{doc_id}-S{순번}

형식: HE-CAE-2026-000001-S001
      ↑ doc_id 전체 ↑    ↑ ↑
                         S  3자리 순번

예:   HE-CAE-2026-000001-S001   (1번 외부 소스, 예: 브라켓 CATPart)
      HE-CAE-2026-000001-S002   (2번 외부 소스, 예: 메인보드 ODB++)
```

- `S` 는 Source 구분자. 반드시 대문자.
- 순번은 3자리 제로패딩 (001 ~ 999).
- 같은 외부 파일이 여러 문서에서 참조되어도, 각 문서에서 별도의 source id를 갖는다 (문서 단위로 ID 발급).
- 동일 파일 여부는 `hash_sha256` 등 메타데이터로 판정한다 (8.5절 참조).

### 9.6 ID 구조 요약

| ID 종류 | 형식 | 예시 |
| ------- | ---- | ---- |
| 문서 | `{사업부}-{팀}-{연도}-{6자리}` | `HE-CAE-2026-000001` |
| 섹션 | `{숫자}.{숫자}` | `1.2`, `2.3.1` |
| 그림 | `{doc_id}-F{3자리}` | `HE-CAE-2026-000001-F001` |
| 표 | `{doc_id}-T{3자리}` | `HE-CAE-2026-000001-T001` |
| 외부 소스 | `{doc_id}-S{3자리}` | `HE-CAE-2026-000001-S001` |

### 9.7 일관성 규칙

- `figures[i].id` 값과 `sections`의 `figure_refs` 배열 내 값이 **정확히 일치**해야 한다.
- `tables[i].id` 값과 `sections`의 `table_refs` 배열 내 값이 **정확히 일치**해야 한다.
- `figures[i].source_ref` 또는 `tables[i].source_ref`가 명시된 경우, 해당 값이 `sources` 배열에 실제 존재해야 한다.
- 참조는 있는데 실제 객체가 없거나, 실제 객체는 있는데 어떤 섹션에서도 참조하지 않는 경우는 오류다.

---

## 10. 필드 필수/선택 요약표

| 경로 | 필드 | 타입 | 필수 |
|------|------|------|------|
| 최상위 | `schema_version` | string | **필수** |
| 최상위 | `meta` | object | **필수** |
| 최상위 | `toc` | array | **필수** |
| 최상위 | `sections` | array | **필수** |
| 최상위 | `figures` | array | **필수** (빈 배열 가능) |
| 최상위 | `tables` | array | **필수** (빈 배열 가능) |
| 최상위 | `sources` | array | **필수** (빈 배열 가능) |
| meta | `doc_id` | string | **필수** |
| meta | `title` | string | **필수** |
| meta | `source_format` | string (enum) | **필수** |
| meta | `source_file` | string | 권장 |
| meta | `doc_type` | string (enum) | **필수** |
| meta | `created` | string (날짜) | **필수** |
| meta | `modified` | string (날짜) | **필수** |
| meta | `author` | string | 권장 |
| meta | `department` | string | 권장 |
| meta | `project` | string | 선택 |
| meta | `version` | string | 권장 |
| meta | `tags` | string[] | **필수** |
| meta | `summary` | string | **필수** |
| meta | `agent_scope` | string[] | 권장 |
| toc[] | `id` | string | **필수** |
| toc[] | `level` | integer | **필수** |
| toc[] | `title` | string | **필수** |
| sections[] | `id` | string | **필수** |
| sections[] | `level` | integer | **필수** |
| sections[] | `title` | string | **필수** |
| sections[] | `content` | string | **필수** (빈 문자열 가능) |
| sections[] | `figure_refs` | string[] | **필수** (빈 배열 가능) |
| sections[] | `table_refs` | string[] | **필수** (빈 배열 가능) |
| sections[] | `children` | array | **필수** (빈 배열 가능) |
| figures[] | `id` | string | **필수** |
| figures[] | `number` | integer | **필수** |
| figures[] | `caption` | string | **필수** |
| figures[] | `section_ref` | string | **필수** |
| figures[] | `image_path` | string | 권장 |
| figures[] | `source_ref` | string | 선택 |
| tables[] | `id` | string | **필수** |
| tables[] | `number` | integer | **필수** |
| tables[] | `caption` | string | **필수** |
| tables[] | `section_ref` | string | **필수** |
| tables[] | `source_ref` | string | 선택 |
| tables[] | `headers` | string[] | **필수** |
| tables[] | `rows` | array[][] | **필수** |
| sources[] | `id` | string | **필수** |
| sources[] | `type` | string (enum) | **필수** |
| sources[] | `format` | string | **필수** |
| sources[] | `file_name` | string | **필수** |
| sources[] | `file_path` | string | **필수** |
| sources[] | `modified` | string (ISO 8601) | **필수** |
| sources[] | `size_bytes` | integer | **필수** |
| sources[] | `hash_sha256` | string | 권장 |
| sources[] | `description` | string | 권장 |

---

## 11. 금지 사항

변환 품질을 보장하기 위해 다음을 금지한다.

### 금지 1: 키 생략

```json
// 나쁜 예 — figures 키 자체를 빠뜨림
{
  "schema_version": "1.0",
  "meta": { ... },
  "toc": [ ... ],
  "sections": [ ... ]
}

// 좋은 예 — 없어도 빈 배열로 명시
{
  ...
  "figures": [],
  "tables": []
}
```

### 금지 2: 표를 평문 문자열로 넣기

```json
// 나쁜 예
"content": "항목 FEM IGA 형상함수 Lagrange NURBS 연속성 C0 Cp-1"

// 좋은 예 — tables 배열로 분리
"tables": [{ "headers": ["항목","FEM","IGA"], "rows": [...] }]
```

### 금지 3: 번호 없는 caption

```json
// 나쁜 예
"caption": "해석 결과 이미지"

// 좋은 예
"caption": "Figure 3: 해석 결과 이미지 (조건: ...)"
```

### 금지 4: 참조 불일치

```json
// 나쁜 예 — section이 fig-99를 참조하지만 figures에 fig-99가 없음
"figure_refs": ["HE-CAE-2026-000001-F099"]

// 나쁜 예 — figures에 fig-1이 있지만 어느 섹션도 참조하지 않음
"figures": [{ "id": "HE-CAE-2026-000001-F001", ... }]
```

### 금지 5: rows와 headers 길이 불일치

```json
// 나쁜 예 — headers는 3개인데 row는 2개
"headers": ["A", "B", "C"],
"rows": [["a1", "b1"]]

// 좋은 예
"headers": ["A", "B", "C"],
"rows": [["a1", "b1", "c1"]]
```

### 금지 6: summary를 제목 반복으로 작성

```json
// 나쁜 예
"summary": "IGA 가이드입니다."

// 좋은 예
"summary": "KooRemapper v1.3.0에서 NURBS 기반 Trimmed Volume 방식으로 FE solid mesh를 IGA로 자동 변환하는 절차를 설명한다. YAML 설정 문법, bbox 확장 계산 방식, 생성 카드 구조를 포함한다."
```

---

## 12. 완전한 예시

`iga_guide.docx`를 변환한 전체 JSON 예시.

```json
{
  "schema_version": "1.0",

  "meta": {
    "doc_id":        "HE-CAE-2026-000001",
    "title":         "IGA (Isogeometric Analysis) 가이드",
    "source_format": "docx",
    "source_file":   "iga_guide.docx",
    "doc_type":      "manual",
    "created":       "2026-05-07",
    "modified":      "2026-05-07",
    "author":        "홍길동",
    "department":    "CAE팀",
    "project":       "KooRemapper",
    "version":       "1.0",
    "tags":          ["IGA", "LS-DYNA", "NURBS", "KooRemapper", "FEM", "솔리드해석"],
    "summary":       "KooRemapper v1.3.0의 IGA(등기하해석) 기능 사용 가이드. NURBS 기반 Trimmed Volume 방식으로 FE solid mesh를 IGA로 자동 변환하는 절차, YAML 설정 문법, 생성 파일 구조를 설명한다. LS-DYNA R12 이상 환경 전용.",
    "agent_scope":   ["iga-analyst", "code-assistant"]
  },

  "toc": [
    { "id": "1",   "level": 1, "title": "개요" },
    { "id": "1.1", "level": 2, "title": "IGA란 무엇인가" },
    { "id": "1.2", "level": 2, "title": "LS-DYNA에서의 IGA" },
    { "id": "1.3", "level": 2, "title": "KooRemapper가 자동화하는 것" },
    { "id": "2",   "level": 1, "title": "작동 원리" },
    { "id": "2.1", "level": 2, "title": "Trimmed NURBS Volume 개념" },
    { "id": "2.2", "level": 2, "title": "생성 흐름" },
    { "id": "3",   "level": 1, "title": "YAML 문법" }
  ],

  "sections": [
    {
      "id":          "1",
      "level":       1,
      "title":       "개요",
      "content":     "",
      "figure_refs": [],
      "table_refs":  [],
      "children": [
        {
          "id":          "1.1",
          "level":       2,
          "title":       "IGA란 무엇인가",
          "content":     "IGA(Isogeometric Analysis, 등기하해석)는 CAD와 CAE를 통합하는 수치해석 방법론이다. 기존 유한요소법(FEM)이 Lagrange 다항식 기반의 형상 함수를 사용하는 반면, IGA는 NURBS(Non-Uniform Rational B-Spline)를 형상 함수로 직접 사용한다. IGA는 특히 얇은 쉘, 유체-구조 연성(FSI), 접촉 해석에서 FEM 대비 높은 정밀도를 보인다.",
          "figure_refs": [],
          "table_refs":  ["HE-CAE-2026-000001-T001"],
          "children":    []
        },
        {
          "id":          "1.2",
          "level":       2,
          "title":       "LS-DYNA에서의 IGA",
          "content":     "LS-DYNA는 R12 버전부터 IGA solid 해석을 지원한다. KooRemapper가 활용하는 핵심 방식은 Trimmed NURBS Volume으로, *IGA_DEV_VOLUME_XYZ + TETMSH=-1 옵션을 사용한다.",
          "figure_refs": ["HE-CAE-2026-000001-F001"],
          "table_refs":  [],
          "children":    []
        }
      ]
    },
    {
      "id":          "2",
      "level":       1,
      "title":       "작동 원리",
      "content":     "",
      "figure_refs": [],
      "table_refs":  [],
      "children": [
        {
          "id":          "2.1",
          "level":       2,
          "title":       "Trimmed NURBS Volume 개념",
          "content":     "FE mesh를 trim 경계로 사용하는 방식으로, NURBS 박스가 FE mesh를 감싸는 구조이다. FE mesh는 원본 LS-DYNA FE solid 파트로 trim 경계 역할을 하고, NURBS 박스는 FE mesh bbox를 offset만큼 확장한 직육면체다.",
          "figure_refs": ["HE-CAE-2026-000001-F002"],
          "table_refs":  [],
          "children":    []
        }
      ]
    }
  ],

  "figures": [
    {
      "id":          "HE-CAE-2026-000001-F001",
      "number":      1,
      "caption":     "Figure 1: LS-DYNA R12의 IGA Trimmed NURBS Volume 구조 — NURBS 직육면체 박스가 FE solid mesh를 완전히 감싸며, FE mesh 외면이 trim 경계로 사용됨",
      "section_ref": "1.2",
      "image_path":  "HE-CAE-2026-000001/F001.png",
      "source_ref":  "HE-CAE-2026-000001-S001"
    },
    {
      "id":          "HE-CAE-2026-000001-F002",
      "number":      2,
      "caption":     "Figure 2: NURBS 박스(파란 외곽)와 FE mesh(회색 내부)의 공간 관계 — offset 값만큼 FE bbox를 확장한 직육면체가 NURBS 박스가 됨",
      "section_ref": "2.1"
    }
  ],

  "tables": [
    {
      "id":          "HE-CAE-2026-000001-T001",
      "number":      1,
      "caption":     "Table 1: FEM(HEX8/TET4)과 IGA(NURBS)의 형상 함수·연속성·메시 세분화 특성 비교",
      "section_ref": "1.1",
      "headers":     ["항목", "FEM (HEX8/TET4)", "IGA (NURBS)"],
      "rows": [
        ["형상 함수",   "Lagrange 다항식",      "NURBS 기저함수"],
        ["형상 표현",   "근사 (절점 보간)",      "정확 (CAD와 동일)"],
        ["연속성",      "C0 (요소 경계)",        "Cp-1 (p차 기준)"],
        ["메시 세분화", "h-refinement (분할)",  "k-refinement (차수+세분화 동시)"],
        ["곡면 품질",   "메시에 의존",           "NURBS로 항상 보장"]
      ]
    }
  ],

  "sources": [
    {
      "id":          "HE-CAE-2026-000001-S001",
      "type":        "SIM",
      "format":      "k",
      "file_name":   "block_2x2x1.k",
      "file_path":   "//file-server/PLM/HE/CAE/2026/iga_examples/block_2x2x1.k",
      "modified":    "2026-04-15T14:32:00",
      "size_bytes":  4521,
      "hash_sha256": "9f3b2e8a47c1d9e0b5a6f8d2c4e1b3a7d9f0e2c5b8a4d6f1e3c7b9a2d5f8e0c4",
      "description": "IGA 변환 예제용 베이스 FE 모델 (HEX8 4요소, 2층)"
    }
  ]
}
```

---

## 13. 검증 체크리스트

변환 완료 후 아래 항목을 확인한다.

### 구조

- [ ] 최상위 7개 키가 모두 있는가 (`schema_version`, `meta`, `toc`, `sections`, `figures`, `tables`, `sources`)
- [ ] 그림·표·소스가 없는 경우 빈 배열 `[]`로 명시되어 있는가

### meta

- [ ] `doc_id`가 `{사업부}-{팀}-{연도}-{6자리 순번}` 형식인가 (예: `HE-CAE-2026-000001`)
- [ ] `doc_type`이 허용값(report/manual/spec/slide/data/form) 중 하나인가
- [ ] `tags`가 2개 이상 있는가
- [ ] `summary`가 1문장 이상이며 제목 반복이 아닌가
- [ ] `source_format`이 허용값 중 하나인가
- [ ] `created`, `modified`가 `YYYY-MM-DD` 형식인가

### toc

- [ ] toc의 모든 `id`가 sections 트리에 실제로 존재하는가
- [ ] toc의 `title`이 해당 section의 `title`과 일치하는가

### sections

- [ ] 모든 section에 `children` 키가 있는가 (빈 배열 포함)
- [ ] `figure_refs`에 명시된 id가 `figures` 배열에 실제 존재하는가
- [ ] `table_refs`에 명시된 id가 `tables` 배열에 실제 존재하는가

### figures

- [ ] 모든 그림의 `id`가 어느 section의 `figure_refs`에 포함되어 있는가
- [ ] `caption`이 번호("Figure N:")로 시작하는가
- [ ] `caption`이 단순 번호만이 아닌 내용 설명을 포함하는가
- [ ] `section_ref`가 실제 존재하는 section id인가

### tables

- [ ] 모든 표의 `id`가 어느 section의 `table_refs`에 포함되어 있는가
- [ ] `headers` 배열의 길이와 모든 `rows` 행의 길이가 일치하는가
- [ ] 빈 셀은 `null`로 표현되어 있는가 (빈 문자열 `""` 금지)
- [ ] `caption`이 번호("Table N:")로 시작하며 내용 설명을 포함하는가

### sources

- [ ] 모든 source의 `id`가 `{doc_id}-S{3자리}` 형식인가
- [ ] `type`이 허용값(MCAD/ECAD/DRAWING/SIM/DOC/OTHER) 중 하나인가
- [ ] `file_name`, `file_path`, `modified`, `size_bytes`가 모두 명시되어 있는가
- [ ] `modified`가 ISO 8601 형식인가 (`YYYY-MM-DDTHH:MM:SS`)
- [ ] `figures[].source_ref` 또는 `tables[].source_ref`에 명시된 id가 `sources`에 실제 존재하는가

---

*본 규칙서는 v1.0 기준이며, 스키마 변경 시 함께 업데이트됩니다.*

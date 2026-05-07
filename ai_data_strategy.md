# 사업부 문서 AI 데이터화 전략
## 범용 Document → JSON 파이프라인 설계서

> 작성일: 2026-05-07  
> 목적: PPT · Excel · PDF · Word → 표준 Word → 범용 JSON → PostgreSQL 저장 및 AI 연동 체계 수립

---

## 목차

1. [문제 정의: 왜 지금 바꿔야 하는가](#1-문제-정의)
2. [전략 개요: 4단계 파이프라인](#2-전략-개요)
3. [1단계: 소스 포맷 → 표준 Word 변환 지침](#3-1단계-소스-포맷-변환)
4. [2단계: Word 작성 3원칙 (핵심)](#4-2단계-word-작성-3원칙)
5. [3단계: 범용 JSON 스키마 설계](#5-3단계-json-스키마-설계)
6. [스키마 필드별 설계 근거 (핵심)](#6-스키마-설계-근거)
7. [4단계: PostgreSQL 데이터 저장 및 활용](#7-4단계-postgresql-데이터-저장-및-활용)
8. [전략 평가: 강점과 보완점](#8-전략-평가-강점과-보완점)
9. [Excel·PPT 특수 처리 전략](#9-excelppt-특수-처리-전략)
10. [구현 로드맵](#10-구현-로드맵)
11. [Word 작성 체크리스트](#11-word-작성-체크리스트)

---

## 1. 문제 정의

### 1.1 AI가 사업부 데이터를 읽지 못하는 이유

AI(LLM)가 문서를 읽어 답변을 생성하려면 **텍스트로 된 구조화된 정보**가 필요하다.  
현재 사업부에서 사용하는 파일 포맷들은 대부분 이 조건을 충족하지 못한다.

| 포맷 | AI가 읽을 수 있는가 | 주요 문제 |
|------|-------------------|-----------|
| PPT (pptx) | 부분적 | 슬라이드 레이아웃 정보 소실, 그림이 대부분 내용 |
| Excel (xlsx) | 부분적 | 수식·셀 병합·색상 의미 전달 불가 |
| PDF | 어려움 | 스캔 PDF는 이미지로만 존재, 텍스트 PDF도 레이아웃 정보 소실 |
| Word (docx) | 가능하나 품질 편차 큼 | 스타일 미사용 시 계층 구조 소실, 표/그림 레이블 없음 |

### 1.2 구조화되지 않은 문서가 AI에게 미치는 영향

**예시: 구조 없는 Word에서 AI가 보는 것**
```
FEM 과 IGA 의 주요 차이점 : 항목 FEM (HEX8/TET4) IGA (NURBS) 형상 함수 
Lagrange 다항식 NURBS 기저함수 형상 표현 근사 정확 연속성 C0 Cp-1...
```

이것이 표(Table)였다는 사실을 AI는 알 수 없다.  
"FEM과 IGA의 형상 표현 차이는?"이라는 질문에 AI는 잘못 답하거나 답하지 못한다.

**핵심 문제**: 현재 파일들은 **사람이 화면에서 보기 위한 포맷**이지, **기계가 처리하기 위한 포맷**이 아니다.

---

## 2. 전략 개요

### 2.1 4단계 파이프라인

```
[소스 파일들]           [표준화 단계]          [AI 입력 포맷]    [저장 및 활용]
PPT  ─┐
Excel ─┤ → [표준 Word] → [Word 작성 3원칙] → [범용 JSON]  →  [PostgreSQL]
PDF   ─┤    (변환)        (번호/캡션 규칙)    (자동 변환)       (검색·AI 연동)
Word  ─┘
```

### 2.2 왜 Word를 중간 포맷으로 선택했는가

Word를 중간 포맷으로 선택한 이유는 다음과 같다:

1. **모두가 이미 알고 있다**: 새 도구 학습 비용 없음. PPT·Excel 작성자도 Word는 사용 가능.
2. **계층 구조 표현 가능**: Heading 1/2/3 스타일이 JSON의 섹션 트리로 1:1 매핑된다.
3. **캡션 기능 내장**: Word의 "캡션 삽입" 기능이 그림·표 번호를 자동 관리한다.
4. **변환 도구 성숙**: python-docx, Pandoc 등 Word → 다른 포맷 변환 도구가 잘 갖춰져 있다.
5. **원본 포맷 호환**: PPT, PDF, Excel 모두 Word로 열거나 붙여넣기가 가능하다.

### 2.3 JSON이 최종 포맷인 이유

직원들이 JSON을 직접 다루지는 않는다. JSON은 **자동 변환 도구가 생성**하며, 다음 목적으로 사용된다:

- **RAG (Retrieval-Augmented Generation)**: AI가 질문에 답할 때 관련 JSON 문서를 검색해서 참조
- **챗봇/검색 시스템**: 사업부 내부 AI 어시스턴트의 지식 베이스
- **문서 비교·분석**: 버전 간 차이, 파라미터 비교 자동화
- **재활용**: 다른 포맷(HTML 보고서, 발표자료 등)으로 재생성 가능

---

## 3. 1단계: 소스 포맷 변환

### 3.1 PPT → Word 변환

**방법**: PowerPoint에서 "다른 이름으로 저장" → Word 문서 (.docx)  
또는: PPT 내용을 새 Word 문서에 **슬라이드 단위로 구조화하여 붙여넣기**

**변환 규칙**:

| PPT 요소 | Word에서의 처리 |
|----------|----------------|
| 슬라이드 번호 + 제목 | Heading 2 (예: "슬라이드 3: 해석 결과") |
| 본문 텍스트 / 불릿 | 일반 단락 또는 목록 |
| 이미지 / 다이어그램 | Word에 삽입 후 **캡션 필수 작성** |
| 표 | Word 표로 변환 후 **표 캡션 필수** |
| 발표자 노트 | 섹션 말미에 "Note:" 단락으로 추가 |

**주의**: PPT의 시각적 레이아웃(배치, 색상, 애니메이션)은 Word에서 의미가 없다.  
**내용(What)** 을 Word로 옮기는 것이 목적이지, **시각 디자인(How it looks)** 을 옮기는 것이 아니다.

### 3.2 Excel → Word 변환

**중요**: Excel의 성격에 따라 처리 방법이 다르다.

| Excel 시트 유형 | 처리 방법 |
|----------------|-----------|
| 파라미터/사양 표 (몇 십 행) | Word 표로 복사 후 표 캡션 추가 |
| 대용량 데이터 (수백~수천 행) | **Excel 유지** (→ 별도 표 전용 JSON으로 직접 변환, 9장 참조) |
| 분석 결과 요약 + 그래프 | 요약 표는 Word로, 그래프는 이미지로 삽입 + 캡션 |
| 서식 위주 양식/폼 | Word 표로 재구성 |

Excel을 무조건 Word로 옮기는 것은 비효율적이다.  
**표 데이터 자체는 Excel이 더 AI 친화적**이다 (행/열 구조가 명확). 이 경우 Excel → JSON 직변환이 우선이다.

### 3.3 PDF → Word 변환

**방법**: Microsoft Word에서 PDF를 직접 열기 (Word 2013+)  
또는: Adobe Acrobat의 "Word로 내보내기" 기능

**변환 후 필수 검토 항목**:
- [ ] 표가 제대로 Word 표로 변환되었는가 (이미지로 남아있지 않은가)
- [ ] Heading 스타일이 적용되었는가 (굵은 텍스트 ≠ Heading)
- [ ] 스캔 PDF의 경우 OCR 품질 확인
- [ ] 수식/특수문자가 깨지지 않았는가

### 3.4 기존 Word 문서 검토

기존 Word 파일들은 변환 필요는 없지만, **구조화 작업**이 필요하다.

검토 항목:
- [ ] 제목들이 "굵은 글씨"가 아닌 실제 Heading 스타일로 지정되어 있는가
- [ ] 그림에 캡션이 있는가
- [ ] 표에 캡션(제목)이 있는가
- [ ] 번호 체계가 일관적인가

---

## 4. 2단계: Word 작성 3원칙

이것이 전략의 핵심이다. **Word를 올바르게 작성하지 않으면 JSON 변환 품질도 낮아진다.**

### 원칙 1: 번호 체계 — Heading 스타일을 반드시 사용한다

**잘못된 방법** (폰트 크기/굵기로 제목 흉내):
```
[굵게 + 폰트 16pt] 1. 개요
[굵게] 1.1 배경
```

**올바른 방법** (Word Heading 스타일 사용):
```
[Heading 1] 1. 개요
[Heading 2] 1.1 배경
[Heading 3] 1.1.1 용어 정의
```

**왜 Heading 스타일이어야 하는가?**  
Word에서 굵은 텍스트와 Heading 스타일은 화면에서 비슷하게 보이지만, 파일 내부 XML에서 완전히 다르다.  
python-docx 같은 변환 도구는 `w:pStyle` 속성에서 `Heading1`, `Heading2`를 읽어 계층을 파악한다.  
굵은 텍스트는 `w:b` 태그이며, 도구가 "이게 제목이다"라고 인식할 수 없다.

**번호 체계 규칙**:
- 1레벨: `1.`, `2.`, `3.` (Heading 1)
- 2레벨: `1.1`, `1.2`, `2.1` (Heading 2)
- 3레벨: `1.1.1`, `1.1.2` (Heading 3)
- 3레벨 이상은 피한다 (깊을수록 AI 이해 품질 저하)

### 원칙 2: 그림 캡션 — 구체적이고 독립적으로 작성한다

**잘못된 캡션** (번호만 있거나, 설명이 없거나):
```
그림 1
그림 1: 해석 결과
```

**올바른 캡션** (AI가 이미지 없이 내용을 이해할 수 있는 수준):
```
Figure 1: 브라켓 부품의 von Mises 응력 분포 해석 결과 (하중 조건 1,000N, 최대 응력 250 MPa, 빨간색 영역이 응력 집중부)
```

**왜 구체적으로 써야 하는가?**  
AI는 이미지를 직접 볼 수 없다 (텍스트 기반 AI의 경우).  
JSON으로 변환된 후 그림은 캡션 텍스트만 남는다.  
캡션이 "그림 1"뿐이라면 AI는 해당 그림의 존재는 알지만 내용은 전혀 알 수 없다.  
캡션이 충분히 설명적이라면, AI는 **"그림에 무엇이 있는지"를 캡션으로 이해**하고 질문에 답할 수 있다.

**캡션 작성 요령**:
1. **무엇을 보여주는가**: 대상 부품/공정/데이터
2. **어떤 조건인가**: 해석 조건, 측정 조건, 날짜 등
3. **핵심 수치/결과**: 최대값, 범위, 특이점
4. **색상/마킹의 의미**: "빨간색 = 응력 집중부" 같은 해석 키

**캡션 삽입 방법**: 그림 선택 → 오른쪽 클릭 → "캡션 삽입" (Word 자동 번호 관리)

### 원칙 3: 표 캡션 — 표 위에, 내용을 설명하는 제목으로 작성한다

**잘못된 방법**:
```
아래 표는 파라미터를 나타낸다.    ← 본문에 묻어 있음
[표]
```

**올바른 방법**:
```
Table 1: IGA target 설정 YAML 파라미터 전체 목록 (KooRemapper v1.3.0 기준)
[표]
```

**표 캡션 위치**: 반드시 **표 위에** 위치  
**그림 캡션 위치**: 반드시 **그림 아래에** 위치

**왜 표 캡션이 필요한가?**  
표는 JSON 변환 시 `headers + rows` 배열로 파싱된다.  
캡션이 없으면 이 표가 "무엇에 대한 표인가"를 AI가 알 수 없어, 검색 시 연결되지 않는다.

---

## 5. 3단계: 범용 JSON 스키마 설계

### 5.1 스키마 전체 구조

```json
{
  "schema_version": "1.0",
  "meta": { ... },
  "toc": [ ... ],
  "sections": [ ... ],
  "figures": [ ... ],
  "tables": [ ... ]
}
```

최상위 6개 키. **각 키가 왜 존재하는지는 6장에서 상세 설명.**

### 5.2 완전한 스키마 예시

```json
{
  "schema_version": "1.0",

  "meta": {
    "doc_id": "BU-CAE-2026-001",
    "title": "IGA (Isogeometric Analysis) 가이드",
    "source_format": "docx",
    "source_file": "iga_guide.docx",
    "doc_type": "manual",
    "created": "2026-05-07",
    "modified": "2026-05-07",
    "author": "홍길동",
    "department": "CAE팀",
    "project": "KooRemapper",
    "version": "1.0",
    "tags": ["IGA", "LS-DYNA", "NURBS", "KooRemapper", "FEM"],
    "summary": "KooRemapper v1.3.0의 IGA(등기하해석) 기능 사용법을 설명하는 기술 매뉴얼. NURBS 기반 Trimmed Volume 방식으로 FE mesh를 IGA로 자동 변환하는 프로세스, YAML 설정 문법, 생성 파일 구조를 다룬다."
  },

  "toc": [
    { "id": "1",     "level": 1, "title": "개요" },
    { "id": "1.1",   "level": 2, "title": "IGA란 무엇인가" },
    { "id": "1.2",   "level": 2, "title": "LS-DYNA에서의 IGA" },
    { "id": "1.3",   "level": 2, "title": "KooRemapper가 자동화하는 것" },
    { "id": "2",     "level": 1, "title": "작동 원리" },
    { "id": "2.1",   "level": 2, "title": "Trimmed NURBS Volume 개념" }
  ],

  "sections": [
    {
      "id": "1",
      "level": 1,
      "title": "개요",
      "content": "",
      "figure_refs": [],
      "table_refs": [],
      "children": [
        {
          "id": "1.1",
          "level": 2,
          "title": "IGA란 무엇인가",
          "content": "IGA(Isogeometric Analysis, 등기하해석)는 CAD와 CAE를 통합하는 수치해석 방법론이다. 기존 유한요소법(FEM)이 Lagrange 다항식 기반의 형상 함수를 사용하는 반면, IGA는 NURBS(Non-Uniform Rational B-Spline)를 형상 함수로 직접 사용한다.",
          "figure_refs": ["fig-1"],
          "table_refs": ["tbl-1"],
          "children": []
        }
      ]
    }
  ],

  "figures": [
    {
      "id": "fig-1",
      "number": 1,
      "caption": "Figure 1: FEM 메시(TET4)와 IGA NURBS 박스의 관계 — NURBS 직육면체가 FE mesh를 완전히 감싸고 있으며, FE mesh는 trim 경계로 사용됨",
      "section_ref": "1.1"
    }
  ],

  "tables": [
    {
      "id": "tbl-1",
      "number": 1,
      "caption": "Table 1: FEM(HEX8/TET4)과 IGA(NURBS)의 주요 특성 비교",
      "section_ref": "1.1",
      "headers": ["항목", "FEM (HEX8/TET4)", "IGA (NURBS)"],
      "rows": [
        ["형상 함수", "Lagrange 다항식", "NURBS 기저함수"],
        ["형상 표현", "근사 (절점 보간)", "정확 (CAD와 동일)"],
        ["연속성", "C0 (요소 경계)", "Cp-1 (p차 기준)"],
        ["메시 세분화", "h-refinement (분할)", "k-refinement (차수+세분화 동시)"]
      ]
    }
  ]
}
```

### 5.3 doc_type 값 정의

| 값 | 설명 | 원본 포맷 예시 |
|----|------|---------------|
| `report` | 분석/검토/결과 보고서 | Word, PDF |
| `manual` | 사용법·절차·가이드 문서 | Word |
| `spec` | 규격·사양·요구사항 정의서 | Word, Excel |
| `slide` | 발표/교육/회의 자료 | PPT → Word |
| `data` | 측정값·파라미터 데이터 표 | Excel → JSON |
| `form` | 양식·체크리스트 | Word, Excel |

---

## 6. 스키마 설계 근거

**이 장이 핵심이다. 각 필드가 왜 그 위치에 존재해야 하는지, 없으면 어떤 문제가 생기는지를 설명한다.**

### 6.1 schema_version — 미래를 위한 최소 장치

`"schema_version": "1.0"`

**없으면**: 1년 후 스키마를 수정했을 때, 기존 JSON들이 새 도구와 호환되는지 알 수 없다.  
**있으면**: 변환 도구가 버전을 보고 맞는 파서를 선택한다.  
변환 스크립트가 `"1.0"`인지 `"2.0"`인지 먼저 확인하고 처리 방식을 분기할 수 있다.

### 6.2 meta — AI가 문서를 "식별"하고 "판단"하기 위한 정보

`meta`는 문서의 신원증명서다.

**`doc_id`가 필요한 이유**:  
AI가 여러 문서를 참조할 때, "BU-CAE-2026-001 문서의 3.2절에 따르면..." 처럼 인용이 가능해진다.  
ID가 없으면 "iga_guide.docx에 따르면..." 인데, 같은 이름의 파일이 여러 버전 존재하면 모호해진다.

**`doc_type`이 필요한 이유**:  
AI는 `manual` 타입 문서에서 절차/방법을 찾고, `spec` 타입에서 숫자/요구사항을 찾는다.  
타입이 없으면 AI가 "이 문서에서 무엇을 기대할 수 있는가"를 모른다.

**`tags`가 필요한 이유**:  
RAG 시스템에서 "IGA에 대해 알려줘"라는 질문이 들어올 때, 전체 텍스트 검색보다 태그 기반 검색이 훨씬 빠르고 정확하다.  
tags는 AI가 자동 생성하거나, 작성자가 추가할 수 있다.

**`summary`가 필요한 이유 (가장 중요)**:  
RAG 시스템에서 질문이 들어오면, 수백 개의 JSON 중 관련 문서를 찾아야 한다.  
이때 모든 JSON의 전체 내용을 읽으면 느리고 비싸다.  
summary만 먼저 읽어서 "이 문서가 내 질문과 관련 있는가?"를 판단한다.  
**summary가 없으면 RAG 시스템이 제대로 작동하지 않는다.**

### 6.3 toc — AI의 문서 네비게이션 지도

```json
"toc": [
  { "id": "1",   "level": 1, "title": "개요" },
  { "id": "1.1", "level": 2, "title": "IGA란 무엇인가" }
]
```

**왜 sections 트리와 별도로 toc가 필요한가?**  
sections 트리는 전체 내용을 포함해 매우 크다. "이 문서의 구조가 어떻게 되나요?"라는 질문에  
AI가 sections 전체를 읽는 것은 낭비다.  
toc는 목차만 담은 가벼운 배열이라, AI가 먼저 전체 구조를 파악한 뒤 필요한 section만 깊이 읽을 수 있다.

**`level`이 필요한 이유**:  
AI가 "3장의 주요 내용은?"이라고 물었을 때, level이 없으면 `3`, `3.1`, `3.1.1`이 모두 같은 레벨로 보인다.  
level 정보로 "3장의 직접 하위 항목(level 2)"만 필터링할 수 있다.

### 6.4 sections — 문서 내용의 계층 트리

**왜 평탄한 배열이 아니라 중첩 트리인가?**

평탄한 배열 방식 (나쁜 예):
```json
"sections": [
  { "id": "1",   "title": "개요" },
  { "id": "1.1", "title": "배경" },
  { "id": "1.2", "title": "목적" },
  { "id": "2",   "title": "방법론" }
]
```

중첩 트리 방식 (좋은 예):
```json
"sections": [
  {
    "id": "1", "title": "개요",
    "children": [
      { "id": "1.1", "title": "배경", "children": [] },
      { "id": "1.2", "title": "목적", "children": [] }
    ]
  }
]
```

**트리의 장점**:  
AI가 "1장 전체의 내용을 요약해줘"라고 하면, 트리 구조에서는 node `id:"1"`을 가져오면  
`children`인 `1.1`, `1.2`의 내용이 자동으로 포함된다.  
평탄 배열에서는 `id`가 `"1"`로 시작하는 항목을 모두 필터링하는 추가 로직이 필요하다.

**`content`의 범위**:  
해당 섹션 제목 바로 다음부터 다음 제목이 나오기 전까지의 텍스트 단락들.  
`Heading 2: 1.1 배경` 다음 단락들이 `sections[0].children[0].content`에 들어간다.

**`figure_refs`, `table_refs`가 필요한 이유**:  
AI가 "1.1절에 어떤 그림과 표가 있나요?"라고 물으면,  
refs 배열에서 바로 `["fig-1", "tbl-1"]`을 찾아 `figures`와 `tables` 배열에서 상세 정보를 가져온다.  
없으면 전체 content를 스캔해 "Figure 1"이라는 텍스트를 찾아야 한다 (비효율적이고 오류 가능).

### 6.5 figures — 이미지의 "텍스트 대역"

**AI는 이미지를 직접 볼 수 없다 (텍스트 기반 AI 기준).**

이것이 그림 캡션이 핵심인 이유다. JSON으로 변환하면 그림 파일 자체는 별도 저장되거나 생략되고,  
`caption` 텍스트만 남는다. 이 캡션이 AI가 "이 그림에 무엇이 있는지" 이해하는 유일한 수단이다.

**`section_ref`가 필요한 이유**:  
"2장의 그림들을 설명해줘"라는 질문에, `section_ref: "2"`인 그림들만 필터링할 수 있다.  
없으면 모든 그림의 캡션을 읽어봐야 어느 섹션 그림인지 알 수 있다.

**`number`가 정수(int)인 이유**:  
`"Figure 1"`, `"그림 1"`, `"Fig.1"` 등 표기 방식의 혼란을 피하기 위해,  
순수 숫자를 별도 필드로 저장한다. AI가 "3번 그림"과 "Figure 3"을 같은 것으로 확실히 연결할 수 있다.

### 6.6 tables — 표의 구조를 살리는 유일한 방법

**표가 평문(plain text)으로 추출되면 무슨 일이 일어나는가:**

Word에서 표를 평문 추출:
```
항목 FEM IGA 형상함수 Lagrange NURBS 연속성 C0 Cp-1
```

이 텍스트에서 AI는:
- 컬럼이 몇 개인지 알 수 없다
- 각 값이 어느 열에 속하는지 알 수 없다
- "FEM의 연속성은?"이라는 질문에 신뢰할 수 있는 답을 줄 수 없다

**`headers + rows` 배열로 저장하면:**
```json
{
  "headers": ["항목", "FEM", "IGA"],
  "rows": [
    ["형상함수", "Lagrange", "NURBS"],
    ["연속성", "C0", "Cp-1"]
  ]
}
```

AI는 2차원 배열로 표를 읽어 "FEM의 연속성 = C0"을 정확히 찾을 수 있다.  
이것이 표를 `headers + rows` 구조로 저장하는 이유다.

---

## 7. 4단계: PostgreSQL 데이터 저장 및 활용

### 7.1 PostgreSQL 소개

PostgreSQL은 오픈소스 관계형 데이터베이스(RDBMS)로, 30년 이상의 역사를 가진 엔터프라이즈급 시스템이다.  
단순한 행·열 데이터뿐 아니라 **JSON을 네이티브로 저장·쿼리**할 수 있으며, 전문 검색(Full-Text Search)과 AI 벡터 검색(pgvector)까지 하나의 데이터베이스에서 처리 가능하다.

**왜 PostgreSQL을 선택하는가:**

| 이유 | 설명 |
|------|------|
| JSONB 타입 | JSON을 이진 형식으로 저장·인덱싱 → `content` 내부 필드를 SQL로 직접 쿼리 |
| 전문 검색 | 한국어 포함 다국어 전문 검색 기능 내장 |
| pgvector 확장 | AI 벡터 임베딩 저장 및 유사도 검색 (RAG 시스템의 핵심) |
| 안정성 | 뱅킹·항공우주 등 미션 크리티컬 환경에서 수십 년간 검증 |
| 무료 | 오픈소스, 라이선스 비용 없음 |

### 7.2 파이프라인에서의 역할

JSON 파일은 변환 도구가 만들어내는 중간 결과물이다.  
이 JSON들을 파일 시스템에만 두면 단순 보관에 그치지만, PostgreSQL에 적재하면 **검색·필터링·버전 관리·AI 연동**이 가능해진다.

```
[Word 문서]
    ↓ 변환 스크립트
[JSON 파일]
    ↓ INSERT
[PostgreSQL]
    ├─ 태그/부서/타입 필터 검색
    ├─ 전문 검색 (키워드 → 섹션 찾기)
    └─ AI 벡터 검색, pgvector (시맨틱 검색 → RAG)
```

### 7.3 데이터베이스 테이블 설계

문서를 저장하는 핵심 테이블은 두 가지다.

**documents 테이블** — 문서 메타데이터 및 전체 JSON 저장

```sql
CREATE TABLE documents (
    id          SERIAL PRIMARY KEY,
    doc_id      VARCHAR(50) UNIQUE NOT NULL,   -- "BU-CAE-2026-001"
    title       TEXT NOT NULL,
    doc_type    VARCHAR(20),                   -- report/manual/spec/slide/data
    department  VARCHAR(100),
    author      VARCHAR(100),
    source_fmt  VARCHAR(10),                   -- docx/xlsx/pptx/pdf
    tags        TEXT[],                        -- 배열: {'IGA','LS-DYNA','NURBS'}
    summary     TEXT,                          -- RAG용 요약문
    content     JSONB NOT NULL,                -- 전체 JSON (sections/figures/tables)
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 인덱스
CREATE INDEX idx_docs_type    ON documents(doc_type);
CREATE INDEX idx_docs_tags    ON documents USING GIN(tags);
CREATE INDEX idx_docs_content ON documents USING GIN(content jsonb_path_ops);
CREATE INDEX idx_docs_summary ON documents USING GIN(to_tsvector('simple', summary));
```

**sections 테이블** — 섹션별 분리 저장 (청크 검색용)

```sql
CREATE TABLE sections (
    id          SERIAL PRIMARY KEY,
    doc_id      VARCHAR(50) REFERENCES documents(doc_id),
    section_id  VARCHAR(20),      -- "1.2.3"
    level       SMALLINT,         -- 1, 2, 3
    title       TEXT,
    content     TEXT,
    fts_vector  TSVECTOR GENERATED ALWAYS AS (
                    to_tsvector('simple',
                        coalesce(title,'') || ' ' || coalesce(content,''))
                ) STORED
);

CREATE INDEX idx_sections_doc_id ON sections(doc_id);
CREATE INDEX idx_sections_fts    ON sections USING GIN(fts_vector);
```

**왜 sections를 별도 테이블로 분리하는가?**  
RAG 시스템은 문서 전체가 아니라 **관련 섹션 조각(chunk)** 을 LLM 프롬프트에 전달한다.  
sections 테이블이 있으면 "1.2절 내용만" 또는 "level 2 섹션들만" 같은 정밀 검색이 가능하다.

### 7.4 주요 활용 쿼리

#### 태그로 문서 검색

```sql
SELECT doc_id, title, summary
FROM documents
WHERE tags @> ARRAY['IGA', 'LS-DYNA'];
```

#### 전문 검색 — 섹션 내용에서 키워드 찾기

```sql
SELECT d.title, s.section_id, s.title, s.content
FROM sections s
JOIN documents d ON s.doc_id = d.doc_id
WHERE s.fts_vector @@ to_tsquery('simple', 'NURBS & 제어점');
```

#### JSON 내부 표 데이터 검색

```sql
-- tables 배열에서 caption에 "파라미터"가 포함된 표 찾기
SELECT doc_id, title,
       jsonb_path_query(content, '$.tables[*] ? (@.caption like_regex "파라미터")')
FROM documents;
```

### 7.5 pgvector 확장 — AI 시맨틱 검색

RAG 시스템에서 PostgreSQL을 벡터 데이터베이스로 활용하려면 pgvector 확장을 추가한다.

```sql
CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE sections ADD COLUMN embedding VECTOR(1536);
-- 1536은 OpenAI ada-002 기준; Claude 임베딩 차원에 맞게 조정

CREATE INDEX idx_sections_vec ON sections
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

#### 시맨틱 검색 — 질문의 의미와 가장 유사한 섹션 찾기

```sql
SELECT d.title, s.section_id, s.title, s.content,
       1 - (s.embedding <=> $1) AS similarity
FROM sections s
JOIN documents d ON s.doc_id = d.doc_id
ORDER BY s.embedding <=> $1
LIMIT 5;
-- $1: 사용자 질문을 임베딩한 벡터
```

이 결과로 찾아낸 섹션들을 LLM(Claude 등)의 프롬프트에 컨텍스트로 전달하면,  
AI가 사업부 문서 기반으로 근거 있는 답변을 생성한다.

### 7.6 도입 시 고려사항

| 항목 | 내용 |
|------|------|
| 설치 | PostgreSQL 16+ 권장, Docker로 간단히 설치 가능 |
| pgvector | `CREATE EXTENSION vector` 한 줄로 활성화 |
| 한국어 검색 | `pg_bigm` 확장 또는 `simple` 딕셔너리 + 형태소 분석기 조합 권장 |
| 백업 | `pg_dump`로 전체 백업, S3 연동 자동 백업 설정 권장 |
| 접근 제어 | 부서별 ROLE 설정으로 문서 접근 권한 관리 |

---

## 8. 전략 평가: 강점과 보완점

### 8.1 강점

| 강점 | 설명 |
|------|------|
| 학습 비용 최소화 | 직원들은 Word만 잘 쓰면 됨. JSON/Markdown 지식 불필요. |
| 단계적 도입 가능 | Word 작성 원칙 → 변환 자동화 순서로 점진적 적용 |
| 기존 자산 재활용 | 기존 Word 문서에 Heading 스타일만 적용하면 즉시 변환 가능 |
| 명확한 품질 기준 | "Heading 스타일 사용했는가, 캡션 있는가" → 검증 가능한 기준 |
| 변환 자동화 여지 | 잘 작성된 Word는 python-docx 스크립트로 배치 변환 가능 |

### 8.2 보완이 필요한 부분

#### 보완점 1: "보기에 제목 같은 것" vs "실제 Heading 스타일"

**문제**: 직원들이 스타일 적용 없이 굵게 + 큰 글씨로 제목을 흉내낼 수 있다.  
**해결책**: 
- 회사 표준 Word 템플릿(`.dotx`)을 제공한다
- 템플릿에 Heading 1~3 스타일을 미리 설정하고 번호 자동 추가
- 문서 제출 시 스타일 준수 여부를 간단한 스크립트로 검증

#### 보완점 2: Excel의 대용량 데이터

**문제**: 수백 행의 측정 데이터를 Word 표로 옮기는 것은 비효율적이고, Word가 느려진다.  
**해결책**: Excel 시트 유형에 따라 분기 처리 (9장 참조).  
파라미터 표는 Word로, 대용량 데이터 시트는 Excel에서 직접 JSON으로 변환.

#### 보완점 3: 이미지의 실제 내용

**문제**: 캡션을 아무리 잘 써도, 그래프의 수치나 도면의 세부 형상은 전달에 한계가 있다.  
**해결책 (장기)**: 
- AI 비전 모델(Vision LLM)을 활용해 이미지 자체를 분석하고 description 자동 생성
- 단기적으로는 작성 가이드에 "캡션에 핵심 수치를 반드시 포함"을 강조

#### 보완점 4: 변환 도구 구현 필요

**문제**: Word → JSON 변환 스크립트를 누군가 만들어야 한다.  
**해결책**: python-docx 기반 변환 스크립트는 비교적 단순하며 1~2주면 구현 가능.  
처음에는 반자동(스크립트 실행 + 수동 검토)으로 시작해도 충분하다.

---

## 9. Excel·PPT 특수 처리 전략

### 9.1 Excel: 표 전용 JSON 직변환

대용량 Excel 데이터는 Word를 거치지 않고 **직접 JSON으로 변환**한다.

**Excel → JSON 규칙**:
- 시트 이름 → `meta.title`
- 1행 → `headers` 배열
- 2행 이후 → `rows` 2차원 배열

**Excel 작성 규칙 (AI 친화성 향상)**:
1. **1행은 반드시 헤더**: 셀 병합 없이 각 열의 의미를 명확히 적는다
2. **단위를 헤더에 포함**: "무게"가 아니라 "무게(kg)"
3. **빈 행/열 없음**: A1부터 시작, 빈 셀이 없도록
4. **하나의 시트 = 하나의 주제**: 여러 주제를 한 시트에 섞지 않음
5. **색상 의미 제거**: 빨간 행 = 불량 같은 색상 의미를 별도 컬럼으로 표현

**변환 결과 JSON 구조**:
```json
{
  "schema_version": "1.0",
  "meta": {
    "doc_id": "BU-DATA-2026-001",
    "title": "브라켓 부품 하중 테스트 결과",
    "source_format": "xlsx",
    "doc_type": "data",
    "sheet_name": "하중_테스트_결과"
  },
  "tables": [
    {
      "id": "tbl-1",
      "caption": "브라켓 부품 하중 테스트 결과 전체 데이터",
      "headers": ["시료ID", "하중(N)", "최대응력(MPa)", "파괴여부"],
      "rows": [
        ["BK-001", 1000, 250, "N"],
        ["BK-002", 1200, 298, "N"],
        ["BK-003", 1500, 380, "Y"]
      ]
    }
  ]
}
```

### 9.2 PPT: 발표 구조를 살린 Word 변환

PPT를 Word로 변환할 때, 발표의 흐름(섹션 구분)을 보존하는 것이 중요하다.

**PPT → Word 변환 구조 매핑**:

```
[PPT 구조]                      [Word 구조]
─────────────────────────────────────────────
발표 제목 (Title Slide)    →   문서 제목 + 개요(Heading 1)
챕터 제목 슬라이드         →   Heading 1 (챕터 구분)
일반 슬라이드 (제목+내용) →   Heading 2 (슬라이드 제목)
                                일반 단락 (슬라이드 내용)
이미지/다이어그램          →   Word 이미지 삽입 + 캡션 필수
표                         →   Word 표 + 표 캡션 필수
발표자 노트                →   섹션 말미 "(발표자 노트: ...)" 단락
```

**PPT 변환 후 Word에서 추가 작업**:
- 슬라이드 번호를 Heading 번호로 교체 (예: "슬라이드 3"이 아니라 "3. 해석 결과")
- 불릿 포인트를 의미 있는 단락으로 확장 (짧은 키워드를 문장으로)
- 발표 맥락에서만 이해되는 내용을 자기완결적으로 보완

---

## 10. 구현 로드맵

### Phase 1: 기반 준비 (1~2주)

- [ ] 표준 Word 템플릿 (`.dotx`) 제작 — Heading 1~3 스타일, 번호 자동화 설정
- [ ] 작성 가이드 문서 배포 (본 문서를 기반으로 1페이지 요약본)
- [ ] 부서 내 파일럿 문서 3~5개 선정

### Phase 2: 파일럿 변환 (2~4주)

- [ ] 파일럿 문서들을 표준 Word로 변환·재작성
- [ ] 수동으로 JSON 변환 테스트 (python-docx 스크립트 초안)
- [ ] 변환 결과 검토 및 스키마 보완

### Phase 3: 자동화 및 확산 (1~2개월)

- [ ] Word → JSON 변환 스크립트 안정화
- [ ] Excel → JSON 변환 스크립트 완성
- [ ] 전 부서 대상 Word 작성 원칙 교육
- [ ] 기존 주요 문서 일괄 변환

### Phase 4: AI 연동 (3개월 이후)

- [ ] 변환된 JSON들을 RAG 시스템에 인덱싱
- [ ] 내부 AI 어시스턴트에 연결 ("우리 부서 문서에서 찾아줘")
- [ ] 주기적 업데이트 프로세스 확립 (문서 수정 → 재변환 → 재인덱싱)

---

## 11. Word 작성 체크리스트

문서 작성/변환 완료 후 이 항목들을 확인한다.

### 구조

- [ ] 모든 제목이 Word Heading 스타일(Heading 1~3)로 지정되어 있는가
- [ ] 번호 체계가 1, 1.1, 1.1.1 형식으로 일관되어 있는가
- [ ] 본문이 Heading 없이 시작하지는 않는가

### 그림

- [ ] 모든 그림에 캡션이 있는가
- [ ] 캡션이 "Figure X: [구체적 설명]" 형식인가
- [ ] 캡션에 핵심 수치 또는 조건이 포함되어 있는가
- [ ] 캡션이 그림 **아래에** 위치하는가

### 표

- [ ] 모든 표에 캡션(제목)이 있는가
- [ ] 캡션이 "Table X: [설명]" 형식인가
- [ ] 캡션이 표 **위에** 위치하는가
- [ ] 표의 첫 행이 헤더인가 (굵게 처리 또는 헤더 행 스타일 적용)

### 내용

- [ ] 약어는 처음 등장할 때 풀어서 쓰는가 (예: IGA(Isogeometric Analysis))
- [ ] 수치에 단위가 명시되어 있는가
- [ ] 단락이 너무 짧지 않은가 (키워드 나열이 아닌 문장 형태)

### Excel (해당되는 경우)

- [ ] 1행이 헤더인가
- [ ] 헤더에 단위가 포함되어 있는가
- [ ] 빈 행/열이 없는가
- [ ] 색상 의미가 별도 컬럼으로 표현되어 있는가

---

## 12. 최종 전략: Cline SR + API 서버 기반 에이전트 아키텍처

### 12.1 왜 챗봇이 아닌가

일반적인 AI 데이터화 전략의 종착점은 "사내 챗봇" 구축이다.  
그러나 이 방향에는 구조적인 문제가 있다.

**전통적 챗봇 방식의 문제:**

```
사용자 질문 → [챗봇 서버] → [LLM 추론] → 답변
                   ↑
          동시 사용자 수 × GPU 연산
```

사용자가 늘수록 GPU 서버가 늘어야 한다.  
사내 LLM을 운영하면 유지보수 조직이 필요하고, 외부 API를 쓰더라도 별도 챗봇 UI·인증·히스토리 관리 시스템을 만들어야 한다.  
**결국 소프트웨어 팀이 없는 사업부에서 운영하기에 너무 무겁다.**

### 12.2 Cline SR을 챗봇 대신 쓴다

사내 엔지니어들은 이미 **Cline SR** 을 사용하고 있다.  
Cline SR은 삼성이 사내 전용으로 운영하는 Cline으로, 외부 Cline과 동일한 AI 코딩 에이전트 방식으로 동작하지만 사내 인프라 위에서 구동된다.  
챗봇 전용 GPU 서버를 별도로 구성할 필요가 없다.

핵심 통찰은 다음과 같다:

> Cline SR이 단순히 코드만 작성하는 도구가 아니라,  
> **API를 호출해서 데이터를 가져오고 분석하는 에이전트**로 동작할 수 있다.

즉, 챗봇 서버를 따로 만들 필요 없이 **Cline SR 자체가 챗봇 역할**을 한다.  
사내 인프라는 LLM 추론이 없는 **가벼운 API 서버 + PostgreSQL** 만 있으면 된다.

### 12.3 전체 아키텍처

```
[엔지니어 PC]                    [사내 서버 (경량)]
┌─────────────────┐              ┌──────────────────────────┐
│  Cline SR (IDE) │              │  API 서버 (FastAPI 등)    │
│                 │  REST 호출   │                          │
│  "IGA 해석 설정 │─────────────▶│  GET /data?agent=iga     │
│   검토해줘"     │              │                          │
│                 │◀─────────────│  → PostgreSQL 쿼리       │
│  JSON 수신      │  JSON 반환   │  → 관련 섹션 반환         │
│  → Claude로     │              └──────────────────────────┘
│    분석·답변    │                         ↑
└─────────────────┘              ┌──────────────────────────┐
      ↑                          │  PostgreSQL              │
  Cline SR 사내 인프라         │  (변환된 문서 JSON 저장)  │
  (LLM 추론은 여기서)          └──────────────────────────┘
```

**흐름 설명:**

1. 엔지니어가 Cline SR에 질문하거나 작업을 요청한다
2. Cline SR이 작업 유형에 맞는 API 엔드포인트를 호출한다
3. API 서버가 PostgreSQL에서 관련 문서 JSON을 조회해 반환한다
4. Cline SR이 반환된 데이터를 컨텍스트로 삼아 분석·답변을 생성한다
5. LLM 추론은 Cline SR 사내 인프라에서 발생 — **챗봇 전용 GPU 별도 구성 불필요**

### 12.4 에이전트 유형과 데이터 분류

이 아키텍처의 핵심은 **"어떤 에이전트가 어떤 데이터를 보는가"** 를 사전에 정의하는 것이다.

**PostgreSQL에 에이전트 매핑 테이블 추가:**

```sql
CREATE TABLE agent_scope (
    agent_type   VARCHAR(50) NOT NULL,  -- 에이전트 유형 식별자
    doc_id       VARCHAR(50) REFERENCES documents(doc_id),
    section_ids  TEXT[],                -- NULL이면 문서 전체
    priority     SMALLINT DEFAULT 1,   -- 우선순위 (높을수록 먼저 반환)
    PRIMARY KEY (agent_type, doc_id)
);
```

**에이전트 유형 정의 예시:**

| agent_type | 역할 | 참조하는 데이터 |
| ---------- | ---- | --------------- |
| `iga-analyst` | IGA 해석 설정·검토 | iga_guide, nurbs_spec, solver_manual |
| `cae-reporter` | 해석 결과 보고서 작성 | report_template, result_criteria, unit_standard |
| `material-reviewer` | 재료 물성 검토 | material_db, test_standard, spec_approval |
| `process-checker` | 공정 절차 검증 | process_manual, checklist, quality_standard |
| `code-assistant` | KooRemapper 코드 작업 | api_reference, changelog, example_yaml |

**JSON 문서 스키마에 agent_scope 필드 추가:**

```json
"meta": {
  "doc_id": "BU-CAE-2026-001",
  "title": "IGA 가이드",
  "agent_scope": ["iga-analyst", "code-assistant"],
  ...
}
```

문서를 PostgreSQL에 적재할 때 `agent_scope` 배열을 읽어 `agent_scope` 테이블에 자동 등록한다.

### 12.5 API 서버 설계

**핵심 엔드포인트:**

```
GET  /api/data
     ?agent={agent_type}        에이전트 유형 (필수)
     &query={검색어}             키워드 (선택)
     &section={섹션 ID}         특정 섹션만 (선택)
     &limit={n}                 반환 개수 제한 (기본 5)

응답: 관련 섹션 JSON 배열
```

**예시 호출과 응답:**

```http
GET /api/data?agent=iga-analyst&query=offset+계산

응답:
[
  {
    "doc_id": "BU-CAE-2026-001",
    "section_id": "4.2",
    "title": "offset 계산 수식",
    "content": "bbox_scale 또는 bbox_scale_r/s/t 사용 시: off_axis = ...",
    "relevance": 0.94
  },
  ...
]
```

Cline SR은 이 응답을 프롬프트에 삽입하여 사업부 문서 기반으로 답변을 생성한다.

### 12.6 Cline SR에서의 사용 방식

Cline SR은 두 가지 방식으로 API를 활용할 수 있다.

#### 방식 A: MCP 서버로 등록 (권장)

Cline SR은 MCP(Model Context Protocol)를 지원한다.  
API 서버를 MCP 서버로 구성하면, Cline SR이 자동으로 어떤 도구가 있는지 인식하고 필요할 때 호출한다.

```json
// Cline SR MCP 설정
{
  "mcpServers": {
    "bu-knowledge": {
      "url": "http://내부서버:8000/mcp",
      "tools": ["query_data", "list_agents", "search_docs"]
    }
  }
}
```

이후 Cline SR에게 "IGA offset 계산법 알려줘"라고 하면,  
Cline SR이 자동으로 `query_data(agent="iga-analyst", query="offset 계산")`을 호출해 답변한다.

#### 방식 B: CLAUDE.md에 API 사용 지침 명시

```markdown
# CLAUDE.md
## 사내 지식베이스 API
- 기술 문서 참조가 필요할 때: GET http://내부서버:8000/api/data?agent={역할}&query={검색어}
- IGA 관련 작업: agent=iga-analyst
- 코드 작업: agent=code-assistant
- 항상 API 응답을 먼저 조회한 후 답변할 것
```

### 12.7 이 전략의 강점

| 항목 | 전통적 챗봇 방식 | Cline SR + API 방식 |
| ---- | -------------- | --------------- |
| **인프라 비용** | LLM 서버 + GPU + 챗봇 UI | API 서버 + PostgreSQL (GPU 없음) |
| **LLM 운영** | 챗봇 전용 LLM 서버 별도 운영 | Cline SR 사내 인프라 공용 사용 (이미 운영 중) |
| **사용자 인터페이스** | 별도 챗봇 UI 개발 필요 | 기존 Cline SR 그대로 사용 |
| **동시 사용자** | GPU 병목 발생 | 엔지니어별 독립 실행, 병목 없음 |
| **도구 학습 비용** | 새 챗봇 사용법 학습 | Cline SR 이미 사용 중 |
| **에이전트 전문화** | 에이전트 코드 직접 개발 | API 엔드포인트 정의만으로 완성 |
| **보안** | 챗봇 인증 체계 구축 필요 | API 키 + 사내망 제한으로 충분 |
| **유지보수** | LLM 모델 업데이트, 서버 관리 | PostgreSQL 데이터 업데이트만 |

### 12.8 전체 파이프라인 완성 그림

```text
[문서 작성/변환]                [저장]              [활용]
PPT/Excel/PDF/Word
    ↓
표준 Word 작성
(Heading + 캡션 원칙)
    ↓
Word → JSON 변환               → PostgreSQL      → API 서버
(python-docx 스크립트)           (JSONB 저장)       ↑
    ↓                            ↑                 |
  JSON 파일                  agent_scope         REST 호출
                             테이블 등록           ↓
                                           [Cline SR (MCP)]
                                                  ↓
                                           엔지니어 분석·작업
                                           (Cline SR 사용)
```

**이것이 이 전략의 진짜 가치다.**  
사업부 엔지니어들이 생산한 모든 문서가 구조화된 지식으로 변환되어 PostgreSQL에 축적되고,  
Cline SR이 그 지식을 실시간으로 참조하면서 설계 검토, 코드 작성, 보고서 작성을 보조한다.  
추가 GPU 서버 없이, 추가 도구 교육 없이, 기존 Cline SR 워크플로우 안에서 실현된다.

---

*본 문서는 사업부 AI 데이터화 전략의 기반 설계서이며, 파일럿 결과에 따라 스키마와 변환 규칙이 보완될 수 있습니다.*

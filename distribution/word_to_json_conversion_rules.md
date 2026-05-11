# Word → JSON 변환 규칙서
## 자동화 프로그램 구현 가이드 v1.3 (코드/룰 완전 동기)

> 작성일: 2026-05-07 (개정: 2026-05-10 — `meta.doc_id` / `meta.agent_scope` 코드 정합)
> 적용 대상: 표준 Word 문서를 [json_schema_rules.md](./json_schema_rules.md) 스키마로 변환하는 자동화 프로그램
> 시작점: [`CONVERSION_RULES_INDEX.md`](./CONVERSION_RULES_INDEX.md)
> 자매 문서 (모두 동일 JSON 스키마 출력):
>
> - [json_schema_rules.md](./json_schema_rules.md) — JSON 스키마 전반 (모든 변환기 공통)
> - [excel_to_json_conversion_rules.md](./excel_to_json_conversion_rules.md) — Excel 변환 규칙
> - [ppt_to_json_conversion_rules.md](./ppt_to_json_conversion_rules.md) — PPT 변환 규칙
> - [md_to_json_conversion_rules.md](./md_to_json_conversion_rules.md) — Markdown 변환 규칙
> - [pdf_to_json_conversion_rules.md](./pdf_to_json_conversion_rules.md) — PDF 변환 규칙 (OCR opt-in)
> - [html_to_json_conversion_rules.md](./html_to_json_conversion_rules.md) — HTML 변환 규칙

---

## 0. 코드 정합 노트 (필독)

본 문서는 [`api_server/src/converter/`](./api_server/src/converter/) 의 실제 출력을 단일 진실 공급원으로 한다.

| 항목 | 본 문서 표기 / 변환기 출력 | normalizer 흡수 | DB 컬럼 |
|---|---|---|---|
| 식별자 | `meta.doc_id` (`converter/core.py:796`) | `meta.doc_id` 우선, `meta.id` / `raw.id` 폴백 | `records.id` |
| 에이전트 | `meta.agent_scope` (`converter/core.py:809-810`) | `meta.agent_scope` 우선, `raw.agents` 폴백 | `records.agents` |
| 파생/생애주기 (10개 0006 필드) | `derivation` enum: `original` / `extracted` / `aggregated` / `translated`. classification/status/domain/subject_keywords/source_system/language/parent_record_id/derivation/quality_score/valid_from/valid_until 모두 `meta.*` 출력 시 normalizer 흡수 (`normalizer.py:103-153`) ✅ | 동일명 컬럼 |
| 0007 agent-discovery 자동 채움 | `agent_hints` (자동 생성), `query_examples` (title/tag 기반 최대 3개), `access_pattern="occasional"`, `related_record_ids=[]` — `_apply_agent_discovery_defaults` (`converter/core.py:57-129`) | `records.agent_hints` 등 |
| 자동 추출 (A-3) | `summary` (extractive lead-3, 한국어 종결어 처리), `tags` (RAKE + KO/EN stopword) `converter/core.py:1119-1142` | `records.summary` / `tags` |

(이전 v1.2 까지 "KNOWN GAP" 으로 표기되었던 normalizer 미흡수 / 변환기 미채움 이슈는 v1.3 (커밋 `c2c66c6`) 에서 해소됨.)

---

## 목차

1. [목적 및 범위](#1-목적-및-범위)
2. [권장 도구 스택](#2-권장-도구-스택)
3. [Word XML 기본 구조](#3-word-xml-기본-구조)
4. [Heading 감지 규칙](#4-heading-감지-규칙)
5. [단락 및 본문 추출](#5-단락-및-본문-추출)
6. [캡션 감지 규칙](#6-캡션-감지-규칙)
7. [그림 추출 및 저장](#7-그림-추출-및-저장)
8. [표 추출 — 병합 셀 처리 포함](#8-표-추출)
9. [그림·표와 섹션 연결 규칙](#9-그림표와-섹션-연결-규칙)
10. [외부 소스 파일 메타데이터](#10-외부-소스-파일-메타데이터)
11. [자동 생성 필드 처리](#11-자동-생성-필드-처리)
12. [비표준 문서 처리 정책](#12-비표준-문서-처리-정책)
13. [변환 파이프라인 의사코드](#13-변환-파이프라인-의사코드)
14. [사전 검증 — Word 품질 체크](#14-사전-검증)
15. [사후 검증 — JSON 유효성](#15-사후-검증)
16. [알려진 한계와 처리 방침](#16-알려진-한계)
17. [Excel 변환 (별도 문서)](#17-excel-변환-별도-문서)
18. [변환기별 매핑 표](#18-변환기별-매핑-표)
19. [휴리스틱 헤딩 감지 알고리즘](#19-휴리스틱-헤딩-감지-알고리즘)
20. [검증 후 DB 적재 체크리스트](#20-검증-후-db-적재-체크리스트)

---

## 1. 목적 및 범위

본 문서는 표준 Word 문서(.docx)를 [json_schema_rules.md](./json_schema_rules.md) 스키마에 맞는 JSON으로 자동 변환하는 프로그램을 **AI 또는 개발자가 그대로 구현할 수 있도록** 모든 결정 사항을 명시한다.

### 1.1 입력

- **포맷**: `.docx` (Microsoft Word 2007+ Office Open XML)
- **전제 조건**: [ai_data_strategy.md](./ai_data_strategy.md) 의 "Word 작성 3원칙"이 준수되었다고 가정
  - Heading 1/2/3 스타일 적용
  - 그림 캡션은 그림 아래
  - 표 캡션은 표 위

### 1.2 출력

- 단일 JSON 파일 (스키마 v1.0, `data_type = "DOC"`).
- 추출된 그림·첨부 바이너리는 별도 폴더(`{doc_id}/` 하위)에 저장하고 JSON 에는 POSIX 형식의 상대 경로만 기록한다.
- 출력 JSON 은 [json_schema_rules.md](./json_schema_rules.md) 의 7-키 구조를 따르며, `api.ingest.normalizer` 가 이를 `records / record_sections` 테이블로 적재한다.
  - `meta.title` → `records.title`
  - `meta.summary` → `records.summary`
  - `meta.tags` → `records.tags`
  - `sections[].id / level / title / content` → `record_sections.section_id / level / title / content_text`
  - `sections[].figure_refs / table_refs` → `record_sections.figure_refs / table_refs`
  - 본문 전체 JSON → `records.content` (JSONB)
  - 정규화 시 `records.content_hash = sha256(canonical_json)` 자동 계산

### 1.3 처리하지 않는 것 (범위 밖)

- `.doc` (Word 97-2003 이진 포맷): 사전에 `.docx`로 변환되어 있어야 함
- 매크로(VBA): 무시
- 추적 변경(track changes): 무시 (최종 본문만 사용)
- 코멘트: 무시
- 텍스트 박스: 본문으로 변환 (위치 정보 소실)
- SmartArt, 차트: 이미지로 변환 후 처리 (Word가 자체 렌더링한 EMF/PNG 사용)

---

## 2. 권장 도구 스택

### 2.1 핵심 라이브러리

```python
# Python 3.10+
python-docx>=1.1.0     # Word 문서 파싱 (Heading, 단락, 표, 스타일)
lxml>=5.0              # python-docx가 노출하지 않는 XML 직접 접근
Pillow>=10.0           # 추출된 이미지 검증
hashlib                # SHA-256 해시 (표준 라이브러리)
```

### 2.2 라이브러리 선택 근거

| 후보 | 평가 | 결정 |
|------|------|------|
| **python-docx** | 가장 성숙, 스타일 인식, 표 구조 직접 접근 | **선택** |
| docx2txt | 단순 텍스트 추출만, 구조 손실 | 제외 |
| Pandoc | Markdown 등 다양한 출력 가능하지만 커스터마이징 어려움 | 제외 |
| mammoth | HTML 변환에 강점, JSON에는 부적합 | 제외 |

### 2.3 보조 도구

- **이미지 추출**: python-docx의 `document.part.related_parts` + `inline_shapes`
- **EMF/WMF 처리**: Windows 환경에서 LibreOffice 또는 Inkscape CLI로 PNG 변환

---

## 3. Word XML 기본 구조

`.docx`는 ZIP 아카이브이며, 핵심 파일은 다음과 같다.

```
mydoc.docx (ZIP)
├── word/
│   ├── document.xml      ← 본문 (단락, 표, 그림 위치)
│   ├── styles.xml        ← 스타일 정의 (Heading1, Caption 등)
│   ├── numbering.xml     ← 자동 번호 정의
│   ├── media/            ← 그림 파일들 (image1.png, image2.jpeg ...)
│   └── _rels/
│       └── document.xml.rels   ← 그림·하이퍼링크 관계
├── docProps/
│   └── core.xml          ← 작성자, 생성일, 수정일 메타데이터
└── [Content_Types].xml
```

### 3.1 주요 XML 요소

| 요소 | 의미 |
|------|------|
| `<w:p>` | 단락(paragraph). 모든 본문 텍스트의 단위 |
| `<w:r>` | run. 하나의 단락 내 동일 서식 텍스트 조각 |
| `<w:t>` | 실제 텍스트 |
| `<w:pStyle w:val="..."/>` | 단락의 스타일 ID |
| `<w:tbl>` | 표 |
| `<w:tr>` | 표의 행 |
| `<w:tc>` | 표의 셀 |
| `<w:gridSpan w:val="N"/>` | 가로 병합 |
| `<w:vMerge w:val="restart|continue"/>` | 세로 병합 |
| `<w:drawing>` | 그림(이미지) 삽입 위치 |

---

## 4. Heading 감지 규칙

### 4.1 표준 스타일 ID

Word는 Heading을 다음 스타일 ID로 표시한다.

| level | 영문 Word | 한글 Word | 스타일 ID (XML) |
|-------|----------|----------|----------------|
| 1 | Heading 1 | 제목 1 | `Heading1` |
| 2 | Heading 2 | 제목 2 | `Heading2` |
| 3 | Heading 3 | 제목 3 | `Heading3` |

JSON 매핑: `Heading{N}` → `sections[].level = N` (N ∈ {1,2,3}).
각 Heading 단락의 텍스트는 `sections[].title` 로 보존되며, 최상위(level=1) 첫 번째 헤딩의 텍스트는 `docProps/core.xml` 의 `title` 이 비어 있을 때 `meta.title` 후보가 된다.

### 4.2 감지 알고리즘

```
for each paragraph p in document:
    style_id = p.style.style_id  # python-docx에서 제공
    
    if style_id == "Heading1":
        level = 1
    elif style_id == "Heading2":
        level = 2
    elif style_id == "Heading3":
        level = 3
    elif style_id matches regex "Heading[4-9]":
        level = 4+ → 본문 단락으로 처리 (섹션 분리하지 않음, json_schema_rules 5.4 참조)
    else:
        level = None → 본문 단락
```

### 4.3 한글 Word 호환

한글 Word도 내부 XML에서는 영문 ID(`Heading1`)를 사용한다. **표시 이름**만 "제목 1"로 보일 뿐 ID는 동일하다. 따라서 위 알고리즘이 그대로 동작한다.

### 4.4 사용자 지정 스타일 처리

회사 표준 템플릿에서 `Heading1`을 베이스로 한 `MyHeading1` 같은 파생 스타일을 만들 수 있다. python-docx의 `style.base_style`을 따라 올라가서 베이스가 `Heading1/2/3`인지 확인한다.

```
def detect_heading_level(paragraph):
    style = paragraph.style
    while style is not None:
        if style.style_id in {"Heading1", "Heading2", "Heading3"}:
            return int(style.style_id[-1])
        style = style.base_style
    return None
```

### 4.5 섹션 번호 추출

Heading 텍스트는 보통 다음 두 가지 형태다.

```
형태 A:  "1.2 작동 원리"        ← 숫자가 텍스트로 입력됨
형태 B:  "작동 원리"              ← 숫자는 Word 자동번호(numbering.xml)에서 생성
```

**추출 알고리즘:**

1. Heading 단락 텍스트 앞부분에서 정규식 `^(\d+(?:\.\d+){0,2})\s+(.*)$` 적용
2. 매칭되면 group 1 = section_id, group 2 = title
3. 매칭 안 되면 (형태 B) → numbering.xml에서 자동번호 가져와 조립

### 4.6 섹션 ID 자동 부여

원본 텍스트에 번호가 없거나 불완전한 경우, 변환기가 자동으로 부여한다.

```
counter_l1 = 0; counter_l2 = 0; counter_l3 = 0

for each heading h in order:
    if h.level == 1:
        counter_l1 += 1
        counter_l2 = 0
        counter_l3 = 0
        h.id = f"{counter_l1}"
    elif h.level == 2:
        counter_l2 += 1
        counter_l3 = 0
        h.id = f"{counter_l1}.{counter_l2}"
    elif h.level == 3:
        counter_l3 += 1
        h.id = f"{counter_l1}.{counter_l2}.{counter_l3}"
```

원본 번호가 있는데 자동 계산값과 다르면 → **경고 로그 출력 후 자동 계산값 사용** (저자가 잘못 매긴 번호 신뢰하지 않음).

---

## 5. 단락 및 본문 추출

### 5.1 content 영역 정의

특정 섹션의 `content`는 다음과 같이 결정된다.

```
section S의 content
= S 헤딩 단락 직후부터, 다음 같거나 더 높은 레벨의 헤딩 직전까지의
  모든 일반 단락을 \n\n으로 연결한 텍스트
```

**예외:**
- 표 단락은 content에 포함하지 않음 (`tables` 배열로 분리)
- 그림 단락(`<w:drawing>` 포함)은 content에 포함하지 않음
- 캡션 단락(스타일 `Caption`)은 content에 포함하지 않음 (`figures`/`tables`의 caption으로)

### 5.2 단락 텍스트 추출 규칙

```
def paragraph_text(p):
    # 모든 run의 텍스트를 이어붙임
    text = "".join(run.text for run in p.runs)
    
    # 추적 변경의 삽입은 포함, 삭제는 제외 (python-docx 기본 동작)
    # 코멘트 참조는 제거
    
    return text.strip()
```

### 5.3 빈 단락 처리

- 단순 빈 단락(`""`)은 무시.
- 빈 단락이 두 개 이상 연속되어도 `\n\n` 한 번으로 처리.

### 5.4 리스트(불릿/번호) 처리

Word의 리스트는 `<w:numPr>` 속성으로 표시된다.

**규칙:** 리스트 항목은 일반 단락처럼 추출하되, 앞에 마커를 붙인다.

```
불릿 리스트:    "• {텍스트}"
번호 리스트:    "{n}. {텍스트}"
```

복잡한 다단계 리스트 들여쓰기는 단순화한다 (들여쓰기는 공백 2칸 단위).

### 5.5 인라인 서식 처리

볼드, 이탤릭, 색상 등 인라인 서식은 **모두 무시**하고 평문으로만 추출한다. JSON 스키마는 서식을 표현하지 않는다.

예외: **하이퍼링크**는 `[표시 텍스트](URL)` 형식으로 변환하여 보존.

### 5.6 수식(OMath)

`<m:oMath>` 요소는 LaTeX 형식으로 변환을 시도하되, 실패 시 평문 표기 그대로 추출. 수식 변환 도구는 별도 라이브러리(`pandoc` 또는 `mathml2latex`) 권장.

---

## 6. 캡션 감지 규칙

### 6.1 1차 감지: Caption 스타일

Word의 "캡션 삽입" 기능을 사용하면 단락에 `Caption` 스타일이 적용된다.

```
if paragraph.style.style_id == "Caption":
    이 단락은 캡션
```

### 6.2 2차 감지: 텍스트 패턴

Caption 스타일이 없는 경우, 텍스트 패턴으로 추정한다.

```
정규식 (정식): ^(Figure|Fig\.|그림|Table|Tbl\.|표)\s*\d+\s*[:\.\-]\s*.+$
구현용 그룹화: ^(Figure|Fig\.?|그림|Table|Tbl\.?|표)\s*(\d+)\s*[:\.\-]\s*(.+)$
```

매칭되면:

- group 1: 종류 ("Figure"/"Fig."/"그림" → 그림, "Table"/"Tbl."/"표" → 표)
- group 2: 번호 (정수)
- group 3: 캡션 본문 (필수, 빈 문자열 불허)

캡션 본문이 비면 6.4절의 "캡션 누락" 처리 규칙을 따른다.

### 6.3 캡션-그림 연결 규칙

| 조건 | 그림 캡션? |
|------|----------|
| 캡션 단락 **바로 위**에 그림 단락이 있음 | 그림 캡션 |
| 캡션 단락 **바로 아래**에 그림 단락이 있음 | 그림 캡션 (Word 작성 원칙 위반이지만 허용) |
| 캡션 단락 **바로 위**에 표(`<w:tbl>`)가 있음 | 표 캡션 |
| 캡션 단락 **바로 아래**에 표가 있음 | 표 캡션 |

"바로 위/아래"의 정의: 사이에 본문 단락이 없고 빈 단락만 있는 경우까지 허용.

### 6.4 모호한 경우

- 캡션 텍스트는 "Figure 3"으로 시작하지만 주변에 그림도 표도 없는 경우 → **경고 로그**, 본문 단락으로 처리
- 그림 위·아래 모두 캡션이 있는 경우 → 아래 캡션 우선 (작성 원칙)
- 한 그림에 캡션이 없는 경우 → **경고 로그**, `caption: "Figure N: (캡션 누락)"` 으로 자동 생성

---

## 7. 그림 추출 및 저장

### 7.1 추출 위치

`.docx` 내부 경로:
```
word/media/image1.png
word/media/image2.jpeg
...
```

python-docx에서:
```
for rel_id, rel in document.part.rels.items():
    if "image" in rel.target_ref:
        blob = rel.target_part.blob   # 이미지 바이트
        ext = rel.target_ref.split(".")[-1]
```

### 7.2 저장 규칙

추출된 이미지 바이너리는 **반드시** 다음 경로 패턴에 따라 별도 파일로 저장한다:

```
{output_dir}/
├── HE-CAE-2026-0000000001.json
└── HE-CAE-2026-0000000001/                ← 폴더명 = doc_id
    ├── F001.png
    ├── F002.jpeg
    └── F003.emf
```

- 폴더명: `doc_id` 그대로 (예: `HE-CAE-2026-0000000001`).
- 파일명: `F{3자리 번호}.{원본 확장자}` (예: `F001.png`).
- 확장자는 docx 내 image part 의 원본 확장자 또는 content_type
  (`image/png` → `png`, `image/jpeg` → `jpg`) 으로 결정한다.
- 파일은 `{output_dir}/{doc_id}/F{nnn}.{ext}` 절대 경로에 쓰고,
  JSON 의 `figures[i].image_path` 에는 그 **상대 경로** `"{doc_id}/F{nnn}.{ext}"`
  를 기록한다 (정적 마운트 `/figures` 의 직하 경로).
- 인라인 그림이 본문에서는 감지됐지만 image part 매칭에 실패한 경우
  (텍스트 전용 ASCII 다이어그램 등) `image_path` 는 생략 또는 `null`.

### 7.3 figures 배열의 이미지 경로

JSON 의 `figures[i]` 는 추출된 그림 바이너리에 대한 **상대 경로** 를
`image_path` 에 기록한다.

```json
{
  "id":          "HE-CAE-2026-0000000001-F001",
  "number":      1,
  "caption":     "Figure 1: ...",
  "section_ref": "1.2",
  "image_path":  "HE-CAE-2026-0000000001/F001.png"
}
```

- 값은 `"{doc_id}/F{nnn}.{ext}"` 형식 (선행 슬래시 없음).
- API 서버는 환경변수 `FIGURES_DIR` (기본 `./figures`) 을 정적 마운트
  `/figures` 의 루트로 사용하므로, 클라이언트는 `/figures/{image_path}`
  로 그림을 받을 수 있다.
- 이미지 추출에 실패한 경우 `image_path` 는 생략하거나 `null` 로 둔다.

### 7.4 EMF/WMF 처리

Word가 그래프나 SmartArt를 EMF/WMF 벡터로 저장한 경우:

1. EMF/WMF 원본도 보존 (`.emf` 그대로 저장)
2. PNG로 추가 변환 (LibreOffice CLI 사용):
   ```
   libreoffice --headless --convert-to png input.emf
   ```
3. JSON에는 PNG 경로 사용

### 7.5 그림 순번

문서 본문 등장 순서대로 1부터 부여 (섹션별로 초기화하지 않음).

### 7.6 첨부(attachments) 추출 — kind/mime/caption/file_path

`.docx` 안의 비텍스트 자원은 모두 `attachments[]` 로 추출하며, 본문 흐름 안에서는 `sections[].blocks[]` 의 ref 블록으로 위치를 보존한다. `kind` 는 다음 9종 중 하나로 결정한다.

- `figure` — XML 단서: `a:blip` (`<w:drawing>` 안). 대상: `image/png`, `image/jpeg`, `image/gif`, `.emf`, `.wmf`.
- `document` — XML 단서: `o:OLEObject` 임베디드 파트. 대상: `.pdf` (`application/pdf`), `.doc(x)`.
- `spreadsheet` — XML 단서: `o:OLEObject` 임베디드 파트. 대상: `.xls(x)`, `.csv`.
- `presentation` — XML 단서: `o:OLEObject` 임베디드 파트. 대상: `.ppt(x)`.
- `media` — XML 단서: `<w:object>` 의 audio/video `r:embed`. 대상: `.mp3`, `.mp4`, `.wav`, `.avi`.
- `archive` — XML 단서: `package` 관계 `r:embed`. 대상: `.zip`, `.7z`, `.tar.gz`.
- `cad` — XML 단서: `o:OLEObject` 외부 CAD. 대상: `.step`, `.stp`, `.iges`, `.igs`, `.x_t`, `.CATPart`, `.sldprt`.
- `code` — XML 단서: `o:OLEObject` 텍스트형 소스. 대상: `.py`, `.c`, `.cpp`, `.k`, `.inp`.
- `other` — 위 어디에도 매핑되지 않음.

분류 함수는 `converter/docx_parser.py:infer_attachment_kind(filename, mime)` 가 권위 있는 구현이다 (서버 측 `api.schemas.attachment.infer_attachment_kind` 의 거울).

각 첨부에 대해 다음 4개 필드를 **필수**로 채운다.

```json
{
  "id":         "HE-CAE-2026-0000000001-A001",
  "kind":       "spreadsheet",
  "mime_type":  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "caption":    "Table 3: 시험 결과 원시 데이터 (.xlsx)",
  "file_path":  "HE-CAE-2026-0000000001/A001.xlsx"
}
```

규칙:

- `kind`, `mime_type`, `caption`, `file_path` 4개 모두 비어 있어선 안 된다 (검증 단계에서 거부).
- `caption` 누락 시 6.4절의 자동 캡션 규칙을 적용 (`"<Kind> N: (캡션 누락 — 검수 필요)"`).
- `file_path` 는 **POSIX 슬래시** (`/`) 만 사용. 백슬래시(`\\`) 절대 금지.
- 경로 형식: `"{doc_id}/A{nnn}.{ext}"` (선행 슬래시 없음, 정적 마운트 `/attachments` 직하).
- `mime_type` 결정 우선순위: `[Content_Types].xml` 의 override → 파일 확장자 → `application/octet-stream` (fallback).

---

## 8. 표 추출

### 8.1 기본 추출

```
for tbl in document.tables:
    rows = []
    for row in tbl.rows:
        cells = []
        for cell in row.cells:
            cells.append(cell.text.strip())
        rows.append(cells)
    
    headers = rows[0]
    data_rows = rows[1:]
```

### 8.2 병합 셀 처리

python-docx의 `cell.text`는 병합된 셀에 대해 **같은 값을 반복 반환**한다.

| 상황 | 처리 |
|------|------|
| 가로 병합 (`gridSpan`) | 같은 값을 각 열 셀에 그대로 반복 기재 |
| 세로 병합 (`vMerge`) | 같은 값을 각 행 셀에 그대로 반복 기재 |

이 동작은 [json_schema_rules.md](./json_schema_rules.md) 7.4절 규칙과 일치한다.

### 8.3 셀 내부의 복잡한 콘텐츠

| 셀 내용 | 처리 |
|---------|------|
| 일반 텍스트 | 그대로 추출 |
| 여러 단락 | `\n`으로 연결 |
| 셀 안의 그림 | 별도 figures 배열로 추출, 셀에는 `"[Figure N]"` 표시 |
| 셀 안의 중첩 표 | **경고 로그**, 평문 직렬화 (행을 `;`로, 셀을 `,`로) |

### 8.4 헤더 행 식별

기본적으로 첫 행을 헤더로 본다. 단, 다음 경우 헤더 행이 다를 수 있다.

- 표 첫 행이 병합되어 표 제목으로 쓰인 경우 → 두 번째 행이 실제 헤더
- 감지: 첫 행의 모든 셀이 동일한 값(병합 결과)이면 헤더 아님 → 다음 행을 헤더로

### 8.5 빈 셀

- Word에서 빈 셀(`cell.text == ""`)은 JSON에서 `null` 로 변환.
- 의도적인 공백("-", "N/A" 등)은 그대로 문자열로 보존.

### 8.6 숫자 형 변환

기본은 모든 셀 값을 문자열로 저장. 단, 다음 정규식 일치 시 숫자로 변환:

```
정수:   ^-?\d+$
실수:   ^-?\d+\.\d+$
지수:   ^-?\d+(?:\.\d+)?[eE][+-]?\d+$
```

단위 포함 값 (`"250 MPa"`, `"1.5 mm"`)은 문자열로 유지.

---

## 9. 그림·표와 섹션 연결 규칙

### 9.1 위치 기반 우선

기본 규칙: **그림/표가 물리적으로 어느 섹션 안에 있는가**.

```
def find_owning_section(figure_or_table):
    # 문서 단락 순서대로 스캔
    current_section = None
    for paragraph in document.paragraphs_in_order:
        if is_heading(paragraph):
            current_section = section_id_of(paragraph)
        if paragraph contains figure_or_table:
            return current_section
```

### 9.2 텍스트 참조 추가 등록

본문에서 "Figure 3 참조" 처럼 다른 섹션의 그림을 언급하는 경우:

- 정규식 `(Figure|그림)\s*(\d+)` 또는 `(Table|표)\s*(\d+)` 로 본문 스캔
- 매칭되면 해당 섹션의 `figure_refs`/`table_refs`에도 추가 등록 (중복 허용 안 함, set으로 관리)

### 9.3 일치 검증

변환 후 모든 `figure.section_ref`가 sections 트리에 실제로 존재하는지, 모든 `figure_refs[i]`가 figures 배열에 존재하는지 검증한다.

---

## 10. 외부 소스 파일 메타데이터

### 10.1 sources 배열 구성 (반자동)

`sources` 배열은 **완전 자동 생성이 어렵다**. 다음 방식 권장:

#### 방식 A: 사용자 지정 (권장)

작성자가 Word 문서 끝에 다음 형식의 표를 추가:

| type | format | file_path | description |
|------|--------|-----------|-------------|
| MCAD | CATPart | //file-server/PLM/HE/CAE/2026/bracket_v3.CATPart | 브라켓 v3 형상 |
| ECAD | ODB++ | //file-server/PLM/HE/EDA/2026/mainboard_rev2.tgz | 메인보드 rev2 |

이 표를 변환기가 인식해 `sources` 배열로 변환. 표는 일반 표와 구분하기 위해 직전 단락에 `[SOURCES]` 마커 사용.

#### 방식 B: 본문 정규식

본문에서 파일 경로처럼 보이는 문자열 자동 추출:

```
정규식: (?://|[A-Z]:\\)[\w\-./\\]+\.(CATPart|STEP|stp|sldprt|prt|dxf|dwg|odb|brd|sch|k|inp|cdb)
```

매칭된 경로마다 sources 항목 생성.

### 10.2 메타데이터 자동 수집

`file_path`만 알면 나머지 메타데이터는 자동 수집 가능.

```python
import os, hashlib

def collect_source_metadata(file_path):
    if not os.path.exists(file_path):
        return None  # 경로 없음 → 경고 로그 후 제외
    
    stat = os.stat(file_path)
    return {
        "file_name": os.path.basename(file_path),
        "file_path": file_path,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "size_bytes": stat.st_size,
        "hash_sha256": sha256_of_file(file_path)
    }

def sha256_of_file(path, chunk=65536):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for buf in iter(lambda: f.read(chunk), b""):
            h.update(buf)
    return h.hexdigest()
```

### 10.3 파일 접근 불가 시 처리

- 네트워크 드라이브 미접속, 권한 없음 → `hash_sha256` 생략, `size_bytes`/`modified` 도 null
- 그래도 `file_name`, `file_path`, `type`, `format`, `description`은 사용자 입력값으로 보존

---

## 11. 자동 생성 필드 처리

### 11.1 doc_id 발급

수동 또는 PostgreSQL 시퀀스 사용. **변환기가 임의로 생성하지 않는다.**

권장 흐름:

```
1. 변환 실행 시 사용자가 팀·그룹·연도 인자 제공:
   converter.py iga_guide.docx --team HE --group CAE --year 2026

2. 변환기가 PostgreSQL에 다음 쿼리:
   SELECT MAX(순번) FROM documents WHERE team='HE' AND group='CAE' AND year=2026

3. 다음 순번 = MAX + 1, 6자리 패딩
4. doc_id 조립: HE-CAE-2026-0000000034
```

PostgreSQL 미연동 시: 같은 폴더의 기존 JSON 파일들을 스캔하여 최대 순번 +1.

### 11.2 summary 자동 생성

작성자가 Word 문서에 summary를 직접 쓰지 않은 경우, LLM(Cline SR 사내 LLM)을 호출하여 자동 생성한다.

```
입력: 문서 1장 + 2장 첫 단락의 본문 (보통 "개요"/"서론")
프롬프트: "다음 문서의 핵심을 1~3문장(300자 이내)으로 요약하라.
          용도와 주요 내용을 포함할 것. 제목 반복 금지."
출력: summary 문자열
```

작성자가 Word 문서 머리말에 `[SUMMARY]` 단락을 직접 쓴 경우, 그 단락을 그대로 사용하고 LLM 호출 생략.

### 11.3 tags 자동 생성

```
방법 1 (간단): 본문에서 자주 등장하는 명사 상위 10개 추출 (TF-IDF)
방법 2 (정확): LLM 호출
  프롬프트: "다음 문서의 핵심 키워드 5~10개를 추출하라.
            영어 약어는 원래 표기 유지(IGA, NURBS 등). 부서명·작성자명 제외."
```

작성자가 `[TAGS] IGA, NURBS, ...` 단락을 명시한 경우 그대로 사용.

### 11.4 agent_scope 결정

자동 생성하지 않는다. 작성자가 Word 문서에 `[AGENT_SCOPE] iga-analyst, code-assistant` 형식으로 명시. 누락 시 변환기는 빈 배열 `[]` 으로 두고 경고.

### 11.5 doc_type 결정

Word 문서에 `[DOC_TYPE] manual` 등으로 명시. 누락 시 다음 휴리스틱 적용:

| 휴리스틱 | doc_type |
|---------|----------|
| 제목에 "보고서"/"Report" 포함 | report |
| 제목에 "가이드"/"매뉴얼"/"Guide" 포함 | manual |
| 제목에 "사양"/"규격"/"Spec" 포함 | spec |
| Source format이 pptx | slide |
| Source format이 xlsx + 표가 90% 이상 | data |
| 외 | report (기본값) |

---

## 12. 비표준 문서 처리 정책

| 상황 | 처리 정책 |
|------|----------|
| Heading 스타일이 전혀 없는 문서 | **변환 거부** + 오류 메시지: "Heading 스타일을 적용해 주세요" |
| 일부 헤딩만 스타일 미적용 (굵은 글씨로 흉내) | **경고** + 휴리스틱(폰트 크기 + 굵기) 시도, 결과는 검수 필요 표시 |
| Heading 1 없이 Heading 2부터 시작 | 가상 Heading 1 "본문" 자동 추가 + 경고 |
| 그림에 캡션 없음 | **경고** + 자동 캡션 `"Figure N: (캡션 누락 — 검수 필요)"` |
| 표에 캡션 없음 | **경고** + 자동 캡션 `"Table N: (캡션 누락 — 검수 필요)"` |
| 캡션이 그림 위에 있음 (원칙 위반) | **경고** + 그래도 연결 |
| 표 헤더 행 식별 불가 (병합 등) | **경고** + 첫 행을 헤더로 강제 |
| 매크로 포함 | 무시하고 진행 + 경고 |
| 암호화된 docx | **변환 거부** + 오류 메시지 |
| 손상된 docx | **변환 거부** + 오류 메시지 |

### 12.1 경고 로그 형식

모든 경고는 변환 결과 JSON과 함께 별도 로그 파일에 기록:

```
output/HE-CAE-2026-0000000001.json
output/HE-CAE-2026-0000000001.warnings.log    ← 경고 모음
```

로그 형식:
```
[WARN] line 152 (단락 47): 그림 1에 캡션이 없습니다. 자동 캡션을 사용했습니다.
[WARN] line 203 (표 3): 헤더 행이 병합되어 있어 두 번째 행을 헤더로 사용했습니다.
```

### 12.2 변환 거부 시 동작

JSON을 생성하지 않고 오류 코드와 메시지를 stdout으로 출력 후 종료. 외부 시스템(예: 자동 수집 파이프라인)이 재시도 또는 사람 검수로 라우팅하기 위한 기준.

---

## 13. 변환 파이프라인 의사코드

```python
def convert(docx_path, team, group, year):
    # 1. 사전 검증
    validate_input_format(docx_path)        # 12장 정책 적용
    
    # 2. 문서 로드
    doc = python_docx.Document(docx_path)
    
    # 3. doc_id 발급
    doc_id = issue_new_doc_id(team, group, year)
    
    # 4. meta 구성
    meta = build_meta(doc, doc_id, docx_path)
    
    # 5. 본문 순회 — Heading/단락/표/그림 단계별 처리
    sections_tree = []
    figures = []
    tables = []
    sources = []
    current_path = []   # 헤딩 스택 [(level, section_obj), ...]
    
    fig_counter = 0
    tbl_counter = 0
    src_counter = 0
    
    for element in iterate_body_in_order(doc):
        if is_heading(element):
            level = detect_heading_level(element)
            section_id = next_section_id(current_path, level)
            section = make_section(section_id, level, heading_title(element))
            attach_to_tree(sections_tree, current_path, section, level)
            current_path = update_stack(current_path, level, section)
        
        elif is_paragraph(element):
            if is_special_marker(element):    # [SUMMARY], [TAGS], [SOURCES] 등
                handle_marker(element, meta, sources)
                continue
            current_section = top_of(current_path)
            current_section.content += paragraph_text(element) + "\n\n"
        
        elif is_table(element):
            if is_sources_marker_table(element):
                src_counter, sources = parse_sources_table(element, doc_id, src_counter)
            else:
                tbl_counter += 1
                tbl = extract_table(element, doc_id, tbl_counter)
                tables.append(tbl)
                top_of(current_path).table_refs.append(tbl["id"])
        
        elif is_figure(element):
            fig_counter += 1
            fig = extract_figure(element, doc_id, fig_counter)
            figures.append(fig)
            top_of(current_path).figure_refs.append(fig["id"])
        
        elif is_caption(element):
            attach_caption_to_recent_figure_or_table(element, figures, tables)
    
    # 6. 본문 텍스트의 figure/table 참조 추가 등록 (9.2절)
    augment_refs_from_text(sections_tree, figures, tables)
    
    # 7. 자동 생성 필드 채움
    if not meta.get("summary"):
        meta["summary"] = llm_generate_summary(sections_tree)
    if not meta.get("tags"):
        meta["tags"] = llm_generate_tags(sections_tree)
    
    # 8. toc 생성
    toc = flatten_toc(sections_tree, max_level=3)
    
    # 9. 최종 JSON 조립
    output = {
        "schema_version": "1.0",
        "meta": meta,
        "toc": toc,
        "sections": sections_tree,
        "figures": figures,
        "tables": tables,
        "sources": sources
    }
    
    # 10. 사후 검증
    validation_result = validate_output(output)   # 15장 정책 적용
    
    # 11. 저장
    write_json(output, f"output/{doc_id}.json")
    write_warnings_log(f"output/{doc_id}.warnings.log")
    
    return output, validation_result
```

---

## 14. 사전 검증

변환 시작 전 Word 파일의 적합성을 확인.

### 14.1 거부 항목 (변환 중단)

- [ ] 파일이 .docx 포맷인가 (확장자 + ZIP 매직 바이트 확인)
- [ ] 암호화되어 있지 않은가
- [ ] 손상되지 않았는가 (python-docx 로드 성공)
- [ ] Heading 스타일을 사용한 단락이 최소 1개 이상 있는가

### 14.2 경고 항목 (변환 진행)

- [ ] 모든 그림에 캡션이 있는가
- [ ] 모든 표에 캡션이 있는가
- [ ] Heading 1 → 2 → 3 순서가 건너뛰지 않는가 (예: 1 다음에 바로 3 등장)
- [ ] [SUMMARY], [TAGS], [DOC_TYPE], [AGENT_SCOPE] 마커가 있는가

---

## 15. 사후 검증

생성된 JSON이 [json_schema_rules.md](./json_schema_rules.md) 의 검증 체크리스트(13장)를 통과하는지 자동 확인.

### 15.1 자동 검증 항목 (반드시 통과)

```python
def validate_output(json_obj):
    errors = []
    
    # 최상위 키
    required_top = {"schema_version", "meta", "toc", "sections",
                    "figures", "tables", "sources"}
    missing = required_top - json_obj.keys()
    if missing:
        errors.append(f"최상위 키 누락: {missing}")
    
    # doc_id 형식
    if not re.match(r"^[A-Z]{2,4}-[A-Z]{2,5}-\d{4}-\d{6}$", json_obj["meta"]["doc_id"]):
        errors.append(f"doc_id 형식 오류: {json_obj['meta']['doc_id']}")
    
    # tags 개수
    if len(json_obj["meta"]["tags"]) < 2:
        errors.append("tags가 2개 미만")
    
    # summary 길이
    if len(json_obj["meta"]["summary"]) < 30:
        errors.append("summary가 너무 짧음 (30자 미만)")
    
    # 참조 일치
    fig_ids = {f["id"] for f in json_obj["figures"]}
    referenced_fig_ids = collect_all_figure_refs(json_obj["sections"])
    if fig_ids != referenced_fig_ids:
        errors.append(f"figure_refs와 figures.id 불일치: "
                     f"{fig_ids ^ referenced_fig_ids}")
    
    # tables도 동일하게 검증
    # sources의 file_path 존재 여부 (선택, 네트워크 접근 가능 시)
    
    # headers와 rows 길이
    for tbl in json_obj["tables"]:
        h_len = len(tbl["headers"])
        for i, row in enumerate(tbl["rows"]):
            if len(row) != h_len:
                errors.append(f"표 {tbl['id']}, 행 {i}: "
                             f"길이 {len(row)} ≠ headers {h_len}")
    
    return errors
```

### 15.2 검증 실패 시

- 에러 1개 이상 → JSON은 생성하되 별도 폴더 `output/invalid/`에 저장
- 검수 큐에 등록 (사람이 확인 후 재변환)

---

## 16. 알려진 한계

| 한계 | 대응 |
|------|------|
| Heading 스타일 미사용 문서는 사실상 변환 불가 | 사전 교육 + 표준 템플릿 배포 |
| 그림 내부의 텍스트(다이어그램의 라벨)는 추출 불가 | 캡션을 충분히 상세하게 작성하도록 가이드 |
| Excel에서 복사한 표는 Word 표가 아닌 이미지로 들어올 수 있음 | 사전 검증에서 감지 후 경고 |
| 같은 외부 CAD 파일을 두 문서가 참조해도 sources의 id는 서로 다름 | hash_sha256 기준으로 동일성 사후 판정 |
| LLM 기반 summary·tags 생성은 LLM 호출 비용 발생 | 작성자가 [SUMMARY]/[TAGS] 마커로 직접 작성 권장 |
| 한글 Word의 일부 사용자 지정 스타일은 자동 인식 어려움 | base_style 추적으로 우회, 안 되면 경고 |

---

## 17. Excel 변환 (별도 문서)

Excel→JSON 변환 규칙은 [excel_to_json_conversion_rules.md](./excel_to_json_conversion_rules.md) 에 분리되어 있다.

해당 문서는 다음을 다룬다.

- 표 데이터(`type=DATA`) 로의 직접 변환 규칙
- Excel 작성 5원칙 (시트 상단 고정, 헤더 단위 명시, 셀 병합 금지, 색상 별도 컬럼화, 1시트 1주제)
- CLI 옵션 (`--mode`, `--header-row`, `--start-cell`, `--skip-blank-rows`, `--skip-empty`, `--infer-units`)
- 불규칙 Excel 처리 절차 (자동 탐지 + `--start-cell` 보정)
- 변환기별 매핑 표 및 알려진 한계

변환기 구현은 `src/excel_converter/` 의 openpyxl 기반 모듈을 따른다.

---

## 18. 변환기별 매핑 표

각 Word 요소가 어느 XML 위치에서 발견되어 어느 JSON 출력으로 매핑되는지 한눈에 정리한다. 이 표는 [json_schema_rules.md](./json_schema_rules.md) 의 8-키 구조 (`schema_version` · `meta` · `toc` · `sections` · `figures` · `tables` · `sources` · `attachments`) 및 `record_sections` 컬럼과 직접 대응된다.

**최상위 출력 키 (참조용)** — `converter/models.py:218-228` `ConversionResult.to_dict()`:

| 키 | 설명 | 비고 |
|---|---|---|
| `schema_version` | "1.0" 고정 | normalizer 가 검증 |
| `meta` | 문서 신원 + 생애주기 | 4장 / json_schema_rules §4 |
| `toc` | 목차 (검증 보조) | DB 미저장 |
| `sections` | 본문 트리 (`record_sections` 행으로 평탄화) | json_schema_rules §6 |
| `figures` | (deprecated) — `attachments[kind=image]` 로 통합 중 | 하위호환 위해 유지 |
| `tables` | 표 데이터 (`tables[]` 1행 = 하나의 표) | json_schema_rules §8 |
| `sources` | 외부 파일 참조 (시뮬레이션 입출력 등) | json_schema_rules §10 |
| `attachments` | **모든 비텍스트 자원** (image · OLE · chart · audio · video · external_link · archive · other). `kind` 9종은 7.6절 + json_schema_rules §9 참조 | normalizer → `record_attachments` 테이블 |

| Word 요소 | XML 위치 | JSON 출력 위치 |
| --------- | -------- | -------------- |
| Heading 1 | `w:pStyle="Heading1"` | `sections[].level=1` |
| Heading 2 | `w:pStyle="Heading2"` | `sections[].level=2` |
| Heading 3 | `w:pStyle="Heading3"` | `sections[].level=3` |
| 단락 | `w:p` (no special style) | `sections[].blocks[type=paragraph]` |
| 표 | `w:tbl` | `tables[]` + `sections[].table_refs[]` |
| 인라인 그림 | `a:blip` | `attachments[kind=figure]` + `sections[].blocks[type=figure]` |
| OLE 객체 | `w:object/o:OLEObject` | `attachments[kind=document/spreadsheet/...]` |
| 캡션 | next paragraph `w:pStyle="Caption"` | `tables[].caption` 또는 `attachments[].caption` |
| 자동 번호 매겨진 헤딩 | `numbering.xml` + Heading style | `section_id` (sequential `1`, `1.1`, `2`, ...) |
| 리스트(불릿/번호) | `w:p` + `w:numPr` | `sections[].blocks[type=list]` |
| 코드/등폭 단락 | `w:p` (style `Code`/`Source` 또는 등폭 폰트) | `sections[].blocks[type=code]` |
| 하이퍼링크 | `w:hyperlink` | 본문 `[text](url)` 인라인 |

표 행 수: 12.

---

## 19. 휴리스틱 헤딩 감지 알고리즘

Heading 스타일이 누락된 단락을 텍스트 패턴으로 복구하는 알고리즘이다. `converter/docx_parser.py` 가 이 사양의 권위 있는 구현이다.

### 19.1 Pre-scan (1차 통과)

```text
for each paragraph p in document:
    if p has Heading{1,2,3} style:
        confirmed[p] = level
        continue
    if regex_match(p.text, r"^(\d+(?:\.\d+){0,2})\.?\s+(.+)$"):
        candidates.append((p, parsed_number, parsed_title))
```

### 19.2 Confirm (2차 통과 — 등급별 규칙)

- **level-2/3 candidates** (`N.M` 또는 `N.M.K` 형식)는 **항상 확정**한다.
- **level-1 candidates** (`N` 단독)는 다음 중 하나일 때만 확정한다:
  - 같은 prefix 의 sub-heading (`N.1`, `N.2`, ...) 이 문서 뒷부분에 존재.
  - 또는 19.3 의 sequence rule 에 의해 보조 확정.

### 19.3 Sequence rule (확장 확정)

- 마지막으로 확정된 level-1 번호를 `last_l1` 이라 하자.
- 이후 등장하는 후보 중 번호가 정확히 `last_l1+1`, `last_l1+2`, ... 처럼 **연속**이면 추가로 확정한다.
- 비연속(예: `last_l1=3` 인데 `7` 등장)은 본문 단락으로 폐기.

### 19.4 결과 적용

확정된 후보는 `level` 과 `section_id` 를 부여받고 4.6 의 자동 부여와 동일한 트리에 삽입된다. 확정 실패한 후보는 일반 본문 단락으로 처리되며 경고 로그를 남기지 않는다 (오탐 방지).

### 19.5 한계

- 형식이 일정하지 않은 문서(예: 같은 레벨에 `1)`, `1.`, `Chapter 1` 혼재)는 부분 복구만 가능. 12장 비표준 처리 정책 적용.
- 휴리스틱이 동작한 문서는 출력 JSON 의 `meta.heading_source = "heuristic"` 으로 표시.

---

## 20. 검증 후 DB 적재 체크리스트

`api.ingest.normalizer` 가 변환된 JSON 을 DB 에 삽입하기 직전에 수행하는 최종 검증이다. 한 항목이라도 실패하면 적재 거부 후 `output/invalid/` 로 격리한다.

1. **id 형식 유효성** — `^DOC-[A-Z]{2,4}-[A-Z]{2,6}-\d{4}-\d{6}$` 정규식 통과.
2. **data_type='DOC'** — 정규화 후 최종 `Record.data_type` 이 `"DOC"` 인지 확인.
3. **meta.title non-empty** — 공백 제거 후 길이 ≥ 1.
4. **sections recursion depth ≤ 3** — `sections[].sections[].sections[]` 까지만 허용 (level=4 이상 거부).
5. **figure_refs 해소** — 모든 `sections[].figure_refs[]` 의 id 가 `figures[].id` 또는 `attachments[kind=figure].id` 에 존재.
6. **table_refs 해소** — 모든 `sections[].table_refs[]` 의 id 가 `tables[].id` 에 존재.
7. **첨부 캡션 비어 있지 않음** — `attachments[].caption` 의 trim 길이 ≥ 1 (자동 캡션 허용).
8. **첨부 file_path POSIX** — 백슬래시 미포함 + 선행 슬래시 미포함 + `{doc_id}/` 로 시작.
9. **content_hash 계산됨** — `records.content_hash` 가 `sha256(canonical_json(records.content))` 와 일치.
10. **schema_version 일치** — 출력의 `schema_version` 이 정규화기가 기대하는 값(`"1.0"`)과 일치.
11. **tags 최소 2개** — `meta.tags` 길이 ≥ 2 (LLM 태깅 실패 시 기본 태그 보강).
12. **summary 최소 길이** — `meta.summary` 길이 ≥ 30자.

검증 항목 수: 12.

실패 시 동작은 `json_schema_rules.md` 13장 검증 체크리스트와 일관되게 처리한다.

---

## 부록 A: 권장 표준 마커 형식

작성자가 Word 문서에 다음 마커를 포함하면 변환 품질이 향상된다.

```
[DOC_TYPE] manual
[SUMMARY] KooRemapper v1.3.0의 IGA 기능 사용 가이드. NURBS 기반 Trimmed Volume 방식으로...
[TAGS] IGA, LS-DYNA, NURBS, KooRemapper, FEM, 솔리드해석
[AGENT_SCOPE] iga-analyst, code-assistant

[SOURCES]
| type | format  | file_path                                          | description       |
| ---- | ------- | -------------------------------------------------- | ----------------- |
| MCAD | CATPart | //file-server/PLM/HE/CAE/2026/bracket_v3.CATPart   | 브라켓 v3 형상     |
| SIM  | k       | //file-server/PLM/HE/CAE/2026/block_2x2x1.k        | IGA 변환 베이스    |
```

이 마커들은 본문 첫머리(Heading 1 직전) 또는 끝머리(부록 다음)에 위치시킨다.

---

## 부록 B: 변환기 CLI 사용 예시

```bash
# 기본 변환
converter.py iga_guide.docx --team HE --group CAE --year 2026

# 결과
output/
├── HE-CAE-2026-0000000001.json
├── HE-CAE-2026-0000000001.warnings.log
└── HE-CAE-2026-0000000001/
    ├── F001.png
    └── F002.png

# 옵션
--no-llm                 # summary/tags 자동 생성 생략 (작성자 수동 입력 강제)
--strict                 # 경고 발생 시 변환 거부
--postgres-url=...       # doc_id를 DB에서 발급
--validate-sources       # sources의 file_path 존재 여부 확인
```

---

*본 변환 규칙서는 [json_schema_rules.md](./json_schema_rules.md) 의 v1.3 출력 스키마 (호환성: v1.0 JSON 페이로드 그대로 사용 가능)를 기준으로 작성되었으며, 스키마가 변경될 때 함께 갱신됩니다.*

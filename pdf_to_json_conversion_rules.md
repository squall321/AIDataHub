# PDF → JSON 변환 규칙서
## 자동화 프로그램 구현 가이드 v1.3 (코드/룰 완전 동기)

> 작성일: 2026-05-08
> 적용 대상: PDF 문서를 [json_schema_rules.md](./json_schema_rules.md) 스키마로 변환하는 자동화 프로그램
> 시작점: [`CONVERSION_RULES_INDEX.md`](./CONVERSION_RULES_INDEX.md)
> 자매 문서 (모두 동일 JSON 스키마 출력):
>
> - [json_schema_rules.md](./json_schema_rules.md) — JSON 스키마 전반 (모든 변환기 공통)
> - [word_to_json_conversion_rules.md](./word_to_json_conversion_rules.md) — Word 변환 규칙
> - [excel_to_json_conversion_rules.md](./excel_to_json_conversion_rules.md) — Excel 변환 규칙
> - [ppt_to_json_conversion_rules.md](./ppt_to_json_conversion_rules.md) — PPT 변환 규칙
> - [md_to_json_conversion_rules.md](./md_to_json_conversion_rules.md) — Markdown 변환 규칙
> - [html_to_json_conversion_rules.md](./html_to_json_conversion_rules.md) — HTML 변환 규칙

---

## 0. 코드 정합 노트 (필독)

본 문서는 [`api_server/src/pdf_converter/`](./api_server/src/pdf_converter/) 의 실제 출력을 단일 진실 공급원으로 한다.

| 항목 | 변환기 출력 / 코드 위치 | normalizer 흡수 / DB |
|---|---|---|
| 식별자 | `meta.doc_id` (`pdf_converter/core.py:635`) | `meta.doc_id` 우선 → `records.id` |
| 에이전트 | CLI `--agents` → `meta.agent_scope` (`pdf_converter/core.py:649`) | `meta.agent_scope` 우선, `raw.agents` 폴백 → `records.agents` |
| PDF own-extras | `meta.pdf` (`{page_count, heading_strategy, creator, producer, creation_date, modification_date, ocr_pages?}`) `pdf_converter/core.py:651-664` | `records.content` JSONB 보존 |
| OCR 적용 페이지 | `meta.pdf.ocr_pages: [page_no, ...]` (`--ocr` 사용 시) | 동상 |
| 분류/생애주기 (0006 10개) | CLI `--extra-meta '{"classification": "..."}'` 또는 PDF `/Info.*` 매핑으로 `meta.*` 채우면 normalizer 흡수 (`normalizer.py:103-153`) ✅ | `records.classification` 등 |
| 0007 agent-discovery 자동 채움 | `agent_hints`/`query_examples`/`access_pattern` 자동 생성 (`pdf_converter/core.py:691-705`) | `records.agent_hints` 등 |

(v1.2 의 "KNOWN GAP" 은 v1.3 커밋 `c2c66c6` 에서 해소.)

---

## 목차

1. [목적과 한계](#1-목적과-한계)
2. [입력 / 출력 명세](#2-입력--출력-명세)
3. [라이브러리 선택 근거](#3-라이브러리-선택-근거)
4. [텍스트 추출 + 페이지 단위 처리](#4-텍스트-추출--페이지-단위-처리)
5. [헤딩 감지 우선순위](#5-헤딩-감지-우선순위)
6. [표 추출](#6-표-추출)
7. [이미지 추출](#7-이미지-추출)
8. [PDF 메타데이터 매핑](#8-pdf-메타데이터-매핑)
9. [변환기별 매핑 표](#9-변환기별-매핑-표)
10. [PDF 작성 표준 (작성자용 가이드)](#10-pdf-작성-표준-작성자용-가이드)
11. [비표준 PDF 처리](#11-비표준-pdf-처리)
12. [검증 후 DB 적재 체크리스트](#12-검증-후-db-적재-체크리스트)
13. [라이브러리 의존성 + CLI 사용법](#13-라이브러리-의존성--cli-사용법)
14. [알려진 한계](#14-알려진-한계)

---

## 1. 목적과 한계

### 1.1 목적

본 문서는 PDF 문서(`.pdf`)를 [json_schema_rules.md](./json_schema_rules.md) 스키마(`data_type = "DOC"`)에 맞는 JSON 으로 자동 변환하는 프로그램의 결정 사항을 명시한다. 구현은 `src/pdf_converter/` (Python 3.12, `pdfplumber` + `pypdf` 기반).

### 1.2 한계 — PDF 는 정보 손실이 가장 큰 포맷

PDF 는 **인쇄 출력 포맷**이다. 의미 구조(섹션, 헤딩, 표 셀)가 명시되지 않고, 좌표 기반의 글자/선만 남는다. 이 때문에 변환 품질은 다음 요인에 강하게 의존한다.

| 요인 | 변환 품질 |
|------|-----------|
| PDF 에 outline(북마크)이 있음 | 헤딩 매우 정확 |
| Word 에서 PDF 로 출력 (텍스트 검색 가능) | 본문 정확, 표는 확률적 |
| 스캔 PDF (이미지로만 구성) | **OCR opt-in 으로 지원** — `--ocr` 플래그 + Tesseract / pdf2image (poppler) 설치. 13.4절 참조. |
| 다단(2-column) 레이아웃 | 본문 순서가 뒤섞일 수 있음 |
| 폼 PDF / 보호된 PDF / 수식 / 차트 | 부분 지원 또는 미지원 |

**원칙: PDF 는 가급적 직접 만들지 않는다. Word/MD 로 작성한 후 PDF 로 출력하라.** [10. PDF 작성 표준](#10-pdf-작성-표준-작성자용-가이드) 참조.

---

## 2. 입력 / 출력 명세

### 2.1 입력

- **포맷**: `.pdf` (PDF 1.4 이상 권장)
- **권장 조건**:
  - 텍스트가 검색 가능 (이미지로만 된 PDF 불가)
  - outline(북마크) 포함
  - 표는 그리드(셀이 분리된 형태) 로 작성

### 2.2 출력

- 단일 JSON 파일 (스키마 v1.0, `data_type = "DOC"`, `meta.source_format = "pdf"`).
- ID 형식:
  - `meta.doc_id` = `DOC-{div}-{group}-{year}-{seq:06d}`
  - 그림: `{doc_id}-F{nnn}`
  - 표: `{doc_id}-T{nnn}`
  - 첨부: `{doc_id}-A{nnn}`
- `meta.pdf` 부가 정보:
  - `page_count` — 전체 페이지 수
  - `heading_strategy` — `outline` / `pattern+fontsize` / `fontsize` / `none` 중 하나
  - `creator` / `producer` — PDF 생성 도구
  - `creation_date` / `modification_date` — ISO 8601

### 2.3 처리하지 않는 것 (범위 밖)

- 스캔 PDF — `--ocr` opt-in 으로 지원 (§13.4 의존성 표 참조). 의존성 (Tesseract + pdf2image + poppler) 미설치 시 자동 skip + 경고.
- 수식 (LaTeX, MathML) → 평문 추출 시 깨짐
- 차트 (벡터/래스터) → 이미지로 추출되지만 데이터 없음
- 폼 필드 (입력 양식) → 베스트 노력으로 attachment(kind=other)
- 다단 레이아웃 → 위→아래 순서로 평탄화 (정확도 제한)
- 인라인 서식(볼드/이탤릭) → 모두 무시 (스키마는 평문)

---

## 3. 라이브러리 선택 근거

| 라이브러리 | 역할 | 선택 이유 |
|-----------|------|-----------|
| `pdfplumber>=0.11.0` | 텍스트 라인 + 표 추출, char-level 메타(폰트 크기) | 표 추출 품질이 가장 우수, char 단위 메타데이터 제공 |
| `pypdf>=4.0.0` | /Info 메타데이터 + /Outlines(북마크) + 페이지 수 | outline 추출이 깔끔, 표준 라이브러리에 가장 가까움 |

두 라이브러리 모두 **순수 Python** 으로 Windows/Linux/macOS 에서 동일하게 동작한다 (네이티브 의존 없음).

대안 검토:
- `PyMuPDF (fitz)` — 빠르지만 AGPL 라이선스 (사내 폐쇄형 사용 시 제약).
- `pdfminer.six` — pdfplumber 의 백엔드 — 직접 사용보다 pdfplumber API 가 편리.
- `Camelot` — 표 추출 특화 but Ghostscript 의존 — 크로스플랫폼 배포 부담.

---

## 4. 텍스트 추출 + 페이지 단위 처리

### 4.1 라인 단위 클러스터링

`pdfplumber.Page.chars` 는 글자별 좌표 + 폰트 메타를 제공한다. 변환기는 **같은 `top` 좌표(±1pt)** 의 글자들을 하나의 라인으로 묶고, 라인 별로:

- `text` — 글자들을 좌→우 순서로 이어붙인 평문
- `avg_font_size` — 라인 안 글자들의 평균 폰트 크기 (헤딩 휴리스틱에 사용)
- `is_bold` — 폰트 이름에 "Bold" 가 포함되면 True
- `y_top` — 페이지 상단으로부터의 y 좌표
- `page_number` — 1-base 페이지 번호

### 4.2 페이지 순서 = 본문 순서

변환기는 **페이지 1 → 마지막 페이지** 순으로 라인을 평탄화한다. 한 페이지 안에서는 pdfplumber 의 기본 정렬(top → x0)을 따른다.

### 4.3 다단(2-column) 한계

PDF 는 다단 정보를 명시하지 않으므로, 좌→우 단을 자동 분리할 수 없다. 다단 레이아웃은:

- pdfplumber 의 평탄화 (`top → x0`) 가 좌단 → 우단을 행 단위로 섞을 수 있다.
- 변환 후 본문 순서가 뒤섞이면 **작성자가 단일 단(single column) 으로 PDF 를 재출력** 해야 한다.

---

## 5. 헤딩 감지 우선순위

PDF 에는 명시적인 "헤딩" 개념이 없다. 변환기는 다음 우선순위로 헤딩을 추론한다.

### 5.1 우선순위 1 — outline (북마크)

PDF 에 `/Outlines` 가 있으면 그것을 권위적 헤딩 소스로 사용한다.

- outline 항목의 중첩 깊이 → `section.level` (1 ≤ level ≤ 3, 더 깊으면 collapse).
- outline 의 destination page → 본문 라인 매칭에 사용.
- outline 항목의 title 이 `1.2 제목` 패턴이면 section_id 추출.

`meta.pdf.heading_strategy = "outline"`.

### 5.2 우선순위 2 — 본문 패턴 (`1. 개요` 스타일)

outline 이 없을 때, 본문 라인 중 다음 패턴에 매칭되는 것을 헤딩 후보로:

```regex
^(\d+(?:\.\d+){0,4})\.?\s+(.+)$
```

- 점 개수 + 1 = level (`1` → 1, `1.2` → 2, `1.2.3` → 3, 그 이상은 3 으로 collapse).
- 매칭 텍스트 길이가 80자 초과면 본문 단락으로 간주 (헤딩 후보 제외).

이 패턴은 Word/MD 변환기와 **완전히 동일**하다 — 작성자가 일관된 번호 규칙(`1.`, `1.2`, `1.2.3`)을 지키면 4가지 변환기가 동일한 ID 를 생성한다.

### 5.3 우선순위 3 — 폰트 크기 휴리스틱 (마지막 수단)

본문 평균 폰트 크기를 계산하고, 라인의 평균 폰트 크기가 **본문 평균 × 1.2 이상**이면 헤딩 후보로 추가한다.

- 큰 글씨 사이즈를 정렬해 상위 3개를 level 1, 2, 3 에 매핑.
- 그 외는 모두 level 3 으로 collapse.
- 이 휴리스틱은 표지/장 제목(번호 없음) 을 잡는 데 유용하지만 오탐 가능 — 패턴 매칭 후 **추가 후보**로만 사용.

### 5.4 결합 결과 — `meta.pdf.heading_strategy`

| 시나리오 | strategy 값 |
|---------|------------|
| outline 있음 | `outline` |
| outline 없음 + 패턴 매칭 + 폰트 크기 후보 | `pattern+fontsize` |
| outline 없음 + 패턴 매칭 0건 + 폰트 크기 후보만 | `fontsize` |
| 헤딩 후보 0건 (가상 '본문' 섹션 1개) | `none` |

`none` 일 때는 `warnings` 에 명시 경고를 남긴다.

---

## 6. 표 추출

`pdfplumber.Page.extract_tables()` 가 셀 경계(grid) 를 분석해 표를 list[list[str]] 형태로 반환한다.

### 6.1 변환 규칙

- raw 표의 첫 행 → `headers`
- 나머지 행 → `rows`
- `None` 셀 → 빈 문자열 `""` 로 정규화
- 빈 행/완전히 빈 표는 무시 (경고 발생)
- 캡션은 자동 생성: `Table {N} (page {P})` — 작성자가 추후 손보기 위한 placeholder.

### 6.2 표 ID 와 위치 참조

- 표 ID: `{doc_id}-T{nnn}` (3자리 zero-pad)
- 표가 추출된 페이지의 **마지막 활성 섹션** 을 `section_ref` 로 사용
- 본문 흐름에는 `block.type = "table"` + `ref = "{tbl_id}"` 블록을 추가
- 해당 섹션의 `table_refs[]` 에도 ID 등록

### 6.3 표 추출 한계

- **셀 경계가 없는 표** (공백으로만 정렬된 표) 는 인식 불가 — 작성 단계에서 **그리드 표**를 사용해야 한다.
- **셀 병합**(merged cell) 은 부분 지원 — 같은 값을 여러 셀에 복제해 반환.
- **회전된 표 / 비스듬한 표** 는 미지원.
- **이미지로 캡처된 표** 는 OCR 미지원으로 본문에 추출되지 않음.

---

## 7. 이미지 추출

### 7.1 현재 동작 (베스트 노력)

`pdfplumber.Page.images` 메타로 페이지 별 이미지 개수를 셈하고, 각 이미지에 대해:

- `figures[]` 에 placeholder 등록 (caption 누락 — 검수 필요)
- `attachments[kind=figure]` 에 `extra.page_number` 와 함께 등록

이미지 바이너리는 **현재 별도 디스크 추출하지 않는다** (placeholder 만 등록). 본문 텍스트 추출이 부족한 페이지는 `--ocr` opt-in 으로 OCR 텍스트를 채우며, 그 외 이미지 바이너리 분리 추출은 향후 검토.

### 7.2 캡션

PDF 는 그림 캡션을 그림 객체와 연결하지 않으므로, 자동으로 `Figure {N} (page {P}: 캡션 누락 — 검수 필요)` 라는 placeholder 가 들어간다. **검수자가 수동으로 채워야 한다.**

`warnings` 에 페이지 번호와 함께 명시 경고를 남긴다.

---

## 8. PDF 메타데이터 매핑

PDF `/Info` 딕셔너리(`pypdf.PdfReader.metadata`) 의 필드를 다음과 같이 매핑한다.

| /Info 키 | meta 키 | 변환 |
|----------|---------|------|
| `/Title` | `meta.title` | 그대로 |
| `/Author` | `meta.author` | 그대로 |
| `/Subject` | `meta.summary` | 그대로 (본문 요약으로 사용) |
| `/Keywords` | `meta.tags` | `,` `;` `/` `\|` 구분 → list[str], CLI `--tags` 와 병합 |
| `/CreationDate` | `meta.created` | `D:YYYYMMDDHHMMSS+TZ` → ISO 8601 → 날짜만 (`YYYY-MM-DD`) |
| `/ModDate` | `meta.modified` | 동일 |
| `/Creator` | `meta.pdf.creator` | 그대로 |
| `/Producer` | `meta.pdf.producer` | 그대로 |

### 8.1 누락 처리

- `Title` 누락 → 첫 헤딩 제목 → 파일명(stem) 순으로 fallback.
- `Author` 누락 → 빈 문자열, `warnings` 에 경고.
- `Subject` 누락 → 빈 `meta.summary`, `warnings` 에 경고.
- `Keywords` 누락 → CLI `--tags` 만 사용, 둘 다 없으면 빈 리스트 + 경고.

### 8.2 CLI 우선순위

CLI `--team/--group/--year/--seq/--agents/--tags` 는 PDF 메타보다 **우선**이다. PDF /Info 에 의존하면 안 되는 정책 필드 (팀/그룹 코드 등) 는 CLI 에서만 받는다.

---

## 9. 변환기별 매핑 표

| PDF 요소 | JSON 매핑 | 비고 |
|----------|-----------|------|
| /Outlines item (depth N) | `sections[].level = min(N, 3)` | 깊이 4 이상은 level 3 으로 collapse |
| 본문 라인 `1.2 제목` | `sections[].id = "1.2"` | level = 점 개수 + 1 |
| 본문 라인 폰트 크기 ≥ body_avg × 1.2 | 헤딩 후보 (last resort) | level 은 큰 사이즈 순 매핑 |
| 일반 본문 라인 | `block.type = "paragraph"` | text 만 보존 (서식 제거) |
| `extract_tables()` 결과 | `tables[]` 등록 + `block.type=table` ref | 첫 행 = headers |
| `Page.images` 항목 | `figures[]` + `attachments[kind=figure]` | 바이너리 미추출 (placeholder) |
| /Info.Title | `meta.title` | 누락 시 첫 헤딩 → 파일명 |
| /Info.Author | `meta.author` | |
| /Info.Subject | `meta.summary` | |
| /Info.Keywords | `meta.tags` (병합) | 구분자 자동 감지 |
| /Info.CreationDate | `meta.created` | ISO 8601 → YYYY-MM-DD |
| 폼 필드 (현재 미구현) | `attachments[kind=other]` | 향후 지원 |

---

## 10. PDF 작성 표준 (작성자용 가이드)

> **PDF 는 정보 손실이 가장 큰 포맷이다. 가능하면 PDF 를 직접 만들지 말고, Word/MD 로 작성 후 PDF 로 출력하라.**

### 10.1 권장 작성 절차

1. **Word 또는 Markdown 으로 본문을 작성**한다.
2. Word: 헤딩 스타일을 정확히 부여 (Heading 1/2/3) — 자동 outline 생성됨.
3. PDF 로 출력 시 **"북마크 포함" 옵션 활성화**.
4. 표는 Word 표 도구로 작성 (수기로 그린 선이 아님).
5. 폰트는 검색 가능한 임베드 폰트 사용 (이미지화 금지).

### 10.2 PDF 직접 작성 시 필수 조건

부득이하게 PDF 를 직접 만들 경우:

| 조건 | 이유 |
|------|------|
| 북마크(/Outlines) 작성 | 헤딩 추론 우선순위 1 — 가장 신뢰 |
| 검색 가능 텍스트 (이미지 PDF 금지) | 본문 추출 가능 |
| 헤딩은 본문 폰트의 1.2배 이상 크기 | 폰트 크기 휴리스틱 fallback |
| 헤딩 번호: `1.`, `1.2`, `1.2.3` 일관 | 패턴 매칭 |
| 표는 그리드 표 (셀 경계 명시) | pdfplumber 표 추출 |
| 단일 단 (single-column) 레이아웃 | 본문 순서 보장 |
| /Info 메타 (Title/Author/Subject/Keywords) 작성 | meta 자동 채움 |

### 10.3 금지 사항

| 금지 | 이유 |
|------|------|
| 스캔 PDF | OCR 미지원 |
| 암호 보호 PDF | 변환 거부됨 |
| 다단 + 그래픽 헤더가 섞인 잡지 스타일 | 본문 순서 깨짐 |
| 표를 ASCII 아트(공백 정렬)로 작성 | 표 추출 불가능 |
| 수식(LaTeX) 을 텍스트로 직접 입력 | 평문 깨짐 |

---

## 11. 비표준 PDF 처리

### 11.1 스캔 PDF (이미지로만 구성) — OCR opt-in

- `pdfplumber.Page.chars` 가 비어 있음 → 변환기가 `warnings` 에 경고.
- `meta.pdf.heading_strategy = "none"`.
- 기본은 텍스트 추출 우선이며, 추출 결과가 비어있는 페이지는 경고로 남는다.
- `--ocr` 플래그를 켜면 빈/거의 빈 페이지를 자동 감지해 Tesseract 로 OCR 한다 (`pdf_converter/ocr.py`).
  - 임계값: 페이지 텍스트 길이 < `--ocr-min-chars` (기본 5).
  - 언어: `--ocr-lang eng` 또는 `kor` 또는 `eng+kor` (Tesseract 언어 코드).
  - DPI: `--ocr-dpi` (기본 200).
- 의존성 (런타임): `pytesseract` + Tesseract 시스템 바이너리 + `pdf2image` + `poppler` (Windows 별도 설치).
  - 의존성 누락 시 OCR 단계 자동 skip + 경고 (변환 자체는 실패하지 않는다).
- 출력: OCR 텍스트는 해당 페이지의 `sections[].blocks[]` paragraph 로 추가되며, 출처는 `meta.pdf.ocr_pages: [page_no, ...]` 로 기록.

### 11.2 암호 보호 PDF

- `pypdf.PdfReader.is_encrypted == True` → 빈 비밀번호 시도 후 실패하면 **`RuntimeError`** 발생.
- CLI 는 종료 코드 2 로 종료.
- 작성자가 PDF 암호를 제거 후 재시도해야 한다.

### 11.3 폼 PDF (입력 양식)

- 폼 필드는 현재 별도 처리하지 않음.
- 향후: `attachments[kind=other]` 로 폼 필드 메타(이름/값) 보존 예정.

### 11.4 다단(2-column) 레이아웃

- pdfplumber 가 좌→우 순서로 평탄화하지만, 행 단위로 좌단/우단이 섞일 수 있음.
- 본문 순서가 깨지면 작성자가 단일 단으로 재출력해야 한다.
- 향후: `tatr` (Table Transformer) 또는 layout-parser 통합으로 단 분리 자동화 검토.

### 11.5 회전된 페이지

- `Page.rotation` 이 0이 아니면 pdfplumber 가 자동 보정한다.
- 보정 실패 시 본문이 `(rotated)` 형태로 추출될 수 있음 — 검수 필요.

---

## 12. 검증 후 DB 적재 체크리스트

PDF 변환 결과를 DB 에 적재하기 전 다음 항목을 확인한다.

- [ ] `meta.doc_id` 가 정규식 `^DOC-[A-Z]+-[A-Z]+-\d{4}-\d{6}$` 매칭
- [ ] `meta.pdf.heading_strategy` 값이 `outline` 또는 `pattern+fontsize` (그 외면 작성자 점검 필요)
- [ ] `sections` 깊이가 3 이하 (4 이상이 있으면 변환 버그)
- [ ] 표가 있다면 `tables[].headers` 가 비어 있지 않음
- [ ] 그림이 있다면 `figures[].caption` 의 placeholder ("캡션 누락 — 검수 필요") 가 사람이 검수한 캡션으로 교체됨
- [ ] `warnings` 가 비어 있거나, 모든 경고가 작성자에 의해 acknowledge 됨
- [ ] `meta.tags` 가 비어 있지 않음
- [ ] `meta.summary` 가 비어 있지 않음
- [ ] 본문에 깨진 글자(`?`, `□`)가 없음 (폰트 임베드 누락 여부 확인)

---

## 13. 라이브러리 의존성 + CLI 사용법

### 13.1 의존성 (`requirements.txt`)

```
pdfplumber>=0.11.0
pypdf>=4.0.0
```

테스트 전용 (dev only):

```
reportlab>=4.0
```

### 13.2 CLI 사용법

```bash
python -m pdf_converter input.pdf \
    --team HE --group CAE --year 2026 --seq 7 \
    --output-dir output \
    --agents iga-analyst,doc-curator \
    --tags KooRemapper,IGA,NURBS
```

옵션:

| 플래그 | 설명 | 기본값 |
|--------|------|-------|
| `--team` | 팀 코드 (대문자) | (필수) |
| `--group` | 그룹 코드 (대문자) | (필수) |
| `--year` | 연도 (YYYY) | (필수) |
| `--seq` | 순번 | 1 |
| `--output-dir` | 출력 폴더 | `./output` |
| `--agents` | agent_scope (콤마 구분) | (없음) |
| `--tags` | 추가 태그 (콤마 구분, /Info.Keywords 와 병합) | (없음) |
| `--fontsize-ratio` | 헤딩 폰트 크기 임계 (본문 평균 대비) | 1.2 |
| `--ocr` | 빈/거의 빈 페이지에 대해 Tesseract OCR 수행 | off |
| `--ocr-lang` | Tesseract 언어 코드 (예: `eng`, `kor`, `eng+kor`) | `eng` |
| `--ocr-dpi` | OCR 렌더링 DPI | 200 |
| `--ocr-min-chars` | 페이지 텍스트가 이 미만이면 OCR 후보 | 5 |
| `--verbose` | 상세 로그 | off |

### 13.3 출력

- `output/{doc_id}.json` — 변환 결과
- `output/{doc_id}.warnings.log` — 경고 로그 (경고가 있을 때만)

### 13.4 OCR 시스템 의존성 (Windows)

`--ocr` 사용 시 추가 설치 필요 (변환기 자체는 의존성 없어도 동작):

| 항목 | 설치 | 검증 |
|---|---|---|
| Tesseract 바이너리 | <https://github.com/UB-Mannheim/tesseract/wiki> 인스톨러. 기본 경로 `C:\Program Files\Tesseract-OCR\`. PATH 등록 또는 `pytesseract.tesseract_cmd` 설정 | `tesseract --version` |
| 한국어 언어팩 | 인스톨러 옵션에서 Korean 체크 또는 `kor.traineddata` 수동 다운로드 → `tessdata/` | `tesseract --list-langs` 에 `kor` |
| poppler (Windows) | <https://github.com/oschwartz10612/poppler-windows/releases> → `poppler\Library\bin` 을 PATH 에 추가 | `pdftoppm -v` |
| Python 패키지 | `pip install pytesseract pdf2image Pillow` | `python -c "import pytesseract, pdf2image"` |

설치 누락 시 동작: `pdf_converter/ocr.is_available()` 가 False 반환 → OCR 단계 자동 skip + warning. 변환은 텍스트 추출 결과만으로 정상 완료.

---

## 14. 알려진 한계

| 카테고리 | 상태 | 해결 계획 |
|---------|------|-----------|
| 스캔 PDF (이미지 only) | **opt-in 지원** (`--ocr`) — Tesseract + pdf2image. 미설치 시 자동 skip | (해소) — paddleocr 백엔드 옵션은 향후 검토 |
| 다단(2-column) 레이아웃 | 본문 순서 부정확 | layout-parser/tatr 통합 검토 |
| 수식 (LaTeX/MathML) | 평문 깨짐 | mathpix/pix2tex 통합 검토 |
| 차트 데이터 | 이미지로만 추출 (데이터 없음) | 차트 OCR 미정 |
| 폼 PDF (입력 양식) | 폼 필드 무시 | `attachments[kind=other]` 로 추출 예정 |
| 셀 경계 없는 표 | 추출 불가 | 작성자가 그리드 표로 재작성 필요 |
| 이미지 바이너리 추출 | 미지원 (placeholder) | OCR 통합과 동시 진행 |
| 회전 페이지 | 부분 지원 | pdfplumber 자동 보정에 의존 |
| 인라인 서식 (볼드/이탤릭) | 무시 (스키마 정책) | 영구 — 스키마는 평문 |
| 하이퍼링크 | 평문 텍스트만 보존 | URL 메타 보존은 향후 검토 |

---

## 변경 이력

| 날짜 | 변경 |
|------|------|
| 2026-05-08 | v1.0 초안 — pdfplumber + pypdf 기반 1차 구현 |

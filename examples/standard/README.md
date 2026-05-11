# 표준 작성 예제 (examples/standard/)

> 이 폴더의 6개 파일을 **그대로 복사해서 시작점으로 사용**하세요.
> 각 파일은 변환 규칙서(`*_to_json_conversion_rules.md`)의 모든 핵심 원칙을
> 최소 단위로 시연합니다.

핵심 메시지: **헤딩은 헤딩 스타일로, 표 캡션은 위, 그림 캡션은 아래,
Excel 은 `_META`+`_GLOSSARY` 시트로 의미를 명시 — 이 4가지만 지키면
변환기가 손실 없이 JSON 으로 추출합니다.**

---

## 파일 목록

| 파일                            | 용도                          | 시연하는 핵심 원칙                                                           |
|---------------------------------|-------------------------------|------------------------------------------------------------------------------|
| `sample_report.docx`            | Word 작성 표준                 | Heading 1/2 스타일, 표 캡션 위, `[TAGS]`/`[SUMMARY]`/`[AGENT_SCOPE]` 마커     |
| `sample_presentation.pptx`      | PPT 작성 표준                  | 슬라이드 제목 placeholder, 자연스러운 불릿 본문, 표 도형, 그림+캡션, 발표자 노트 |
| `sample_data.xlsx`              | Excel 작성 표준 (6원칙)        | A1 시작, 헤더 단위, 셀 병합 X, 색상→컬럼, 1시트 1주제, `_META`+`_GLOSSARY` |
| `sample_doc.pdf`                | PDF 작성 표준                  | /Info dict, outline 북마크, 검색가능 텍스트, 폰트 크기 차이, 셀 기반 표      |
| `sample_doc.md`                 | Markdown 작성 표준              | 풀 YAML front matter, 번호형 h1~h3, GFM 표·캡션, 인라인 그림·캡션, 코드 펜스(lang), 인용, 리스트, 체크박스, 인라인 링크 |
| `sample_doc.html`               | HTML 작성 표준                  | `<head><meta>` 풀세트, 번호형 h1~h3, `<table><caption>`, `<figure><figcaption>`, `<pre><code class="language-...">`, `<blockquote>`, `<ul>`/`<ol>` |
| `nurbs_box.png`                 | (placeholder, 1×1 PNG)        | Markdown / HTML 인라인 그림이 가리키는 외부 자원                              |

생성 스크립트:

| 스크립트              | 무엇을 만드는가                                |
|-----------------------|------------------------------------------------|
| `_generate_word.py`   | `sample_report.docx` (python-docx)             |
| `_generate_ppt.py`    | `sample_presentation.pptx` (python-pptx)       |
| `_generate_excel.py`  | `sample_data.xlsx` (openpyxl)                  |
| `_generate_pdf.py`    | `sample_doc.pdf` (reportlab)                   |
| `_generate_all.py`    | 위 4개 + `nurbs_box.png` 생성 + 변환 검증 일괄 |

`sample_doc.md` 와 `sample_doc.html` 은 손으로 작성된 hand-written 예제이며 `_generate_all.py` 가 손대지 않는다.

---

## 빠른 시작 — 한 번에 다시 만들기

```powershell
$env:PYTHONPATH = "d:\Personal\AI_data\api_server\src"
& "d:\Personal\AI_data\api_server\.venv\Scripts\python.exe" `
    "d:\Personal\AI_data\examples\standard\_generate_all.py"

# 결과 확인
ls "d:\Personal\AI_data\examples\standard\converted\"
```

`_generate_all.py` 는 다음을 한 번에 수행합니다.

1. 6종 예제 파일 + `nurbs_box.png` 재생성 (idempotent — Word/PPT/Excel/PDF 만 코드 생성, MD/HTML 은 hand-written 보존)
2. 6개 변환기를 차례로 실행해 JSON 을 `converted/` 에 저장
3. 각 JSON 의 핵심 메타(doc_id · title · sections · figures · tables · warnings) 요약 출력

---

## 개별 변환 명령

```powershell
$env:PYTHONPATH = "d:\Personal\AI_data\api_server\src"
$py = "d:\Personal\AI_data\api_server\.venv\Scripts\python.exe"
$out = "examples\standard\converted"

# 1) Word
& $py -m converter        examples/standard/sample_report.docx       --division HE --team CAE --year 2026 --seq 100 --output-dir $out

# 2) Excel  (--start-seq 사용, 다른 변환기는 --seq)
& $py -m excel_converter  examples/standard/sample_data.xlsx         --division HE --team MFG --year 2026 --start-seq 200 --infer-units --output-dir $out

# 3) PPT
& $py -m ppt_converter    examples/standard/sample_presentation.pptx --division HE --team CAE --year 2026 --seq 300 --tags IGA,NURBS,KooRemapper,sample,standard --agents iga-analyst,cae-reporter --output-dir $out

# 4) PDF
& $py -m pdf_converter    examples/standard/sample_doc.pdf           --division HE --team CAE --year 2026 --seq 400 --tags IGA,NURBS,KooRemapper,sample,standard --agents iga-analyst,cae-reporter --output-dir $out

# 5) Markdown
& $py -m md_converter     examples/standard/sample_doc.md            --division HE --team CAE --year 2026 --seq 500 --output-dir $out

# 6) HTML
& $py -m html_converter   examples/standard/sample_doc.html          --division HE --team CAE --year 2026 --seq 600 --output-dir $out
```

---

## 검증된 변환 결과 (마지막 실행 기준)

모든 변환기가 성공(`ALL OK`)했고 산출물은 `converted/` 에 있습니다.

| 변환기            | 입력                          | 출력 JSON                                  | sections / figures / tables / rows | warnings        |
|-------------------|-------------------------------|--------------------------------------------|------------------------------------|-----------------|
| `converter`       | `sample_report.docx`          | `HE-CAE-2026-0000000100.json`                  | 3 / 0 / 1 / —                      | 0               |
| `excel_converter` | `sample_data.xlsx`            | `DATA-HE-MFG-2026-0000000200.json`             | — / — / — / 5                      | 0               |
| `ppt_converter`   | `sample_presentation.pptx`    | `DOC-HE-CAE-2026-0000000300.json`              | 4 / 0 / 1 / —                      | 0               |
| `pdf_converter`   | `sample_doc.pdf`              | `DOC-HE-CAE-2026-0000000400.json`              | 3 / 0 / 1 / —                      | 5 (정보성)       |
| `md_converter`    | `sample_doc.md`               | `DOC-HE-CAE-2026-0000000500.json`              | 6 / 1 / 1 / —                      | 0               |
| `html_converter`  | `sample_doc.html`             | `DOC-HE-CAE-2026-0000000600.json`              | 6 / 1 / 1 / —                      | 0               |

핵심 메타 추출 확인:

- Word — `tags=[IGA,NURBS,KooRemapper,sample,standard]`, `agent_scope=[iga-analyst,cae-reporter]`,
  `summary=…표준 예제 보고서…` (모두 본문 마커에서 추출)
- Excel — `meta.title=브라켓 하중 시험 결과 (2026-04)`, `tags=[시험,브라켓,하중,2026Q2]`,
  `content.context.method=KS B 0814`, `units={무게:g, 단면적:mm², …}` (모두 `_META`+`_GLOSSARY` 에서 추출)
- PPT — `meta.title=KooRemapper IGA 변환 결과 (표준 예제)` (core_properties 에서 추출),
  4개 슬라이드가 4개 섹션으로 매핑, 표 1개 추출, Slide 4 발표자 노트 보존
- PDF — `meta.title=IGA 검토 보고서 (2026-04)`, `meta.pdf.heading_strategy=outline`,
  `tags=[IGA,NURBS,KooRemapper]` (CLI + /Info Keywords 병합), 셀 표 1개 추출
- Markdown — `meta.title=KooRemapper IGA 변환 가이드 (표준 예제)`,
  `tags=[IGA,NURBS,KooRemapper,sample,standard]`, `agent_scope=[iga-analyst,cae-reporter,doc-curator]`,
  6개 번호형 섹션, GFM 표 1개 + 인라인 그림 1개(figures+attachments 둘 다 등록),
  Python 코드 펜스(`block.marker=lang:python`), 인용 블록, 리스트, 체크박스 모두 보존
- HTML — `meta.title=KooRemapper IGA 변환 가이드 (HTML 표준 예제)`,
  `tags=[IGA,NURBS,KooRemapper,sample,standard,html]` (head meta keywords 에서 분리),
  `agent_scope=[iga-analyst,cae-reporter,doc-curator]`, `head_meta_extra={classification,status,domain,language}`,
  6개 번호형 섹션 (h1/h2 트리: 1, 1.1, 1.2, 2, 2.1, 2.2, 2.3, 3, 4, 4.1, 4.2, 5, 6),
  `<table><caption>` → table caption 정상 추출, `<figure><figcaption>` → figure caption 정상 추출,
  Python/Bash 코드 펜스 (`<pre><code class="language-...">`), `<blockquote>`, `<ul>`/`<ol>` 모두 보존

### 알려진 무해 경고

| 변환기 | 경고                                              | 의미                                                                              |
|--------|---------------------------------------------------|-----------------------------------------------------------------------------------|
| PPT    | `summary 미지정 → 빈 문자열`                       | PPT 변환기는 `--summary` CLI 가 없어 core_properties 에 의존. 추후 LLM 으로 보강. |
| PDF    | `섹션 번호 불일치 ... 본문 값 사용`                | outline + 본문 패턴이 둘 다 잡혀 자동 카운터와 본문 번호가 다를 때의 정보 로그.   |

두 경고 모두 변환 거부가 아니며 `meta.title`·`sections` 모두 정상 생성됩니다.

---

## 시연되는 원칙 매트릭스

### Word (`sample_report.docx`)

- ✓ Heading 1/2 스타일 사용 (변환 규칙서 4장)
- ✓ 표 캡션을 표 **위** 에 배치 (Caption 스타일 + "Table 1: …")
- ✓ `[DOC_TYPE]`/`[SUMMARY]`/`[TAGS]`/`[AGENT_SCOPE]` 마커 (부록 A)
- (그림 없음 — 그림이 있는 문서에서는 그림 **아래** 에 캡션을 둔다)

### PPT (`sample_presentation.pptx`)

- ✓ 슬라이드 제목 placeholder 사용 (4.1 절 우선순위 1)
- ✓ 본문은 자연스러운 불릿 텍스트박스 (들여쓰기 level 활용 — list_item 으로 추출)
- ✓ 표 도형 (이미지 X, 임베디드 Excel X — 7.1 절)
- ✓ 그림 영역 + `Figure N: ...` 캡션 텍스트박스 (7.4 절)
- ✓ 발표자 노트 (6장)
- ✓ 빌트인 속성 `core_properties.title` 사용

> 변환기는 슬라이드 본문에 어떤 양식도 강제하지 않는다. `[목적]`/`[방법]`/`[결과]`
> 같은 보고서식 라벨은 **불필요** — 발표자료의 자연스러운 불릿 / 도식 / 비교 흐름을
> 그대로 사용하면 된다. 진짜 필요한 건 ① 제목 placeholder 채움 ② 본문이 텍스트
> 프레임 안 ③ 표는 PPT 내장 도형 ④ 그림은 PICTURE shape — 이 4가지뿐이다.

### Excel (`sample_data.xlsx`) — 6원칙 모두 시연

| 원칙                        | 시연 방식                                              |
|-----------------------------|--------------------------------------------------------|
| 1. 시트 상단 고정           | Sheet1 의 헤더가 A1 부터 시작                          |
| 2. 헤더에 단위 명시          | `무게(g)`, `단면적(mm²)`, `최대하중(N)` ...            |
| 3. 셀 병합 금지             | 헤더·데이터 모두 단일 셀                               |
| 4. 색상 의미 별도 컬럼화     | "파괴여부 Y/N" 컬럼으로 표현 (조건부 서식 없음)        |
| 5. 1시트 1주제              | Sheet1 만 데이터, `_META`/`_GLOSSARY` 는 메타          |
| 6. 데이터 의미 명시 (★ 핵심) | `_META` (15 키) + `_GLOSSARY` (6 컬럼)                 |

### PDF (`sample_doc.pdf`)

- ✓ /Info dict (Title/Author/Subject/Keywords) — `meta.pdf.creator` 등으로 매핑
- ✓ Outline (북마크) 4개 — `1.개요` / `1.1 배경` / `2.결과` / `2.1 검증`
- ✓ 검색 가능한 텍스트 (이미지 PDF 가 아님)
- ✓ 폰트 크기 차이로 헤딩과 본문 구분 (헤딩 18pt/14pt, 본문 10pt)
- ✓ 셀 기반 표 + 캡션 위 (이미지 캡처 X)

### Markdown (`sample_doc.md`)

- ✓ 풀 YAML front matter (title/summary/tags/agents/classification/status/domain/language/author/doc_type/version/created/modified)
- ✓ 번호형 h1~h3 (`# 1. 개요`, `## 1.1 배경`, `### ...`) — h4 이하 없음 (변환기는 h4 부터 본문 단락으로 흡수)
- ✓ GFM 표 (`|---|`) + 단위가 헤더에 명시 (`소요 시간(s)`)
- ✓ 표 직전 `Table 1: ...` 캡션 단락 (Excel 작성 표준과 동일 정신)
- ✓ 인라인 그림 `![설명](nurbs_box.png)` + 인용 캡션 (`> Figure 1: …`)
- ✓ Python 코드 펜스 (`marker=lang:python`)
- ✓ Bash 코드 펜스 (`marker=lang:bash`)
- ✓ 불릿 리스트 (`-`) + 번호 리스트 (`1.`)
- ✓ 체크박스 (`- [ ] ...`) — 검증 체크리스트
- ✓ 인용 블록 (`> 주의: ...`)
- ✓ 인라인 링크 (`[Hughes 등(2005)](https://...)`) — 본문 텍스트에 보존
- ✓ 섹션 간 상호참조 (`[3절 표 1](#3-표-기준)`)

### HTML (`sample_doc.html`)

- ✓ 풀 `<head><meta>` (title / description / author / keywords / agents / classification / status / domain / language / doc_type / version / created / modified)
- ✓ 번호형 `<h1>`~`<h2>` (h3 이상 시연 시 동일 매핑) — h4 이하는 본문 단락으로 강등
- ✓ `<table><caption>...</caption><thead><tbody>` 풀 구조 — caption 그대로 추출
- ✓ `<figure><img alt><figcaption>` 패턴 — figcaption 이 figure caption 으로 우선 사용됨
- ✓ `<pre><code class="language-python">` / `language-bash` — 언어가 `marker=lang:xxx` 로 보존
- ✓ `<blockquote><p>` — paragraph marker=`"> "` 로 인용 식별
- ✓ `<ul>`/`<ol>` 리스트
- ✓ 인라인 `<a href>` 링크 → `[text](url)` 본문 텍스트 보존
- ✓ 인라인 `<code>` → 백틱으로 감싸기
- ✓ 인라인 서식 (`<b>`, `<i>`, `<em>`, `<strong>`, `<span>`) → 모두 평문화 (스키마 일관성)

---

## 수정 후 다시 변환하려면

각 예제 파일을 손으로 직접 편집한 뒤(또는 `_generate_*.py` 를 수정 후),
`_generate_all.py` 를 다시 실행하면 됩니다.
스크립트는 idempotent — `converted/` 폴더가 매 실행마다 초기화됩니다.

```powershell
& "d:\Personal\AI_data\api_server\.venv\Scripts\python.exe" `
    "d:\Personal\AI_data\examples\standard\_generate_all.py"
```

---

## 한 줄 요약

> **헤딩 스타일 + 표 캡션 위 / 그림 캡션 아래 + Excel `_META`·`_GLOSSARY`**
> — 이 세 가지만 지키면 변환기가 알아서 정확한 JSON 을 만들어 줍니다.

# HTML → JSON 변환 규칙서

## 자동화 프로그램 구현 가이드 v1.3 (코드/룰 완전 동기)

> 작성일: 2026-05-08
> 적용 대상: 표준 HTML 문서를 [json_schema_rules.md](./json_schema_rules.md) 스키마로 변환하는 자동화 프로그램
> 시작점: [`CONVERSION_RULES_INDEX.md`](./CONVERSION_RULES_INDEX.md)
> 자매 문서 (모두 동일 JSON 스키마 출력):
>
> - [json_schema_rules.md](./json_schema_rules.md) — JSON 스키마 전반 (모든 변환기 공통)
> - [md_to_json_conversion_rules.md](./md_to_json_conversion_rules.md) — Markdown 변환 규칙 (구조가 거의 1:1로 동일)
> - [word_to_json_conversion_rules.md](./word_to_json_conversion_rules.md) — Word 변환 규칙
> - [excel_to_json_conversion_rules.md](./excel_to_json_conversion_rules.md) — Excel 변환 규칙
> - [ppt_to_json_conversion_rules.md](./ppt_to_json_conversion_rules.md) — PPT 변환 규칙
> - [pdf_to_json_conversion_rules.md](./pdf_to_json_conversion_rules.md) — PDF 변환 규칙

---

## 0. 코드 정합 노트 (필독)

본 문서는 [`api_server/src/html_converter/`](./api_server/src/html_converter/) 의 실제 출력을 단일 진실 공급원으로 한다.

| 항목 | 변환기 출력 / 코드 위치 | normalizer 흡수 / DB |
|---|---|---|
| 식별자 | `meta.doc_id` (`html_converter/core.py:625`) | `meta.doc_id` 우선 → `records.id` |
| 에이전트 | `<meta name="agents">` 또는 `agent-scope` → `meta.agent_scope` (`html_converter/core.py:260-263, 617, 639`) | `meta.agent_scope` 우선, `raw.agents` 폴백 → `records.agents` |
| head meta own-extras | 표준 매핑 안 된 `<meta name=...>` 모두 → `meta.head_meta_extra` (`html_converter/core.py:641-650`) | `records.content` JSONB 보존 |
| 분류/생애주기 (0006 10개) | `<meta name="classification" content="...">` 등으로 기술하면 normalizer 가 흡수 (`normalizer.py:103-153`) ✅ | `records.classification` 등 |
| 0007 agent-discovery 자동 채움 | `agent_hints`/`query_examples`/`access_pattern` 자동 생성 (`html_converter/core.py:670-685`). `<meta name="agent_hints" ...>` 로 override | `records.agent_hints` 등 |

(v1.2 의 "KNOWN GAP" 은 v1.3 커밋 `c2c66c6` 에서 해소.)

---

## 1. 목적

본 문서는 표준 HTML 문서(`.html` / `.htm`)를 [json_schema_rules.md](./json_schema_rules.md) 스키마(`data_type = "DOC"`, `source_format = "html"`)에 맞는 JSON 으로 자동 변환하는 프로그램의 결정 사항을 명시한다. 구현은 `src/html_converter/` (Python 3.12, `lxml.html` 기반).

HTML 은 Markdown 과 거의 1:1 로 매핑되며 — h1/h2/h3 헤딩, p, ul/ol, pre/code, blockquote, table, img — 변환기는 [md_converter](./md_to_json_conversion_rules.md) 의 공통 헬퍼(`extract_section_id_from_heading`, `parse_figure_caption`, `infer_attachment_kind_from_url`, `is_absolute_url`)를 그대로 재사용한다.

---

## 2. 입력 / 출력 명세

### 2.1 입력

- **포맷**: `.html` / `.htm` (UTF-8 권장)
- **방언**: HTML5 권장. 깨진 마크업은 `lxml.html` 의 관용적 파싱으로 복구 처리.
- **선택적 `<head>` 메타**: `<title>`, `<meta name="description"|"author"|"keywords"|"agents"|...>` 가 있으면 meta 필드로 흡수.

### 2.2 출력

- 단일 JSON 파일 (스키마 v1.0, `data_type = "DOC"`, `meta.source_format = "html"`).
- ID 형식 — Markdown 변환기와 동일:
  - `meta.doc_id` = `DOC-{div}-{team}-{year}-{seq:06d}`
  - 그림: `{doc_id}-F{nnn}`
  - 표: `{doc_id}-T{nnn}`
  - 첨부: `{doc_id}-A{nnn}`
- 그림 바이너리는 변환기가 직접 복사하지 않는다 (HTML 도 외부 링크 모델). 후속 파이프라인이 `attachments[].file_path` 또는 `figures[].image_path` 를 따라 자료를 적재한다.

### 2.3 처리하지 않는 것 (범위 밖)

- JavaScript 동적 렌더링: 정적 HTML 만 파싱한다 (서버사이드 렌더 결과를 입력으로 권장).
- CSS 스타일: 무시.
- iframe / 임베드 / 비디오: 인식되더라도 본문에 등록하지 않음 (필요 시 향후 확장).
- 인라인 서식(`<b>`, `<i>`, `<em>`, `<strong>`, `<u>`, `<mark>`, `<span>` 등): 모두 평문화.
- 폼(`<form>`, `<input>`): 무시.
- SVG 인라인: 평문 보존만 (이미지로 추출하지 않음). `<img src="*.svg">` 형태는 figure 로 정상 등록됨.

---

## 3. 헤딩 깊이 매핑

Markdown 변환기와 동일 정책.

| HTML | JSON 매핑 | 비고 |
|------|-----------|------|
| `<h1>` | `sections[].level = 1` | 새 섹션 |
| `<h2>` | `sections[].level = 2` | level 1 의 자식 |
| `<h3>` | `sections[].level = 3` | level 2 의 자식 |
| `<h4>` ~ `<h6>` | 본문 단락(`block.type=paragraph`, marker=`"h4 "` 등) | level 3 콘텐츠로 흡수 |

### 3.1 섹션 ID 추출

헤딩 텍스트가 다음 패턴이면 ID 와 제목을 분리한다 (md_converter 와 동일 정규식).

```
^(\d+(?:\.\d+){0,4})\.?\s+(.*)$
```

| 헤딩 텍스트 | section_id | title |
|-------------|------------|-------|
| `<h1>1. 개요</h1>` | `1` | `개요` |
| `<h2>1.2 작동원리</h2>` | `1.2` | `작동원리` |
| `<h3>1.2.3 NURBS 변환</h3>` | `1.2.3` | `NURBS 변환` |
| `<h1>개요</h1>` (번호 없음) | 자동 부여 (`1`) | `개요` |

원본 번호와 자동 계산값이 다르면 **경고 후 본문 값** 사용.

---

## 4. 블록 태그 매핑

| HTML | JSON 매핑 | 비고 |
|------|-----------|------|
| `<h1>`~`<h3>` | `sections[]` | level/id 추출 |
| `<h4>`~`<h6>` | `paragraph` (marker=`"hN "`) | 강등 |
| `<p>` | `paragraph` | 인라인 서식 평문화 |
| `<ul><li>` | `list_item` (marker=`"•"`) | 다단계 평탄화 |
| `<ol><li>` | `list_item` (marker=`"1."`) | 자동 번호 부여 |
| `<pre><code class="language-xxx">` | `code` (marker=`"lang:xxx"`) | 언어 보존 |
| `<pre>` (code 자식 없음) | `code` (marker 없음) | indent code 와 동등 |
| `<blockquote><p>` | `paragraph` (marker=`"> "`) | 다중 단락 OK |
| `<table>` | `tables[]` + `block.type=table, ref=...` | 4.4 절 |
| `<img>` / `<figure><img>` | `figures[]` + `attachments[kind=figure]` | 4.5 절 |
| `<hr>` | (무시) | 단락 구분자 |
| `<div>`/`<section>`/`<article>`/`<main>` | 컨테이너 — 자식 재귀 | |

### 4.1 인라인 서식 처리

`<b>`, `<i>`, `<em>`, `<strong>`, `<u>`, `<s>`, `<mark>`, `<span>`, `<sub>`, `<sup>`, `<small>` 등은 **모두 평문화** 한다 (스키마는 평문만).

| 인라인 태그 | 처리 |
|-------------|------|
| `<a href="url">text</a>` | `[text](url)` 마크다운 형식으로 보존 |
| `<code>text</code>` | `` `text` `` 백틱으로 감싸기 |
| `<br>` | 공백 하나 |
| `<img>` (인라인) | alt 만 평문에 포함, 첨부로 별도 추출 |
| 그 외 인라인 서식 | 모두 평문화 (텍스트만 보존) |

### 4.2 코드 펜스 — 언어 추출

`<pre><code class="language-python">...</code></pre>` 패턴에서 `language-xxx` 또는 `lang-xxx` 클래스로 언어를 추출한다.

```html
<pre><code class="language-python">def hello(): pass</code></pre>
```

→ `{ "type": "code", "text": "def hello(): pass", "marker": "lang:python" }`

### 4.3 리스트

```html
<ul><li>apple</li><li>banana</li></ul>
```

→ 두 개의 `list_item` (marker=`•`).

```html
<ol><li>first</li><li>second</li></ol>
```

→ 두 개의 `list_item` (marker=`1.`, `2.`).

다단계 리스트(`<ul><li><ul>...</ul></li></ul>`)는 평탄화한다 — 들여쓰기 정보 손실, 텍스트는 보존.

### 4.4 표

```html
<table>
  <caption>Table 1: 매핑 결과</caption>
  <thead>
    <tr><th>단계</th><th>입력</th><th>출력</th></tr>
  </thead>
  <tbody>
    <tr><td>1</td><td>.k</td><td>NURBS</td></tr>
    <tr><td>2</td><td>NURBS</td><td>.iga</td></tr>
  </tbody>
</table>
```

→

```json
{
  "id": "DOC-HE-CAE-2026-000001-T001",
  "number": 1,
  "caption": "Table 1: 매핑 결과",
  "section_ref": "1",
  "headers": ["단계", "입력", "출력"],
  "rows": [["1", ".k", "NURBS"], ["2", "NURBS", ".iga"]]
}
```

- `<caption>` 태그가 있으면 **그대로 캡션** 으로 사용. 없으면 `"Table N"`.
- `<thead>` 가 있으면 그 안의 첫 `<tr>` 가 헤더. 없으면 `<tbody>` 의 첫 `<tr>` 가 헤더 (단 `<th>` 만 있는 경우).
- 셀 병합(`rowspan`, `colspan`) — **현재 미지원** (좌상단 값만 들어가고 나머지는 빈 문자열). 작성자에게 병합 금지 권장.

### 4.5 그림

권장 패턴 (`<figure>` + `<figcaption>`):

```html
<figure>
  <img src="bracket.png" alt="브라켓 응력 분포">
  <figcaption>Figure 1: 응력은 노치 끝단에 집중된다.</figcaption>
</figure>
```

→ `figures[0].caption == "Figure 1: 응력은 노치 끝단에 집중된다."` (figcaption 우선)

`<figure>` 없이 `<img>` 만 있는 경우 — alt text 가 캡션:

```html
<img src="bracket.png" alt="브라켓 응력 분포">
```

→ `figures[0].caption == "브라켓 응력 분포"`

직후 단락이 `Figure N: ...` 패턴이면 캡션으로 승격(md_converter 와 동일 규칙):

```html
<img src="bracket.png" alt="">
<p>Figure 1: 응력 분포</p>
```

→ `figures[0].caption == "Figure 1: 응력 분포"`

캡션이 모두 누락되면 `caption = "Figure N: (캡션 누락 — 검수 필요)"` 자동 + 경고.

---

## 5. `<head>` 메타 매핑

`<head>` 의 `<title>` 와 `<meta>` 태그를 meta 필드로 흡수한다 (Markdown 의 YAML front matter 와 동일 역할).

### 5.1 표준 매핑

| HTML | meta 매핑 |
|------|-----------|
| `<title>` | `meta.title` |
| `<meta name="description" content="...">` | `meta.summary` |
| `<meta name="author" content="...">` | `meta.author` |
| `<meta name="keywords" content="a,b,c">` | `meta.tags` (콤마/세미콜론 분리) |
| `<meta name="agents" content="x,y">` | `meta.agent_scope` |
| `<meta name="classification" content="...">` | `meta.head_meta_extra.classification` |
| `<meta name="status" content="...">` | `meta.head_meta_extra.status` |
| `<meta name="domain" content="...">` | `meta.head_meta_extra.domain` |
| `<meta name="language" content="ko">` | `meta.head_meta_extra.language` |
| `<meta name="doc_type" content="manual">` | `meta.doc_type` |
| `<meta name="version" content="1.0">` | `meta.version` |
| `<meta name="created" content="2026-04-15">` | `meta.created` |
| `<meta name="modified" content="2026-05-08">` | `meta.modified` |
| 그 외 `<meta name="X" content="Y">` | `meta.head_meta_extra.X` |

### 5.2 우선순위

- `<head>` 메타가 있으면 **언제나 최우선**.
- 메타의 tags/agents 가 비어 있을 때만 CLI `--tags`, `--agents` 값을 사용.
- `title` 이 누락되면 첫 번째 `<h1>` 의 텍스트 → 그것도 없으면 파일명(stem) 사용.

### 5.3 권장 형식

```html
<head>
  <meta charset="UTF-8">
  <title>KooRemapper IGA 가이드</title>
  <meta name="description" content="본 가이드는 KooRemapper의 IGA 기능 사용법을 설명한다.">
  <meta name="author" content="HE/CAE 팀">
  <meta name="keywords" content="IGA, NURBS, KooRemapper">
  <meta name="agents" content="iga-analyst, doc-curator">
  <meta name="classification" content="internal">
  <meta name="status" content="draft">
  <meta name="version" content="1.0">
  <meta name="created" content="2026-05-01">
  <meta name="modified" content="2026-05-08">
</head>
```

`property` 속성(OpenGraph 등)도 `name` 과 동등하게 인식한다 (`<meta property="og:description" content="...">`).

---

## 6. 외부 링크 / 그림 처리

### 6.1 상대 경로 그림

```html
<img src="figures/bracket.png" alt="분포">
```

→ `attachments[0].file_path == "figures/bracket.png"`, `figures[0].image_path == "figures/bracket.png"`

### 6.2 절대 URL

```html
<img src="https://cdn.example.com/img.png" alt="알트">
```

→ `attachments[0].extra.url == "https://cdn.example.com/img.png"`, `file_path` 비어 있음.

`figures[0].image_path` 는 절대 URL 의 경우 비어 있음 (정적 마운트 직하 상대 경로 슬롯이므로).

### 6.3 인라인 텍스트 링크

`<a href="...">text</a>` → 단락 텍스트에 `[text](url)` 마크다운 형식으로 보존. 별도 attachment 로 추출하지 않는다.

---

## 7. 변환기별 매핑 비교

| 원본 요소 | Word | PPT | MD | HTML |
|-----------|------|-----|-----|------|
| 헤딩 | Heading 1/2/3 | 슬라이드 제목 | `#` `##` `###` | `<h1>` `<h2>` `<h3>` |
| 단락 | `<w:p>` | 텍스트 placeholder | paragraph | `<p>` |
| 코드 | 등폭 휴리스틱 | 등폭 박스 | fenced ``` | `<pre><code>` |
| 리스트 | `<w:numPr>` | bullet ph | `-` / `1.` | `<ul>`/`<ol>` |
| 표 | `<w:tbl>` | shape table | GFM `\|` | `<table>` |
| 그림 | `<w:drawing>` | picture shape | `![](url)` | `<img>` / `<figure>` |
| 인용 | 인용 스타일 | (없음) | `>` | `<blockquote>` |
| 캡션 | Caption 스타일 | 텍스트박스 | alt + `Figure N:` | `<figcaption>` 또는 `<caption>` 또는 alt |
| 메타데이터 | core.xml + 마커 | 슬라이드 노트 | YAML front matter | `<head><meta>` |

---

## 8. 검증 후 DB 적재 체크리스트

생성된 JSON 이 [json_schema_rules.md](./json_schema_rules.md) 13장 검증 체크리스트를 통과해야 한다.

- [ ] `meta.doc_id` 가 `DOC-{div}-{team}-{year}-{seq:06d}` 패턴인가
- [ ] `meta.source_format == "html"`
- [ ] `meta.title` 이 비어 있지 않은가
- [ ] `meta.tags` 가 2개 이상인가 (head meta keywords 또는 CLI)
- [ ] `meta.summary` 가 30자 이상인가 (head meta description)
- [ ] 모든 `figure.id` 가 `figure_refs` 와 일치하는가
- [ ] 모든 `table.id` 가 `table_refs` 와 일치하는가
- [ ] `attachments[].file_path` 가 POSIX-style (forward slashes) 인가
- [ ] 절대 URL 그림은 `extra.url` 에 보존되었는가
- [ ] `tables[i].headers` 길이와 `rows[j]` 길이가 일치하는가
- [ ] `warnings` 가 비어 있거나 검수 가능한 수준인가

검증 실패 → `output/invalid/` 로 이동, 검수 큐 등록.

---

## 9. 라이브러리 의존성 + CLI 사용법

### 9.1 의존성

`api_server/requirements.txt` 에 이미 포함된 `lxml>=5.3.0` 만 사용한다 (별도 추가 의존성 없음).

### 9.2 CLI

```bash
python -m html_converter input.html \
    --division HE --team CAE --year 2026 --seq 7 \
    --output-dir output \
    --agents iga-analyst,doc-curator \
    --tags KooRemapper,IGA,NURBS
```

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--division` | 팀 코드 (대문자화) | — |
| `--team` | 그룹 코드 (대문자화) | — |
| `--year` | 연도 | — |
| `--seq` | 순번 (6자리 패딩) | 1 |
| `--output-dir` | 출력 폴더 | `output` |
| `--agents` | agent_scope 콤마 구분 | `""` |
| `--tags` | meta.tags 콤마 구분 (head meta 보다 후순위) | `""` |
| `--verbose` | 상세 로그 | False |

### 9.3 출력 파일

```
output/
├── DOC-HE-CAE-2026-000007.json
└── DOC-HE-CAE-2026-000007.warnings.log    (경고가 있을 때만)
```

### 9.4 라이브러리 사용

```python
from html_converter import HtmlConverter, HtmlConverterOptions, write_output

opts = HtmlConverterOptions(division="HE", team="CAE", year=2026, seq=7)
conv = HtmlConverter(opts)
result = conv.convert("path/to/input.html")    # 또는 conv.convert_text(html_string)
json_path, log_path = write_output(result, opts.output_dir)
```

---

## 10. 알려진 한계

| 한계 | 대응 |
|------|------|
| JavaScript 동적 렌더링 미지원 | 서버사이드 렌더 결과(HTML) 를 입력으로 사용 |
| `rowspan` / `colspan` 셀 병합 손실 | 작성자에게 병합 금지 + 값 반복 입력 권장 |
| 인라인 서식(b/i/em/strong) 평문화 | 스키마 일관성을 위해 의도된 동작 |
| iframe / 임베드 / video 태그 무시 | 텍스트 콘텐츠가 외부에 있으면 별도 변환기로 |
| 다단계 리스트 들여쓰기 평탄화 | depth 정보가 필요한 경우 별도 정책 정의 |
| 폼(form, input) 무시 | 폼은 본문 콘텐츠가 아님 (의도된 동작) |
| 그림 바이너리 자동 복사 안 함 (외부 링크 모델) | 후속 파이프라인이 `attachments[].file_path` 따라 복사 |
| HTML 깨진 마크업의 관용적 복구 | 입력 검증을 작성자 측에서 우선 권장 |

---

## 11. HTML 작성 표준

좋은 변환 결과를 위한 작성 원칙 — Markdown 작성 표준과 동일 철학.

### 11.1 헤딩 번호

- 자동 번호 권장: `<h1>1. 개요</h1>`, `<h2>1.1 작동원리</h2>`, `<h3>1.1.1 NURBS</h3>`
- 변환기가 텍스트 앞 번호를 추출해 `section_id` 에 사용한다.
- `<h4>` 이상은 본문 단락으로 강등되므로 주요 목차는 `<h3>` 까지.

### 11.2 그림은 `<figure>` + `<figcaption>` 권장

```html
<figure>
  <img src="figures/bracket.png" alt="브라켓 응력 분포">
  <figcaption>Figure 1: 응력은 노치 끝단에 집중된다.</figcaption>
</figure>
```

- figcaption 이 가장 강력한 캡션 신호.
- figcaption 이 없으면 alt → `Figure N:` 패턴 단락 → 자동 폴백 순서.

### 11.3 `<head><meta>` 로 메타데이터 명시

문서 머리에 표준 meta 태그를 두는 것을 강력 권장. `<title>` + `description` + `keywords` + `author` 가 최소 세트.

### 11.4 `<pre><code class="language-xxx">` 로 언어 명시

```html
<pre><code class="language-python">
def f(): ...
</code></pre>
```

언어 미명시 시 `marker` 비어 있음 — AI 분석 시 언어 추론 비용 발생.

### 11.5 표 작성

- `<thead>` + `<tbody>` 구조 권장 — 헤더 분리가 명확해진다.
- `<caption>` 으로 캡션 명시 (가장 깔끔함).
- 단위는 헤더 셀에 괄호로 명시 (`응력(MPa)`) — Excel 표준과 동일 정신.
- `rowspan` / `colspan` 사용 금지 — 병합 정보가 손실된다.

### 11.6 인용은 `<blockquote>`, 강조는 본문 단어

- 외부 인용/주의는 `<blockquote><p>...</p></blockquote>` 로 마크업하면 marker 로 구분된다.
- 단순 강조(`<b>`, `<i>`, `<strong>`, `<em>`)는 변환 시 평문화되므로 의미 손실에 유의.

### 11.7 권장 디렉터리 구조

```
my_doc.html
figures/
├── bracket.png
├── stress.png
└── flow.svg
```

- 그림은 별도 `figures/` 폴더에 두고 상대 경로로 참조.
- 후속 파이프라인이 `attachments[].file_path` 를 따라 정적 마운트(`/attachments`) 로 복사한다.

---

*본 변환 규칙서는 [json_schema_rules.md](./json_schema_rules.md) 의 v1.3 출력 스키마 (호환성: v1.0 JSON 페이로드 그대로 사용 가능)를 기준으로 작성되었으며, 스키마가 변경될 때 함께 갱신됩니다.*

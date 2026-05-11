# Markdown → JSON 변환 규칙서
## 자동화 프로그램 구현 가이드 v1.3 (코드/룰 완전 동기)

> 작성일: 2026-05-08
> 적용 대상: 표준 Markdown 문서를 [json_schema_rules.md](./json_schema_rules.md) 스키마로 변환하는 자동화 프로그램
> 시작점: [`CONVERSION_RULES_INDEX.md`](./CONVERSION_RULES_INDEX.md)
> 자매 문서 (모두 동일 JSON 스키마 출력):
>
> - [json_schema_rules.md](./json_schema_rules.md) — JSON 스키마 전반 (모든 변환기 공통)
> - [word_to_json_conversion_rules.md](./word_to_json_conversion_rules.md) — Word 변환 규칙
> - [excel_to_json_conversion_rules.md](./excel_to_json_conversion_rules.md) — Excel 변환 규칙
> - [ppt_to_json_conversion_rules.md](./ppt_to_json_conversion_rules.md) — PPT 변환 규칙
> - [pdf_to_json_conversion_rules.md](./pdf_to_json_conversion_rules.md) — PDF 변환 규칙 (OCR opt-in)
> - [html_to_json_conversion_rules.md](./html_to_json_conversion_rules.md) — HTML 변환 규칙

---

## 0. 코드 정합 노트 (필독)

본 문서는 [`api_server/src/md_converter/`](./api_server/src/md_converter/) 의 실제 출력을 단일 진실 공급원으로 한다.

| 항목 | 변환기 출력 / 코드 위치 | normalizer 흡수 / DB |
|---|---|---|
| 식별자 | `meta.doc_id` (`md_converter/core.py:633`) | `meta.doc_id` 우선 → `records.id` |
| 에이전트 | YAML frontmatter `agents` 를 `meta.agent_scope` 로 매핑 (`md_converter/core.py:625, 647`) | `meta.agent_scope` 우선, `raw.agents` 폴백 → `records.agents` |
| frontmatter own-extras | 표준 키 외 모든 frontmatter → `meta.front_matter_extra` (`md_converter/core.py:649-656`) | `records.content` JSONB 보존 |
| 분류/생애주기 (0006 10개) | YAML frontmatter (`classification: confidential` 등) 에 기술하면 normalizer 가 그대로 흡수 (`normalizer.py:103-153`) ✅ | `records.classification` 등 |
| 0007 agent-discovery 자동 채움 | `agent_hints`/`query_examples`/`access_pattern` 자동 생성 (`md_converter/core.py:678-694`). frontmatter override 가능 | `records.agent_hints` 등 |

(v1.2 의 "KNOWN GAP" 은 v1.3 커밋 `c2c66c6` 에서 해소.)

---

## 목차

1. [목적](#1-목적)
2. [입력 / 출력 명세](#2-입력--출력-명세)
3. [헤딩 깊이 매핑](#3-헤딩-깊이-매핑)
4. [CommonMark + GFM 지원 범위](#4-commonmark--gfm-지원-범위)
5. [블록 타입 매핑](#5-블록-타입-매핑)
6. [YAML front matter](#6-yaml-front-matter)
7. [캡션 처리](#7-캡션-처리)
8. [외부 링크 / 그림 처리](#8-외부-링크--그림-처리)
9. [변환기별 매핑 표](#9-변환기별-매핑-표)
10. [검증 후 DB 적재 체크리스트](#10-검증-후-db-적재-체크리스트)
11. [라이브러리 의존성 + CLI 사용법](#11-라이브러리-의존성--cli-사용법)
12. [알려진 한계](#12-알려진-한계)
13. [MD 작성 표준](#13-md-작성-표준)

---

## 1. 목적

본 문서는 표준 Markdown 문서(`.md`)를 [json_schema_rules.md](./json_schema_rules.md) 스키마(`data_type = "DOC"`)에 맞는 JSON 으로 자동 변환하는 프로그램의 결정 사항을 명시한다. 구현은 `src/md_converter/` (Python 3.12, `markdown-it-py` 기반).

Markdown 은 CommonMark 의 명시적인 헤딩 계층(`#`, `##`, ...) 덕분에 Word/PPT 보다 손실이 적게 변환된다. 본 가이드는 **AI 또는 개발자가 그대로 구현하거나 사후 검증할 수 있도록** 모든 매핑을 표로 정리한다.

---

## 2. 입력 / 출력 명세

### 2.1 입력

- **포맷**: `.md` 또는 `.markdown` (UTF-8 인코딩 권장)
- **방언**: CommonMark + GitHub Flavored Markdown(GFM) 의 **표** 와 **취소선**
- **선택적 YAML front matter**: 문서 첫 줄이 `---` 으로 시작하면 YAML 메타데이터로 인식

### 2.2 출력

- 단일 JSON 파일 (스키마 v1.0, `data_type = "DOC"`, `meta.source_format = "md"`).
- ID 형식:
  - `meta.doc_id` = `DOC-{div}-{group}-{year}-{seq:06d}`
  - 그림: `{doc_id}-F{nnn}` (예: `DOC-HE-CAE-2026-0000000001-F001`)
  - 표: `{doc_id}-T{nnn}`
  - 첨부: `{doc_id}-A{nnn}`
- 본문/그림/표 모두 [json_schema_rules.md](./json_schema_rules.md) 7-키 구조와 동일.
- 그림 바이너리는 변환기가 직접 복사하지 않는다 (Markdown 은 외부 링크 기반). 후속 파이프라인이 `attachments[].file_path` 또는 `figures[].image_path` 를 따라 자료를 적재한다.

### 2.3 처리하지 않는 것 (범위 밖)

- HTML 임베드(`<div>`, `<iframe>` 등): 평문 단락으로 보존만 수행.
- 사용자 정의 markdown-it 확장(footnote, container 등): 기본 비활성화.
- 인라인 서식(볼드/이탤릭/취소선): **모두 무시** (스키마는 평문만).
- LaTeX 수식: 평문으로 보존.

---

## 3. 헤딩 깊이 매핑

| Markdown | JSON 매핑 | 비고 |
|----------|-----------|------|
| `# H1`   | `sections[].level = 1` | 새 섹션 |
| `## H2`  | `sections[].level = 2` | level 1 의 자식 |
| `### H3` | `sections[].level = 3` | level 2 의 자식 |
| `#### H4` ~ `###### H6` | 본문 단락(`block.type=paragraph`) | level 3 콘텐츠로 흡수 |

### 3.1 섹션 ID 추출

헤딩 텍스트가 다음 패턴이면 ID 와 제목을 분리한다.

```
^(\d+(?:\.\d+){0,4})\.?\s+(.*)$
```

| 헤딩 텍스트 | section_id | title |
|-------------|------------|-------|
| `# 1. 개요` | `1`   | `개요` |
| `## 1.2 작동원리` | `1.2` | `작동원리` |
| `### 1.2.3 NURBS 변환` | `1.2.3` | `NURBS 변환` |
| `# 개요` (번호 없음) | 자동 부여 (`1`) | `개요` |

원본 번호와 자동 계산값이 다르면 **경고 후 본문 값** 사용.

---

## 4. CommonMark + GFM 지원 범위

| 기능 | 기본 | 처리 |
|------|------|------|
| Heading (#)         | ✓ | sections[] 트리 |
| Paragraph           | ✓ | block.type = paragraph |
| Fenced code (```)   | ✓ | block.type = code, marker = "lang:..." |
| Indented code       | ✓ | block.type = code (lang 없음) |
| Bullet list (`-`/`*`) | ✓ | block.type = list_item, marker = "•" |
| Ordered list (`1.`) | ✓ | block.type = list_item, marker = "1." |
| Blockquote (`>`)    | ✓ | block.type = paragraph, marker = "> " |
| Table (GFM)         | ✓ | tables[] + section.table_refs[] |
| Image (`![](url)`)  | ✓ | figures[] + attachments[kind=figure] |
| Link (`[text](url)`)| ✓ | 단락 텍스트에 `[text](url)` 마크다운 형식 보존 |
| Strikethrough (GFM) | ✓ | 평문으로 흡수 |
| HR (`---`)          | ✓ | 단락 구분자로만 사용, 별도 블록 없음 |
| Inline HTML         | ✓ | 평문 보존 |
| Inline em/strong    | ✓ | 서식 무시, 평문만 |
| Footnote, definition list 등 | ✗ | 기본 비활성화 (필요 시 mdit-py-plugins 추가) |

YAML front matter 는 `mdit_py_plugins.front_matter_plugin` 으로 토큰 스트림에 포함되며, 변환기가 자체 파서로 키-값을 추출해 `meta` 에 흡수한다.

---

## 5. 블록 타입 매핑

`Section.blocks` 는 본문 등장 순서를 그대로 보존한다. 각 블록 타입과 매핑은 다음과 같다.

### 5.1 단락 → `paragraph`

```json
{ "type": "paragraph", "text": "본문 텍스트" }
```

- 인라인 링크는 `[표시](url)` 형태로 텍스트에 포함.
- 인라인 서식(`**bold**`, `*italic*`)은 평문화.

### 5.2 코드 펜스 → `code`

```markdown
```python
def hello(): ...
```
```

```json
{ "type": "code", "text": "def hello(): ...", "marker": "lang:python" }
```

- 펜스에 명시한 언어는 `marker = "lang:<언어>"` 로 저장 (스키마는 marker 만 인정하므로 우회).
- 들여쓰기 코드 블록(4-space) 도 동일 type, marker 없음.

### 5.3 리스트 → `list_item`

| Markdown | text | marker |
|----------|------|--------|
| `- apple`    | `apple` | `•` |
| `1. first`   | `first` | `1.` |
| `2. second`  | `second`| `2.` |

다단계 리스트(들여쓰기 중첩)는 평탄화한다 — 들여쓰기 정보는 손실되지만 텍스트는 보존.

### 5.4 인용 → `paragraph` (marker = `"> "`)

```markdown
> 인용된 한 문장.
```

```json
{ "type": "paragraph", "text": "인용된 한 문장.", "marker": "> " }
```

여러 줄 인용은 인용 안의 각 단락이 별도 `paragraph` 블록으로 추가된다.

### 5.5 표 → `tables[]`

GFM 표는 본문 흐름의 위치에 `{ "type": "table", "ref": "<id>" }` 블록으로 표시되고, 실제 데이터는 최상위 `tables[]` 에 등록된다.

```json
{
  "id": "DOC-HE-CAE-2026-0000000001-T001",
  "number": 1,
  "caption": "Table 1",
  "section_ref": "1.2",
  "headers": ["입력", "출력", "비고"],
  "rows": [[".k", ".iga", "NURBS"], [".inp", ".iga", "trimmed"]]
}
```

- Markdown 표에는 캡션 문법이 없으므로 기본 캡션은 `"Table N"`.
- 헤더 행 분리(`|---|---|`) 위쪽이 헤더, 아래가 본문 행.

### 5.6 그림 → `attachments[kind=figure]` + `figures[]`

```markdown
![브라켓 응력 분포](bracket.png "옵션 제목")
```

→

```json
"figures": [
  {
    "id": "DOC-HE-CAE-2026-0000000001-F001",
    "number": 1,
    "caption": "브라켓 응력 분포",
    "section_ref": "1.2",
    "image_path": "bracket.png"
  }
],
"attachments": [
  {
    "id": "DOC-HE-CAE-2026-0000000001-A001",
    "number": 1,
    "kind": "figure",
    "caption": "브라켓 응력 분포",
    "section_ref": "1.2",
    "file_name": "bracket.png",
    "file_path": "bracket.png",
    "extra": { "figure_ref": "DOC-HE-CAE-2026-0000000001-F001", "title": "옵션 제목" }
  }
]
```

본문 흐름에는 `{ "type": "figure", "ref": "DOC-...-F001" }` 블록이 추가된다.

---

## 6. YAML front matter

문서 첫 줄이 `---` 으로 시작하면 다음 `---` 까지를 YAML front matter 로 인식한다.

### 6.1 권장 형식

```markdown
---
title: KooRemapper IGA Guide
summary: 본 가이드는 KooRemapper의 IGA 기능 사용법을 설명한다.
tags: [IGA, NURBS, KooRemapper]
agents: [iga-analyst, doc-curator]
classification: internal
status: draft
author: 홍길동
doc_type: manual
version: 1.0
created: 2026-05-01
modified: 2026-05-08
---
```

### 6.2 키 매핑

| YAML 키 | meta 매핑 |
|---------|-----------|
| `title` | `meta.title` |
| `summary` | `meta.summary` |
| `tags`  | `meta.tags` (list) |
| `agents` | `meta.agent_scope` (list) |
| `author` | `meta.author` |
| `doc_type` | `meta.doc_type` (기본 `manual`) |
| `version` | `meta.version` |
| `created`, `modified` | `meta.created`, `meta.modified` |
| 그 외 (예: `classification`, `status`) | `meta.front_matter_extra.<키>` |

### 6.3 우선순위

- YAML front matter 가 있으면 **언제나 최우선**.
- front matter 의 tags/agents 가 비어 있을 때만 CLI `--tags`, `--agents` 값을 사용.
- `title` 이 누락되면 첫 번째 `# H1` 의 텍스트 → 그것도 없으면 파일명(stem) 사용.

### 6.4 YAML 파서

복잡한 YAML 의존을 피하기 위해 변환기는 자체 미니 파서를 사용한다.

지원:

- `key: value` (스칼라)
- `key: [a, b, c]` (인라인 리스트)
- ```yaml
  key:
    - a
    - b
  ```
  (블록 리스트)
- 따옴표 제거 (`"..."`, `'...'`)
- 불리언/숫자/null 자동 변환

지원하지 않음: 중첩 매핑, 앵커, 멀티라인 문자열. 필요 시 `pyyaml` 도입 검토.

---

## 7. 캡션 처리

### 7.1 1차 — alt text 가 캡션

```markdown
![브라켓 응력 분포](bracket.png)
```

→ `figures[0].caption == "브라켓 응력 분포"`

### 7.2 2차 — 직후 단락의 `Figure N: ...` 패턴

```markdown
![](image.png)

Figure 1: 정확한 설명
```

직전 figure 의 캡션이 비어 있거나 자동 생성된 경우, 다음 패턴의 단락이 등장하면 캡션으로 승격한다.

```
^(Figure|Fig\.?|그림|Table|Tbl\.?|표)\s*(\d+)\s*[:\.\-]\s*(.+)$
```

직전 figure 에 이미 alt 가 있으면 단락은 그대로 본문으로 둔다 (덮어쓰지 않음).

### 7.3 캡션 누락

alt 도 없고 다음 단락에 `Figure N:` 패턴도 없으면:

- `caption = "Figure N: (캡션 누락 — 검수 필요)"` 자동 생성
- `warnings` 에 경고 추가

---

## 8. 외부 링크 / 그림 처리

### 8.1 상대 경로 그림 — `file_path` 보존

```markdown
![분포](figures/bracket.png)
```

→ `attachments[0].file_path == "figures/bracket.png"`, `figures[0].image_path == "figures/bracket.png"`

후속 파이프라인이 `attachments_dir` 로 파일을 복사할 책임을 진다 (변환기는 경로만 보존).

### 8.2 절대 URL — `extra.url` 보존

```markdown
![알트](https://cdn.example.com/img.png)
```

→ `attachments[0].extra.url == "https://cdn.example.com/img.png"`, `file_path` 는 비어 있음.

`figures[0].image_path` 는 절대 URL 의 경우 **비어 있음** (image_path 는 정적 마운트 직하 상대 경로 슬롯이므로).

### 8.3 인라인 텍스트 링크

`[text](url)` 형식의 일반 링크는 단락 텍스트 안에 그대로 보존된다.

```markdown
자세한 내용은 [공식 문서](https://example.com/iga)를 참조.
```

→ block.text = `"자세한 내용은 [공식 문서](https://example.com/iga)를 참조."`

별도 attachment 로 추출하지 않는다 (그림이 아닌 일반 링크는 본문의 일부).

### 8.4 attachment kind 추정

URL 확장자 기반 자동 분류:

| 확장자 | kind |
|--------|------|
| `.png .jpg .jpeg .gif .svg .webp .emf .wmf` | `figure` |
| `.pdf .docx .hwp .txt` | `document` |
| `.xlsx .csv .ods` | `spreadsheet` |
| `.pptx .odp` | `slide` |
| `.mp4 .mov .mp3 .wav` | `media` |
| `.zip .tar .gz .7z` | `archive` |
| `.catpart .step .stp .iges .stl` | `cad` |
| `.dxf .dwg` | `drawing` |
| `.k .inp .cdb .json .yaml` | `data` |
| 그 외 | `other` |

단, `![]()` 마크다운은 의미상 항상 그림이므로 `attachments[].kind` 는 **`figure` 로 고정**한다 (확장자 추정은 `parser.infer_attachment_kind_from_url` 단독 헬퍼가 일반 첨부 분류용으로만 사용).

---

## 9. 변환기별 매핑 표

| 원본 요소 | Word 변환기 | PPT 변환기 | MD 변환기 |
|-----------|-------------|------------|-----------|
| 헤딩      | Heading 1/2/3 스타일 | 슬라이드 제목 + 번호 | `#` `##` `###` |
| 단락      | `<w:p>` (스타일 없음) | 텍스트 placeholder | `paragraph` 토큰 |
| 코드      | 등폭 글꼴 휴리스틱 | 등폭 텍스트 박스 | fenced code (```/~~~) |
| 리스트    | `<w:numPr>` | bullet placeholder | `-` / `1.` |
| 표        | `<w:tbl>` | shape table | GFM `\|---\|` 표 |
| 그림      | `<w:drawing>` | picture shape | `![](url)` |
| 인용      | 인용 스타일 (감지 어려움) | 별도 처리 없음 | `>` |
| 캡션      | Caption 스타일 + 패턴 | 텍스트 박스 패턴 | alt text + `Figure N:` 패턴 |
| 메타데이터 | `docProps/core.xml` + 마커 | 슬라이드 노트 + 마커 | YAML front matter |

---

## 10. 검증 후 DB 적재 체크리스트

생성된 JSON 이 [json_schema_rules.md](./json_schema_rules.md) 13장 검증 체크리스트를 통과해야 한다.

- [ ] `meta.doc_id` 가 `DOC-{div}-{group}-{year}-{seq:06d}` 패턴인가
- [ ] `meta.source_format == "md"`
- [ ] `meta.title` 이 비어 있지 않은가
- [ ] `meta.tags` 가 2개 이상인가 (front matter 또는 CLI)
- [ ] `meta.summary` 가 30자 이상인가 (front matter)
- [ ] 모든 `figure.id` 가 `figure_refs` 와 일치하는가
- [ ] 모든 `table.id` 가 `table_refs` 와 일치하는가
- [ ] `attachments[].file_path` 가 POSIX-style (forward slashes) 인가
- [ ] `attachments[kind=figure].extra.figure_ref` 가 `figures[].id` 와 일치하는가
- [ ] 절대 URL 그림은 `extra.url` 에 보존되었는가
- [ ] `tables[i].headers` 길이와 `rows[j]` 길이가 일치하는가
- [ ] `warnings` 가 비어 있거나 검수 가능한 수준인가

검증 실패 → `output/invalid/` 로 이동, 검수 큐 등록.

---

## 11. 라이브러리 의존성 + CLI 사용법

### 11.1 의존성

`api_server/requirements.txt` 에 추가:

```
markdown-it-py>=3.0
mdit-py-plugins>=0.4
```

설치:

```bash
pip install "markdown-it-py>=3.0" "mdit-py-plugins>=0.4"
```

### 11.2 CLI

```bash
python -m md_converter input.md \
    --team HE --group CAE --year 2026 --seq 7 \
    --output-dir output \
    --agents iga-analyst,doc-curator \
    --tags KooRemapper,IGA,NURBS
```

### 11.3 출력 파일

```
output/
├── DOC-HE-CAE-2026-0000000007.json
└── DOC-HE-CAE-2026-0000000007.warnings.log    (경고가 있을 때만)
```

### 11.4 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--team` | 팀 코드 (필수, 대문자화) | — |
| `--group` | 그룹 코드 (필수, 대문자화) | — |
| `--year` | 연도 (필수) | — |
| `--seq` | 순번 (6자리 패딩) | 1 |
| `--output-dir` | 출력 폴더 | `output` |
| `--agents` | agent_scope 콤마 구분 | `""` |
| `--tags` | meta.tags 콤마 구분 (front matter 보다 후순위) | `""` |
| `--verbose` | 상세 로그 | False |

### 11.5 라이브러리 사용

```python
from md_converter import MarkdownConverter, MarkdownConverterOptions, write_output

opts = MarkdownConverterOptions(team="HE", group="CAE", year=2026, seq=7)
conv = MarkdownConverter(opts)
result = conv.convert("path/to/input.md")          # 또는 conv.convert_text(md_string)
json_path, log_path = write_output(result, opts.output_dir)
```

---

## 12. 알려진 한계

| 한계 | 대응 |
|------|------|
| HTML 임베드(`<div>`, `<details>`)는 평문으로만 보존 | 작성자에게 순수 마크다운 권장 |
| markdown-it 사용자 정의 확장(footnote, def_list 등) 미지원 | 필요 시 `mdit-py-plugins` 추가 후 enable |
| 표 캡션 문법 부재 → 자동 캡션 `"Table N"` | 필요 시 표 직전 단락에 `Table N: ...` 작성 |
| 다단계 리스트 들여쓰기 평탄화 | depth 정보가 필요한 경우 별도 정책 정의 |
| 인라인 서식(볼드/이탤릭) 평문화 | 스키마 일관성을 위해 의도된 동작 |
| 수식(`$...$`) 미파싱 | 평문 보존 |
| 그림 바이너리 자동 복사 안 함 (외부 링크 모델) | 후속 파이프라인이 `attachments[].file_path` 따라 복사 |
| YAML 미니 파서 — 중첩 매핑 미지원 | 단순 키/값/리스트만 사용 권장 |

---

## 13. MD 작성 표준

좋은 변환 결과를 위한 작성 원칙. Word/PPT/Excel 의 작성 표준과 동일한 철학을 따른다.

### 13.1 헤딩 번호

- 자동 번호를 권장: `# 1. 개요`, `## 1.1 작동원리`, `### 1.1.1 NURBS`
- 변환기가 텍스트 앞 번호를 추출해 `section_id` 에 사용한다.
- 번호 없이 작성해도 무방 — 자동으로 1, 2, 1.1 ... 부여된다. 단 일관성 유지를 위해 권장한다.
- `####` 이상은 본문 단락으로 강등되므로, 주요 목차는 `###` 까지로 제한.

### 13.2 그림 alt text 를 캡션으로

```markdown
![브라켓 응력 분포](figures/bracket.png)
```

- alt text 를 **반드시** 작성 — 변환기가 캡션으로 사용한다.
- 빈 alt(`![](path)`)는 경고 발생 + 자동 캡션 `"Figure N: (캡션 누락 — 검수 필요)"`.
- 추가 캡션이 필요하면 그림 직후 단락을 `Figure 1: 추가 설명` 패턴으로 작성.

### 13.3 YAML front matter 로 메타데이터 명시

문서 머리에 YAML front matter 를 두는 것을 강력 권장.

```markdown
---
title: KooRemapper IGA 가이드
summary: 본 가이드는 ...
tags: [IGA, NURBS, KooRemapper]
agents: [iga-analyst]
---
```

- `title` 누락 시 첫 `# H1` 또는 파일명 사용.
- `tags`, `summary` 누락 시 경고. CLI `--tags`, 또는 사후 LLM 보조로 채울 수 있다.

### 13.4 코드 펜스에 언어 명시

```markdown
```python
def f(): ...
```
```

- 언어 태그를 명시하면 `block.marker = "lang:python"` 으로 보존된다.
- 언어 미명시 시 `marker` 비어 있음 — AI 분석 시 언어 추론 비용 발생.

### 13.5 표 작성

- 헤더 행 1줄 + 구분자 1줄(`|---|---|`) + 데이터 행 N줄.
- 셀 병합 없음 (CommonMark/GFM 모두 지원하지 않음).
- 단위는 헤더에 괄호로 명시 (`응력(MPa)`) — Excel 표준과 동일 정신.

### 13.6 인용은 `>`, 강조는 본문 단어

- 외부 인용이나 주의 사항은 `> ...` 로 마크업하면 별도 marker 로 구분된다.
- 단순 강조(`**bold**`, `*italic*`)는 변환 시 평문화되므로 의미 손실에 유의.

### 13.7 권장 디렉터리 구조

```
my_doc.md
figures/
├── bracket.png
├── stress.png
└── flow.svg
```

- 그림은 별도 `figures/` 폴더에 두고 상대 경로로 참조.
- 후속 파이프라인이 `attachments[].file_path` 를 따라 정적 마운트(`/attachments`) 로 복사한다.

---

*본 변환 규칙서는 [json_schema_rules.md](./json_schema_rules.md) 의 v1.3 출력 스키마 (호환성: v1.0 JSON 페이로드 그대로 사용 가능)를 기준으로 작성되었으며, 스키마가 변경될 때 함께 갱신됩니다.*

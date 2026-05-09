# PPT → JSON 변환 규칙서

## 자동화 프로그램 구현 가이드 v1.0

> 작성일: 2026-05-08
> 적용 대상: 표준 PowerPoint(.pptx) 문서를 [json_schema_rules.md](./json_schema_rules.md) 의 `data_type=DOC` JSON 으로 변환하는 자동화 프로그램.
> 변환기 구현체: `src/ppt_converter/` (python-pptx 기반)
> 자매 문서:
>
> - [json_schema_rules.md](./json_schema_rules.md) — 모든 변환기가 공유하는 JSON 스키마 (Agent 18 소유)
> - [word_to_json_conversion_rules.md](./word_to_json_conversion_rules.md) — Word 변환 규칙
> - `md_to_json_conversion_rules.md` — Markdown 변환 규칙 (별도 파일)

---

## 목차

1. [목적](#1-목적)
2. [입력 / 출력 명세](#2-입력--출력-명세)
3. [슬라이드 단위 섹션 매핑](#3-슬라이드-단위-섹션-매핑)
4. [슬라이드 제목 처리 + 섹션 번호 추출](#4-슬라이드-제목-처리)
5. [텍스트박스 → blocks](#5-텍스트박스--blocks)
6. [슬라이드 노트 → blocks](#6-슬라이드-노트)
7. [표·그림 추출 + 캡션](#7-표-그림-추출)
8. [첨부파일 (이미지·차트·임베드)](#8-첨부파일)
9. [캡션 작성 표준 (PPT 작성 표준 슬라이드 참조)](#9-캡션-작성-표준)
10. [변환기별 매핑 표 (PPT 요소 → JSON)](#10-매핑-표)
11. [비표준 PPT 처리](#11-비표준-ppt-처리)
12. [검증 후 DB 적재 체크리스트](#12-검증-체크리스트)
13. [라이브러리 의존성 + CLI 사용법](#13-cli-사용법)
14. [알려진 한계](#14-알려진-한계)

---

## 1. 목적

본 문서는 표준 PowerPoint 프레젠테이션(.pptx)을 [json_schema_rules.md](./json_schema_rules.md) 스키마(`data_type=DOC`, `source_format=pptx`)로 자동 변환하는 프로그램을 **AI 또는 개발자가 그대로 구현할 수 있도록** 모든 결정 사항을 명시한다.

PPT 는 Word 와 달리 **슬라이드 단위로 자연스러운 단절** 이 있고 본문 흐름이 약하다. 따라서 변환 정책의 핵심은 다음과 같다.

- 슬라이드 1장 = JSON 섹션 1개 (level 1 기본).
- 한 슬라이드 안의 도형 등장 순서가 곧 reading order — 시각적 좌우 배치는 보존되지 않는다.
- 발표자 노트(`notes_slide`)는 본문 흐름에 `[Speaker Notes]` 마커와 함께 추가된다.

---

## 2. 입력 / 출력 명세

### 2.1 입력

- **포맷**: `.pptx` (Office Open XML, PowerPoint 2007+)
- **전제 조건**:
  - 슬라이드 마다 제목 placeholder 가 채워져 있다.
  - 본문은 텍스트 placeholder 또는 일반 텍스트박스 안에 들어간다.
  - 표/그림은 PPT 내장 도형으로 삽입된다 (이미지로 캡처된 표 X, 임베디드 Excel 표 X).
  - 발표자 노트는 작성자가 의도적으로 추가한 경우에만 활용된다.

### 2.2 처리하지 않는 것 (범위 밖)

- `.ppt` (PowerPoint 97-2003 이진 포맷): 사전에 `.pptx` 로 변환되어 있어야 함.
- 매크로(VBA): 무시.
- 애니메이션 / 빌드 효과 / 슬라이드 전환: **보존되지 않음**.
- 마스터 슬라이드의 디자인 요소(헤더·푸터 이미지, 로고 등): 무시.
- SmartArt: 텍스트는 추출되지만 도해 구조는 평탄화된다.

### 2.3 출력

- 단일 JSON 파일 (스키마 v1.0, `data_type=DOC`)
- 추출된 이미지/임베드는 별도 폴더 `{output_dir}/{doc_id}/` 하위 (Word 변환기와 동일 정책)
- 경고 로그: `{doc_id}.warnings.log`

---

## 3. 슬라이드 단위 섹션 매핑

### 3.1 기본 매핑

| PPT 요소               | JSON 필드                |
|-----------------------|--------------------------|
| 슬라이드 N (1-based)  | `sections[N-1]` (level 1 기본) |
| 슬라이드 제목          | `sections[i].title`       |
| 본문 placeholder       | `blocks[]` (`paragraph` / `list_item`) |
| 표 도형                | `tables[]` + `blocks[type=table]` |
| 그림 도형              | `figures[]` + `attachments[]` + `blocks[type=figure]` |
| 차트 도형              | `figures[]` + `attachments[kind=other]` + `blocks[type=figure]` (placeholder 만) |
| 발표자 노트            | `[Speaker Notes]` 마커 + `paragraph` blocks |

### 3.2 reading order

한 슬라이드 안에서 도형 처리 순서는 **python-pptx 의 `slide.shapes` 순회 순서** 와 동일하다. 이는 보통 z-order 와 일치하며, **시각적 좌우 / 위아래 배치 정보는 보존되지 않는다.**

따라서 좌측 텍스트박스 + 우측 그림 같은 슬라이드는 변환 후 텍스트와 그림이 어떤 순서로 나올지 작성자가 통제할 수 없다 — 11.2 절 참조.

### 3.3 빈 슬라이드

제목/본문/노트가 모두 비어 있으면 경고 로그를 남긴다 (`슬라이드 N: 제목/본문/노트가 모두 비어 있음`). 섹션은 그대로 생성된다.

---

## 4. 슬라이드 제목 처리

### 4.1 제목 추출 우선순위

```
1) slide.shapes.title placeholder 의 텍스트
2) placeholder 중 ph_type 이 TITLE / CTR_TITLE 인 것
3) 첫 번째 텍스트 프레임의 첫 줄 (제목 placeholder 가 아예 없는 슬라이드용 폴백)
4) 모두 비어 있으면 → "슬라이드 N" 자동 생성 + 경고
```

### 4.2 섹션 번호 추출

슬라이드 제목이 "1.2 작동원리" 같은 번호 패턴으로 시작하면 번호와 제목을 분리한다.

```python
SECTION_NUM_PATTERN = re.compile(r"^(\d+(?:\.\d+){0,2})[\.\)]?\s+(.*)$")
m = SECTION_NUM_PATTERN.match(title.strip())
if m:
    section_id, clean_title = m.group(1), m.group(2).strip()
    level = section_id.count(".") + 1   # 1.2 → 2, 1.2.3 → 3
```

### 4.3 자동 번호 부여

번호 패턴이 없는 제목은 자동 카운터로 번호를 부여한다.

```
counter_l1 = 0
counter_l2 = 0
counter_l3 = 0

for each slide in order:
    if 제목에 번호 없음 → level 1 자동 번호 (counter_l1 += 1)
    if 제목에 "X.Y" 같은 번호 → level 2, parent 는 가장 최근 level 1
    if 제목에 "X.Y.Z" → level 3, parent 는 가장 최근 level 2
```

부모 레벨 섹션이 없는 상태에서 level 2/3 제목이 나타나면 **level 1 로 폴백** 하고 경고 로그에 남긴다.

### 4.4 동기화

원본 제목에 "3.1" 이 들어 있으면 자동 카운터를 그 값에 맞춰 동기화한다. 다음 자동 번호는 그 다음 값부터 시작한다.

---

## 5. 텍스트박스 → blocks

### 5.1 paragraph vs list_item 판정

```
텍스트박스 안의 단락이 단 1개 + level 0 (들여쓰기 없음) → paragraph
그 외 (다중 단락 또는 들여쓰기 있음)                  → 모든 단락을 list_item 으로 변환
```

이는 PPT 의 본문 placeholder 가 자연스럽게 글머리 기호 리스트로 동작하는 것과 일치한다.

### 5.2 list_item 마커

들여쓰기 단계 (`paragraph.level`) 별로 마커 + 들여쓰기를 부여한다.

| level | marker     |
|-------|-----------|
| 0     | `"•"`      |
| 1     | `"  •"`    |
| 2     | `"    •"`  |

(공백 2칸 단위)

### 5.3 인라인 서식

볼드, 색상, 폰트 등 인라인 서식은 **모두 무시** 한다. JSON 스키마는 서식을 표현하지 않는다.

### 5.4 빈 단락 / 공백

빈 단락은 무시되고 blocks 에 포함되지 않는다.

---

## 6. 슬라이드 노트

### 6.1 추출 위치

```python
slide.notes_slide.notes_text_frame.text
```

`slide.has_notes_slide` 가 False 거나 텍스트가 비어 있으면 노트가 없는 것으로 처리.

### 6.2 blocks 삽입 형태

본문 도형 처리가 모두 끝난 뒤 노트가 있으면 다음 형태로 추가:

```json
{"type": "paragraph", "text": "[Speaker Notes]"},
{"type": "paragraph", "text": "노트 첫 줄"},
{"type": "paragraph", "text": "노트 둘째 줄"}
```

`[Speaker Notes]` 단락이 마커 역할을 한다. 이후 모든 paragraph 는 노트의 줄 단위 텍스트.

### 6.3 노트의 인라인 서식 / 글머리

노트는 거의 항상 평문으로 작성되므로 서식·글머리는 무시한다.

---

## 7. 표·그림 추출

### 7.1 표

```
GraphicFrame.has_table == True
→ 첫 행을 헤더로 추출
→ 둘째 행부터 데이터
→ 빈 셀은 None
→ 정수/실수 패턴 (^-?\d+$, ^-?\d+\.\d+$) 만 숫자로 변환, 그 외는 문자열 보존
```

| 동작                                                          | 처리              |
|---------------------------------------------------------------|------------------|
| 헤더 행이 비어 있음                                            | `col1, col2, ...` 자동 헤더 + 경고 |
| 일부 행이 헤더 길이와 다름                                     | 그대로 두고 경고  |
| 셀 안에 여러 단락                                              | `\n` 으로 연결    |
| 셀 안에 그림                                                  | 미지원 (PPT 표는 셀 그림이 거의 없음) |

### 7.2 그림 (PICTURE shape)

```
shape.shape_type == MSO_SHAPE_TYPE.PICTURE
→ shape.image.blob 으로 바이너리 추출
→ ext 는 image.ext (예: "png") 또는 content_type 에서 유추
→ 파일 저장:  {output_dir}/{doc_id}/F{nnn}.{ext}
→ image_path: "{doc_id}/F{nnn}.{ext}"  (POSIX, slash-only)
```

### 7.3 그림 ↔ 첨부 일반화

Word 변환기와 동일하게, **모든 그림은 `figures[]` 에 추가되고 `attachments[]` 에도 `kind=figure` 로 등록** 된다. attachment 측에는 `extra.figure_ref` 로 figure id 를 함께 기록한다.

### 7.4 캡션 자동 생성

PPT 는 Word 의 "그림 아래 캡션" 같은 표준이 약하므로 **변환기가 캡션을 자동으로 채운다.**

| 종류 | 캡션 포맷                              |
|------|----------------------------------------|
| 그림 | `"Figure {N}: 슬라이드 {S} 이미지"`     |
| 표   | `"Table {N}: 슬라이드 {S} 표"`           |
| 차트 | `"Figure {N}: 차트 — {차트 제목}"` (제목 없으면 `"Figure {N}: 차트"`) |

작성자가 의미 있는 캡션을 원하면 **그림 바로 아래에 별도 텍스트박스를 두고 거기에 설명을 적는다**. 이 텍스트박스는 일반 paragraph 로 추출된다 (자동 캡션과 별도). 향후 버전에서 "그림 인접 텍스트박스 = 캡션" 휴리스틱을 추가할 수 있다.

### 7.5 그림 / 표 / 차트 ID

```
figure id : DOC-{div}-{team}-{year}-{seq:06d}-F{nnn}
table id  : DOC-{div}-{team}-{year}-{seq:06d}-T{nnn}
attach id : DOC-{div}-{team}-{year}-{seq:06d}-A{nnn}
```

번호는 슬라이드 경계와 무관하게 문서 전체에서 1부터 부여된다.

---

## 8. 첨부파일

### 8.1 첨부 종류

| `kind`        | 발생 조건                                              |
|---------------|--------------------------------------------------------|
| `"figure"`    | PICTURE shape — 동일 그림이 figures 에도 등록됨        |
| `"other"`     | CHART shape — 데이터 추출 미구현, 메타만 보존          |

### 8.2 file_path 형식

cross-platform 호환성을 위해 항상 POSIX-style (forward slash) 상대경로:

```
"DOC-HE-CAE-2026-000001/A001.png"
```

API 서버의 `/attachments` 정적 마운트가 이 경로 prefix 를 그대로 사용한다.

### 8.3 메타 보존

| 필드          | 출처                                       |
|---------------|--------------------------------------------|
| `file_name`   | `A{nnn}.{ext}` (변환기가 부여한 안전한 이름)|
| `mime_type`   | 확장자 → MIME 매핑 (`png` → `image/png`)    |
| `size_bytes`  | 저장 후 파일 stat                           |
| `hash_sha256` | 저장 후 sha256 계산                         |
| `extra`       | `{"figure_ref": ...}`, 차트면 `{"chart_title": ...}` 추가 |

### 8.4 임베디드 OLE 객체

PPT 슬라이드에 임베드된 Excel/PDF/Visio 등은 **현재 미지원** 이며 인식되더라도 별도 첨부로 등록되지 않는다 (향후 확장 영역). 임베드된 파일을 보존하려면 작성자가 별도로 원본 파일을 외부에 두고 본문에 경로를 참조하도록 한다.

---

## 9. 캡션 작성 표준

PPT 는 Word 의 "캡션 삽입" 메뉴 같은 강제 표준이 없다. 따라서 **PPT 작성 표준 슬라이드** 에 다음 가이드를 포함한다.

### 9.1 5가지 작성 원칙

1. **1슬라이드 1주제** — 한 슬라이드에 여러 표/그림을 섞지 않는다. 변환기가 슬라이드 단위로 섹션을 나누므로 한 슬라이드의 표·그림은 모두 같은 섹션에 묶인다.
2. **번호 제목 권장** — `"1.2 작동원리"` 처럼 제목에 번호를 직접 포함하면 섹션 ID 가 보존된다. 그렇지 않으면 자동 번호가 부여된다.
3. **캡션은 그림 바로 아래 텍스트박스** — 캡션은 별도 텍스트박스에 적는다 (자동 캡션과 별개로 본문 paragraph 로 추출됨). 향후 휴리스틱이 도입될 때 인식되도록 위치는 그림 바로 아래에 둔다.
4. **표는 PPT 내장 표 도형 사용** — 이미지로 캡처한 표나 임베디드 Excel 은 데이터로 추출되지 않는다.
5. **발표자 노트는 변환 대상** — 슬라이드 위에 표시되지 않는 보조 설명은 노트에 적으면 LLM 이 활용할 수 있다.

### 9.2 비권장

- 텍스트박스를 화려하게 좌우로 분할 (변환 시 순서가 z-order 임의 결과)
- 차트 (현재는 placeholder 만 등록됨)
- SmartArt (텍스트는 추출되지만 구조는 손실됨)
- 슬라이드 마스터의 헤더/푸터에 의미 있는 정보를 두는 것

### 9.3 PPT 작성 표준 — 본문 라벨링

§11.7.3 의 본문 H2/H3 자동 인식 휴리스틱이 발동하도록 작성자는 다음 4가지 양식을 권장한다. 작성 표준을 따른 PPT 는 한 슬라이드 안에서도 RAG 청크가 sub-section 단위로 세분화된다.

#### 1. 본문 첫 줄에 H2 번호 + 부제

본문 placeholder 의 첫 list_item (또는 paragraph) 은 `"1.1 부제"`, `"1.2 부제"`, `"1.1.1 세부"` 형식으로 시작한다. 점 1개 이상이 있어야 인식된다 — 단독 `"1"` 은 슬라이드 제목 영역의 책임이므로 본문에 두지 않는다. 한 슬라이드에 여러 H2 를 두는 것도 허용되지만 같은 id 가 두 번 나오지 않게 작성자가 관리한다.

```text
1.1 AI 도입 및 활용 현황
1.2.1 도입 단계별 어려움
```

#### 2. Claim → Evidence 구조

H2/H3 라인 다음에는 들여쓴 list_item (`paragraph.level≥1`) 으로 근거를 나열한다. 들여쓴 단락은 sub-section 의 blocks 로 흡수되어 Claim 과 Evidence 가 같은 청크 안에 묶인다. 들여쓰기 없이 평탄하게 적으면 Evidence 는 부모 슬라이드 섹션의 blocks 로 빠진다.

```text
1.1 AI 도입 및 활용 현황
  • 설문 조사에서 도입율 X% 로 보고됨
  • 사내 파일럿 사례 N건 진행 중
```

#### 3. Figure N. 형식 캡션

그림 도형 직후 paragraph 에 `"Figure N. 설명"` 명시 — 변환기 자동 캡션(`"Figure {N}: 슬라이드 {S} 이미지"`)은 위치 정보뿐이므로, 의미 있는 캡션이 필요하면 작성자가 별도 텍스트박스를 그림 바로 아래에 둔다. 표 캡션은 `"Table N. 설명"` 으로 동일 양식을 쓴다.

```text
[그림 도형]
Figure 3. 도입 단계별 만족도 분포
```

#### 4. 산문 → 표 변환

정량 정보(수치, 비교, 분류)는 산문 list_item 으로 풀어 쓰지 않고 PPT 내장 표 도형으로 작성한다. 이미지로 캡처한 표나 임베디드 Excel 은 `tables[]` 로 추출되지 않는다 (§11.3 / §2.1 전제 조건).

```text
[표 도형]
| 단계 | 만족도 | 응답 수 |
| 도입 | 72%   | 120    |
| 운영 | 65%   | 95     |
```

---

## 10. 매핑 표

| PPT 요소                                  | python-pptx API                       | JSON 출력                                       |
|------------------------------------------|--------------------------------------|------------------------------------------------|
| 슬라이드                                  | `prs.slides[i]`                       | `sections[i]`                                  |
| 슬라이드 제목                              | `slide.shapes.title.text`             | `sections[i].title` + (선택) `id` 번호           |
| 텍스트 placeholder / 텍스트박스            | `shape.has_text_frame`                | `paragraph` / `list_item` blocks                |
| 단락                                      | `text_frame.paragraphs[j]`            | `block.text`                                    |
| 단락 들여쓰기                              | `paragraph.level`                     | `block.marker` 의 들여쓰기 칸 수                 |
| 표 (GraphicFrame)                          | `shape.has_table`, `shape.table`      | `tables[]` + `blocks[type=table, ref=...]`      |
| 그림                                      | `shape.shape_type == PICTURE`         | `figures[]` + `attachments[kind=figure]` + `blocks[type=figure]` |
| 차트                                      | `shape.shape_type == CHART`, `shape.chart` | `figures[]` (placeholder) + `attachments[kind=other]` |
| 그룹 도형                                  | `shape.shape_type == GROUP`           | 자식 도형으로 평탄화                              |
| 발표자 노트                                | `slide.notes_slide.notes_text_frame.text` | `[Speaker Notes]` 마커 + `paragraph` blocks    |
| Core properties (작성자/제목/날짜)          | `prs.core_properties`                 | `meta.author` / `meta.title` / `meta.created`   |

---

## 11. 비표준 PPT 처리

### 11.1 제목 placeholder 가 없는 슬라이드

폴백 순서로 첫 텍스트 프레임의 첫 줄을 제목으로 사용한다 (4.1 절). 그것마저 비어 있으면 `"슬라이드 N"` 자동 제목 + 경고.

### 11.2 텍스트박스 좌우 배치

같은 슬라이드에 좌측 텍스트박스 + 우측 텍스트박스가 있으면, 두 박스의 paragraph 들이 **z-order 순서로** blocks 에 추가된다. 시각적 좌우 정보는 손실된다.

대응:
- 작성자에게 좌우 분할 대신 **위→아래 단일 흐름** 권장.
- 향후 버전에서 `shape.left` / `shape.top` 좌표를 활용한 정렬을 옵션으로 도입 검토.

### 11.3 셀 병합된 표

PPT 표의 병합 셀 처리는 python-pptx 가 좌상단 값을 모든 셀에 복제해주지 않으므로, **현재는 좌상단 값 외에는 빈 문자열** 로 들어올 수 있다. 작성자에게 표 작성 시 **병합 금지 + 같은 값을 반복 입력** 을 권장한다.

### 11.4 그림에 캡션 없음

자동 캡션 (`"Figure N: 슬라이드 S 이미지"`) 이 자동 채워지므로 변환은 실패하지 않는다. 의미 있는 캡션을 원하는 경우 그림 바로 아래에 텍스트박스를 두고 작성한다.

### 11.5 차트

차트 데이터 (series / categories / values) 추출은 **현재 버전에서 미구현** 이다. 차트가 발견되면 placeholder figure 를 만들고 attachment 메타에 `chart_title` 을 보존하며, 경고 로그를 남긴다. 차트 데이터 표가 필요하면 작성자가 동일 슬라이드에 PPT 내장 표를 추가한다.

### 11.6 SmartArt

SmartArt 는 도형 그룹으로 평탄화되어 텍스트만 추출된다 (구조는 손실). 도해가 중요한 경우 캡처 이미지로 변환해서 그림으로 다시 삽입한다.

### 11.7 자동 적응 휴리스틱 (default ON)

실 데이터(작성자가 변환 양식을 모르는 PPT) 에서 RAG 친화도를 회복하기 위해 **default 활성화** 된 두 가지 후처리.

#### 11.7.1 연속 동일 제목 그룹화 (`group_consecutive_duplicates=True`)

연속 N(≥2)개의 슬라이드가 같은 제목을 공유하면, 첫 번째를 level 1 부모로 두고 나머지 N-1개를 level 2 자식으로 자동 이동한다.

| 입력 (슬라이드 제목 순서) | 출력 (sections 트리) |
|---------------------------|----------------------|
| `["A", "A", "A", "B"]` | `[A (1/3), children=[A (2/3) id=1.1, A (3/3) id=1.2], B id=2]` |
| `["X", "Y", "Y", "Z"]` | `[X, Y (1/2), children=[Y (2/2) id=2.1], Z]` |

자식 ID 는 `{parent.id}.{k}` 형식, 제목에 `(k/N)` 위치 표기 자동 추가.

CLI 끄기: `--no-group-duplicates`

실 측정 결과: 23개 평탄 슬라이드의 강의자료 → 부모 1 + 자식 22의 트리, 54개 평탄 슬라이드의 보고서 → 7 top + 47 자식 분배.

#### 11.7.2 summary 자동 폴백 (`extract_summary=True`)

`meta.summary` 가 비어 있을 때 다단계 폴백:

```
1) core_properties.subject (≥10자)
2) 표지 다음 슬라이드(또는 1슬라이드뿐이면 슬라이드 1)의
   paragraph / list_item 텍스트 합성 (~250자)
3) 모두 실패 → 빈 문자열 + "summary 미지정" 경고
```

CLI 끄기: `--no-extract-summary`

작성자가 양식을 안 지킨 PPT 도 RAG 검색 가중치를 잃지 않게 하는 방어선이다 — 작성 표준을 준수한 PPT 는 영향 없음 (core.subject 가 채워진 경우 그것 그대로 사용).

#### 11.7.3 본문 H2/H3 자동 인식 (`extract_body_headings=True`, default ON)

슬라이드 본문(`paragraph` / `list_item`) 의 텍스트가 `1.1`, `1.2`, `1.2.3` 같은 다단계 번호 패턴으로 시작하면 sub-section 으로 자동 승격된다. 슬라이드 제목 한 장 = level 1 섹션 한 개라는 4절 정책을 깨지 않으면서, 한 슬라이드 안에 H2/H3 단위 RAG 청크를 만들기 위한 보조 휴리스틱이다.

**인식 정규식**

```python
BODY_HEADING_PATTERN = re.compile(r"^(\d+\.\d+(?:\.\d+)?)[\.\)]?\s+(.+)$")
```

적어도 점 1개 이상이 필수다. `1`, `2` 단독 숫자는 슬라이드 제목 영역(§4.2) 책임이므로 본문에서는 무시된다. `1.1`, `1.2.3` 만 매칭된다.

**동작 사양**

| 항목     | 값                                                             |
|----------|---------------------------------------------------------------|
| `id`     | 매칭된 번호 그대로 (예: `"1.1"`, `"1.2.3"`)                     |
| `level`  | `id.count(".") + 1` (`1.1` → 2, `1.2.3` → 3)                  |
| `title`  | 매칭 본문 (번호와 구분자 제거 후 strip)                         |
| `parent` | 해당 슬라이드의 level 1 섹션 (= 슬라이드 자체)                 |
| `blocks` | 매칭된 단락 다음으로 이어지는 들여쓴 단락(level≥1)이 자식 blocks 로 흡수됨 |

매칭된 paragraph / list_item 자체는 sub-section 의 `title` 로 사용되고 blocks 에서는 제거된다 (제목 중복 방지).

**옵션 끄기**

CLI 끄기: `--no-extract-body-headings` — 본문 번호 패턴이 보여도 평문 list_item 으로 둔다.

**id 충돌 폴백**

같은 슬라이드 안에서 동일 `id` (예: `"1.1"`) 가 두 번 등장하면 두 번째는 자동 카운터 폴백 (`{slide_id}.h{k}` 형태) 으로 임시 id 를 부여하고 경고 로그에 남긴다 (`슬라이드 N: 본문 헤딩 id 중복 — 1.1 → 자동 폴백`).

**전후 변환 예 (ppt수정예제 슬라이드 2)**

원본 본문 list_item 시퀀스:

```
"1.1 AI 도입 및 활용 현황"
  "주요 설문 조사에서 도입율 X% 로 보고됨"
  "사내 파일럿 사례 N건 진행 중"
```

변환 결과 (`extract_body_headings=True`):

```json
{
  "id": "DOC-...-S002",
  "level": 1,
  "title": "슬라이드 2 제목",
  "blocks": [],
  "children": [
    {
      "id": "1.1",
      "level": 2,
      "title": "AI 도입 및 활용 현황",
      "blocks": [
        {"type": "list_item", "marker": "  •",
         "text": "주요 설문 조사에서 도입율 X% 로 보고됨"},
        {"type": "list_item", "marker": "  •",
         "text": "사내 파일럿 사례 N건 진행 중"}
      ]
    }
  ]
}
```

슬라이드 3 의 `"1.2 AI 활용 시 가장 큰 어려움은 ..."` 도 동일한 방식으로 level 2 sub-section 으로 추출되며, 그 아래 들여쓴 list_item 들은 Claim → Evidence 흐름을 유지한 채 sub-section 의 blocks 로 들어간다.

본문 번호를 단 적이 없는 PPT 는 이 휴리스틱이 발동하지 않으므로 기존 출력과 동일하다 — 작성 표준을 따른 PPT 만 RAG 청크 단위가 세분화된다.

---

## 12. 검증 체크리스트

DB 적재 전에 변환 결과 JSON 을 다음 항목으로 검증한다 (`*.warnings.log` 도 함께 검토).

### 12.1 거부 항목 (변환 실패)

- [ ] 파일이 .pptx 포맷인가 (확장자 + ZIP 매직 바이트 확인)
- [ ] python-pptx 가 정상적으로 `Presentation()` 호출에 성공하는가
- [ ] 슬라이드가 1장 이상 있는가

### 12.2 경고 항목 (변환은 진행)

- [ ] 모든 슬라이드에 제목이 있는가
- [ ] 그림/차트가 있는데 자동 캡션이 그대로 사용되었는가 → 검수 후 캡션 보강 권장
- [ ] 차트가 있는가 → 데이터 미추출 경고 — 필요 시 표 별도 작성
- [ ] level 2/3 제목이 부모 없이 등장하여 root 폴백되었는가
- [ ] tags / summary 가 비어 있는가 (CLI 인자나 사후 LLM 호출로 보강)

### 12.3 사후 자동 검증

```python
def validate_output(json_obj):
    errors = []

    # 최상위 키
    required_top = {"schema_version", "meta", "toc", "sections",
                    "figures", "tables", "sources", "attachments"}
    missing = required_top - json_obj.keys()
    if missing:
        errors.append(f"최상위 키 누락: {missing}")

    # doc_id 형식 (PPT 도 Word 와 동일)
    if not re.match(r"^DOC-[A-Z]{2,4}-[A-Z]{2,5}-\d{4}-\d{6}$",
                    json_obj["meta"]["doc_id"]):
        errors.append(f"doc_id 형식 오류: {json_obj['meta']['doc_id']}")

    # source_format
    if json_obj["meta"].get("source_format") != "pptx":
        errors.append("source_format 이 'pptx' 가 아님")

    # figure / attachment 참조 일치
    fig_ids = {f["id"] for f in json_obj["figures"]}
    referenced_fig_ids = collect_all_figure_refs(json_obj["sections"])
    if not referenced_fig_ids.issubset(fig_ids):
        errors.append(
            f"figure_refs 와 figures.id 불일치: "
            f"{referenced_fig_ids - fig_ids}"
        )

    # 표 길이 일치
    for tbl in json_obj["tables"]:
        h_len = len(tbl["headers"])
        for i, row in enumerate(tbl["rows"]):
            if len(row) != h_len:
                errors.append(
                    f"표 {tbl['id']}, 행 {i}: "
                    f"길이 {len(row)} ≠ headers {h_len}"
                )

    return errors
```

---

## 13. CLI 사용법

### 13.1 라이브러리 의존성

```
python-pptx>=0.6.21    # 슬라이드/도형/이미지/표/차트 파싱
Pillow                  # python-pptx 가 의존, 이미지 메타 처리
lxml                    # python-pptx 가 의존, XML 파싱
```

### 13.2 설치

```powershell
& "d:\Personal\AI_data\api_server\.venv\Scripts\python.exe" -m pip install "python-pptx>=0.6.21"
```

또는 `requirements.txt` 의 `python-pptx>=0.6.21` 항목을 통해 일괄 설치.

### 13.3 CLI 실행

```powershell
$env:PYTHONPATH = "d:\Personal\AI_data\api_server\src"

& "d:\Personal\AI_data\api_server\.venv\Scripts\python.exe" -m ppt_converter `
    "d:\path\to\slides.pptx" `
    --division HE `
    --team CAE `
    --year 2026 `
    --seq 1 `
    --output-dir "d:\path\to\output" `
    --tags "PPT,튜토리얼" `
    --agents "iga-analyst"
```

| 인자                     | 설명                                                       |
|--------------------------|-----------------------------------------------------------|
| `pptx_path`              | 입력 .pptx 경로 (필수)                                      |
| `--division`             | 팀 코드 (예: HE)                                            |
| `--team`                 | 그룹 코드 (예: CAE)                                         |
| `--year`                 | 연도 (예: 2026)                                             |
| `--seq`                  | 순번 (기본 1)                                               |
| `--output-dir`           | 출력 폴더 (기본 ./output)                                    |
| `--tags`                 | 콤마로 구분된 태그 목록                                       |
| `--agents`               | 콤마로 구분된 agent_scope                                    |
| `--no-extract-images`    | 그림 바이너리 추출 비활성화                                  |
| `--verbose, -v`          | 상세 로그 출력                                               |

### 13.4 출력물

```
output/
├── DOC-HE-CAE-2026-000001.json
├── DOC-HE-CAE-2026-000001.warnings.log
└── DOC-HE-CAE-2026-000001/
    ├── F001.png
    ├── F002.jpg
    ├── A001.png
    └── A002.jpg
```

---

## 14. 알려진 한계

| 한계                                                | 대응                                                            |
|----------------------------------------------------|-----------------------------------------------------------------|
| 애니메이션 / 빌드 효과 / 슬라이드 전환 보존 안 됨  | 본문 정보로만 변환되므로 작성자에게 텍스트만으로 의미 전달 권장. |
| 시각적 좌우 / 위아래 배치 정보 손실                  | 1슬라이드 1주제 + 위→아래 흐름 권장 (11.2 절).                   |
| 차트 데이터 미추출                                   | placeholder figure + 경고. 데이터가 필요하면 동일 슬라이드에 표 도형으로 다시 작성. |
| SmartArt 도해 구조 손실                              | 텍스트만 추출됨. 도해가 중요하면 캡처 이미지로 변환해 그림으로 삽입. |
| 임베디드 OLE (Excel, PDF, Visio) 미지원              | 별도 첨부로 등록되지 않음. 외부 파일을 두고 본문에 경로 참조 권장. |
| 표 셀 병합 정보 손실                                | 병합 금지 + 값 반복 입력 권장 (11.3 절).                          |
| 그림 자동 캡션은 위치 정보뿐                         | 의미 있는 캡션이 필요하면 작성자가 별도 텍스트박스에 작성.        |
| 마스터 슬라이드의 헤더/푸터/로고 무시                 | 의미 있는 정보는 본문 영역에 두기.                                |

---

*본 변환 규칙서는 [json_schema_rules.md](./json_schema_rules.md) 의 v1.0 출력 스키마와 `src/ppt_converter/` (python-pptx 기반) 구현체를 기준으로 작성되었으며, 스키마/구현이 변경될 때 함께 갱신됩니다.*

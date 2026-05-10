# Excel → JSON 변환 규칙서

## 자동화 프로그램 구현 가이드 v1.3 (코드/룰 완전 동기)

## 자동화 프로그램 구현 가이드 v1.0

> 작성일: 2026-05-08
> 적용 대상: 표 데이터(.xlsx) 를 [json_schema_rules.md](./json_schema_rules.md) 의
> `data_type = "DATA"` 스키마로 변환하는 자동화 프로그램
> 변환기 구현: `api_server/src/excel_converter/` (openpyxl 기반)
>
> 시작점: [`CONVERSION_RULES_INDEX.md`](./CONVERSION_RULES_INDEX.md)
> 자매 문서 (모두 동일 JSON 스키마 출력):
>
> - [json_schema_rules.md](./json_schema_rules.md) — JSON 스키마 전반 (모든 변환기 공통)
> - [word_to_json_conversion_rules.md](./word_to_json_conversion_rules.md) — Word 변환 규칙
> - [ppt_to_json_conversion_rules.md](./ppt_to_json_conversion_rules.md) — PPT 변환 규칙
> - [md_to_json_conversion_rules.md](./md_to_json_conversion_rules.md) — Markdown 변환 규칙
> - [pdf_to_json_conversion_rules.md](./pdf_to_json_conversion_rules.md) — PDF 변환 규칙 (OCR opt-in)
> - [html_to_json_conversion_rules.md](./html_to_json_conversion_rules.md) — HTML 변환 규칙

---

## 0. 코드 정합 노트 (필독)

본 문서는 [`api_server/src/excel_converter/`](./api_server/src/excel_converter/) 의 실제 출력을 단일 진실 공급원으로 한다.

| 항목 | 변환기 출력 / 코드 위치 | normalizer 흡수 / DB |
|---|---|---|
| 페이로드 형식 | `schema_version="data.v1"` (별도 변종, 표준 7-키 아님). `excel_converter/core.py:125-159` | `_extract_data()` (`normalizer.py:147-160`) 가 `data_id`/`caption`/`headers`/`rows`/`units` 를 RecordIn 으로 매핑 |
| 식별자 | top-level `data_id` (예: `DATA-HE-CAE-2026-000034`) | `RecordIn.id` |
| 단위 | `units` (객체) **+ `units_map` (동일내용 alias)** 두 키 모두 출력. `core.py:145, 158` | 신규 소비자는 `units` 권장 |
| `meta.agent_scope` (옵션) | `_META.agents` 배열을 `agent_scope` 로 출력 | normalizer 폴백 — `meta.agent_scope` 우선 |
| 분류/생애주기 (0006 10개) | `_META` 시트의 `classification`/`status`/`domain`/`subject_keywords`/`source_system`/`language`/`derivation`/`quality_score`/`valid_from`/`valid_until` → `payload["meta"].*` → normalizer `_common_fields` 흡수 (`normalizer.py:240-274`) ✅ | `records.classification` 등 |
| 0007 agent-discovery 자동 채움 | `agent_hints`/`query_examples`/`access_pattern` 자동 생성 (`excel_converter/core.py:230-247` `to_payload` 가 `payload["meta"]` 에 기록) | `records.agent_hints` 등 |

(v1.2 의 "KNOWN GAP" 은 v1.3 커밋 `c2c66c6` 에서 해소.)

---

## 목차

1. [목적](#1-목적)
2. [입력 / 출력 명세](#2-입력--출력-명세)
3. [Excel 작성 6원칙](#3-excel-작성-6원칙)
4. [CLI 옵션 상세](#4-cli-옵션-상세)
5. [헤더 자동 감지 알고리즘](#5-헤더-자동-감지-알고리즘)
6. [셀 값 타입 추론 규칙](#6-셀-값-타입-추론-규칙)
7. [단위 분리 규칙](#7-단위-분리-규칙)
8. [병합 셀 처리](#8-병합-셀-처리)
9. [불규칙 Excel 처리](#9-불규칙-excel-처리)
10. [`_META` 시트 표준 (데이터의 의미 명시)](#10-_meta-시트-표준-데이터의-의미-명시)
11. [`_GLOSSARY` 시트 표준 (컬럼 의미 정의)](#11-_glossary-시트-표준-컬럼-의미-정의)
12. [메타 컨텍스트 우선순위](#12-메타-컨텍스트-우선순위)
13. [변환기별 매핑 표](#13-변환기별-매핑-표)
14. [변환 후 검증](#14-변환-후-검증)
15. [알려진 한계](#15-알려진-한계)

---

## 1. 목적

본 문서는 표 데이터 중심의 Excel 워크북(.xlsx) 을 자동화 가능한 결정 규칙으로
DATA JSON 으로 변환하기 위한 가이드다. Excel 은 본문 흐름(narrative) 이 없는
2-D 표 컨테이너이므로 **시트 1개 = JSON 1건(레코드)** 을 기본 단위로 한다.

문서(Word/PPT) 변환과 달리, Excel 변환은 다음 점에서 다르다.

- 헤딩 트리(sections) 가 없다 — 표 헤더와 행이 직접 JSON 에 매핑된다.
- 그림·표 인덱싱이 없다 — 시트 자체가 곧 표다.
- summary/tags 자동 생성이 의미가 없다 — 시트 이름과 컬럼 목록이 메타데이터를 대체.

---

## 2. 입력 / 출력 명세

### 2.1 입력

- **포맷**: `.xlsx` (Microsoft Excel 2007+ Office Open XML)
- `.xls` (구형 BIFF) 와 `.xlsm` (매크로 포함) 은 사전에 `.xlsx` 로 저장 후 변환.
- 암호화된 워크북은 거부.

### 2.2 출력

- 변환 모드별 출력 단위:

| 모드               | 출력                                | 사용 시점                                |
|--------------------|-------------------------------------|------------------------------------------|
| `per_sheet` (기본) | 시트마다 별도 DATA record 생성      | 각 시트가 독립 주제일 때 (1시트 1주제)   |
| `combined`         | 모든 시트를 하나의 record 로 묶음   | 데이터셋이 시트별로 쪼개진 경우          |

- 파일 경로: `{output_dir}/{data_id}.json` (`data_id = DATA-{div}-{group}-{year}-{seq:06d}`).
- `per_sheet` 모드에서 시트 이름은 `caption` 으로 사용된다.

### 2.3 출력 JSON 구조 (요약)

```json
{
  "data_id": "DATA-HE-CAE-2026-000001",
  "schema_version": "data.v1",
  "caption": "측정결과",
  "team": "HE",
  "group": "CAE",
  "year": 2026,
  "headers": ["시간", "하중", "변형률"],
  "units":   ["s",    "N",    "%"],
  "rows": [
    [0.0, 0.0,  0.0],
    [0.1, 12.5, 0.02]
  ],
  "row_count": 2,
  "column_count": 3,
  "source": {"sheet": "측정결과", "kind": "xlsx"},
  "generated_at": "2026-05-08T08:00:00+00:00",
  "warnings": []
}
```

---

## 3. Excel 작성 6원칙

작성자에게 사전 배포할 핵심 6원칙. (전략 deck slide 06 과 동일)
원칙 1~5 는 **표 데이터를 자동 인식 가능하게** 만들기 위한 형식 규칙이고,
원칙 6 은 **데이터의 의미를 AI가 알 수 있게** 만들기 위한 컨텍스트 규칙이다.

### 원칙 1 — 시트 상단 고정 (가장 중요)

- 표는 항상 `A1` 부터 시작한다.
- 시트 중간(`B5` 등) 에 표를 띄우지 않는다.
- 1행 = 헤더, 2행부터 데이터.
- 표 위·옆에 빈 셀이나 메타 정보(작성자·날짜)를 두면 헤더 자동 인식이 어려워진다.

### 원칙 2 — 헤더에 단위 명시

- `무게(kg)`, `응력(MPa)`, `시간(s)` 처럼 단위를 헤더에 괄호로 포함한다.
- 변환기의 `--infer-units` 옵션이 자동 분리한다: `("무게", "kg")`.
- 같은 단위라도 명시하지 않으면 분석에서 단위 혼선이 발생한다.

### 원칙 3 — 셀 병합 금지

- 헤더 행은 절대 병합하지 않는다.
- 데이터 행에서 같은 값을 표현하려면 셀을 합치지 말고 같은 값을 반복 입력한다.
- 변환기는 병합된 셀 값을 모든 셀에 복제하지만 의도가 손실될 수 있다.

### 원칙 4 — 색상 의미 별도 컬럼화

- "빨간 행 = 불량" 같은 색상 의미는 별도 컬럼(`불량여부`)으로 명시한다.
- 색상 정보는 변환기에 전달되지 않는다.
- 조건부 서식 대신 데이터 값 자체로 표현한다.

### 원칙 5 — 1시트 1주제

- 하나의 시트에 한 가지 주제만 (한 종류의 측정 데이터).
- 같은 시트에 여러 표를 좌우/상하로 배치하지 않는다.
- 다른 주제는 다른 시트로 분리한다 — 시트 이름이 곧 데이터 주제(record caption) 가 된다.

### 원칙 6 — 데이터 의미 명시 (★ 핵심)

표 자체는 데이터일 뿐, **이 데이터가 무엇인지** 는 표에 들어 있지 않다.
"100 / 250 / 0.85" 라는 숫자만 봐서는 AI도 사람도 의미를 모른다.

작성자는 다음 3가지 방법 중 **최소 1가지** 로 데이터 컨텍스트를 Excel 자체에 기술해야 한다.

1. **`_META` 시트** — 워크북·시트 단위 컨텍스트 (목적, 시험 방법, 조건, 장비 등). 10장 참조.
2. **`_GLOSSARY` 시트** — 컬럼 단위 의미·단위·자료형. 11장 참조.
3. **워크북 빌트인 속성** — File → Info → Properties 의 Title/Subject/Comments/Keywords. 빠른 폴백.

권장 조합:

- 정식 데이터셋 → `_META` + `_GLOSSARY` 둘 다.
- 빠른 시제품 → `_META` 만 (또는 빌트인 속성만).
- 양산 자동 출력 → 헤더에 단위 명시(원칙 2) + `_GLOSSARY`.

> 원칙 6 가 없으면 변환기는 데이터 행은 잘 추출해도 "이 표가 무엇인가"
> 를 메타에 기록할 수 없다. 표가 시간이 지나 작성자도 잊을 즈음,
> 메타데이터가 없는 데이터는 **검색 불가능한 행렬** 로 남는다.

---

## 4. CLI 옵션 상세

```bash
python -m excel_converter input.xlsx \
    --team HE --group CAE --year 2026 \
    [--start-seq 100] [--output-dir output] \
    [--mode per_sheet|combined] \
    [--header-row N] [--start-cell A5] \
    [--skip-empty] [--skip-blank-rows] \
    [--infer-units] [--notes "메모"] [--verbose] \
    [--meta-sheet _META] [--glossary-sheet _GLOSSARY]
```

| 옵션                  | 기본값       | 설명                                                                                  |
|-----------------------|-------------|---------------------------------------------------------------------------------------|
| `--team`          | (필수)      | 팀 코드 (예: `HE`)                                                                    |
| `--group`              | (필수)      | 그룹 코드 (예: `CAE`)                                                                 |
| `--year`              | (필수)      | 연도 (예: `2026`)                                                                     |
| `--start-seq`         | `1`         | 첫 시트의 순번 (이후 시트는 +1)                                                       |
| `--output-dir`        | `output`    | 출력 폴더                                                                             |
| `--mode`              | `per_sheet` | `per_sheet` (시트별 1 JSON) / `combined` (모든 시트 병합)                             |
| `--header-row N`      | `1`         | 헤더가 위치한 1-based 행 번호                                                         |
| `--start-cell A5`     | `null`      | 표 좌상단 셀. 지정 시 `--header-row` + column 1 을 무시. 별칭: `--header-cell`        |
| `--skip-empty`        | off         | 빈 시트와 빈 행을 모두 건너뜀                                                         |
| `--skip-blank-rows`   | off         | 데이터 사이의 빈 행만 제거 (시트는 유지). `--skip-empty` 와 직교                      |
| `--infer-units`       | off         | 헤더의 `(단위)` 를 별도 `units` 배열로 분리                                           |
| `--notes "..."`       | `""`        | 모든 출력 JSON 의 `notes` 에 첨부할 메모                                              |
| `--meta-sheet NAME`   | `_META`     | 워크북/시트 컨텍스트가 들어 있는 예약 시트 이름 (10장)                                |
| `--glossary-sheet NAME` | `_GLOSSARY` | 컬럼 의미·단위·자료형 정의 시트 이름 (11장)                                          |
| `--verbose, -v`       | off         | 상세 로그 출력                                                                        |

### 4.1 모드별 동작

- `per_sheet`: N 개 시트 → N 개 JSON 파일.
- `combined`: N 개 시트 → 1 개 JSON. 헤더는 첫 시트 기준이며,
  각 행 앞에 `__sheet__` 컬럼이 추가되어 출처를 보존.

---

## 5. 헤더 자동 감지 알고리즘

### 5.1 현재 구현

```
1. --start-cell 이 지정되면: parse_cell_address(--start-cell) → (header_row, start_col).
2. 그렇지 않으면: header_row = --header-row (기본 1), start_col = 1.
3. ws.cell(header_row, start_col..max_col) 의 값들을 headers_raw 로 추출.
4. --infer-units 가 켜져 있으면 parse_header_units(h) 로 (label, unit) 분리.
5. 빈 헤더 셀은 col_N 으로 자동 채움 (경고 발생).
```

### 5.2 자동 탐지 (`detect_irregular`)

`--start-cell` 미지정 시, 변환기는 시트마다 첫 10×10 영역을 훑어
**불규칙 구조** 신호를 찾고 `suggested_start_cell` 을 제안한다.

규칙(휴리스틱):

1. `A1` 이 비어 있고, 어딘가에 헤더처럼 보이는 행(모든 셀이 문자열인 비-빈 행 + ≥2 컬럼)이
   있으면 → 해당 행의 첫 비-빈 컬럼을 `suggested_start_cell` 로 제안.
2. `A1` 에 값이 있어도, 그 행이 모두 문자열이 아니면 → 다음 후보 행 탐색.
3. `A1` 부터 깨끗한 헤더 행이면 → "정상" 판정, 제안 없음.

탐지 결과는 변환을 강제로 바꾸지 않고 **`warnings` 배열에 기록만 한다.**
사용자는 경고를 보고 `--start-cell` 옵션을 명시적으로 추가해 재실행한다.

### 5.3 향후 확장

- 자동 보정(`--auto-detect`): `suggested_start_cell` 을 자동으로 적용해 변환.
- 2행 헤더(`--header-row 1,2`): 그룹 헤더 + 서브 헤더를 `"그룹 / 서브"` 형태로 결합.

---

## 6. 셀 값 타입 추론 규칙

`coerce_value()` 함수가 openpyxl 셀 값을 JSON 친화적 타입으로 정규화한다.

| 입력 타입        | 출력                                    |
|------------------|-----------------------------------------|
| `None`           | `None` → JSON `null`                    |
| `bool`           | 그대로                                   |
| `int`, `float`   | 그대로                                   |
| `datetime`       | ISO8601 문자열 (`"2026-05-08T..."`)      |
| `str` (숫자 형식)| 정규식 매칭 시 int/float 으로 캐스팅      |
| `str` (그 외)    | `strip()` 후 그대로                      |
| 기타 객체        | `str()` 캐스팅                           |

숫자 정규식:

```
정수:   ^-?\d+$
실수:   ^-?\d+\.\d+$ | ^-?\.\d+$ | ^-?\d+\.$
지수:   ^-?\d+(?:\.\d+)?[eE][+-]?\d+$
```

단위가 포함된 문자열(`"250 MPa"`, `"1.5 mm"`)은 문자열로 보존된다.

---

## 7. 단위 분리 규칙

`--infer-units` 가 켜지면 `parse_header_units()` 가 헤더 문자열을
`(label, unit)` 으로 분리한다.

예시:

```
parse_header_units("하중(N)")        -> ("하중", "N")
parse_header_units("Stress [MPa]")    -> ("Stress", "MPa")
parse_header_units("온도 (deg C)")    -> ("온도", "deg C")
parse_header_units("count")           -> ("count", None)
parse_header_units("")                -> ("", None)
```

규칙:

- 마지막에 등장하는 `(...)` 또는 `[...]` 의 내용을 단위로 추출.
- 단위 후보 길이는 1~16 자. 너무 길면 설명문으로 간주(분리 안 함).
- 헤더가 None/빈 값이면 `("", None)`.

출력 매핑:

```json
{
  "headers": ["하중", "응력", "변형률"],
  "units":   ["N",   "MPa",  "%"]
}
```

---

## 8. 병합 셀 처리

openpyxl 은 병합 영역의 좌상단 셀에만 값을 두고 나머지 셀은 `None` 을 반환한다.
변환기는 `_build_merge_lookup()` 으로 병합 범위 전체에 좌상단 값을 **복제**한다.

| 상황              | 처리                                         |
|-------------------|----------------------------------------------|
| 가로 병합         | 같은 값을 각 열에 반복                        |
| 세로 병합         | 같은 값을 각 행에 반복                        |
| 헤더 행 병합      | 빈 헤더 셀이 자동 복제 — **경고 발생**        |
| 그룹 헤더(2행)    | 미지원 — 첫 행만 헤더로 사용 + 경고            |

이 동작은 [json_schema_rules.md](./json_schema_rules.md) 7.4 절과 일치한다.

---

## 9. 불규칙 Excel 처리

표준 5원칙을 따르지 않는 기존 자료를 변환할 때의 보정 절차.

### 9.1 표가 시트 중간에 있을 때

```bash
# 표가 B5 부터 시작하는 시트
python -m excel_converter messy.xlsx \
    --team HE --group CAE --year 2026 \
    --start-cell B5
```

`--start-cell` 은 표 **좌상단(헤더의 첫 셀)** 을 가리킨다. 데이터 행은 자동으로
다음 행부터 끝까지 스캔된다.

자동 탐지가 시트의 시작 셀을 제안한 경우 `warnings[]` 에 다음과 같이 기록된다.

```
sheet 'Sheet1' looks irregular (table doesn't start at A1).
Suggested --start-cell B5. Reasons: A1 is empty; row 5 looks like a header (...)
```

### 9.2 2행 헤더 (그룹 헤더)

현재 미지원. 임시 우회:

- Excel 에서 그룹 행을 제거하고 컬럼명을 `"그룹/서브"` 형태로 직접 입력.
- 또는 `--header-row 2` 로 서브 헤더 행만 사용 (그룹 정보 손실).

향후 `--header-row 1,2` 로 두 행을 결합해 헤더로 사용하는 옵션을 검토.

### 9.3 한 시트에 여러 표 — `--detect-multi-tables` opt-in

기본 동작은 시트당 1개 표 (`A1` 부터 첫 헤더 + 연속 행). 같은 시트에 표가 여러 개 (수직 / 수평 배치) 있는 경우 `--detect-multi-tables` 플래그로 자동 탐지 가능 (`excel_converter/detect_multi.py`).

휴리스틱:

1. 시트의 모든 행을 순회하며 **빈 행** (모든 셀이 None/공백) 으로 분리.
2. 비어있지 않은 연속 블록 (contiguous block) 들을 후보 표로 식별.
3. 각 블록의 첫 행을 헤더로, 나머지를 데이터로 가정.
4. 좌우 빈 컬럼은 자동 trim.
5. 블록이 ≥ 2개면 `has_multi_tables=True` → 각 블록을 `tables[]` 의 별도 행으로 출력 (`tables[0]`, `tables[1]`, ...).

플래그가 꺼져 있을 때 (default): 기존 동작 그대로. 다중 표 시트를 감지하면 변환은 계속하되 `warnings[]` 에 "multiple non-empty blocks detected — consider --detect-multi-tables" 추가.

권장: 가능하면 작성자가 시트를 분리. 분리할 수 없는 레거시 시트에 한해 본 옵션 사용.

### 9.4 데이터 사이의 빈 행

```bash
python -m excel_converter messy.xlsx \
    --team HE --group CAE --year 2026 \
    --skip-blank-rows
```

`--skip-blank-rows` 는 모든 셀이 비어 있는 행을 건너뛴다.
`--skip-empty` 와 다른 점:

- `--skip-empty`: 빈 시트 + 빈 행 모두 제거.
- `--skip-blank-rows`: 빈 시트는 유지, 빈 행만 제거.

### 9.5 차트 · 이미지

현재 미지원. 워크북의 차트/이미지는 변환되지 않는다. 향후 별도 attachment
(`kind=figure`)로 추출하는 옵션을 검토.

### 9.6 불규칙 vs 친화적 형태 (요약 표)

| ✗ 불규칙한 형태 | ✓ 변환기 친화적 형태 |
|------|------|
| 표가 시트 중간(예: `B5`)에서 시작 | `A1` 부터 헤더 시작 |
| 시트 상단에 제목 셀과 메타 정보가 박혀 있음 | 메타 정보는 `_META` 시트로 분리 |
| 데이터 사이에 빈 행이 들어가 있음 | 빈 행 없이 연속된 데이터 |
| 여러 표가 한 시트에 좌우로 나란히 배치 | 1시트 1주제, 시트 이름 = 데이터 주제 |
| 셀 병합으로 그룹 헤더 표현 (2행 헤더) | 1행 헤더, 단위는 괄호로 명시 |
| 차트·이미지가 데이터와 겹쳐 있음 | 색상·서식 의미는 별도 컬럼으로 |
| 데이터의 의미가 어디에도 없음 | `_META` + `_GLOSSARY` 시트로 명시 |

---

## 10. `_META` 시트 표준 (데이터의 의미 명시)

### 10.1 정의

이름이 `_META` (언더스코어 접두) 인 시트는 **데이터 시트가 아니라 메타데이터 시트** 로
변환기가 인식한다. 변환 시 이 시트는 데이터로 변환되지 않으며, 그 내용은 RecordIn 메타와
각 데이터 시트의 `content.context` 에 머지된다.

### 10.2 구조

2열 테이블: `key | value`. 첫 행은 헤더 `key`, `value`.

```text
| key                          | value                                    |
|------------------------------|------------------------------------------|
| title                        | 브라켓 하중 시험 결과 (2026-04)          |
| summary                      | 100개 시료에 대한 정적 하중 시험 결과.   |
| tags                         | 시험,브라켓,하중,2026Q2                  |
| agents                       | material-reviewer,cae-reporter           |
| domain                       | mechanical-test                          |
| classification               | internal                                 |
| status                       | approved                                 |
| language                     | ko                                       |
| source_system                | Universal Testing Machine (UTM-500)      |
|                              |                                          |
| sheet:Sheet1.description     | 시료별 최대 하중 측정 결과               |
| sheet:Sheet1.method          | KS B 0814 표준 인장시험                  |
| sheet:Sheet1.condition       | 상온 23±2℃, 습도 50±5%RH                 |
| sheet:Sheet1.equipment       | UTM-500, 50kN 로드셀                     |
| sheet:Sheet1.operator        | 박지수                                   |
| sheet:Sheet1.date            | 2026-04-15                               |
| sheet:Sheet2.description     | 응력-변형 곡선 핵심점                    |
```

### 10.3 키 종류

#### 10.3.1 워크북 레벨 (시트 무관)

| key 이름                  | 매핑 (RecordIn 메타)                        |
|---------------------------|---------------------------------------------|
| `title`                   | `meta.title`                                |
| `summary`                 | `meta.summary`                              |
| `tags`                    | `meta.tags` (콤마로 분리하여 배열)          |
| `agents`                  | `meta.agent_scope` (콤마로 분리하여 배열). 코드는 `agent_scope` 키로 출력. |
| `domain`                  | `meta.domain`                               |
| `classification`          | `meta.classification`                       |
| `status`                  | `meta.status`                               |
| `language`                | `meta.language`                             |
| `source_system`           | `meta.source_system`                        |
| `author`                  | `meta.author`                               |
| `department`              | `meta.department`                           |
| `project`                 | `meta.project`                              |
| `version`                 | `meta.version`                              |
| `subject_keywords`        | `meta.subject_keywords` (콤마 분리)         |

#### 10.3.2 시트 레벨 (`sheet:<시트이름>.<항목>`)

`sheet:` 접두 + 시트 이름 + `.` + 항목명. 해당 시트의 record `content.context` 로 들어간다.

| 항목명         | 의미                                       |
|----------------|--------------------------------------------|
| `description`  | 이 시트가 무엇을 측정/기록한 것인가          |
| `method`       | 측정 방법·시험 표준 (예: `KS B 0814`)       |
| `condition`    | 환경·시험 조건 (온도·습도·하중 조건 등)     |
| `equipment`    | 사용 장비 (장비 모델, 센서, 측정기)         |
| `operator`     | 시험 수행자                                |
| `date`         | 측정/시험 날짜 (시트 데이터의 일자)         |
| `notes`        | 특이사항·비고                              |
| `caveats`      | 데이터 한계·주의사항                       |

> 구현은 `sheet:X.Y` 키를 dotted-path 로 읽어 `context["X"]["Y"]` 로 nesting 한다.
> 시트 이름이 `_META` 자체에 정확히 일치하지 않으면 (예: 오타) 무시된다 + 경고.

### 10.4 변환 결과 (예시)

위 `_META` + Sheet1 데이터 → 변환기 출력:

```json
{
  "meta": {
    "title": "브라켓 하중 시험 결과 (2026-04)",
    "summary": "100개 시료에 대한 정적 하중 시험 결과.",
    "tags": ["시험", "브라켓", "하중", "2026Q2"],
    "agent_scope": ["material-reviewer", "cae-reporter"]
  },
  "content": {
    "caption": "Sheet1",
    "headers": ["시료ID", "무게", "단면적", "최대하중"],
    "rows": [...],
    "context": {
      "description": "시료별 최대 하중 측정 결과",
      "method": "KS B 0814 표준 인장시험",
      "condition": "상온 23±2℃, 습도 50±5%RH",
      "equipment": "UTM-500, 50kN 로드셀",
      "operator": "박지수",
      "date": "2026-04-15"
    }
  }
}
```

---

## 11. `_GLOSSARY` 시트 표준 (컬럼 의미 정의)

### 11.1 정의

이름이 `_GLOSSARY` 인 시트는 **컬럼 헤더의 의미·단위·자료형** 을 정의한다.
이 시트도 데이터로 변환되지 않으며, 정의는 모든 데이터 시트의 컬럼에 매칭된다.

### 11.2 구조

4열 테이블: `column | description | unit | dtype`.

```text
| column   | description                              | unit | dtype  |
|----------|------------------------------------------|------|--------|
| 시료ID   | 시료 고유 식별자                          | -    | string |
| 무게     | 시료 무게 (시험 직전 측정)                | g    | float  |
| 단면적   | 시험부 단면적                            | mm²  | float  |
| 최대하중 | 시험 중 기록된 최대 하중                 | N    | float  |
| 항복하중 | 응력-변형 곡선의 항복점에서의 하중       | N    | float  |
| 파괴여부 | 시험 종료 시 파괴 발생 여부              | -    | enum:Y/N |
```

### 11.3 컬럼 정의

| 필드          | 의미                                                  |
|---------------|-------------------------------------------------------|
| `column`      | 데이터 시트의 헤더와 매칭 (정확히 일치).              |
| `description` | 컬럼이 의미하는 바. `content.column_descriptions` 로 매핑. |
| `unit`        | 단위. 인라인 `(...)` 단위와 충돌 시 우선순위 12장 참조. |
| `dtype`       | 자료형 힌트. `string` / `int` / `float` / `bool` / `date` / `enum:A/B` 중 하나. |

### 11.4 dtype 힌트 동작

- `int` / `float` / `bool` / `date` 힌트가 있으면, 셀 값을 강제 변환 시도.
- 변환 실패 시 → 원래 값을 유지 + `warnings[]` 에 경고 기록.
- `enum:Y/N` 같은 enum 힌트는 **검증만** 수행 (값이 enum 에 속하지 않으면 경고).
- 힌트가 없거나 `string` 이면 기본 `coerce_value()` 추론을 사용.

### 11.5 변환 결과 (예시)

```json
{
  "content": {
    "caption": "Sheet1",
    "headers": ["시료ID", "무게", "단면적", "최대하중"],
    "rows": [["S001", 12.34, 25.0, 1250.5], ...],
    "units": {"무게": "g", "단면적": "mm²", "최대하중": "N"},
    "column_descriptions": {
      "시료ID":   "시료 고유 식별자",
      "무게":     "시료 무게 (시험 직전 측정)",
      "단면적":   "시험부 단면적",
      "최대하중": "시험 중 기록된 최대 하중"
    }
  }
}
```

---

## 12. 메타 컨텍스트 우선순위

같은 메타 정보가 여러 출처에 존재할 때의 우선순위 (낮음 → 높음).

```
1. (낮음) 워크북 빌트인 속성 (File → Info → Properties)
2.        헤더 인라인 단위 (예: "무게(kg)" → unit "kg")
3.        _GLOSSARY 시트의 컬럼 정의 (description / unit / dtype)
4.        _META 시트의 워크북·시트 레벨 키
5. (높음) CLI 인자 (--notes 등)
```

규칙:

- 상위 출처의 값이 있으면 하위는 덮어쓴다 (override).
- 하위 출처에만 값이 있으면 그대로 사용 (보강).
- 충돌 시 변환기는 사용된 값과 무시된 값을 `warnings[]` 에 기록한다.

> 빌트인 속성은 빠른 폴백, `_META`/`_GLOSSARY` 는 정식 명세, CLI 는 일회성 보정용이다.

### 12.1 빌트인 속성 매핑

| Excel 속성 (`wb.properties.*`) | 매핑                            |
|--------------------------------|--------------------------------|
| `title`                        | `meta.title`                   |
| `subject`                      | `meta.domain`                  |
| `description`                  | `meta.summary`                 |
| `category`                     | `meta.classification`          |
| `keywords`                     | `meta.tags` (콤마로 분리)       |
| `creator`                      | `meta.author`                  |
| `lastModifiedBy`               | (참고용 — 매핑 안 함)           |

`_META` 시트가 있으면 빌트인 속성은 보강 폴백으로만 사용된다.

---

## 13. 변환기별 매핑 표

| Excel 요소               | 위치                            | JSON 출력                                |
|--------------------------|---------------------------------|------------------------------------------|
| 시트 이름                | `sheet.title`                   | `caption` + `source.sheet`               |
| 헤더 행                  | row 1 (또는 `--header-row N`)   | `headers[]`                              |
| 헤더 + `--start-cell A5` | row 5, col A                    | `headers[]` (오프셋 적용)                |
| 단위가 포함된 헤더       | `"무게(kg)"`                    | `headers=["무게"]`, `units=["kg"]`       |
| 데이터 행                | row 2..N (또는 시작 행+1..N)    | `rows[][]`                               |
| 병합 셀                  | merged_cells.ranges             | 좌상단 값을 모든 셀에 복제                |
| 빈 셀                    | `cell.value is None`            | `None` (JSON `null`)                     |
| 시트 노트(properties)    | `properties.description`        | `meta.summary` 폴백 (12장 우선순위)      |
| 워크북 빌트인 속성       | `wb.properties.title` 등        | `meta.*` 폴백 (12.1 표 참조)             |
| `_META` 시트             | 시트 이름 = `_META`             | `meta.*` 및 `content.context.*`          |
| `_GLOSSARY` 시트         | 시트 이름 = `_GLOSSARY`         | `content.column_descriptions`, `units`   |
| 변환 경고                | `IrregularReport.reasons` 등    | `warnings[]`                             |

---

## 14. 변환 후 검증

### 14.1 자동 검증 항목

- `data_id` 형식: `^DATA-[A-Z]{2,4}-[A-Z]{2,5}-\d{4}-\d{6}$`
- `headers` 의 길이 = `column_count`.
- 모든 `rows[i]` 의 길이 = `column_count` (변환기가 자동 패딩).
- `units` 가 있으면 길이 = `headers` 길이.
- `row_count` = `len(rows)`.
- `_GLOSSARY` 의 `column` 항목이 데이터 시트 헤더에 매칭되는가 (미매칭 시 경고).
- `_META` 의 `sheet:<X>.<항목>` 의 `<X>` 가 실제 시트 이름인가 (오타 시 경고).

### 14.2 검수 워크플로

- 변환 후 `*.warnings.log` 또는 JSON 의 `warnings[]` 확인.
- 시트 이름이 비어 있거나 헤더 행이 비어 있는 경우 경고 발생.
- 병합된 셀, 불규칙 시작 위치 발견 시 변환은 계속되지만 경고 기록.

### 14.3 사전 배포 전략

일반 사용자가 Excel 을 자유롭게 만들면 변환 손실이 크다.

- 표 작성 표준(본 문서 3장)을 사전 배포한다 (사내 가이드 + 샘플 .xlsx 템플릿).
- 표준을 따르지 않는 기존 자료는 `--start-cell` / `--header-row` /
  `--skip-blank-rows` 옵션으로 보정한다.
- 샘플 `.xlsx` 템플릿에는 빈 `_META` / `_GLOSSARY` 시트를 미리 포함시켜
  작성자가 데이터 의미(원칙 6) 를 자연스럽게 채우도록 유도한다.

---

## 15. 알려진 한계

| 한계                                      | 대응                                                                |
|-------------------------------------------|---------------------------------------------------------------------|
| 수식은 계산값(value) 만 추출됨            | `data_only=True` 로 로드. 계산되지 않은 수식은 `None` 또는 식 문자열 |
| 차트는 변환 대상 아님                     | 별도 attachment 로 추출하는 옵션은 향후 과제                        |
| 셀 서식(색상·굵기·배경)은 손실            | 의미는 별도 컬럼으로 표현하도록 가이드                              |
| 한 시트에 여러 표 — 기본 미지원, **opt-in 지원** | `--detect-multi-tables` 플래그로 자동 탐지 (9.3 절). 권장은 시트 분리. |
| 그룹 헤더(2행 헤더) 미지원                 | 작성자가 1행 헤더로 평탄화                                          |
| 매크로(VBA) 무시                          | 데이터만 사용                                                       |
| `.xls` (BIFF) 미지원                      | `.xlsx` 로 사전 변환                                                |
| 암호화된 워크북 변환 거부                 | 사전 복호화 필요                                                    |
| `_GLOSSARY` 의 dtype 강제 변환 실패 시      | 원래 값 유지 + `warnings[]` 기록 (검증은 검수 단계에서)            |
| `_META` 키가 미정의(오타) 일 때             | 알 수 없는 키는 `meta.extra` 에 보존하지 않고 무시 + 경고            |

---

## 부록 A: 빠른 사용 예시

```bash
# 표준(원칙 5개 준수) 워크북
python -m excel_converter battery_test.xlsx \
    --team HE --group CAE --year 2026 \
    --start-seq 100 --infer-units --skip-empty

# 표가 B5 부터 시작하는 불규칙 워크북
python -m excel_converter legacy_data.xlsx \
    --team HE --group CAE --year 2026 \
    --start-cell B5 --skip-blank-rows --infer-units

# 모든 시트를 하나의 record 로 병합
python -m excel_converter multi_sheet.xlsx \
    --team HE --group CAE --year 2026 \
    --mode combined --skip-empty
```

---

## 부록 B: 출력 파일 배치

```
output/
├── DATA-HE-CAE-2026-000100.json     ← 시트1
├── DATA-HE-CAE-2026-000101.json     ← 시트2
└── DATA-HE-CAE-2026-000102.json     ← 시트3
```

`per_sheet` 모드에서는 시트 수만큼 파일이 생성되고,
`combined` 모드에서는 1개 파일만 생성된다.

---

*본 변환 규칙서는 [json_schema_rules.md](./json_schema_rules.md) 의 v1.3 출력 스키마 (호환성: v1.0 JSON 페이로드 그대로 사용 가능)를
기준으로 작성되었으며, 스키마가 변경될 때 함께 갱신됩니다.*

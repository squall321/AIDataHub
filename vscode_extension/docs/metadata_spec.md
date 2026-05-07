# Metadata Spec — Single Source of Truth

확장의 메타데이터 폼이 **백엔드 `RecordIn` (`api_server/src/api/schemas/common.py`)** 와 **`POST /api/convert/ingest` 폼 필드** 와 정확히 1:1 매핑되도록 정리한다.

## 1. 필드 분류

### 그룹 A — 식별 (필수, ID 구성요소)

| Field      | Type   | Required | UI 컨트롤        | 백엔드 매핑 (`/api/convert/ingest`) | 비고                                      |
|------------|--------|----------|------------------|--------------------------------------|-------------------------------------------|
| `division` | string | Y        | `<select>` (옵션 API) | `division` (form)                | `HE`/`EV`/... — 백엔드 enum 옵션          |
| `team`     | string | Y        | `<select>` (옵션 API) | `team` (form)                    | `division` 선택 시 종속적으로 필터링      |
| `year`     | int    | Y        | `<input type=number>` | `year` (form)                    | 기본값: 현재 연도                         |
| `seq`     | int    | Y        | `<input type=number>` | `seq` (form)                     | 기본값: 1, 백엔드 추후 자동증가 옵션 검토 |

### 그룹 B — 분류 (필수 / 선택 혼합)

| Field            | Type            | Required | UI 컨트롤 | 백엔드 매핑       | 비고 / 허용값                                              |
|------------------|-----------------|----------|-----------|-------------------|------------------------------------------------------------|
| `classification` | enum            | Y (default) | select | `classification`   | `public` / `internal` / `confidential` / `restricted`      |
| `status`         | enum            | N        | select    | (현재 ingest form 에 없음 — 백엔드 추가 필요) | `draft` / `review` / `approved` / `deprecated` |
| `domain`         | string          | N        | text      | `domain`           | 자유 입력 (예: "battery", "iga")                           |
| `language`       | enum (ISO 639-1)| N        | select    | (백엔드 추가 필요)  | 기본 `ko` / `en` / `ja` / ...                              |

### 그룹 C — 검색/연결

| Field              | Type      | Required | UI 컨트롤              | 백엔드 매핑 | 비고                                       |
|--------------------|-----------|----------|------------------------|-------------|--------------------------------------------|
| `tags`             | string[]  | N        | tag input (chips)      | `tags`       | 콤마 join 으로 송신 ("a,b,c")             |
| `agents`           | string[]  | N        | multi-select (옵션 API)| `agents`     | 콤마 join, `data_type` 와 호환되는 agent  |
| `subject_keywords` | string[]  | N        | tag input              | (백엔드 추가) | RAG 검색 가중치용 별도 키워드             |

### 그룹 D — 표제 오버라이드 (선택)

변환기가 추출한 `title` / `summary` 가 부정확할 때 사용자가 덮어쓸 수 있게 한다. 백엔드는 변환 결과 dict 의 `meta.title` / `meta.summary` 를 우선 사용하지만, 폼 값이 있으면 **변환 결과 위에 머지** 되도록 백엔드 변경 필요.

| Field     | Type   | Required | UI 컨트롤            | 비고                                          |
|-----------|--------|----------|----------------------|-----------------------------------------------|
| `title`   | string | N        | text (placeholder=auto) | 비우면 변환기 결과 그대로                  |
| `summary` | string | N        | textarea             | 비우면 변환기 결과 그대로                     |

### 그룹 E — 품질/유효기간 (선택)

| Field           | Type   | Required | UI 컨트롤       | 비고                          |
|-----------------|--------|----------|-----------------|-------------------------------|
| `quality_score` | int    | N        | slider (0~100)  |                               |
| `valid_from`    | date   | N        | date picker     |                               |
| `valid_until`   | date   | N        | date picker     |                               |

## 2. 확장자 → `data_type` 자동 매핑

확장 UI 는 드롭된 파일의 확장자만 보고 다음 추론을 수행하며, **사용자가 변경할 수 없게** 한다 (백엔드 `converter_dispatch` 의 매핑과 동일하므로 사용자 선택권을 주면 오류 가능).

| 확장자                 | `data_type` (표시) | 변환기            |
|------------------------|--------------------|-------------------|
| `.docx`                | DOC                | `converter`       |
| `.pdf`                 | DOC                | `pdf_converter`   |
| `.pptx`                | DOC                | `ppt_converter`   |
| `.md`, `.markdown`     | DOC                | `md_converter`    |
| `.xlsx`                | DATA / DATA_BUNDLE | `excel_converter` |
| 그 외                  | (UI 차단 + 토스트) |                   |

## 3. UI 폼 예시 (mock)

```
┌─ AI Data Hub Uploader ─────────────────────────────────────┐
│  📂 iga_guide.docx          (DOC, 1.2 MB)        [Remove]  │
│                                                            │
│  🏢 Identification                                         │
│  ┌────────────┐ ┌────────────┐ ┌──────┐ ┌────┐             │
│  │ Division ▾ │ │  Team    ▾ │ │ Year │ │Seq │             │
│  └────────────┘ └────────────┘ └──────┘ └────┘             │
│                                                            │
│  🏷️  Classification                                        │
│  Classification ( internal ▾ )    Status ( draft ▾ )       │
│  Domain [_________________]       Lang   ( ko      ▾ )     │
│                                                            │
│  🔎 Discoverability                                        │
│  Tags    [ iga ] [ ls-dyna ] [ + ]                         │
│  Agents  [ iga-analyst ] [ + ]                             │
│  Subject [ offset ] [ shell ] [ + ]                        │
│                                                            │
│  ✏️  Override (optional)                                    │
│  Title   [ leave empty to use auto-extract ____________ ]  │
│  Summary [______________________________________________ ] │
│                                                            │
│  📊 Quality (optional)                                     │
│  Score    [───●─────] 70                                   │
│  Valid    [2026-05-08] ~ [          ]                      │
│                                                            │
│                                       [ Send to Backend ]  │
└────────────────────────────────────────────────────────────┘
```

## 4. 송신 페이로드 (multipart/form-data)

```
POST /api/convert/ingest
Content-Type: multipart/form-data
X-API-Key: <secret>

file:             <binary>
division:         HE
team:             CAE
year:             2026
seq:              1
classification:   internal
status:           draft               # 백엔드 추가 후
domain:           battery
language:         ko                  # 백엔드 추가 후
tags:             iga,ls-dyna
agents:           iga-analyst
subject_keywords: offset,shell        # 백엔드 추가 후
title:            (선택)
summary:          (선택)
quality_score:    70                  # 백엔드 추가 후
valid_from:       2026-05-08          # 백엔드 추가 후
valid_until:                          # 백엔드 추가 후
```

## 5. 검증 규칙 (클라이언트 사이드)

- `division`, `team`, `year`, `seq` 미입력 → `Send` 비활성.
- `year`: 1990~2100 범위 강제.
- `seq`: 1~999999 범위 강제.
- `tags` / `agents` / `subject_keywords`: 각 항목 trim 후 빈 문자열 제거.
- `quality_score`: 0~100.
- `valid_from <= valid_until` (둘 다 입력 시).
- 파일 크기: `MAX_UPLOAD_MB` 초과 시 사전 차단 (옵션 API 응답에서 받음).

## 6. 백엔드 enum 의존성

다음 enum 은 **백엔드 신규 엔드포인트 `GET /api/meta/options`** 가 권위적으로 내려줘야 한다 (확장 자체에 하드코딩 금지):

- `divisions: string[]`
- `teams: { [division: string]: string[] }`
- `agents: { agent_type: string; name: string; data_types: string[] }[]`
- `classifications: string[]`
- `statuses: string[]`
- `derivations: string[]`
- `languages: string[]`
- `max_upload_mb: number`
- `supported_extensions: string[]`

이는 `api_server/docs/extension_integration_plan.md` 의 §2 와 동일한 스펙.

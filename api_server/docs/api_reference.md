# API Reference

AI Data Hub REST API. 베이스 URL: `http://localhost:8000`.
모든 응답은 JSON.

> 본 문서는 Agent 3 의 라우터 계약(`/api/data`, `/api/records`, `/api/search`,
> `/api/agents`, `/api/analytics`)을 기준으로 작성되었습니다. 실제 응답 예시 키는
> 구현체에 따라 일부 다를 수 있으니, OpenAPI(<http://localhost:8000/docs>)를 함께 참고하세요.

## 목차

- [Health](#health)
- [GET /api/system/health](#get-apisystemhealth)
- [GET /api/meta/options](#get-apimetaoptions)
- [POST /api/auth/keys/verify](#post-apiauthkeysverify)
- [GET /api/data](#get-apidata)
- [GET /api/records](#get-apirecords)
- [GET /api/records/{id}](#get-apirecordsid)
- [GET /api/search](#get-apisearch)
- [GET /api/agents](#get-apiagents)
- [GET /api/analytics/distribution](#get-apianalyticsdistribution)
- [POST /api/convert](#post-apiconvert)
- [POST /api/convert/ingest](#post-apiconvertingest)
- [GET /figures/{doc_id}/{filename}](#get-figuresdoc_idfilename)
- [공통 에러 응답](#공통-에러-응답)

---

## Health

### `GET /`

- 설명: 서비스 메타정보.
- 응답:

```json
{
  "service": "ai-data-api",
  "version": "0.1.0",
  "status": "running"
}
```

### `GET /health`

- 설명: 헬스체크.
- 응답: `{"status": "ok"}`
- curl: `curl -s http://localhost:8000/health`

---

## `GET /api/system/health`

`/health` 의 상위 호환. VS Code 확장 등 클라이언트가 한 번에 인증 모드 (`AUTH_REQUIRED`)
와 빌드 메타를 판단할 수 있도록 추가 필드를 노출한다.

응답:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "auth_required": false,
  "build": "dev"
}
```

| 필드            | 타입    | 설명                                                |
|-----------------|---------|-----------------------------------------------------|
| `status`        | string  | 항상 `"ok"`                                          |
| `version`       | string  | `api.__version__`                                    |
| `auth_required` | bool    | `AUTH_REQUIRED` 환경변수 반영                        |
| `build`         | string  | `BUILD_SHA` 환경변수 (없으면 `"dev"`)                |

- curl: `curl -s http://localhost:8000/api/system/health`

---

## `GET /api/meta/options`

VS Code 확장 등 클라이언트가 폼 옵션을 일관되게 받기 위한 **권위 메타 카탈로그**.
`Cache-Control: public, max-age=300` 응답 헤더가 포함되며, 클라이언트는 5분 인메모리
캐시를 권장한다. 인증 비요구 (모든 호출자 접근 가능).

응답 예:

```json
{
  "version": "1.0",
  "divisions": ["HE", "EV", "PT", "DA", "MX", "VD"],
  "teams": {
    "HE": ["CAE", "Test", "Design"],
    "EV": ["BMS", "Battery", "Motor"],
    "PT": ["Material", "Process"]
  },
  "agents": [
    {
      "agent_type": "iga-analyst",
      "name": "IGA 해석 분석가",
      "description": "...",
      "data_types": ["DOC", "SIM", "DATA"]
    }
  ],
  "classifications": ["public", "internal", "confidential", "restricted"],
  "statuses":        ["draft", "review", "approved", "deprecated"],
  "derivations":     ["original", "extracted", "aggregated", "translated"],
  "languages":       ["ko", "en", "ja", "zh"],
  "data_types":      ["DOC", "DATA", "SIM", "CAD", "LOG", "FORM", "OTHER"],
  "supported_extensions": [".docx", ".markdown", ".md", ".pdf", ".pptx", ".xlsx"],
  "max_upload_mb":  50,
  "allow_custom": {
    "division": false,
    "team":     true,
    "domain":   true
  }
}
```

| 필드                      | 출처                                                 |
|---------------------------|------------------------------------------------------|
| `divisions` / `teams`     | `api.seed.divisions` (정적 매핑)                     |
| `agents`                  | `agents` 테이블 (`/api/agents` 와 같은 데이터)       |
| `classifications`         | `api.schemas.common.CLASSIFICATIONS`                 |
| `statuses`                | `api.schemas.common.STATUSES`                        |
| `derivations`             | `api.schemas.common.DERIVATIONS`                     |
| `languages`               | 정적 (`["ko","en","ja","zh"]`)                       |
| `data_types`              | `api.schemas.id_format.DATA_TYPES`                   |
| `supported_extensions`    | `api.services.converter_dispatch.EXTENSION_MAP.keys` |
| `max_upload_mb`           | `settings.max_upload_mb`                             |
| `allow_custom`            | 정적 (확장 UI 의 자유 입력 허용 플래그)              |

- curl: `curl -s http://localhost:8000/api/meta/options`

---

## `POST /api/auth/keys/verify`

발급된 `X-API-Key` 가 활성 상태인지 확인한다. 부트스트랩 키 미요구 — 일반 발급 키만으로
호출 가능하다. 만료 / 폐기 / 미존재 키는 401.

| 위치    | 이름          | 타입    | 필수 | 설명                             |
|---------|---------------|---------|------|----------------------------------|
| header  | `X-API-Key`   | string  | Y    | 발급된 plaintext API 키           |

응답 (200):

```json
{
  "ok": true,
  "key_name": "vscode-extension-tester",
  "agent_scopes": ["iga-analyst", "cae-reporter"]
}
```

응답 (401, 통합 에러 envelope):

```json
{
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "invalid or revoked API key",
    "details": {},
    "request_id": "..."
  }
}
```

- curl: `curl -X POST -H "X-API-Key: $KEY" http://localhost:8000/api/auth/keys/verify`

---

## `GET /api/data`

지정된 에이전트가 사용할 데이터 레코드/섹션을 반환한다 (RAG 컨텍스트용).

| 위치 | 이름     | 타입    | 필수 | 설명                                       |
|------|----------|---------|------|--------------------------------------------|
| query | `agent`  | string | Y    | 에이전트 타입 (`iga-analyst`, `cae-reporter` 등) |
| query | `query`  | string | N    | 키워드 (제목/요약/섹션 매칭)               |
| query | `limit`  | int    | N    | 최대 개수 (기본 5, 최대 20)                |

응답 예:

```json
{
  "agent": "iga-analyst",
  "query": "offset",
  "results": [
    {
      "record_id": "DOC-HE-CAE-2026-000001",
      "title": "IGA 가이드",
      "data_type": "DOC",
      "section_id": "1.1",
      "section_title": "Offset 처리",
      "snippet": "offset 값은 0.5mm로 설정한다.",
      "score": 0.82
    }
  ]
}
```

curl:

```bash
curl -s "http://localhost:8000/api/data?agent=iga-analyst&query=offset&limit=5"
```

에러: `400` (agent 누락), `404` (등록되지 않은 agent).

---

## `GET /api/records`

레코드 메타 목록 (페이지네이션).

| 위치 | 이름        | 타입    | 필수 | 설명                                            |
|------|-------------|---------|------|-------------------------------------------------|
| query | `data_type` | string | N    | `DOC` / `DATA` / `SIM` / `CAD` / `LOG` / ...    |
| query | `division`  | string | N    | `HE` / `EV` / ...                               |
| query | `team`      | string | N    | `CAE` / ...                                     |
| query | `year`      | int    | N    |                                                 |
| query | `agent`     | string | N    | `agent_records` 매핑 기준 필터                  |
| query | `limit`     | int    | N    | 기본 50                                         |
| query | `offset`    | int    | N    | 기본 0                                          |

응답 예:

```json
{
  "items": [
    {
      "id": "DOC-HE-CAE-2026-000001",
      "data_type": "DOC",
      "division": "HE",
      "team": "CAE",
      "year": 2026,
      "title": "IGA 가이드",
      "summary": "...",
      "tags": ["iga", "guide"],
      "created_at": "2026-05-07T08:00:00Z"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

curl: `curl -s "http://localhost:8000/api/records?data_type=DOC&limit=10"`

---

## `GET /api/records/{id}`

단일 레코드 전체 페이로드(섹션 포함).

- path `id`: 레코드 ID (예: `DOC-HE-CAE-2026-000001`).
- 응답: `Record` 모델 + `sections[]`.

```json
{
  "id": "DOC-HE-CAE-2026-000001",
  "data_type": "DOC",
  "division": "HE",
  "team": "CAE",
  "year": 2026,
  "seq": 1,
  "title": "IGA 가이드",
  "summary": "...",
  "tags": ["iga", "guide"],
  "agents": ["iga-analyst"],
  "schema_version": "1.0",
  "content": { "...": "원본 변환 JSON 전체" },
  "content_hash": "ab12...",
  "sections": [
    {
      "section_id": "1",
      "level": 1,
      "title": "개요",
      "content_text": "...",
      "figure_refs": [],
      "table_refs": []
    }
  ]
}
```

curl: `curl -s http://localhost:8000/api/records/DOC-HE-CAE-2026-000001`

에러: `404 Not Found`.

---

## `GET /api/search`

| 위치 | 이름    | 타입       | 필수 | 설명                                                         |
|------|---------|------------|------|--------------------------------------------------------------|
| query | `mode`  | string    | Y    | `tag` / `fts` / `semantic`                                   |
| query | `q`     | string    | 조건부 | `fts` / `semantic` 모드에서 필수                           |
| query | `tags`  | string[]  | 조건부 | `tag` 모드에서 필수 (반복 가능: `?tags=a&tags=b`)          |
| query | `limit` | int       | N    | 기본 20                                                      |

응답 예:

```json
{
  "mode": "fts",
  "q": "battery crash",
  "results": [
    {
      "record_id": "SIM-HE-CAE-2026-000003",
      "title": "Battery Side Crash 시뮬레이션",
      "score": 1.42
    }
  ]
}
```

curl:

```bash
curl -s "http://localhost:8000/api/search?mode=tag&tags=battery&tags=crash"
curl -s "http://localhost:8000/api/search?mode=fts&q=battery%20crash"
```

---

## `GET /api/agents`

등록된 에이전트와 그 데이터 스코프.

응답 예:

```json
{
  "items": [
    {
      "agent_type": "iga-analyst",
      "name": "IGA Analyst",
      "description": "Isogeometric analysis 가이드 전담",
      "common_tags": ["iga"],
      "data_types": ["DOC", "SIM"]
    }
  ]
}
```

curl: `curl -s http://localhost:8000/api/agents`

---

## `GET /api/analytics/distribution`

데이터 타입/사업부/팀별 분포 통계.

응답 예:

```json
{
  "by_data_type": {"DOC": 12, "SIM": 5, "DATA": 3},
  "by_division": {"HE": 18, "EV": 2},
  "by_team":     {"CAE": 15, "Test": 5},
  "total": 20
}
```

curl: `curl -s http://localhost:8000/api/analytics/distribution`

---

## `POST /api/convert`

업로드된 파일을 변환기에 전달하고 결과 JSON 을 그대로 반환한다 (DB 적재 없음).

지원 확장자:

| 확장자                 | 변환기                | 출력 `data_type`        |
|------------------------|-----------------------|-------------------------|
| `.docx`                | `converter`           | `DOC`                   |
| `.xlsx`                | `excel_converter`     | `DATA` 또는 `DATA_BUNDLE` |
| `.pptx`                | `ppt_converter`       | `DOC`                   |
| `.md`, `.markdown`     | `md_converter`        | `DOC`                   |
| `.pdf`                 | `pdf_converter` (선택) | `DOC`                   |

`Content-Type` 은 `multipart/form-data` 만 허용한다.

| 위치 | 이름             | 타입    | 필수 | 설명                                               |
|------|------------------|---------|------|----------------------------------------------------|
| form | `file`           | file    | Y    | 업로드 파일 (지원 확장자 중 하나)                  |
| form | `division`       | string  | Y    | 사업부 코드 (`HE`, `VE` …)                         |
| form | `team`           | string  | Y    | 팀 코드 (`CAE`, `BMS` …)                           |
| form | `year`           | int     | Y    | 연도 (예: `2026`)                                  |
| form | `seq`            | int     | N    | 순번 (기본 `1`)                                    |
| form | `tags`           | string  | N    | 콤마 구분 태그 (`"IGA,LS-DYNA"`)                   |
| form | `agents`         | string  | N    | 콤마 구분 agent_type (`"iga-analyst,cae-reporter"`) |
| form | `classification` | string  | N    | `internal`(기본)/`public`/`confidential` 등        |
| form | `domain`         | string  | N    | 도메인 라벨                                        |

응답: 변환기 결과 dict 그대로 (`schema_version`/`meta`/`sections`/... 또는 `headers`/`rows` …).

에러:

| HTTP | code                   | 설명                                |
|------|------------------------|-------------------------------------|
| 415  | `UNSUPPORTED_FORMAT`   | 확장자 매핑 없음                    |
| 413  | `PAYLOAD_TOO_LARGE`    | 업로드 크기 > `MAX_UPLOAD_MB`(50MB) |
| 422  | `VALIDATION_ERROR`     | 폼 필드 누락/타입 오류              |
| 500  | `CONVERSION_FAILED`    | 변환기 단계에서 예외                |
| 501  | `PDF_NOT_AVAILABLE`    | PDF 변환기가 설치되지 않음          |

curl:

```bash
curl -X POST http://localhost:8000/api/convert/ \
  -H "X-API-Key: $KEY" \
  -F "file=@iga_guide.docx" \
  -F "division=HE" -F "team=CAE" -F "year=2026" -F "seq=1" \
  -F "tags=IGA,LS-DYNA" -F "agents=iga-analyst"
```

---

## `POST /api/convert/ingest`

`/api/convert` 와 동일하게 업로드 → 변환을 수행한 뒤, 결과를
`api.ingest.normalizer.normalize` → `api.ingest.db_writer.write_record` 파이프라인에
넘겨 DB 에 INSERT 또는 UPDATE 한다.

폼 필드는 `/api/convert` 의 모든 필드 + 메타 확장 필드를 지원한다 (VS Code 확장 폼과
1:1 매핑 — `vscode_extension/docs/metadata_spec.md` 참조).

### 확장 메타 폼 필드

| 폼 필드            | 타입             | 기본값       | 매핑 (`RecordIn`) | 비고                                 |
|--------------------|------------------|--------------|-------------------|--------------------------------------|
| `status`           | enum string      | `draft`      | `status`          | `draft`/`review`/`approved`/`deprecated` |
| `language`         | string           | `ko`         | `language`        | ISO 639-1 (`ko`/`en`/`ja`/`zh`)       |
| `subject_keywords` | string (csv)     | `""`         | `subject_keywords`| 콤마 구분, 공백/빈 토큰 제거          |
| `derivation`       | enum string      | `original`   | `derivation`      | `original`/`extracted`/`aggregated`/`translated` |
| `quality_score`    | int (0..100)     | `null`       | `quality_score`   | 범위 외 → 422                         |
| `valid_from`       | string (ISO date)| `""`         | `valid_from`      | 빈값 허용 → `null`                    |
| `valid_until`      | string (ISO date)| `""`         | `valid_until`     | `valid_from > valid_until` 시 422     |
| `title_override`   | string           | `""`         | (덮어쓰기)         | 비어있지 않으면 변환기 추출 `title` 대체 |
| `summary_override` | string           | `""`         | (덮어쓰기)         | 비어있지 않으면 변환기 추출 `summary` 대체 |

빈 값(빈 문자열 / `null`) 은 정규화 결과 / `RecordIn` 기본값을 유지한다.

응답 본문:

```json
{
  "record_id": "DOC-HE-CAE-2026-000001",
  "status": "inserted",
  "sections_written": 12,
  "record": {
    "id": "DOC-HE-CAE-2026-000001",
    "data_type": "DOC",
    "title": "IGA 가이드",
    "summary": "...",
    "tags": ["IGA", "LS-DYNA"],
    "agents": ["iga-analyst"],
    "division": "HE",
    "team": "CAE",
    "year": 2026,
    "seq": 1,
    "source_file": "iga_guide.docx",
    "content_hash": "..."
  }
}
```

`status` 는 `inserted` / `updated` / `skipped` 중 하나이다.
`skipped` 는 같은 `id` 로 동일 `content_hash` 가 이미 존재할 때 발생 (멱등 동작).

curl:

```bash
curl -X POST http://localhost:8000/api/convert/ingest \
  -H "X-API-Key: $KEY" \
  -F "file=@iga_guide.docx" \
  -F "division=HE" -F "team=CAE" -F "year=2026" -F "seq=1" \
  -F "tags=IGA,LS-DYNA" -F "agents=iga-analyst"
```

---

## `GET /figures/{doc_id}/{filename}`

문서에서 추출된 그림(이미지) 바이너리를 정적으로 서빙한다.

| 위치 | 이름 | 타입 | 필수 | 설명 |
|------|------|------|------|------|
| path | `doc_id`   | string | Y | 레코드 ID (예: `DOC-HE-CAE-2026-000001`) |
| path | `filename` | string | Y | 파일명 (예: `F001.png`)                  |

- 마운트: FastAPI `StaticFiles`. 파일 시스템 루트는 환경변수 `FIGURES_DIR`
  (기본 `./figures`).
- JSON 의 `figures[i].image_path` 값(`"{doc_id}/F001.png"`) 을 그대로
  `/figures/` 접두사 뒤에 붙이면 된다.
- 응답 Content-Type 은 파일 확장자 기반 (`image/png`, `image/jpeg`, ...).
- 존재하지 않는 파일은 `404`.

curl:

```bash
curl -s -o F001.png \
  "http://localhost:8000/figures/DOC-HE-CAE-2026-000001/F001.png"
```

> 참고: 그림 바이너리가 아직 추출되지 않은 문서(예: 기존 텍스트 전용 문서)는
> 해당 폴더 자체가 없을 수 있다. 클라이언트는 `image_path` 가 비어 있는지
> 먼저 확인해야 한다.

---

## 공통 에러 응답

모든 에러 경로는 통합 envelope 으로 응답한다 (Agent 12 / `api.errors`):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "사람이 읽을 수 있는 에러 설명",
    "details": {},
    "request_id": "ac229f2a6d504d45a3b103cd75115b9d"
  }
}
```

- `code` 는 항상 SCREAMING_SNAKE 식 도메인 코드.
- `request_id` 는 `RequestLoggingMiddleware` 가 생성하는 헥스 문자열. 응답
  헤더에도 `X-Request-ID` 로 노출된다.
- `details` 는 도메인별 추가 정보 (e.g. `PAYLOAD_TOO_LARGE` → `{"max_bytes": ..., "received_bytes": ...}`).

| HTTP | code                   | 의미                                |
|------|------------------------|-------------------------------------|
| 400  | `BAD_REQUEST`          | 잘못된 쿼리/바디                     |
| 401  | `AUTHENTICATION_ERROR` | API 키 누락/잘못됨/폐기됨            |
| 403  | `AUTHORIZATION_ERROR`  | 권한 부족 (부트스트랩 키 요구 등)   |
| 404  | `NOT_FOUND`            | 리소스 없음                          |
| 409  | `CONFLICT`             | 중복/충돌                            |
| 413  | `PAYLOAD_TOO_LARGE`    | 업로드 크기 > `MAX_UPLOAD_MB`        |
| 415  | `UNSUPPORTED_FORMAT`   | 변환 가능한 확장자 아님               |
| 422  | `VALIDATION_ERROR`     | 스키마 검증 실패 (Pydantic / 도메인) |
| 429  | `RATE_LIMIT`           | (예약) 레이트 리밋                   |
| 500  | `INTERNAL_ERROR`       | 서버 내부 오류                       |
| 500  | `CONVERSION_FAILED`    | 변환기 단계에서 예외                  |
| 501  | `PDF_NOT_AVAILABLE`    | PDF 변환기가 설치되지 않음            |

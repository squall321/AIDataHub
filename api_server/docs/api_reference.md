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
- [Discovery (start here for AI agents)](#discovery--rag-friendly-endpoints-agent-30)
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

## Discovery / RAG-friendly endpoints (Agent 30)

이 5개 엔드포인트는 LLM 에이전트가 백엔드 source 를 읽지 않고도 허브를 사용할
수 있게 한다. 상세 onboarding 은 [`AGENT_ONBOARDING.md`](./AGENT_ONBOARDING.md).

### `GET /api/discover`

허브 전체 카탈로그. AI 에이전트의 시작점.

- 쿼리 파라미터: `no_cache=true` (선택) — 60초 in-memory 캐시 우회.
- 응답 키:
  - `version` (계약 버전, 현재 `1.0`)
  - `total_records`, `by_data_type`, `by_division`, `by_classification`
  - `agents[]` — 에이전트 메타 + record_count + sample_query
  - `data_types_explained` — 한글 설명 dict
  - `starting_points` — 다음 단계 endpoint 모음
  - `schema_url`, `hints_url`, `llm_doc_url`, `ask_url`
  - `generated_at` (ISO timestamp)

### `GET /api/schema`

머신 리더블 JSON Schema (draft-2020-12). 정적, DB 접근 없음.

- `properties.data_type.enum`, `properties.classification.enum`, `properties.status.enum`,
  `properties.derivation.enum`, `properties.access_pattern.enum` 등 모든 enum 을 한 곳에서 노출.
- `oneOf` 로 data_type 별 `content` 페이로드 모양 분기 (DOC/DATA/SIM/CAD/LOG_FORM_OTHER).
- `examples[]` 에 DOC + DATA 샘플 포함.
- `x-relationships` 에 parent_record_id / agents[] / attachments / sections / related_record_ids 관계 설명.

### `GET /api/hints`

자연어 힌트 카탈로그.

- 쿼리: `context` ∈ {`getting_started`, `searching`, `filtering_by_agent`,
  `tabular_data`, `time_bounded`, `attachments`, `cross_record_relations`}.
- 생략 시 전체 힌트.
- 응답: `{context, available_contexts, hints:[{hint, sample_endpoint, why_useful, context}]}`.

### `GET /api/docs/llm.txt`

LLM 컨텍스트 1회 주입용 통합 마크다운. `text/plain`, 5-10KB.

8개 섹션: What is this hub / Core concepts / ID format / Key endpoints /
Common query patterns / data_type → content shape map / How to start / Discovery contract.

### `POST /api/ask`

자연어 쿼리 → 구조화된 필터 + 결과.

- 바디: `{"query": "최근 1주일 IGA 시뮬", "limit": 5}` (limit 1-50).
- 응답:
  ```json
  {
    "interpreted_query": {
      "agent": "iga-analyst",
      "data_type": "SIM",
      "created_at_gte": "2026-05-01",
      "explanation": "키워드로 ...",
      "source": "keyword"
    },
    "results": [...],
    "total_matched": 12,
    "follow_up_queries": [...],
    "raw_query": "..."
  }
  ```
- 동작: `OPENAI_API_KEY` 가 있으면 LLM (`source: "llm"`), 없으면 키워드 폴백 (`source: "keyword"`).
- 폴백은 다음 키를 추출한다: `agent`, `data_type`, `capabilities`, `created_at_gte` (최근 N일/주/개월),
  `quality_score_gte`, `year`, `status`, `classification`.

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
  "teams": ["HE", "EV", "PT", "DA", "MX", "VD"],
  "groups": {
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
    "team": false,
    "group":     true,
    "domain":   true
  }
}
```

| 필드                      | 출처                                                 |
|---------------------------|------------------------------------------------------|
| `teams` / `groups`     | `api.seed.teams` (정적 매핑)                     |
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
| query | `team`  | string | N    | `HE` / `EV` / ...                               |
| query | `group`      | string | N    | `CAE` / ...                                     |
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
      "team": "HE",
      "group": "CAE",
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
  "team": "HE",
  "group": "CAE",
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

데이터 타입/팀/그룹별 분포 통계.

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

## 거버넌스 (Migration 0008, Agent 31)

### `GET /api/analytics/usage`

상위 접근 레코드. soft-deleted 는 제외.

| 위치 | 이름   | 타입 | 필수 | 설명 (기본) |
|------|--------|------|------|-------------|
| query | `limit` | int | N | 1..100 (20) |

응답 예:

```json
{
  "items": [
    {"id": "DOC-HE-CAE-2026-000001", "title": "...", "data_type": "DOC",
     "read_count": 42, "last_accessed_at": "2026-05-08T12:34:56+00:00"}
  ],
  "total": 1,
  "limit": 20
}
```

### `GET /api/records/{id}/lineage`

레코드 계보(조상/자손)를 반환. 조상은 부모 → 조부모 순서, 자손은 BFS 순서.

응답 예:

```json
{
  "record_id": "DOC-HE-CAE-2026-000004",
  "self": {"id": "DOC-HE-CAE-2026-000004", "data_type": "DOC", "title": "G3", "version": "4.0", "status": "draft", "derivation": "extracted", "parent_record_id": "DOC-HE-CAE-2026-000003", "content_hash": null, "deleted_at": null, "created_at": "..."},
  "ancestors": [
    {"id": "DOC-HE-CAE-2026-000003", "...": "..."},
    {"id": "DOC-HE-CAE-2026-000002", "...": "..."},
    {"id": "DOC-HE-CAE-2026-000001", "...": "..."}
  ],
  "descendants": [],
  "ancestor_count": 3,
  "descendant_count": 0
}
```

### `GET /api/records/{id}/diff?from={other_id}`

두 레코드의 메타/섹션을 비교한다. `meta_changes` 는 `{field: [old, new]}`,
`section_changes` 는 `section_id` 매칭으로 `added/removed/modified` 분류.

응답 예:

```json
{
  "from": "DOC-HE-CAE-2026-000001",
  "to":   "DOC-HE-CAE-2026-000002",
  "meta_changes": {
    "title": ["원본 제목", "변경 제목"],
    "tags":  [["a"], ["a", "b"]]
  },
  "section_changes": [
    {
      "section_id": "1.1",
      "kind": "modified",
      "title_changes": null,
      "content_diff": "@@ ... @@\n-old line\n+new line\n"
    },
    {"section_id": "2.1", "kind": "added",   "title_changes": [null, "새 섹션"],   "content_diff": "..."},
    {"section_id": "1.2", "kind": "removed", "title_changes": ["삭제될 섹션", null], "content_diff": "..."}
  ],
  "block_changes": "summary"
}
```

### `POST /api/records/{id}/restore`

soft-deleted 레코드의 `deleted_at` 을 NULL 로 되돌린다 (멱등).

### `DELETE /api/records/{id}` (soft / hard)

기본은 soft delete (`deleted_at = NOW()`). `?hard=true` 는 부트스트랩 API 키
(`X-API-Key`) 가 필요한 물리 삭제.

### `GET /api/records?include_deleted=true` & `GET /api/records/{id}?include_deleted=true`

soft-deleted 레코드까지 조회.

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
| form | `team`       | string  | Y    | 팀 코드 (`HE`, `VE` …)                             |
| form | `group`           | string  | Y    | 그룹 코드 (`CAE`, `BMS` …)                         |
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
  -F "team=HE" -F "group=CAE" -F "year=2026" -F "seq=1" \
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
| `seq`              | int              | `1`          | (ID seq)          | `0` 또는 빈 값이면 backend 가 `MAX(seq)+1` 자동 할당 (S1, Agent 32) |
| `persist_attachments` | bool          | `true`       | (운영 동작)        | `false` 면 변환 산출물의 첨부 폴더를 `attachments_dir` 로 복사하지 않음 (S5) |

빈 값(빈 문자열 / `null`) 은 정규화 결과 / `RecordIn` 기본값을 유지한다.

응답 본문:

```json
{
  "record_id": "DOC-HE-CAE-2026-000001",
  "status": "inserted",
  "sections_written": 12,
  "assigned_seq": 1,
  "attachments_persisted": 3,
  "record": {
    "id": "DOC-HE-CAE-2026-000001",
    "data_type": "DOC",
    "title": "IGA 가이드",
    "summary": "...",
    "tags": ["IGA", "LS-DYNA"],
    "agents": ["iga-analyst"],
    "team": "HE",
    "group": "CAE",
    "year": 2026,
    "seq": 1,
    "source_file": "iga_guide.docx",
    "content_hash": "..."
  }
}
```

`assigned_seq` 는 실제로 사용된 seq (자동 할당이면 backend 가 결정한 값).
`attachments_persisted` 는 `attachments_dir` 로 복사된 파일 수 (S5).

`status` 는 `inserted` / `updated` / `skipped` 중 하나이다.
`skipped` 는 같은 `id` 로 동일 `content_hash` 가 이미 존재할 때 발생 (멱등 동작).

curl:

```bash
curl -X POST http://localhost:8000/api/convert/ingest \
  -H "X-API-Key: $KEY" \
  -F "file=@iga_guide.docx" \
  -F "team=HE" -F "group=CAE" -F "year=2026" -F "seq=1" \
  -F "tags=IGA,LS-DYNA" -F "agents=iga-analyst"
```

---

## `/api/jobs` — 비동기 잡 큐 (S3, Agent 32)

장시간 작업(임베딩 backfill, OCR, 배치 인제스트) 을 background `asyncio.Task` 로
돌리고 클라이언트에 진행률/결과를 노출한다. 본 단계에서는 **process-local 인-메모리**
잡 저장소를 사용한다 — 다중 프로세스 배포 시 외부 큐(Arq/Redis 등) 로 이전 필요.

### `POST /api/jobs/embed`

임베딩 backfill 잡을 등록하고 `202 Accepted` + `job_id` 반환.

요청 본문 (JSON):

```json
{ "record_id": "DOC-HE-CAE-2026-000001" }
```

또는 다수 레코드:

```json
{ "record_ids": ["DOC-HE-CAE-2026-000001", "DOC-HE-CAE-2026-000002"] }
```

본문이 비어 있으면 (`{}`) 모든 미임베딩 섹션을 대상으로 한다.

응답 (202):

```json
{
  "id": "embed-3a14b29cf021",
  "kind": "embed",
  "status": "running",
  "progress": 0.0,
  "result": null,
  "error": null,
  "created_at": 1746676120.83,
  "started_at": 1746676120.85,
  "finished_at": null,
  "payload": {"record_id": "DOC-HE-CAE-2026-000001"}
}
```

### `GET /api/jobs/{id}`

단일 잡 상태/결과 조회. 404 면 만료(>1h) 또는 미존재.

### `GET /api/jobs?kind=embed`

최근 잡 목록. `kind` 미지정이면 모든 종류. `limit` 으로 상한 조절(기본 100).

### 환경변수

| 변수                   | 기본 | 설명                                        |
|------------------------|------|---------------------------------------------|
| `AUTO_EMBED_ON_INSERT` | false | 레코드 INSERT/UPDATE 후 임베딩 잡 자동 등록 |
| `JOBS_TTL_SECONDS`     | 3600  | 완료 잡 보관 TTL                            |
| `JOBS_LIST_LIMIT`      | 100   | `/api/jobs` 응답 최대 개수                  |

> 한계: 잡 영속화 / 재시작 후 복구 / 분산 큐는 본 사이클 deferred. 운영 트래픽
> 의존 시 Arq/Celery 도입 필요.

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

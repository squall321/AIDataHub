# Extension Integration Plan — Backend Changes

> 이 문서는 **VS Code 확장 (`AI_data/vscode_extension`)** 통합을 위해 백엔드 (`api_server`) 가 처리해야 할 변경사항을 정리한다. 백엔드 개발 에이전트가 그대로 처리할 수 있게끔 **체크리스트 + 구현 위치 + 응답 스키마** 를 명시한다.
>
> 짝 문서: `AI_data/vscode_extension/docs/PLAN.md`, `metadata_spec.md`.
>
> **Owner**: backend-dev-agent. 본 문서의 §2 ~ §6 항목은 모두 머지 대상.

---

## 0. 전체 요약

| # | 변경                                            | 우선순위 | 추정 |
|---|-------------------------------------------------|----------|------|
| 1 | CORS: `vscode-webview://*` 허용                  | P0       | XS   |
| 2 | `GET /api/meta/options` 신규                     | P0       | M    |
| 3 | `POST /api/auth/keys/verify` 신규                | P0       | S    |
| 4 | `/api/convert/ingest` 폼 필드 확장              | P1       | M    |
| 5 | 에러 envelope 통일 (`/api/convert/*` 포함)       | P1       | S    |
| 6 | `/api/meta/options` 의 `max_upload_mb` 노출      | P1       | XS   |

---

## 1. CORS — `vscode-webview` 오리진 허용

### 배경
VS Code Webview 의 fetch 는 오리진이 `vscode-webview://<uuid>` 로 발급된다. 현재 `api_server` 는 `CORSMiddleware` 가 비활성/제한적이어서 사전요청(OPTIONS) 단계에서 차단된다.

### 변경
`src/api/main.py` (또는 `src/api/__init__.py` 의 앱 팩토리) 에:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^vscode-webview://.*$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["X-API-Key", "Content-Type", "Accept"],
    expose_headers=["X-Request-ID"],
)
```

추가로 환경변수 `EXTRA_ALLOWED_ORIGINS` (콤마 구분) 를 둬서 운영 시 `https://datahub.example.com` 같은 도메인을 추가할 수 있게.

### 수용 기준
- Webview 에서 `OPTIONS /api/meta/options` 사전요청이 200 + 적절한 헤더로 응답.
- 기존 정적 마운트(`/figures`, `/attachments`) 는 영향 없음.

---

## 2. `GET /api/meta/options` — 클라이언트 메타 옵션 카탈로그

### 배경
확장 폼의 `division` / `team` / `agents` / `classification` / `status` / `language` 셀렉트박스는 백엔드가 **권위적인 옵션 목록** 을 내려줘야 일관성을 유지한다.

### 위치
새 파일: `src/api/routes/meta.py`. `src/api/routes/__init__.py` 에서 `app.include_router(meta.router)`.

### 응답 스키마

```jsonc
GET /api/meta/options
HTTP 200
{
  "version": "1.0",
  "divisions": ["HE", "EV", "PT"],
  "teams": {
    "HE": ["CAE", "Test", "Design"],
    "EV": ["BMS", "Battery"],
    "PT": ["Material"]
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
  "supported_extensions": [".docx", ".pdf", ".pptx", ".md", ".markdown", ".xlsx"],
  "max_upload_mb":  50,
  "allow_custom": {
    "division": false,
    "team":     true,
    "domain":   true
  }
}
```

### 데이터 소스

| 키                | 출처                                                                        |
|-------------------|-----------------------------------------------------------------------------|
| `divisions`       | `src/api/seed/divisions.py` 신규 정적 리스트 (또는 `records.division` distinct) |
| `teams`           | 1차: 정적 매핑 dict; 2차: `records` 의 distinct 결과를 머지                   |
| `agents`          | `agents` 테이블 SELECT (이미 `/api/agents` 에 존재 — 동일 직렬화 재사용)    |
| `classifications` | `api.schemas.common.CLASSIFICATIONS`                                        |
| `statuses`        | `api.schemas.common.STATUSES`                                               |
| `derivations`     | `api.schemas.common.DERIVATIONS`                                            |
| `languages`       | `["ko","en","ja","zh"]` 정적 (확장 가능)                                    |
| `data_types`      | `api.schemas.id_format.DATA_TYPES`                                          |
| `supported_extensions` | `api.services.converter_dispatch.EXTENSION_MAP.keys()`                  |
| `max_upload_mb`   | `settings.max_upload_mb`                                                    |
| `allow_custom`    | 정적 dict — 향후 환경변수화                                                 |

### 캐싱
- 응답에 `Cache-Control: public, max-age=300` 헤더.
- 클라이언트에서 5분 in-memory 캐시.

### 인증
- `AUTH_REQUIRED=true` 일 때 `X-API-Key` 필요.
- 단, 부트스트랩 키 불필요 (어떤 발급 키여도 OK).

### 수용 기준
- 응답 키마다 위 출처와 동일한 값.
- `agent_type` 시드와 정합성 (`python -m api.seed` 결과와 동일).
- pytest 로 응답 shape 스냅샷 검증.

---

## 3. `POST /api/auth/keys/verify` — 키 유효성만 검증

### 배경
현재 `/api/auth/keys` 는 부트스트랩 키 보유자만 호출 가능하다. 확장은 일반 사용자의 발급키가 **유효하기만 한지** 만 알면 된다.

### 위치
`src/api/routes/auth.py` 에 핸들러 추가:

```python
@router.post("/verify", status_code=status.HTTP_200_OK)
async def verify_key(
    principal: Principal = Depends(require_api_key),  # 이미 존재하는 의존성
) -> dict:
    return {
        "ok": True,
        "key_name": principal.key_name,
        "agent_scopes": principal.agent_scopes or [],
    }
```

`require_api_key` (혹은 동일 효과의 dependency) 가 401 을 던지므로 정상 200 = 유효.

### 응답 스키마

```jsonc
POST /api/auth/keys/verify
HTTP 200
{
  "ok": true,
  "key_name": "iga-team-uploader",
  "agent_scopes": ["iga-analyst", "cae-reporter"]
}
```

401 시 기존 에러 envelope:
```json
{ "error": { "code": "INVALID_API_KEY", "message": "...", "request_id": "..." } }
```

### 수용 기준
- 부트스트랩 키 없이도 호출 가능.
- 만료/폐기 키는 401.

---

## 4. `/api/convert/ingest` 폼 필드 확장

### 배경
[`metadata_spec.md`](../../vscode_extension/docs/metadata_spec.md) 에서 합의된 필드 중 현재 ingest 라우터에 **누락된 폼 필드** 를 추가한다.

### 추가할 폼 필드 (`src/api/routes/convert.py`)

| 폼 필드            | 타입               | 매핑 (`RecordIn`)         | 비고                              |
|--------------------|--------------------|---------------------------|-----------------------------------|
| `status`           | str                | `status`                  | enum (`STATUSES`)                 |
| `language`         | str                | `language`                | 기본 `ko`                         |
| `subject_keywords` | str (csv)          | `subject_keywords`        | `_split_csv` 동일 처리            |
| `derivation`       | str                | `derivation`              | 기본 `original`                   |
| `quality_score`    | int (None)         | `quality_score`           | 0~100                             |
| `valid_from`       | str (ISO date)     | `valid_from` (date)       | 빈 값 허용 → None                 |
| `valid_until`      | str (ISO date)     | `valid_until` (date)      | 빈 값 허용 → None                 |
| `title_override`   | str (None)         | (note: 별도 처리)          | normalize 후 `title` 덮어씀       |
| `summary_override` | str (None)         | (note: 별도 처리)          | normalize 후 `summary` 덮어씀     |

### normalize 머지 로직

`src/api/ingest/normalizer.py` 의 `normalize(payload, *, overrides)` 시그니처 확장 (혹은 라우터에서 `record_in = normalize(payload); record_in = record_in.model_copy(update=overrides)`).

```python
overrides = {
    k: v for k, v in {
        "status": status_value,
        "language": language_value,
        "subject_keywords": _split_csv(subject_keywords),
        "derivation": derivation_value,
        "quality_score": quality_score,
        "valid_from": _parse_date(valid_from),
        "valid_until": _parse_date(valid_until),
        "title": title_override or None,      # truthy 만 덮어씀
        "summary": summary_override or None,
    }.items() if v not in (None, "", [])
}
record_in = normalize(payload).model_copy(update=overrides)
```

### 검증
- `status not in STATUSES` → 422 `VALIDATION_ERROR`.
- `quality_score` 범위 검증은 `RecordIn._quality_in_range` 가 이미 처리.
- `valid_from > valid_until` → 422.

### 수용 기준
- 신규 필드를 모두 채워 보냈을 때 DB 컬럼에 정확히 반영.
- 비어 있는 필드는 기본값 유지 (regression 없음).
- 기존 호출자 (CLI / 기존 curl 예시) 도 그대로 동작.

---

## 5. 에러 envelope 통일

### 배경
`/api/convert/*` 의 일부 경로는 현재 `{"detail": "..."}` 형태로 반환. 확장은 `{ error: { code, message, request_id } }` 를 가정하고 매핑 테이블을 만든다.

### 변경
- `src/api/errors.py` 의 `APIError` 핸들러를 **모든 라우트** 에 일관 적용 — 이미 글로벌 핸들러가 있다면 누락 경로 점검.
- `PayloadTooLargeError`, `UnsupportedFormatError`, `ValidationError` 모두 `code` 필드 포함.
- `request_id` 는 미들웨어가 생성/주입.

### 수용 기준
- 413/415/422/500 모든 경로에서 `body.error.code` 가 채워짐.
- pytest: 음성 케이스(`test_convert_errors.py`) 추가.

---

## 6. 작은 보강

### 6.1 `GET /api/meta/options` 의 `max_upload_mb`
- 위 §2 에 포함. 클라이언트 사전 차단용.

### 6.2 `Health` 응답에 `version` / `auth_required` 노출
`GET /` 또는 `GET /health` 응답에 다음 추가:
```json
{ "status": "ok", "version": "0.x.y", "auth_required": true }
```
확장의 [Test Connection] 단계에서 인증 모드를 한 번에 판단 가능.

### 6.3 (선택) 자동 `seq` 발급
- `seq=0` 또는 미지정 시 백엔드가 동일 `(data_type, division, team, year)` 의 `MAX(seq)+1` 자동 부여.
- ingest 라우터에서 `seq=Form(0)` 으로 받고, normalizer 진입 직전 빈 값 보충.
- 이번 사이클은 **계획만**, 실제 구현은 다음 사이클로 미룸 (race 조건 검토 필요).

---

## 7. 구현 순서 (백엔드 에이전트가 따를 순서)

1. **§5** — 에러 envelope 점검 (다른 작업의 회귀 디버깅 토대).
2. **§1** — CORS 미들웨어 추가.
3. **§3** — `/api/auth/keys/verify` (간단, 의존성 분리 검증 용도로 먼저).
4. **§2** — `/api/meta/options` (정적 enum + agents 합치기).
5. **§6.2** — `/health` 보강.
6. **§4** — ingest 폼 필드 확장 + normalize override.
7. **§6.1** — `/api/meta/options` 에 `max_upload_mb` (4 와 함께 끝낼 수 있음).
8. **§6.3** — auto-seq (Agent 32, S1 — 완료): `seq=0` 또는 빈 값 제출 시
   `MAX(seq)+1` 자동 할당. 헬퍼 `api.services.seq.next_seq`. 단일-writer 가정.

각 단계에 단위 테스트 + `pytest -v` 통과 후 커밋.

## 8. 짝 문서 / 참고

- `vscode_extension/docs/PLAN.md`
- `vscode_extension/docs/metadata_spec.md` — 폼 ↔ 백엔드 필드 1:1 매핑
- `vscode_extension/docs/ux_flow.md`
- `api_server/docs/api_reference.md` — 변경 후 함께 갱신할 것
- `api_server/src/api/schemas/common.py` — `RecordIn` 정의

## 9. 변경 후 갱신해야 할 문서

- [ ] `api_server/docs/api_reference.md` — `/api/meta/options`, `/api/auth/keys/verify`, ingest 폼 필드 추가
- [ ] `api_server/README.md` — 확장 통합 섹션 한 줄 추가 (선택)
- [ ] `api_server/.env.example` — 새 환경변수 (`EXTRA_ALLOWED_ORIGINS`) 명시

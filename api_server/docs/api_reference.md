# API Reference

AI Data Hub REST API. 베이스 URL: `http://localhost:8000`.
모든 응답은 JSON.

> 본 문서는 Agent 3 의 라우터 계약(`/api/data`, `/api/records`, `/api/search`,
> `/api/agents`, `/api/analytics`)을 기준으로 작성되었습니다. 실제 응답 예시 키는
> 구현체에 따라 일부 다를 수 있으니, OpenAPI(<http://localhost:8000/docs>)를 함께 참고하세요.

## 목차

- [Health](#health)
- [GET /api/data](#get-apidata)
- [GET /api/records](#get-apirecords)
- [GET /api/records/{id}](#get-apirecordsid)
- [GET /api/search](#get-apisearch)
- [GET /api/agents](#get-apiagents)
- [GET /api/analytics/distribution](#get-apianalyticsdistribution)
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

```json
{
  "detail": "사람이 읽을 수 있는 에러 설명"
}
```

| 코드 | 의미                                |
|------|-------------------------------------|
| 400  | 잘못된 쿼리/바디                    |
| 404  | 리소스 없음                         |
| 422  | 스키마 검증 실패 (Pydantic)         |
| 500  | 서버 내부 오류 (DB 연결 실패 등)    |

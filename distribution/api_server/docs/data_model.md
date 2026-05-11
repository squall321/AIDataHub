# Data Model

## 개요

Mobile eXperience AI Data Hub는 사업부 문서/데이터를 PostgreSQL 에 적재하고
사내 LLM 에이전트가 표준 API/MCP로 조회할 수 있게 만든 백엔드이다.
모델 핵심은 **Record(상위 메타) + RecordSection(본문 청크) + Agent(에이전트)**
+ **AgentRecord(N:M 매핑)** 의 4개 테이블이다.

## ID 포맷

```
<DATA_TYPE>-<DIVISION>-<TEAM>-<YEAR>-<SEQ>
```

| 필드        | 길이/타입       | 예시                                |
|-------------|------------------|-------------------------------------|
| DATA_TYPE   | 3~4자 enum      | `DOC`, `DATA`, `SIM`, `CAD`, `LOG`, `FORM`, `OTHER` |
| DIVISION    | 2~3자          | `HE`, `EV`                          |
| TEAM        | 2~6자          | `CAE`, `Test`                       |
| YEAR        | 4자리 연도     | `2026`                              |
| SEQ         | 6자리 zero-pad | `000001`                            |

예시:

| 데이터 종류 | 예시 ID                            |
|-------------|------------------------------------|
| 가이드 문서 | `DOC-HE-CAE-2026-0000000001`           |
| 측정 데이터 | `DATA-HE-CAE-2026-0000000018`          |
| 시뮬레이션  | `SIM-HE-CAE-2026-0000000045`           |
| CAD 모델    | `CAD-HE-CAE-2026-0000000012`           |
| 로그        | `LOG-HE-CAE-2026-0000000003`           |

> 참고: 변환기(`converter`)는 `meta.doc_id` 를 `<DIV>-<TEAM>-<YEAR>-<SEQ>`
> (data_type 접두사 없이) 형식으로 발급한다. 정규화기(`api.ingest.normalizer`)가
> `data_type` 접두사를 붙여 최종 `Record.id` 를 만든다.

## data_type 별 content 페이로드

`Record.content` 는 JSONB 컬럼이며, `data_type` 마다 페이로드 스키마가 다르다.
구체 스키마는 `src/api/schemas/` 모듈을 참조 (Agent 2 산출물).

| data_type | 스키마 모듈                    | 핵심 키                                                  |
|-----------|--------------------------------|----------------------------------------------------------|
| DOC       | `api.schemas.document`         | `meta`, `toc`, `sections[]`, `figures[]`, `tables[]`, `sources[]` |
| DATA      | `api.schemas.data`             | `dataset` (rows/columns/units), `samples[]`              |
| SIM       | `api.schemas.sim`              | `solver`, `version`, `input_files[]`, `results{}`        |
| CAD       | `api.schemas.cad`              | `format` (STEP/IGES/...), `file_path`, `bbox_mm`, `mass_kg` |
| LOG       | (공통)                         | 타임스탬프 시퀀스                                         |
| FORM      | (공통)                         | 양식 응답                                                 |
| OTHER     | (공통)                         | free-form                                                 |

`api.schemas.common` 의 `RecordIn` / `RecordOut` 이 모든 타입의 공통 메타 필드를
정의하며, `content` 만 data_type 별 모델로 검증된다.

## 데이터베이스 스키마 (텍스트 ER)

```
┌──────────────────────────────────────────────────────────┐
│                          records                          │
├──────────────────────────────────────────────────────────┤
│ id              VARCHAR(80) PK                            │
│ data_type       VARCHAR(20)        ┐                      │
│ division        VARCHAR(10)        │  natural key        │
│ team            VARCHAR(20)        │  (uq)               │
│ year            SMALLINT           │                      │
│ seq             INTEGER            ┘                      │
│ title           TEXT                                       │
│ summary         TEXT                                       │
│ tags            TEXT[]   (GIN)                             │
│ agents          TEXT[]   (GIN)                             │
│ schema_version  VARCHAR(10)                                │
│ content         JSONB    (GIN, jsonb_path_ops)             │
│ content_hash    VARCHAR(64)                                │
│ source_file     TEXT                                       │
│ author/dept/project/version  ...                           │
│ created_at      TIMESTAMPTZ                                │
│ updated_at      TIMESTAMPTZ                                │
└────────────────────┬───────────────────────┬─────────────┘
                     │ 1                  N  │
                     │                       │
                     ▼                       ▼
        ┌──────────────────────┐   ┌────────────────────────┐
        │  record_sections      │   │  agent_records          │
        ├──────────────────────┤   ├────────────────────────┤
        │ id          BIGSERIAL │   │ agent_type   FK ─┐     │
        │ record_id   FK→records│   │ record_id    FK ─┘ PK  │
        │ section_id  VARCHAR   │   │ priority     SMALLINT  │
        │ level       SMALLINT  │   └────────────┬───────────┘
        │ title       TEXT      │                │ N
        │ content_text TEXT     │                │
        │ figure_refs TEXT[]    │                ▼
        │ table_refs  TEXT[]    │      ┌──────────────────────┐
        └──────────────────────┘      │       agents          │
                                      ├──────────────────────┤
                                      │ agent_type  PK        │
                                      │ name        TEXT      │
                                      │ description TEXT      │
                                      │ common_tags TEXT[]    │
                                      │ data_types  TEXT[]    │
                                      │ created_at  TIMESTAMP │
                                      └──────────────────────┘
```

### 인덱스 요약

| 테이블           | 인덱스                                    | 용도                                |
|------------------|-------------------------------------------|-------------------------------------|
| records          | `idx_records_type` (data_type)            | 타입 필터                            |
| records          | `idx_records_div_team`                    | 팀/그룹 필터                         |
| records          | `idx_records_year`                        | 연도 필터                            |
| records          | `idx_records_agents` (GIN)                | `agents` 배열 매칭                   |
| records          | `idx_records_tags` (GIN)                  | 태그 매칭                            |
| records          | `idx_records_content` (GIN, jsonb_path)   | content 키/값 검색                   |
| record_sections  | `idx_sections_record`                     | 부모 레코드 조회                     |
| record_sections  | `uq_sections_record_section`              | 섹션 중복 방지                       |
| agent_records    | `idx_agent_records_agent`                 | agent 별 record 목록                 |

## 흔한 쿼리 패턴

### 1) 특정 에이전트가 사용할 레코드 목록

```sql
SELECT r.*
FROM records r
JOIN agent_records ar ON ar.record_id = r.id
WHERE ar.agent_type = 'iga-analyst'
ORDER BY ar.priority DESC, r.updated_at DESC
LIMIT 20;
```

### 2) 키워드로 섹션 단위 매칭 (FTS 도입 전 단순 버전)

```sql
SELECT r.id, s.section_id, s.title, s.content_text
FROM records r
JOIN record_sections s ON s.record_id = r.id
WHERE s.content_text ILIKE '%offset%'
   OR r.title ILIKE '%offset%'
LIMIT 20;
```

### 3) 태그 매칭 (배열)

```sql
SELECT id, title FROM records
WHERE tags @> ARRAY['battery','crash'];
```

### 4) JSON content 내부 키 매칭

```sql
SELECT id, title FROM records
WHERE content @> '{"solver":"LS-DYNA"}';
```

### 5) 데이터 타입 분포

```sql
SELECT data_type, COUNT(*) AS n
FROM records
GROUP BY data_type
ORDER BY n DESC;
```

## 거버넌스 (Migration 0008)

Migration 0008 은 audit log + soft delete + usage stats 를 추가한다.

### audit_log

```sql
CREATE TABLE audit_log (
  id            BIGSERIAL PRIMARY KEY,
  record_id     VARCHAR(80),                       -- 글로벌 이벤트는 NULL
  actor         VARCHAR(100),                      -- API 키 이름 / 'system' / 'cli'
  action        VARCHAR(50) NOT NULL,              -- INSERT|UPDATE|DELETE|RESTORE|ACCESS|VIEW
  field_changes JSONB NOT NULL DEFAULT '{}',       -- {field: [old, new]} for UPDATE
  request_id    VARCHAR(64),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

`db_writer.write_record` 와 records 라우터(POST/PATCH/DELETE/RESTORE/VIEW)
호출 시 자동으로 한 행이 추가된다. 실패는 best-effort — 메인 흐름을 막지
않는다.

### soft delete

`records.deleted_at TIMESTAMPTZ` 컬럼이 추가됐다. NULL 이면 활성, 값이
있으면 soft-deleted.

- 기본 list/get 은 활성 행만 반환.
- `?include_deleted=true` 로 복구된 옵션.
- 부분 인덱스 `idx_records_deleted_at WHERE deleted_at IS NULL` 로 활성 행
  조회를 빠르게 한다.

### 버전 체인 (lineage)

`parent_record_id` (Migration 0006 도입) 를 그대로 사용한다.
- "동일 데이터의 새 리비전" 은 `derivation = "extracted"` 또는 자유롭게 선택
  (기존 enum 4개 중). `content_hash` 가 새로 산출돼 다르면 새 버전.
- 부모 자동 deprecate(`status = "deprecated"`) 는 본 사이클에서는 CLI 헬퍼로
  분리 — `governance.md` 참고.

### usage stats

`records.read_count INTEGER NOT NULL DEFAULT 0` 와
`records.last_accessed_at TIMESTAMPTZ` 가 추가됐다. `GET /api/records/{id}`
및 `GET /api/data` 호출 시 fire-and-forget 으로 증가한다.

## 마이그레이션

- `alembic/versions/0001_initial_schema.py` : 초기 스키마.
- `embedding`(pgvector) 컬럼은 후속 마이그레이션에서 추가 예정.
- 실행:

  ```powershell
  alembic upgrade head
  ```

## 그림 리소스 (figure binaries)

문서에서 추출한 그림 이미지는 DB 가 아닌 **파일 시스템** 에 저장하고
정적 마운트로 서빙한다.

### 디렉터리 레이아웃

```
{FIGURES_DIR}/
├── DOC-HE-CAE-2026-0000000001/
│   ├── F001.png
│   ├── F002.jpeg
│   └── F003.emf
├── DOC-HE-CAE-2026-0000000002/
│   └── ...
```

- `FIGURES_DIR` 기본값: `./figures` (작업 디렉터리 기준).
- 환경변수 `FIGURES_DIR` 로 오버라이드 가능 (운영 배포에서는 영구 볼륨 권장).

### URL 매핑

```
GET /figures/{doc_id}/{filename}
예: GET /figures/DOC-HE-CAE-2026-0000000001/F001.png
```

FastAPI 의 `StaticFiles` 마운트(`src/api/routes/__init__.py`) 가 이 URL 을
`{FIGURES_DIR}/{doc_id}/{filename}` 로 직접 매핑한다.

### JSON 스키마와의 연결

`figures[i].image_path` 필드는 **`/figures` 마운트 직하** 의 상대 경로다.

```json
{
  "id":          "DOC-HE-CAE-2026-0000000001-F001",
  "number":      1,
  "caption":     "Figure 1: ...",
  "section_ref": "1.2",
  "image_path":  "DOC-HE-CAE-2026-0000000001/F001.png"
}
```

URL 조립: `f"http://host/figures/{image_path}"`.

### 적재(ingest) 시 복사

`python -m api.ingest path/to/{doc_id}.json` 실행 시:

- 같은 디렉터리에 `{doc_id}/` 폴더가 있으면 `FIGURES_DIR/{doc_id}/` 로
  `shutil.copytree(dirs_exist_ok=True)` 로 복사된다 (멱등).
- `--figures-source <dir>` 로 원본 위치를 명시 가능.
- `--no-figures` 로 복사를 건너뛸 수 있다.

변환 단계(`python -m converter ...`) 는 출력 폴더에 `{output_dir}/{doc_id}/Fnnn.{ext}`
를 함께 기록하므로, 변환 산출물을 그대로 ingest 하면 그림이 자동으로 따라온다.

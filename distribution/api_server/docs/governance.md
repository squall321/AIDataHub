# Governance

Mobile eXperience AI Data Hub 의 거버넌스 정책 — 누가 무엇을 보고, 변경하고, 삭제할 수 있는가.
관련 마이그레이션: **0008 (Agent 31)**.

## 핵심 원칙

1. **모든 변경은 추적된다.** `audit_log` 테이블은 INSERT/UPDATE/DELETE/RESTORE/
   VIEW 이벤트를 보존한다. 비즈니스 로직 실패와는 분리되어 best-effort 로 기록
   되며, 로그 실패가 트랜잭션을 깨뜨리지 않는다.
2. **삭제는 가역적이어야 한다.** 기본 `DELETE` 는 soft delete — `deleted_at` 만
   세팅하고 본문은 보존한다. 물리 삭제는 부트스트랩 키만 가능.
3. **버전은 체인이다.** 동일 데이터의 새 리비전은 `parent_record_id` 로 연결
   된다. UI/에이전트는 `/api/records/{id}/lineage` 로 전체 체인을 조회할 수
   있다.
4. **사용량은 메트릭이다.** `read_count` / `last_accessed_at` 으로 인기 레코드
   를 식별, 미사용 레코드를 retention 검토 대상으로 잡는다.

## 감사 로그 (audit_log)

| 컬럼            | 타입              | 의미 |
|-----------------|-------------------|------|
| `id`            | BIGSERIAL         | 자동 증가 PK |
| `record_id`     | VARCHAR(80)       | 대상 레코드 (글로벌 이벤트는 NULL) |
| `actor`         | VARCHAR(100)      | API 키 이름 / `'system'` / `'cli'` / `'bootstrap'` / `'anonymous'` |
| `action`        | VARCHAR(50)       | INSERT \| UPDATE \| DELETE \| RESTORE \| ACCESS \| VIEW |
| `field_changes` | JSONB             | `{field: [old, new], ...}` (UPDATE 전용) |
| `request_id`    | VARCHAR(64)       | 요청 추적 ID (RequestLoggingMiddleware) |
| `created_at`    | TIMESTAMPTZ       | 발생 시각 |

### 어떤 이벤트가 자동 기록되나

| 트리거                                  | action     | actor 출처            |
|-----------------------------------------|------------|------------------------|
| `POST /api/records`                     | INSERT     | `request.state.principal.name` |
| `POST /api/convert/ingest` → write_record | INSERT/UPDATE | 호출자가 명시 (없으면 `'system'`) |
| CLI `python -m api.ingest ...`          | INSERT/UPDATE | `'cli'` (스크립트 권장) |
| `PATCH /api/records/{id}`               | UPDATE     | principal.name |
| `GET  /api/records/{id}`                | VIEW       | principal.name |
| `DELETE /api/records/{id}` (soft)       | DELETE     | principal.name |
| `DELETE /api/records/{id}?hard=true`    | DELETE     | bootstrap |
| `POST /api/records/{id}/restore`        | RESTORE    | principal.name |

`field_changes` 의 값은 JSON 직렬화 가능한 형태로 정규화된다 (`datetime` →
ISO8601 문자열, `set/tuple` → list, 그 외는 `str()` 캐스트).

## 보존 정책 (Retention)

| 레코드 상태               | 기본 정책                                |
|---------------------------|------------------------------------------|
| 활성 (`deleted_at IS NULL`) | 무기한 보존                            |
| Soft-deleted              | 90 일 후 운영자가 hard delete 검토 가능 |
| 감사 로그                 | 1 년 이상 (정기 archival 권장)          |

> Soft-deleted 레코드는 자동 정리되지 않는다. CLI 또는 운영자 결정으로
> `DELETE /api/records/{id}?hard=true` (bootstrap 필요) 호출이 필요.

## 누가 무엇을 보는가 (Visibility)

| 호출자          | 활성 list/get | soft-deleted | hard delete | bootstrap 작업 |
|-----------------|---------------|--------------|-------------|-----------------|
| anonymous       | O             | X            | X           | X               |
| 발급된 API 키   | O             | O (`?include_deleted=true`) | X | X       |
| bootstrap 키    | O             | O            | O           | O               |

`classification` (Migration 0006) 별 가시성 — 향후 RBAC 통합 시 추가 강화될
예정. 현재는 정책 라벨만 존재.

## 버전 체인 (Version Chain)

`parent_record_id` self-FK 를 따라 부모 ↔ 자식이 연결된다.

```
DOC-HE-CAE-2026-0000000001 (v1.0, original)
    └── DOC-HE-CAE-2026-0000000002 (v2.0, derivation="extracted", parent=000001)
            └── DOC-HE-CAE-2026-0000000003 (v3.0, derivation="translated", parent=000002)
```

### 새 리비전 생성 절차

1. 동일 데이터의 새 버전을 **새 record id 로** 생성한다 (자연키 unique
   constraint 때문에 동일 data_type/division/team/year/seq 재사용 불가).
2. `parent_record_id` 를 직전 리비전 ID 로 설정한다.
3. `derivation` 을 `"extracted"` 등으로 표시 (현 enum 4개 중 적절한 값).
4. 운영자가 부모를 `status = "deprecated"` 로 마킹 (자동화는 다음 단계 — 아래
   참고).

### 자동 deprecate (Deferred)

본 사이클에서는 부모 자동 deprecate 는 CLI 헬퍼로 분리한다. 자동화 시 식별 키:

```
identity = (data_type, division, team, content_hash)
```

`content_hash` 가 다르면 "새 데이터", 같으면 동일 데이터. 동일 identity 로
새 리비전이 생성되면 직전 활성 리비전을 `deprecated` 로 마킹한다.

> **Deferred** : `src/api/scripts/auto_deprecate.py` 같은 CLI 헬퍼로 후속
> 사이클 진행. 라이브러리 사이드 effect 가 큰 변경이라 자동화 전에 실데이터
> 분포 확인이 필요.

### 계보 조회

`GET /api/records/{id}/lineage` — 자기, 조상, 자손을 분리해 반환. 순환을
방지하기 위해 방문 집합을 추적한다. `descendant_count` / `ancestor_count` 로
체인 길이를 빠르게 알 수 있다.

## 사용량 통계 (Usage)

- `read_count` : `GET /api/records/{id}` 또는 `GET /api/data` 응답에 포함된
  레코드마다 +1 (fire-and-forget UPDATE).
- `last_accessed_at` : 동일 트리거.
- `GET /api/analytics/usage?limit=20` — 상위 N 개. soft-deleted 는 제외.

## 운영자 메모

- 감사 로그는 일/주 단위로 archival 된다고 가정한 인덱스 분포 — 무한 누적
  시 `audit_log` 가 가장 큰 테이블이 될 수 있으므로 cron 기반 sweep 권장.
- soft-delete restore 후에는 의도치 않은 다운스트림 인덱스(예: pgvector
  ANN) 갱신을 잊지 말 것 — 별도 backfill job 필요할 수 있음.
- bootstrap 키는 `BOOTSTRAP_API_KEY` 환경변수에 지정. 지정하지 않으면 hard
  delete 가 영구히 막힌다 (의도된 안전장치).

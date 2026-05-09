# API Reference (Compact)

> 56 endpoint × 13 카테고리 한 페이지 요약. 자세한 schema 는 <http://110.15.177.125:8000/docs> (Swagger UI).

`BASE = http://110.15.177.125:8000`

---

## Discover (입구) — `discover` 카테고리

| Method | Path | 용도 |
|---|---|---|
| GET | `/api/discover` | 카탈로그 — total_records, by_data_type, by_classification, agents, top_tags |
| GET | `/api/schema` | 머신 리더블 JSON Schema (draft 2020-12) |
| GET | `/api/hints` | 자연어 힌트 모음 — 어떻게 query 를 짜야 하는지 |
| GET | `/api/docs/agent-guide` | 에이전트용 markdown (`?size=tiny\|small\|medium\|large`) |
| GET | `/api/docs/llm.txt` | llm.txt 표준 출력 |
| POST | `/api/ask` | 자연어 → interpreted_query + results (LLM-friendly) |

---

## Search — `search` 카테고리

| Method | Path | 핵심 파라미터 |
|---|---|---|
| GET | `/api/search` | `mode={semantic\|fts\|tag}`, `q`, `tags[]`, `limit`, `offset` |
| GET | `/api/search/faceted` | `q`, `mode`, `data_type`, `tags`, `agent`, `domain`, `classification`, `status`, `year_from/to`, `min_quality` — 응답에 facets 포함 |
| GET | `/api/search/by-tags` | `tags=a,b`, `match=any\|all` |

`mode=semantic` 권장 임계값: 0.85+ (hash) / 0.92+ (e5_small).

---

## Records (CRUD) — `records` 카테고리

| Method | Path | 용도 |
|---|---|---|
| GET | `/api/records` | 카탈로그. `data_type`, `tag` (반복), `agent` (반복), `limit`, `offset` |
| GET | `/api/records/{id}` | 단일 record 본문 + 첨부 |
| POST | `/api/records` | RecordIn 형태 직접 생성 (변환기 거치지 않은 입력) |
| PATCH | `/api/records/{id}` | 메타 필드 패치 |
| DELETE | `/api/records/{id}` | soft-delete |
| POST | `/api/records/{id}/restore` | soft-delete 복원 |
| GET | `/api/records/{id}/lineage` | parent/child 관계 |
| GET | `/api/records/{id}/diff` | 버전간 diff (해당하는 경우) |
| POST | `/api/records/bulk` | 다수 record 일괄 조회 (id 배열) |

---

## Data (Excel-only) — `data` 카테고리

| Method | Path | 용도 |
|---|---|---|
| GET | `/api/data` | DATA-* record 목록 (Excel 출신만) |
| GET | `/api/data/{record_id}/rows` | 표의 행 — 페이지네이션 |
| GET | `/api/data/{record_id}/columns` | 표의 컬럼 메타 (단위/설명) |
| GET | `/api/data/{record_id}/aggregate` | 집계 (`?func=avg&col=stress` 등) |

---

## Groups (의미 그룹) — `groups` 카테고리

| Method | Path | 용도 |
|---|---|---|
| POST | `/api/groups/auto` | body=`{q, n_groups, top_k}` — KMeans-style 자동 클러스터 |
| GET | `/api/records/{id}/cluster` | 단일 record 의 시맨틱 이웃 |

---

## Taxonomy (어휘 발견) — `taxonomy` 카테고리

| Method | Path | 용도 |
|---|---|---|
| GET | `/api/taxonomy/tags` | tag 목록 + usage_count |
| GET | `/api/taxonomy/data-types` | data_type 분포 |
| GET | `/api/taxonomy/agents` | agent_type 등록 목록 |
| GET | `/api/taxonomy/classifications` | classification 분포 |
| GET | `/api/taxonomy/statuses` | status 분포 |
| GET | `/api/taxonomy/domains` | domain 분포 |

---

## Agents (등록) — `agents` 카테고리

| Method | Path | 용도 |
|---|---|---|
| GET | `/api/agents` | 등록된 agent_type 목록 |
| POST | `/api/agents` | 신규 agent_type 등록 |
| GET | `/api/agents/{agent_type}` | 상세 |
| PATCH | `/api/agents/{agent_type}` | 메타 패치 |
| DELETE | `/api/agents/{agent_type}` | 삭제 |
| GET | `/api/agents/{agent_type}/records` | 이 agent 가 소비하는 record 목록 |

---

## Analytics — `analytics` 카테고리

| Method | Path | 용도 |
|---|---|---|
| GET | `/api/analytics/distribution` | data_type / domain / status / classification 분포 |
| GET | `/api/analytics/common-tags?agent=...` | 특정 agent 가 자주 쓰는 tag |
| GET | `/api/analytics/cross-agent` | agent 간 record 공유 매트릭스 |
| GET | `/api/analytics/timeline` | 시간 축 ingest 수 |
| GET | `/api/analytics/usage` | 검색 hit 통계 |

---

## Convert (ingest) — `convert` 카테고리

| Method | Path | 용도 |
|---|---|---|
| POST | `/api/convert` | 파일 업로드 → JSON 반환 (DB 적재 안 함) |
| POST | `/api/convert/ingest` | 파일 업로드 → 변환 → DB 적재 → 요약 반환 |

multipart/form-data, `file` 필드. `?ocr=true` (PDF), `?detect_multi_tables=true` (Excel) 옵션.

---

## Jobs (백그라운드) — `jobs` 카테고리

| Method | Path | 용도 |
|---|---|---|
| POST | `/api/jobs` | 잡 등록 (e.g. embed-backfill) |
| GET | `/api/jobs` | 잡 목록 |
| GET | `/api/jobs/{id}` | 단일 잡 상태 |

---

## Meta — `meta` 카테고리

| Method | Path | 용도 |
|---|---|---|
| GET | `/api/meta` | 공용 메타 스키마 (필드 목록 + enum) |

---

## Auth — `auth` 카테고리

| Method | Path | 용도 |
|---|---|---|
| POST | `/api/auth/keys` | API key 발급 (BOOTSTRAP_API_KEY 필요) |
| GET | `/api/auth/keys` | 발급된 키 메타 (값은 안 보임) |
| DELETE | `/api/auth/keys/{id}` | 키 폐기 |
| POST | `/api/auth/keys/verify` | 키 검증 |

---

## System — `system` 카테고리

| Method | Path | 용도 |
|---|---|---|
| GET | `/api/system/health` | 헬스체크 — 시작 직후 1회 호출 권장 |
| GET | `/health` | 단순 health |
| GET | `/` | 서비스명/버전 |
| GET | `/dashboard/` | HTML 대시보드 |

---

## 정적 마운트

| Path | 내용 |
|---|---|
| `/figures/{doc_id}/F{nnn}.{ext}` | 이미지 바이너리 |
| `/attachments/{doc_id}/A{nnn}.{ext}` | 첨부 바이너리 (OLE/audio/video 등) |

---

## 공통 응답 포맷

성공: 각 endpoint 의 schema 그대로 반환.

오류: 통일된 envelope:

```jsonc
{
  "error": {
    "code": "VALIDATION_ERROR | INTERNAL_ERROR | NOT_FOUND | UNAUTHORIZED | ...",
    "message": "...",
    "details": { ... },
    "request_id": "0596b529a8994dfbbfe48d06999b1337"
  }
}
```

`request_id` 를 운영자에게 전달하면 트러블슈팅 가능.

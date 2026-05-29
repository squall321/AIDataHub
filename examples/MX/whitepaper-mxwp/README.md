# MX White Paper × MXWhitePaper 양방향 연동 예제

AX Hub (Mobile eXperience AI Data Hub) ↔ MXWhitePaper (사내 문서 위키 + 풀스택)

- AX Hub: 사업부 전체 데이터(설계/CAE/품질/측정/VOC/문서) 통합 카탈로그
- MXWhitePaper: DocumentJSON v1.0 기반 사내 white paper / 타당성 / 기술 분석 위키
  (FastAPI + Vite + PostgreSQL + MinIO + Meilisearch, apptainer 운영)

이 디렉터리는 **AX Hub 측 설정 패키지** 입니다. MXWhitePaper 측 통합 코드는
`/home/koopark/claude/MXWhitePaper/integrations/aidatahub/` 에 있습니다.

---

## 양방향 모델

```
   ┌──────────────────────┐                 ┌──────────────────────┐
   │  MXWhitePaper        │   1) push       │     AX Hub           │
   │  FastAPI + Vite      │  ──────────────▶│  FastAPI / MCP       │
   │  DocumentJSON v1.0   │                 │  records + agents    │
   │  PG + MinIO + Meili  │   2) pull cron  │  external_id_map     │
   └──────────────────────┘ ◀──────────────  └──────────────────────┘
                              (30 min)
```

- **초기 backfill (1 회)**: MXWhitePaper 가 push
  - 명령: `python aidatahub_sync.py --mode=push-all` (MXWP 측)
  - 모든 `status=published` DocumentJSON 을 AX Hub 로 변환·업로드
  - AX Hub: `POST /api/records/import?auto_seq=true&external_source=mxwp` 사용
- **정기 update (운영)**: AX Hub 가 pull
  - AX Hub `sync_sources` 에 MXWhitePaper 를 한 번 등록 (`setup.sh` 가 수행)
  - AX Hub cron 이 30 분마다 `POST /api/sync/sources/{id}/run` 호출
  - MXWP 가 노출하는 list endpoint 를 페이지네이션 호출 → 변환 → UPSERT
- **매핑 키**: `external_id_map(source='mxwp', external_id=<MXWP document_id>) → record_id`
  - 같은 `document_id` 가 양방향에서 와도 record 한 건만 존재 (UPSERT)

---

## 1 회 셋업

```bash
cd /home/koopark/claude/AIDataHub/examples/MX/whitepaper-mxwp

./setup.sh \
  http://aidatahub:8001 \
  $AIDH_ADMIN_KEY \
  http://mxwp-api:8000 \
  $MXWP_INTERNAL_KEY
```

`setup.sh` 가 자동 수행:
1. `MX/WP` org_group 등록 (409 무시)
2. `whitepaper`, `feasibility_study` doc_type 등록 (alembic 0026 에 이미 있음 → 409 무시)
3. `mx-whitepaper-analyst` agent 등록
4. MXWhitePaper 를 `sync_sources` 로 등록 (`mapping_rules` 포함)
5. dry-run verify (`POST /api/sync/sources/{id}/verify`) — 1 페이지 매핑 점검

---

## 데이터 모델 매핑

MXWhitePaper 의 DocumentJSON v1.0 (`schemas/document.py`) →
AX Hub `records` (POST `/api/records/import`) 매핑.

| MXWhitePaper DocumentJSON       | AX Hub `records`                       |
|--------------------------------:|:---------------------------------------|
| `id` (ULID)                     | `_external_id` (= MXWP `document_id`)  |
| `title`                         | `title`                                |
| `summary` (≤ 500자)             | `summary`                              |
| `sections[]` (계층 + blocks)    | `content.sections[]` (평탄, level)     |
| 본문 평탄화 (paragraph/heading/list/table/code/math/quote/callout) | `content_text` |
| `metadata.tags[]`               | `tags`                                 |
| `metadata.owners[]`             | `tags` (`owner:` prefix 부여)          |
| `status` (`draft`/`published`)  | `tags` (예: `status:published`)        |
| `metadata.confidentiality`      | `classification` (`public`/`internal`/`restricted`) |
| `metadata.team`, `metadata.group` | (기본 `MX` / `WP` override 가능)      |
| `created_at`                    | `valid_from`, `year`                   |
| `updated_at`                    | `tags` (`updated:YYYY-MM`)             |
| `ImageBlock.imageId` (MinIO ULID) | `attachments[]` URL 참조 (아래 정책)  |

고정값:
- `data_type=DOC`, `doc_type=whitepaper` (또는 `feasibility_study` — `metadata.tags` 의 `feasibility` 존재 시)
- `team=MX`, `group=WP`
- `agents=["mx-whitepaper-analyst"]`, `language=ko`

상세 매핑 규칙은 `sync_source.example.json` 의 `mapping_rules` 참고.

### 이미지 (MinIO) → AX Hub attachment 매핑 정책

MXWP 의 ImageBlock 은 `imageId` (ULID) 가 MinIO 객체를 가리킨다.
두 가지 옵션 — **URL 참조 (권장)** 가 기본값:

| 모드 | 동작 | 비고 |
|---|---|---|
| `attachment_mode=url_ref` (기본) | `attachments[].url = {mxwp_base_url}/api/v1/files/{imageId}` 로 참조만 저장 | 저장공간 절감, MXWP 가 단일 진실, dead-link 위험은 MXWP soft-delete 후 발생 |
| `attachment_mode=download_upload` | MXWP MinIO 에서 이미지 다운로드 → AX Hub `POST /api/attachments` 로 업로드 | 자체완결, MXWP downtime 영향 무, 그러나 중복 저장 |

운영 권장: `url_ref` (MXWP 가 위키 단일 진실).
폐쇄망 분리시: `download_upload`.

---

## 운영 안내

### AX Hub 측 cron 등록 예시

```cron
# /etc/cron.d/aidh-mxwp
*/30 * * * *  aidh  curl -s -X POST \
  -H "X-API-Key: $AIDH_ADMIN_KEY" \
  http://aidatahub:8001/api/sync/sources/MXWP_ID/run \
  >> /var/log/aidh/mxwp-sync.log 2>&1
```

`MXWP_ID` 는 `setup.sh` 출력에 표시됩니다.

### Webhook (옵션 — MXWP publish 즉시 push)

MXWP 의 `replace_document` (PUT `/api/v1/documents/{slug}`) 가
`fire_webhook()` 으로 외부 URL 호출 가능 ([[documents.md#webhooks]]).
AX Hub `POST /api/records/import` 를 webhook target 으로 등록하면
30 분 대기 없이 publish 즉시 동기화 가능.

### 검증 쿼리

```bash
# MXWP 출처 record 개수
curl -H "X-API-Key: $AIDH_KEY" \
  "http://aidatahub:8001/api/records?team=MX&group=WP&limit=1" | jq .total

# 특정 document_id 가 들어왔는지
curl -H "X-API-Key: $AIDH_KEY" \
  "http://aidatahub:8001/api/sync/external/mxwp/01JABCDEF..."
```

---

## 트러블슈팅

- `409 already exists` (setup.sh 중) → 정상. 재실행 안전.
- `verify` 실패 → MXWP `base_url` / `api_key` 확인.
- record 수가 0 → MXWP 측 published 문서 존재 여부 확인 (`status=published` 필터).
- 양방향 충돌 — MXWP push 직후 AX Hub pull 이 돌아도 UPSERT 이므로 한 건만 유지.
- DocumentJSON 의 `confidentiality=restricted` 는 AX Hub `classification=restricted` 로 매핑
  → AX Hub 에서도 권한 검사가 그대로 흐름.

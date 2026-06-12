# MX VOC × SignalForge 양방향 연동 예제

> **새 외부 시스템 통합 표준 절차는 [docs/INTEGRATION_GUIDE.md](../../../docs/INTEGRATION_GUIDE.md) 참조.**
> 이 폴더는 SignalForge 한정 검증된 예시 (mapping_rules + setup.sh).

AX Hub (Mobile eXperience AI Data Hub) ↔ SignalForge (사내 VOC Intelligence Platform)
- AX Hub: 사업부 전체 데이터(설계/CAE/품질/측정/VOC) 통합 카탈로그
- SignalForge: 외부 채널 VOC(리뷰/SNS/콜센터/클레임) 수집·분류·감성분석 전문 시스템

이 디렉터리는 **AX Hub 측 설정 패키지**입니다. SignalForge 측 통합 코드는
`/home/koopark/claude/SignalForge/integrations/aidatahub/` 에 있습니다.

---

## 양방향 모델

```
   ┌──────────────────────┐                 ┌──────────────────────┐
   │  SignalForge         │   1) push-all   │     AX Hub           │
   │  (Postgres 5434,     │  ──────────────▶│  (Postgres 5432,     │
   │   Celery worker/beat)│                 │   FastAPI/MCP)       │
   │                      │   2) pull cron  │                      │
   │  VOC 원본 + 분류     │ ◀──────────────  │  external_id_map     │
   └──────────────────────┘   (30min)       └──────────────────────┘
```

- **초기 backfill (1회)**: SignalForge 가 push
  - 명령: `python aidatahub_sync.py --mode=push-all` (SignalForge 측)
  - 수천~수만 건의 과거 VOC 를 한 번에 AX Hub 로 밀어 올림
  - AX Hub: `POST /api/records/import?auto_seq=true&external_source=signalforge` 사용
- **정기 update (운영)**: AX Hub 가 pull
  - AX Hub `sync_sources` 에 SignalForge 를 한 번 등록 (`setup.sh` 가 수행)
  - AX Hub 측 cron 이 30분마다 `POST /api/sync/sources/{id}/run` 호출
  - SignalForge `/api/v1/products/{code}/voc` 를 페이지네이션 호출 → 변환 → UPSERT
- **매핑 안전성**: `external_id_map(external_source, external_id) → record_id` 가 매핑 키
  - 같은 `voc_id` 가 양방향에서 와도 record 는 한 건만 존재 (UPSERT)
  - `voc_id` 가 새로 들어오면 신규 생성, 기존이면 갱신

---

## 1회 셋업

```bash
cd /home/koopark/claude/AIDataHub/examples/MX/voc-signalforge

./setup.sh \
  http://aidatahub:8001 \
  $AIDH_ADMIN_KEY \
  http://signalforge-backend:8000 \
  $SF_INTERNAL_KEY
```

setup.sh 가 자동으로 수행:
1. `MX` team 존재 확인 (없으면 등록)
2. `MX/VOC` org_group 등록 (409 무시)
3. `voc_report`, `voc_metrics` doc_type 등록 (409 무시 — alembic 0026 에 이미 있음)
4. `market-voc-analyst` agent 등록
5. SignalForge 를 `sync_sources` 로 등록 (`mapping_rules` 포함)
6. dry-run verify (`POST /api/sync/sources/{id}/verify`) 로 1페이지 가져와 매핑 검증

---

## 데이터 모델 매핑

| SignalForge `voc_records` | AX Hub `records`             |
|--------------------------:|:-----------------------------|
| `id`                      | `external_id` (= `voc_id`)   |
| `content_original`        | `content` (본문)             |
| 첫 80자                   | `title`                      |
| `product.code`            | `subject_keywords[0]`        |
| `product.name_en/ko`      | `subject_keywords[1]`        |
| `categories[]`            | `tags` (+ `voc:` prefix)     |
| `country_code`            | `tags` (e.g. `country:KR`)   |
| `platform.code`           | `tags` (e.g. `channel:youtube`) |
| `sentiment_label`         | `tags` (e.g. `sentiment:negative`) |
| `published_at`            | `valid_from`, `year`         |

고정값:
- `data_type=DOC`, `doc_type=voc_report`, `team=MX`, `group=VOC`
- `agents=["market-voc-analyst"]`, `language=ko`, `classification=internal`

상세 매핑 규칙은 `sync_source.example.json` 의 `mapping_rules` 참고.

---

## 운영 안내

### 자동 동기화 — 외부 cron 불필요 (v0.14+)

AX Hub 의 **인앱 스케줄러**가 `sync_sources.schedule_cron` (기본 `*/30 * * * *`)
을 자동 실행합니다. 별도 cron 등록이 필요 없으며, 서버만 떠 있으면
30분마다 pull 됩니다. 비활성화: `AIDH_SCHEDULER=off`.

수동 즉시 실행이 필요할 때만:

```bash
curl -s -X POST -H "X-API-Key: $AIDH_ADMIN_KEY" \
  http://aidatahub:8001/api/sync/sources/SIGNALFORGE_ID/run
```

`SIGNALFORGE_ID` 는 `setup.sh` 출력에 표시됩니다.

### 검증 쿼리

```bash
# SignalForge 출처 record 개수
curl -H "X-API-Key: $AIDH_KEY" \
  "http://aidatahub:8001/api/records?team=MX&group=VOC&limit=1" | jq .total

# 특정 voc_id 가 들어왔는지
curl -H "X-API-Key: $AIDH_KEY" \
  "http://aidatahub:8001/api/sync/external/signalforge/12345"
```

### 샘플

`records/sample.json` — SignalForge → AX Hub 변환 결과 1건 (실제 push 페이로드 형식).

---

## 트러블슈팅

- `409 already exists` (setup.sh 중) → 정상. 재실행 안전.
- `verify` 실패 → SignalForge `base_url` / `api_key` 확인.
- 동기화 후 record 수가 0 → SignalForge 측 `voc_records` 에 `processed_at IS NOT NULL` 행이 있는지 확인 (분류 완료된 것만 push 권장).
- 양방향 충돌 — SignalForge 가 같은 `voc_id` 를 push 한 직후 AX Hub pull 이 돌아도, UPSERT 이므로 record 한 건만 유지.

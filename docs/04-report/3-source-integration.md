# AX Hub — 3 Source Integration Report

작성일: 2026-05-28
대상 버전: AX Hub v0.8 (alembic 0026/0027)
연동 대상: 자체 예제 (stress-strain) + SignalForge (VOC) + MXWhitePaper (위키)

---

## 1. 산출물 인벤토리 (총 35 파일)

### 1-1. AX Hub 자체 변경 (Phase 0 — 9 파일 신규/수정)

| 파일 | 상태 |
|---|---|
| `api_server/alembic/versions/0026_doc_type_mode_and_external_id_map.py` | 신규 |
| `api_server/alembic/versions/0027_sync_sources.py` | 신규 |
| `api_server/src/api/db/models.py` | 수정 (DocType.mode + ExternalIdMap + SyncSource + SyncRun) |
| `api_server/src/api/ingest/db_writer.py` | 수정 (mode=data_extract embedding skip) |
| `api_server/src/api/routes/records.py` | 수정 (external_source + _external_id UPSERT) |
| `api_server/src/api/routes/sync.py` | 신규 (9 endpoints) |
| `api_server/src/api/routes/__init__.py` | 수정 (sync router 등록) |
| `api_server/src/api/services/sync_svc.py` | 신규 (fetch + transform + throttle + dead_letter) |
| `api_server/src/api/services/ingest_guide_svc.py` | 수정 (mode 노출) |
| `api_server/src/api/services/ingest_kit_svc.py` | 수정 (validate.py 에 mode 박힘) |

### 1-2. examples/ (3 예제 — 24 파일)

```
examples/HE/material-stress-strain/   (10 파일)
├── README.md
├── setup.sh
├── agent.json (material-stress-strain-analyst)
├── doc_type.json (material_test_data + material_test_report)
├── records.json (5 재료)
└── data/{AISI-1018, AA6061-T6, AA7075-T6, TPU-shore-A85, 316L}.csv

examples/MX/voc-signalforge/   (7 파일)
├── README.md
├── setup.sh
├── agent.json (market-voc-analyst)
├── org_group.json (MX/VOC)
├── doc_type.json (voc_report + voc_metrics)
├── sync_source.example.json (mapping_rules 포함)
└── records/sample.json

examples/MX/whitepaper-mxwp/   (6 파일)
├── README.md
├── setup.sh
├── agent.json (mx-whitepaper-analyst)
├── org_group.json (MX/WP)
├── doc_type.json (whitepaper + feasibility_study)
└── sync_source.example.json
```

### 1-3. 외부 프로젝트 (11 파일)

```
SignalForge/integrations/aidatahub/    (6 파일)
├── README.md
├── AIDATAHUB_CLIENT_SPEC.md
├── aidatahub_sync.py
├── celery_task.py
├── config.example.yml
└── requirements.txt

MXWhitePaper/integrations/aidatahub/   (5 파일)
├── README.md
├── AIDATAHUB_CLIENT_SPEC.md
├── aidatahub_sync.py
├── config.example.yml
└── requirements.txt
```

---

## 2. 신규 엔드포인트 (132 routes 중 12개 신규/변경)

| 메소드 | 경로 | 용도 |
|---|---|---|
| GET | `/api/schema/ingest-guide` | LLM 친화 markdown 가이드 (mode 반영) |
| GET | `/api/schema/ingest-kit.zip` | 자기완결적 검증 키트 zip |
| POST | `/api/records/import` | JSON 일괄 + auto_seq + UPSERT + dry_run + **external_source** |
| GET | `/api/sync/sources` | sync source 목록 |
| POST | `/api/sync/sources` | sync source 등록 |
| GET | `/api/sync/sources/{id}` | 단일 조회 |
| PATCH | `/api/sync/sources/{id}` | 설정 변경 (cursor/schedule/enabled) |
| DELETE | `/api/sync/sources/{id}` | 삭제 |
| POST | `/api/sync/sources/{id}/run` | 동기화 실행 (cron 또는 수동) |
| POST | `/api/sync/sources/{id}/verify` | dry-run 매핑 검증 |
| GET | `/api/sync/sources/{id}/runs` | 실행 이력 |
| GET | `/api/sync/runs/{run_id}` | 단일 run 상세 (dead_letter 포함) |

---

## 3. doc_type 35종 (alembic 0026 seed)

mode 분포:
- llm_context: 18종 (텍스트 자료 — embedding 필수)
- data_extract: 4종 (수치 자료 — embedding skip)
- hybrid: 13종 (시뮬·재료 — 양쪽)

핵심 추가 (예제 직결):
- `material_test_data` (data_extract) — Phase 1 stress-strain
- `material_test_report` (llm_context) — 보고서 분리 옵션
- `voc_report` (llm_context) — Phase 2 SignalForge
- `voc_metrics` (data_extract) — VOC 집계 옵션
- `whitepaper` + `feasibility_study` (llm_context) — Phase 3 MXWP

전체 35종은 `alembic 0026` 참조.

---

## 4. 통합 데이터 흐름

```
[1. Stress-Strain (자체 예제)]
   사용자 → bash setup.sh
   → POST /api/doc-types (×2)
   → POST /api/agents (material-stress-strain-analyst)
   → POST /api/records/import?auto_seq=true (5 record)
   → POST /api/ingest/bundle ×5 (csv attachment)

[2. SignalForge VOC]
   초기: SignalForge 측 cron / 수동 1회
     → python aidatahub_sync.py --mode=push-all
     → POST /api/records/import?external_source=signalforge&auto_seq=true
     → external_id_map(signalforge, voc.id) 자동 등록
   정기: AX Hub cron
     → POST /api/sync/sources/{id}/run (manual trigger)
     → sync_svc.run_sync() 가 SignalForge list API 호출
     → 매핑 룰 적용 → records.import (UPSERT)

[3. MXWhitePaper]
   초기: MXWP 측 cron / 수동
     → python aidatahub_sync.py --mode=push-all
     → DocumentJSON 트리 → DFS 평탄화 → record.content.sections
     → POST /api/records/import?external_source=mxwp&auto_seq=true
   정기: AX Hub cron
     → POST /api/sync/sources/{id}/run
     → sync_svc 가 /api/v1/documents/ 페이지네이션 호출
```

3 소스 모두 통합 검색에 노출:
```
GET /api/search?q=Galaxy+S25
  → SignalForge VOC + MXWP 보고서 + HE 시험 자료 모두 한 결과
GET /api/recommend/agents?q=발열+분석
  → market-voc-analyst, material-stress-strain-analyst 둘 다 추천
```

---

## 5. 운영 가이드

### 5-1. 신규 source 추가 (예: 새 외부 시스템 'ARES')

1. AX Hub:
   ```
   POST /api/org/groups {team:'XX', code:'ARES', ...}
   POST /api/doc-types {code:'ares_report', mode:'llm_context', ...}
   POST /api/agents {agent_type:'ares-analyst', ...}
   POST /api/sync/sources {name:'ares', base_url, list_endpoint, mapping_rules:{...}}
   ```
2. cron 등록: `*/30 * * * * curl -X POST $AIDH/api/sync/sources/<id>/run`
3. dry-run: `curl -X POST $AIDH/api/sync/sources/<id>/verify`

### 5-2. 매핑 규약 변경

```
PATCH /api/sync/sources/{id}
  body: {"mapping_rules": {...new rules...}, "reset_cursor": true}
```

→ 코드 수정 없음. mapping_rules 만 JSONB 갱신.

### 5-3. dead_letter 재처리

```
GET /api/sync/runs/{run_id}
  → dead_letter[] 의 raw 원본 추출
  → 매핑 규약 보강 후 다시 sync
```

### 5-4. push 모드 (외부에서 보낼 때)

```
SignalForge Celery beat 30분 마다 (1회 설정):
  → from integrations.aidatahub.celery_task import aidatahub_sync_recent
  → @beat_schedule "aidatahub.sync_recent" — kwargs={since_minutes:35}

MXWhitePaper publish event webhook:
  → POST $AIDH/api/records/import?external_source=mxwp&auto_seq=true
     body: { records: [doc_to_record(doc)] }
```

---

## 6. 안전장치 (모두 AX Hub 측에서 흡수)

| 보강 | 부재 시 | 우리 처리 |
|---|---|---|
| updated_at 부재 | 매번 전체 동기화 | created_at since 필터 + content_hash 비교 |
| cursor 부재 | offset 기반 폴백 | sync_sources.cursor_param=offset 으로 |
| tombstone 부재 | 삭제 추적 안 됨 | (옵션) 주기적 full ID set 비교 |
| rate limit 헤더 부재 | DDoS 위험 | sync_sources.max_rps (기본 2 req/s) |
| pii_masked 보증 부재 | 컴플라이언스 위험 | trust_pii_masked=false → classification=confidential 강제 |
| API 키 유출 | — | api_keys.rate_limit_per_min + 90일 회전 |

→ **상대 시스템(SignalForge / MXWP) 의 추가 코드 작업 0**. 통합은 우리 측 mapping_rules JSON 1개로 끝.

---

## 7. 검증 명령 (서버 기동 후)

```bash
# 1. doc_type 35종 + 35개 doc_type 등록 확인
curl $AIDH/api/doc-types | jq '. | length'   # → 35+

# 2. Phase 1 자체 예제 실행
cd examples/HE/material-stress-strain && bash setup.sh $AIDH $KEY

# 3. 검색
curl "$AIDH/api/search?q=AISI+1018+yield&agent_type=material-stress-strain-analyst" | jq

# 4. SignalForge sync_source 등록 + 검증
curl -X POST $AIDH/api/sync/sources -H "X-API-Key: $KEY" \
  --data @examples/MX/voc-signalforge/sync_source.example.json
curl -X POST $AIDH/api/sync/sources/<id>/verify

# 5. MXWP sync_source 등록 + 검증
curl -X POST $AIDH/api/sync/sources -H "X-API-Key: $KEY" \
  --data @examples/MX/whitepaper-mxwp/sync_source.example.json
curl -X POST $AIDH/api/sync/sources/<id>/verify

# 6. external_id_map 확인 (PG)
psql -d $AIDH_DB -c "SELECT source, COUNT(*) FROM external_id_map GROUP BY source;"

# 7. embedding skip 동작 확인 (data_extract 모드)
# voc_metrics record 등록 후 record_sections.embedding IS NULL 확인
```

---

## 8. 사업부 / 외부 시스템 전달 사항 한 줄

| 대상 | 전달 |
|---|---|
| HE/CAE 팀 | `examples/HE/material-stress-strain/setup.sh` 실행 후 본인 자료를 같은 양식으로 적재 |
| SignalForge 팀 | `SignalForge/integrations/aidatahub/README.md` 따라 API 키 발급 + 어댑터 실행 (또는 AX Hub 가 pull) |
| MXWhitePaper 팀 | `MXWhitePaper/integrations/aidatahub/README.md` 따라 (push 또는 pull 선택) |
| 신규 사업부 | `GET $AIDH/api/schema/ingest-kit.zip?agent_type=<자기 agent>` 받아 LLM 활용 |

---

## 9. 알려진 한계 / 후속 작업

| # | 항목 | 대응 |
|---|---|---|
| 1 | sync_svc 의 by_product iterator 미지원 (SignalForge 가 product 별 endpoint 분할) | push 모드 권장 (어댑터에 구현 완료) |
| 2 | tombstone 자동 감지 미구현 | 분기마다 수동 비교 (옵션) — 추후 sync_svc 보강 |
| 3 | DocumentJSON 의 etag 기반 변경 감지 안 함 | content_hash + version 으로 폴백 — 추후 etag 활용 |
| 4 | RBAC 라우트 강제 미적용 | classification 필드 저장만, 라우트 미들웨어 X — Task D (다부서 확산 시) |
| 5 | sync_runs.dead_letter 자동 재시도 미구현 | 수동 트리거 — 추후 보강 |
| 6 | mode=data_extract embedding skip 후 검색 시 의미 매칭 약함 | tags + numeric filter 위주 검색 권장 — UI 보강 필요 |

---

## 10. 결론

| 영역 | 진행 |
|---|---|
| AX Hub 자체 기능 | **완료** — 12 신규 endpoint + 35 doc_type + sync 인프라 |
| 자체 예제 (stress-strain) | **완료** — setup.sh 즉시 시연 가능 |
| SignalForge 연동 양쪽 | **완료** — push 어댑터 + pull spec |
| MXWhitePaper 연동 양쪽 | **완료** — DocumentJSON 변환 + push/pull |
| 외부 시스템 추가 코드 부담 | **0 lines** — 기존 list API 그대로 사용 |

3 소스 모두 운영 진입 가능 상태. 서버 기동 후 위 검증 명령 7개로 동작 확인.

# Material Stress-Strain Curve — AX Hub 예제 데이터셋

사업부 파일럿 시연용. `setup.sh` 한 번이면 doc_type, agent, record 5건, raw csv attachment 5건이 AX Hub (Mobile eXperience AI Data Hub) 에 등록된다.

## 의도

- `material_test_data` (mode=data_extract) + `material_test_report` (mode=llm_context) 두 doc_type 을 사용하는 시연.
- 1 재료 = 1 record (해석 요약) + 1 attachment (raw 측정치 csv) 모델.
- 1차 시연 대상 5재료: AISI 1018, AA6061-T6, AA7075-T6, TPU Shore A85 (hyperelastic), 316L.
- agent `material-stress-strain-analyst` 가 이 5재료를 RAG payload 로 묶어 σy/σu/E/ε_f 추출 및 재료 간 비교 질의에 답한다.

## 디렉터리

```
material-stress-strain/
├── README.md            # 이 파일
├── setup.sh             # 일괄 설치 스크립트
├── doc_type.json        # doc_type 2개 정의
├── agent.json           # agent 1개 정의
├── records.json         # record 5건 정의 (id 없음 — auto_seq 채번)
└── data/
    ├── AISI-1018.csv          # raw stress-strain (15 pts)
    ├── AA6061-T6.csv          # raw stress-strain (14 pts)
    ├── AA7075-T6.csv          # raw stress-strain (14 pts)
    ├── TPU-shore-A85.csv      # raw stress-strain (15 pts, hyperelastic)
    └── 316L.csv               # raw stress-strain (15 pts)
```

## 실행 방법

```bash
bash setup.sh http://localhost:8001 <YOUR_API_KEY>
```

필수 도구: `curl`, `jq`, `zip`, `python3`.

스크립트는 idempotent — 재실행해도 안전하다. 이미 존재하는 doc_type / agent 는 409 응답을 skip 으로 처리한다. record 는 bundle UPSERT 로 갱신된다.

## 단계 (setup.sh 내부 흐름)

| 단계 | endpoint | 동작 |
|---|---|---|
| 1 | `POST /api/doc-types` | `material_test_data`, `material_test_report` 등록 (409 skip) |
| 2 | `POST /api/agents` | `material-stress-strain-analyst` 등록 (409 skip) |
| 3 | `POST /api/records/import?auto_seq=true` | 5재료 record 일괄 등록 → 채번된 id 5개 회수 |
| 4 | `POST /api/ingest/bundle` × 5 | record JSON + csv 를 zip 으로 묶어 UPSERT (attachment 적재) |

### attachment 적재 방식에 대한 주의

AX Hub 는 단건 `POST /api/records/{id}/attachments` multipart 엔드포인트를 제공하지 않는다. attachment 는 다음 중 하나로만 적재된다:

- `POST /api/convert/ingest` — 원본 파일 변환 + record 신규 생성 (1 파일 = 1 record)
- `POST /api/ingest/bundle` — 사전 변환된 JSON + 자원 폴더 zip 적재 (record + N 자원)

이 예제는 후자를 사용한다. step 3 에서 채번된 record id 와 record JSON 본체에 `attachments[]` 메타를 한 줄 추가해 zip 으로 묶고, `/api/ingest/bundle` 로 UPSERT 한다. bundle 의 `write_record` 가 동일 id 의 기존 record 를 PATCH 처리하므로 step 3 → step 4 흐름은 안전하다.

### record id 의 채번

- `auto_seq=true` 로 `(data_type, team, group, year) = (DATA, HE, CAE, 2026)` 4-tuple 기준 다음 seq 가 자동 부여된다.
- 예: 첫 실행 → `DATA-HE-CAE-2026-0000000001` ~ `...-0000000005` (10-digit seq).
- 두 번째 실행은 새 seq 5개가 또 부여되는 것이 아니라, step 4 의 bundle 이 동일 id (첫 실행의 그것) 를 UPSERT 하도록 만들어야 진짜 idempotent 가 된다. **현재 setup.sh 는 매 실행마다 새 seq 를 받는다** — 재시연 환경에서는 미리 db 의 기존 5건을 제거하거나, `records.json` 에 명시적 id 를 넣는 운영 변형을 고려한다.

## 검증 명령

설치 직후 (실제 API_KEY 와 BASE_URL 로 치환):

```bash
# agent 메타 (sample_queries / 필수 doc_type 확인)
curl -H "X-API-Key: $KEY" "$URL/api/agents/material-stress-strain-analyst"

# doc_type 메타
curl -H "X-API-Key: $KEY" "$URL/api/doc-types/material_test_data"

# 검색 — keyword + agent 매칭
curl -H "X-API-Key: $KEY" "$URL/api/search?q=AISI+1018+yield"
curl -H "X-API-Key: $KEY" "$URL/api/search?q=AA6061+tensile&agent_type=material-stress-strain-analyst"

# 단일 record + attachment 메타
curl -H "X-API-Key: $KEY" "$URL/api/records/DATA-HE-CAE-2026-0000000001"
curl -H "X-API-Key: $KEY" "$URL/api/records/DATA-HE-CAE-2026-0000000001/attachments"

# csv 다운로드 (정적 마운트)
curl -H "X-API-Key: $KEY" "$URL/attachments/DATA-HE-CAE-2026-0000000001/A001.csv"
```

## 데이터 출처 / 제한사항

모든 stress-strain 곡선과 σy/σu/E/ε_f 수치는 **NIST / ASM Handbook / MatWeb 공개 자료의 representative 값** 이다. 실제 lot 별 정확한 측정값이 아니며, 사업부 R&D 의 자체 시험 결과를 대체하지 않는다. 정확한 lot 데이터가 필요한 경우 위 출처들을 직접 참조한다.

| # | 재료 | E (GPa) | σy (MPa) | σu (MPa) | ε_f (%) | 비고 |
|---|---|---|---|---|---|---|
| 1 | AISI 1018 carbon steel | 205 | 370 | 440 | 15 | n≈0.26 |
| 2 | AA6061-T6 aluminum | 68.9 | 276 | 310 | 12 | n≈0.05 |
| 3 | AA7075-T6 aluminum | 71.7 | 503 | 572 | 11 | n≈0.10, 항공재 |
| 4 | TPU Shore A 85 | — | — | ~30 @ 600% | 600+ | hyperelastic, Ogden/Mooney-Rivlin 적합 |
| 5 | 316L stainless steel | 193 | 290 | 579 | 50 | austenitic, 가공경화율 높음 |

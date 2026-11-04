# AI Data Hub — Apptainer 배포 (Linux/Ubuntu 24.04, Docker 미사용)

Docker 가 금지된 환경에서 **Apptainer** 로 PostgreSQL+pgvector 를 띄우고 API 서버는 native venv 로 실행하는 자동화 셋업.

## 빠른 시작 (새 머신)

```bash
# 0) 사전 요구 (Ubuntu 24.04 기준)
sudo add-apt-repository -y ppa:apptainer/ppa
sudo apt update
sudo apt install -y apptainer python3.12 python3.12-venv curl

# 1) 소스 복사 후
cd <repo>/deploy/apptainer
cp .env.example .env            # 필요 시 프록시/포트 수정
bash install_all.sh             # build → PG start → API start (한 방)
```

완료되면:

- API : `http://127.0.0.1:8001/api/system/health` → `{"status":"ok"...}`
- docs: `http://127.0.0.1:8001/docs`

## 프록시 환경 (사내망 등)

`.env` 에서 채우면 모든 다운로드 (`apptainer pull`, `pip install`) 가 자동으로 프록시 사용:

```env
HTTPS_PROXY=http://proxy.corp.local:8080
HTTP_PROXY=http://proxy.corp.local:8080
NO_PROXY=.corp.local,10.0.0.0/8
```

`localhost`, `127.0.0.1`, `::1` 은 스크립트가 자동으로 `NO_PROXY` 에 추가한다 (PG ↔ API 호출 차단 방지).

## 포트 충돌

기본은 PG=`5435`, API=`8001` (호스트 기본 5432/8000 회피). 점유 중이면 `.env` 의 `POSTGRES_PORT`, `API_PORT` 변경 후 재실행. 사전 검증이 충돌 시 즉시 실패 메시지로 알려준다.

## 개별 명령

| 작업 | 명령 |
|------|------|
| 한방 셋업 | `bash install_all.sh` |
| SIF 빌드만 | `bash build.sh` (재빌드: `bash build.sh --force`) |
| PG 만 기동 | `bash start_postgres.sh` |
| API 만 기동 | `bash start_api.sh` |
| 정지 | `bash stop.sh` |
| 상태 | `apptainer instance list \| grep aidh` |
| 로그 | `tail -f logs/uvicorn.log` 또는 `apptainer logs aidh_postgres` |
| 완전 초기화 | `bash stop.sh && rm -rf data/postgres` |

## 데이터 영속성

- `data/postgres/` — PG 데이터 (호스트 바인드)
- `data/attachments/`, `data/figures/` — API 첨부/그림
- 모두 호스트 디렉터리이므로 백업은 `tar`/`rsync` 로 가능.

## 구성

```
deploy/apptainer/
├── _common.sh           공용 함수 (env 로드, 프록시, 검증)
├── .env.example         포트/프록시/DB 비번 — 반드시 .env 로 복사
├── postgres.def         pgvector/pgvector:pg16 + startscript
├── build.sh             SIF pull + 빌드 (멱등)
├── start_postgres.sh    instance start + CREATE EXTENSION vector
├── start_api.sh         venv + alembic + seed + uvicorn (nohup)
├── stop.sh              모두 정지
├── install_all.sh       한방 셋업
├── data/                런타임 (gitignore)
└── logs/                런타임 (gitignore)
```

## 트러블슈팅

| 증상 | 대응 |
|------|------|
| `apptainer: command not found` | PPA 설치 (위 빠른 시작 0단계) |
| `python venv 모듈 없음` | `sudo apt install -y python3.12-venv` |
| `port X 이미 사용 중` | `.env` 에서 포트 변경 후 재실행 |
| `apptainer pull` 타임아웃 | 프록시 변수 확인 (`.env`) |
| `pip install` 실패 | 동일 — 프록시 또는 사내 PyPI 미러 (`PIP_INDEX_URL` 환경변수) 설정 |
| `CREATE EXTENSION vector` 실패 | base 이미지가 pgvector 포함이라 거의 없음. `tail logs/postgres-ext.log` |
| API 가 DB 못 찾음 | `cat api_server/.env` 의 `DATABASE_URL` 이 PG 포트와 일치하는지 확인 |
| `alembic upgrade head` 실패 — 잠금(`lock_timeout`) | `start_api.sh` 가 이행 전에 쉬는 트랜잭션의 pid · 시간을 찍는다(`⚠ 쉬는 트랜잭션`). 병합 · 백업이 아닌지 보고 `SELECT pg_terminate_backend(<pid>);` 뒤 다시 기동한다. 기동이 실패해도 옛 API 는 그대로 돈다 |

## PG 안전망 — 쉬는 트랜잭션 시한(박스마다 한 번)

트랜잭션을 연 채 쉬는 연결은 열을 더하는 이행(0032 등)을 막는다 — 10-17 cae00 에서 옛 API 의 쉬는 연결이 이행을 막았다. 30분이 지나면 PG 가
그 연결을 끊게 한다(새 연결부터 든다). 병합(`merge-from-drive.sh`) · 백업이 돌지 않을 때 한 번 건다. dev 는 2026-10-17 에 걸었다.

```bash
cd deploy/apptainer && ( set -a; . ./.env; set +a
  apptainer exec "instance://${INST_POSTGRES:-aidh_postgres}" psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -c "ALTER DATABASE \"$POSTGRES_DB\" SET idle_in_transaction_session_timeout = '30min';" )
# 확인(새 연결): ... -tAc 'SHOW idle_in_transaction_session_timeout'  → 30min
```

## dev → cae00 데이터 동기화 — 조각 스냅숏

전체 덤프(`backup-to-drive.sh`, 압축 10 GB 남짓)를 통째로 받아 병합하면 바뀐 줄이 하나여도 한 시간을 넘긴다(받기 · 스테이징 색인 · 전체
스냅숏). 조각 스냅숏은 바뀐 조각만 오간다(HWAXPortal `docs/aidh-parts-sync`).

| 박스 | 명령 | 무엇 |
|---|---|---|
| dev(하루 한 번, 전체 덤프 뒤) | `bash parts-to-drive.sh` | 표를 이진 COPY 로 내되 레코드에 딸린 큰 표는 레코드 id 해시로 64 조각 · 한 시점(pg_export_snapshot). 조각 이름은 내용 해시라 안 바뀐 조각은 다시 올리지 않는다 |
| cae00(update-all §3) | `bash fetch-parts.sh` | 최신 목록을 지난번 병합한 목록과 견줘 다른 조각만 받는다 — 끝 코드 0 받음 · 10 바뀐 것 없음 · 4 목록 없음(전체 덤프 길로) |
| cae00 | `AIDH_MERGE_PARTS=1 bash merge-from-drive.sh` | 스키마로 빈 스테이징(색인 없음) → 받은 조각만 싣기 → 지금의 병합 규칙 그대로. 끝나면 `bash fetch-parts.sh --commit` |
| cae00 | `bash rollback-parts.sh /data/backups/aidh/pre-parts-<시각>` | 그 병합이 덮은 줄의 앞 모습 · 새로 든 키로 병합 앞으로 되돌린다(병합 뒤 이 박스에서 같은 줄을 고쳤으면 그것도 앞 모습으로 간다) |

조회 통계(`records.read_count` · `last_accessed_at`)와 상류 점검 칸은 조각에 내지 않는다 — 그 박스의 것이고, dev 의 조회가 조각을 매일
바꾸지 않게. 그래서 새 행은 이 박스의 기본값(0 · 비움)으로 든다. 시험은 `api_server/tests/test_parts_sync_pg.py`(실 PostgreSQL ·
`AIDH_TEST_PG_BIN`).


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

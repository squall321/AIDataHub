# AI Data Hub — Apptainer 크로스-호스트 배포 가이드

한 서버에서 다른 서버로 옮길 때 부딪히는 모든 함정과 해결책. MX White Paper 의
동명 문서를 기반으로 AI Data Hub 의 실제 구조 (postgres 컨테이너 + native API
venv + VSCode 확장)에 맞춰 정리.

> **TL;DR** — Apptainer 는 Docker 보다 가볍지만 "묵시적 가정"이 많다.
> 사용자/네트워크/파일권한이 호스트와 컨테이너 사이에서 미묘하게 다르게
> 동작하며, 그 차이는 빌드한 호스트와 배포 호스트가 다를 때 폭발한다.

이 문서의 모든 함정은 `deploy/apptainer/` 의 신규 헬퍼 스크립트로 회복 가능:

| 스크립트 | 역할 |
|---|---|
| `desudo.sh` | sudo 흔적 회복 (Issue #1) |
| `restart.sh` | stop + start 명시 재기동 (Issue #11) |
| `diag.sh` | 인스턴스 · 포트 · 헬스 3계층 자동 점검 (Issue #12) |
| `bundle.sh` | 안전한 tar 번들 (data/.git 자동 exclude — Issue #2, #8) |

---

## 1. sudo 함정 — Apptainer 는 rootless 다

**증상**:
- `sudo apptainer instance start` → 인스턴스 동작하는 듯 보임
- 다음에 일반 사용자로 같은 명령 → `Permission denied`
- `deploy/apptainer/data/postgres/` 쓰기 실패

**원인**:
- Apptainer 는 기본적으로 **calling user 로 실행** (Docker 와 다름)
- sudo 로 띄우면 컨테이너 프로세스가 root → 그 프로세스가 만든 bind 된 host
  파일이 root 소유로 남음
- 다음에 일반 사용자가 그 폴더 쓰려고 하면 EACCES

**해결**:
- 룰: **apptainer 명령에 sudo 절대 쓰지 말 것**. 예외는 호스트 패키지 설치
  (`sudo apt-get install apptainer`) 뿐.
- 한 번이라도 sudo 썼다면 회복:
  ```bash
  bash deploy/apptainer/desudo.sh            # dry-run (미리보기)
  bash deploy/apptainer/desudo.sh --yes      # 실제 적용
  bash deploy/apptainer/desudo.sh --yes --hard  # /root/.apptainer 까지 정리
  ```
  스크립트가 자동으로:
  - `deploy/apptainer/data/` 내 비-본인 소유 파일 chown
  - `~/.apptainer/instances` state 정리 (백업 후)
  - `deploy/apptainer/logs/` 권한 복구

---

## 2. tar 번들로 옮길 때 — 데이터 디렉토리는 무조건 비워라

**증상**:
- `tar -xf bundle.tar` 직후 `start_postgres.sh` 돌리면 postgres
  `Permission denied (os error 13)` 또는 `initdb 가 빈 디렉토리 요구`
- alembic 이 0001 부터 다시 돌리려다 `DuplicateTableError`

**원인**:
- tar 안에 `deploy/apptainer/data/postgres/` 가 그대로 들어감
- 그 폴더는 원본 서버에서 컨테이너 내부 postgres user (uid 999 같은) 소유
- 새 서버에선 host user uid 가 다르거나 fakeroot mapping 이 달라서 못 씀

**해결**:
- **bundle.sh 사용** — 위험한 디렉토리 자동 exclude:
  ```bash
  bash deploy/apptainer/bundle.sh
  # 자동 exclude:
  #   ./deploy/apptainer/data        ← Issue #2
  #   ./.git                          ← Issue #8
  #   ./.venv, ./api_server/.venv     ← 호스트 의존
  #   ./vscode_extension/node_modules ← npm install 로 재생성
  #   ./.bkit, ./api_server/.bkit    ← bkit local state
  ```
- 데이터 자체를 옮기고 싶다면 `pg_dump` / `pg_restore` 사용
  (파일시스템 디렉토리 통째 복사 ❌)

---

## 3. Apptainer 네트워크 — "host network" 는 보장이 아니다

**증상**:
- API 서버 (native venv, port 8001) 가 startup 직후
  `connect to 127.0.0.1:5435 failed: Connection refused`
- pg_isready 는 instance exec 안에서는 성공 (`apptainer exec instance://...`)
- 그러나 host 셸에서 `psql -h 127.0.0.1 -p 5435` 는 실패

**원인**:
- 일반 가정: Apptainer 는 host network 공유 (컨테이너 127.0.0.1 == host 127.0.0.1)
- 실제: `/etc/apptainer/apptainer.conf` 에 따라 instance 가 자기 netns 에 들어갈
  수 있음 → 컨테이너 127.0.0.1 = 자기 자신 loopback, host 의 그것과 다름

**해결 — 3가지 옵션**:

| 옵션 | 방법 | 언제 |
|---|---|---|
| **A. 기본** | 아무것도 안 함 — 대부분 환경에서 host network 공유 (`AIDH_APPT_HOST_NET=0`, default) | 기본 시도 |
| **B. host network 명시** | `.env` 의 `AIDH_APPT_HOST_NET=1` → `start_postgres.sh` 가 `--net --network=host` 추가 | A 가 안 될 때, host CNI conflist 가 `/etc/apptainer/network/` 에 있을 때 |
| **C. host CNI config 추가** | `sudo /etc/apptainer/network/40_host.conflist` 직접 작성 | B 도 안 될 때 (드물지만 가능) |

**검증**:
```bash
bash deploy/apptainer/diag.sh
# [B] Ports 섹션에서 5435 가 LISTEN 으로 나오면 host network OK
```

---

## 4. /tmp 가정

본 프로젝트의 postgres 컨테이너는 `/tmp` 사용 안 함 (data 는 별도 bind).
API 서버는 native venv 라 host /tmp 그대로 사용 — 문제 없음.

(VSCode 확장 webview 자체도 별도 file system 안 씀.)

---

## 5. CORS — 외부 IP 로 접속하면 차단

**증상**:
- 외부 IP 로 접속한 VSCode 확장에서 API 호출 시 CORS 에러

**원인**:
- 현재 `api_server/src/api/main.py`:
  ```python
  app.add_middleware(CORSMiddleware, allow_origins=["*"], ...)
  ```
  은 dev 편의값. 운영 시 좁히면 외부 origin 거절.

**해결 — 운영 시작 전**:
1. `main.py` 의 CORS 미들웨어를 `.env` 기반으로 변경 (별도 작업)
2. 또는 `allow_origins` 에 실제 사용 origins 명시:
   ```python
   import os
   CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")
   app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, ...)
   ```
3. `.env.example` 에 가이드 주석 추가 (이미 반영됨)

**현재 상태**: 사내 dev 단계이므로 `["*"]` 유지. 외부 노출 전 좁히기.

---

## 6. 포트 충돌 — 사용 중 포트는 빼앗을 수 없다

**증상**:
- start_postgres.sh 가 silent 하게 fail 한 후 다음 단계로 진행
- 또는 다른 누군가의 프로세스가 5435 점유

**해결 — 이미 들어 있는 가드**:
```bash
# _common.sh 의 require_port_free 가 자동 실행
[ERROR] POSTGRES 포트 5435 이미 사용 중. .env 에서 POSTGRES_PORT 변경하라.
```

**충돌 발견 시**:
```bash
ss -tlnp | grep -E "5435|8001"
lsof -i :5435   # 누군지 확인 후 kill
# 또는 .env 의 POSTGRES_PORT 를 다른 포트로 변경 + restart.sh
```

---

## 7. instance idempotency 의 부작용

**증상**:
- `.env` 수정 후 `bash start_postgres.sh` → 변경 안 반영
- 기존 인스턴스가 `already running` 으로 skip 됨

**원인**:
- `apptainer instance start` 는 같은 이름 instance 있으면 새로 안 만듦
- 새 env / bind 는 instance lifecycle 동안 고정 — 변경하려면 stop → start

**해결**:
```bash
bash deploy/apptainer/restart.sh           # postgres + api 둘 다
bash deploy/apptainer/restart.sh --pg      # postgres 만 (API venv 그대로)
bash deploy/apptainer/restart.sh --api     # api 만 (DB 보존)
```

스크립트가 자동으로:
- API uvicorn 그레이스풀 종료 (TERM → 5초 후 KILL)
- postgres instance stop + 포트 release 대기 (최대 20초)
- start_*.sh 정상 흐름 그대로 재기동

---

## 8. 서비스 상태 ≠ 인스턴스 상태

**증상**:
- `apptainer instance list` 에 `aidh_postgres` 보임
- 그런데 `curl http://127.0.0.1:8001/api/system/health` 는 timeout

**원인**:
- Apptainer instance "running" = startscript 가 fork 되어 동작 중
- 그 안의 실제 서비스 (postgres / uvicorn) 가 startup 직후 crash 해도 instance
  자체는 "running" 으로 표시
- 인스턴스 껍데기 살아있지만 안의 서비스는 죽어있는 상태가 흔함

**해결 — diag.sh 가 자동 결합**:
```bash
bash deploy/apptainer/diag.sh
# [A] apptainer instance list   — 인스턴스 살아있나
# [B] ss -tlnp                   — 포트 LISTEN 하나
# [C] curl /api/system/health    — HTTP 응답 OK 인가
# [D] alembic current             — 스키마 head 적용됐나
# [E] EMBEDDING_DIM 정합          — 컬럼 vs env 일치
```

실패 발견 시 자동 로그 tail:
```bash
bash deploy/apptainer/diag.sh --tail-logs
```

---

## 9. .git 덮어쓰기 — 인증 정보 같이 갈아엎힘

**증상**:
- tar 풀고 나면 `git pull` 에서 SSH 키 없다고 멈춤
- `fatal: origin does not appear to be a git repository`

**원인**:
- tar 가 source 서버의 `.git/` 통째로 가져옴
- source 의 remote URL (SSH `git@github.com:...`) 이 target 에 박힘
- target 서버에는 source 의 SSH 키나 PAT 가 없음

**해결**:
- `bundle.sh` 가 자동으로 `.git` exclude (Issue #2 와 같은 스크립트)
- 받은 쪽에서:
  ```bash
  cd ~/Projects/AIDataHub
  rm -rf .git
  git init
  git remote add origin https://github.com/squall321/AIDataHub.git
  git fetch origin main
  git reset --hard origin/main
  ```

---

## 10. .sif 의존성 drift — 코드는 진화, 이미지는 정지

**증상**:
- postgres 는 영향 없음 (pgvector image 가 안정).
- API 의 sentence-transformers 모델은 native venv 에 설치되므로 .sif 무관.

**위험 시나리오 (앞으로)**:
- 만약 API 서버를 Apptainer 안에 넣게 되면, 그때 .def 의 pip install list 가
  코드의 import 와 sync 해야 함.
- 현재 구조 (native venv) 는 이 문제 없음.

---

## 11. Apptainer 버전 차이

본 프로젝트는 `--net --network=host` 같은 버전 차이 옵션을 기본 비활성 (#3 참고).
`AIDH_APPT_HOST_NET=1` 도 명시 opt-in.

**최소 권장 버전**: Apptainer 1.2+ (Ubuntu 24.04 의 PPA 기본 버전)

```bash
apptainer --version
# 권장: 1.3.0 이상
```

1.0.x 같은 구버전은 일부 flag 자체가 없음 → setup.sh 의 `require_apptainer`
가드가 검출.

---

## 12. 재부팅 후 복구 + 자동 업데이트 — systemd 권장

서버가 reboot 되면 apptainer instance + uvicorn 모두 사라집니다. **데이터는
보존되므로** (data dir 에 그대로) 서비스만 다시 띄우면 끝.

### 권장 셋업 — systemd 한 번 등록 + update.sh 자동화

**한 번만** (운영 시작 시):
```bash
# 1. systemd unit 등록 (사용자 모드, sudo 불필요)
bash deploy/systemd/install-systemd.sh

# 2. 부팅 시 자동 기동 활성화 (사용자 모드 한정)
sudo loginctl enable-linger $(whoami)
```

설치 후 효과:
- 부팅 → 자동으로 postgres + API 기동
- 서비스 죽으면 systemd 가 자동 재시작 시도
- `journalctl --user -u aidh.service -f` 로 통합 로그

### 일상 운영 (update / restart)

**코드 업데이트** (가장 자주 쓰는 명령):
```bash
bash update.sh
```
자동으로:
1. systemd 가 aidh.service 를 멈춤 (감지 자동)
2. postgres 만 임시 기동 (alembic 위해)
3. `git pull` → `pip install` → `alembic upgrade head`
4. systemd 가 aidh.service 다시 기동
5. `/api/system/health` 200 확인까지

**수동 재시작** (`.env` 만 바꿨을 때):
```bash
systemctl --user restart aidh.service
```

**수동 정지**:
```bash
systemctl --user stop aidh.service
```

### 대안 — systemd 못/안 쓸 때

| 옵션 | 자동 기동 | 자동 재시작 | sudo |
|------|---------|----------|------|
| **A. systemd** (권장) | ✓ | ✓ | △ |
| B. crontab @reboot | ✓ (부팅 시 1회) | ✗ | ✗ |
| C. boot.sh 수동 | ✗ | ✗ | ✗ |

대안 명령:
```bash
bash deploy/apptainer/install-crontab.sh   # 옵션 B
bash deploy/apptainer/boot.sh              # 옵션 C (수동 재기동)
```

### `update.sh` 의 자동 분기 로직

내부적으로 systemd 등록 여부 자동 감지:
```bash
SYSMODE="none"
systemctl --user is-enabled aidh.service && SYSMODE="user"
systemctl is-enabled aidh.service          && SYSMODE="system"
```

| 감지 | stop 방법 | start 방법 |
|-----|---------|----------|
| `user` | `systemctl --user stop aidh.service` | `systemctl --user start aidh.service` |
| `system` | `sudo systemctl stop aidh.service` | `sudo systemctl start aidh.service` |
| `none` | `bash deploy/apptainer/stop.sh` | `bash deploy/apptainer/boot.sh` |

→ systemd 가 등록돼 있으면 `update.sh` 가 알아서 그 경로 사용. 사용자는 명령
하나로 stop → update → start → health verify 가 일관되게 됨.

### 안전 장치

`update.sh` 는 다음을 자동 처리:
- **잠금 (`/tmp/aidh-update.lock`)** — 동시 update 방지. 강제 해제: `--force-unlock`
- **health verify (15초 timeout)** — 시작 후 응답 안 오면 명시 실패 + 로그 위치 안내
- **rollback hint** — 실패 시 `git reset --hard <BEFORE>` 명령 출력
- **lockfile cleanup** — trap 으로 비정상 종료에도 잠금 해제

### `boot.sh` 와 systemd 의 관계

`boot.sh` 가 systemd 등록 상태를 감지하면 직접 호출 차단 (이중 기동 방지):
```bash
$ bash deploy/apptainer/boot.sh
[WARN] systemd (--user) 가 aidh.service 를 이미 관리 중
       boot.sh 직접 호출 대신:
         systemctl --user restart aidh.service
       강제로 직접 실행하려면: bash boot.sh --force
```
→ 운영자가 실수로 systemd + boot.sh 두 번 띄우는 사고 방지.

---

### 한 줄 정리

**처음 셋업 시 systemd 한 번 등록, 이후 update.sh 한 줄로 모든 자동화.**

```bash
# 처음 (한 번만)
bash deploy/systemd/install-systemd.sh
sudo loginctl enable-linger $(whoami)

# 이후 매번
bash update.sh
```

---

## 운영 체크리스트 — 새 서버 셋업 시

순서대로 진행:

```bash
# 1. 호스트 패키지 (한 번만, sudo OK)
sudo add-apt-repository -y ppa:apptainer/ppa
sudo apt update && sudo apt install -y apptainer python3.12-venv nodejs git curl

# 2. 코드 받기
git clone https://github.com/squall321/AIDataHub.git ~/Projects/AIDataHub
cd ~/Projects/AIDataHub
# 또는: tar -xzf aidh-bundle-*.tar.gz -C ~/Projects/AIDataHub

# 3. .env 작성
cp deploy/apptainer/.env.example deploy/apptainer/.env
# 필요 시 편집 — POSTGRES_PORT / EMBEDDING_PROVIDER / 프록시 등

# 4. (sudo 실수 있었으면) 정리
bash deploy/apptainer/desudo.sh --yes

# 5. (옵션) 사전 빌드된 SIF 있으면 deploy/apptainer/ 에 풀기
#    아니면 build.sh 가 docker hub 에서 pull (프록시 폴백 포함)

# 6. 풀 부팅
bash setup.sh

# 7. 검증
bash deploy/apptainer/diag.sh
bash deploy/apptainer/diag.sh --tail-logs    # 실패가 있다면

# 8. 안 되면 (가장 흔한 케이스)
#   [B] 포트 5435 또는 8001 NOT listen
#      → AIDH_APPT_HOST_NET=1 (.env) 후 bash deploy/apptainer/restart.sh
#   [A] aidh_postgres NOT running
#      → bash deploy/apptainer/desudo.sh --yes (권한 회복) 후 재기동
```

---

## 핵심 환경변수 정리

`.env` (deploy/apptainer/.env) 의 주요 항목:

```bash
# 데이터베이스
POSTGRES_USER=aidh
POSTGRES_PASSWORD=aidh_change_me
POSTGRES_PORT=5435                  # 호스트 충돌 시 변경
INST_POSTGRES=aidh_postgres

# API 서버 (native venv)
API_PORT=8001
EMBEDDING_PROVIDER=e5_base          # 운영 권장 (한국어, dim=768)

# 프록시 (3단 폴오버 — _common.sh 참고)
HTTPS_PROXY=                        # 최우선 (있을 때)
BUILD_PROXY_HTTPS=http://168.219.61.252:8080  # .env 명시
# 둘 다 비어 있으면 DEFAULT_FALLBACK_PROXY (하드코딩) 적용
# opt-out: BUILD_PROXY_HTTPS=off

# Apptainer host network (Issue #3)
AIDH_APPT_HOST_NET=0                # 기본 0, host CNI 있을 때만 1

# LLM (preview 미리보기 기능, 선택)
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_ASK_MODEL=
```

---

## 마지막 한 줄

**컨테이너 자체는 가벼워도, 그 컨테이너가 host 와 맺는 계약 (네트워크 / FS 권한 /
환경변수 / 인증)은 host 마다 다르게 깨진다. 모든 host-dependent assumption 을
environment variable + diagnostic script 로 환원시키면 이식성이 생긴다.**

본 프로젝트는 위 13가지 함정 중 AIDataHub 구조에 해당하는 것 모두 검증 가능한
diag.sh + 회복 가능한 desudo.sh + 안전한 bundle.sh 로 커버한다.

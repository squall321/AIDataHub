# Apptainer + 오프라인/사내망 — 포팅 가능한 패턴

다른 Apptainer 기반 앱에도 그대로 적용할 수 있는 핵심 패턴만 추려놓은 문서.
프로젝트별 깊이는 `apptainer-cross-host-deployment.md` (카테고리별 reference)
및 `deployment-playbook.md` (실행 순서) 참고.

---

## A. 컨테이너-호스트 계약을 env var 로 환원

호스트 의존 가정 4가지는 코드/스크립트에 박지 말고 **env 로 외부 주입**.

| 가정 | env var | 디폴트 |
|---|---|---|
| 호스트 IP (CORS / proxy target) | `HOST_IP` placeholder → install.sh 자동 치환 | `hostname -I` 또는 `ifconfig.me` |
| 컨테이너에서 host loopback 도달 가능? | `PROXY_TARGET=http://${HOST_IP}:<PORT>` | `http://127.0.0.1:<PORT>` |
| host network 강제 여부 | `APPT_HOST_NET=0/1` | 0 (기본 동작) |
| Node TLS 검증 | `NODE_TLS_VERIFY=0/1` | 1 (보안), 사내 SSL 인터셉션 환경 0 |

**규칙**: 코드 / `vite.config.ts` / `Dockerfile` / `.def` 어디에도 IP 박지 말 것.

---

## B. .env 디자인 = placeholder

```bash
# .env.example
HOST_IP=...      # ← literal token, install.sh 가 sed 로 자동 치환
CHANGE_ME=...    # ← 비밀번호, 사용자 수동 회전
```

한 `.env.example` 이 모든 환경에서 동작.

`install.sh` 의 치환:

```bash
HOST_IP=$(curl -s ifconfig.me || hostname -I | awk '{print $1}')
sed -i "s/HOST_IP/$HOST_IP/g" .env
# CHANGE_ME 는 그대로 두고 경고만 출력
```

---

## C. 번들 제외 리스트 (모든 Apptainer 앱 공통)

```bash
tar --exclude='APP/.git' \              # target git 인증 보호
    --exclude='APP/.env' \              # dev 의 hardcoded 값 안 끌고 감
    --exclude='APP/infra/data' \        # UID/perm 충돌 방지
    --exclude='APP/node_modules/.cache' \
    --exclude='APP/.pnpm-store' \
    --exclude='APP/__pycache__' \
    --exclude='APP/.venv' \
    --exclude='*.log' --exclude='*.tsbuildinfo' \
    -cf - APP | split -b 1G -d - bundle.tar.part
```

---

## D. install.sh — 풀고 셋업까지 한 줄

타겟 서버:

```bash
bash install.sh --target ~/Projects
```

내부 흐름:

1. SHA256 체크섬 검증
2. tar 합치고 추출 (`.env` / `.git` 건드리지 않음)
3. `.env` 없으면 `.env.example` 복사 + `sed "s/HOST_IP/$(detected_ip)/g"`
4. apptainer 없으면 `bootstrap-host.sh` 자동 호출 (sudo)
5. `fresh.sh` 호출 (clean + start + migrate + seed)

---

## E. 컨테이너 `.def` 의 함정 회피

**Corepack 절대 쓰지 말 것** — 사내 SSL 인터셉션 + `COREPACK_ENABLE_NETWORK=0` 의 catch-22:

```dockerfile
# 잘못된 패턴 ❌
corepack enable && corepack prepare pnpm@9 --activate

# 올바른 패턴 ✓
npm install -g pnpm@9
```

**의존성을 `.def` 에 동결**: 모든 deps 를 `%post` 의 pip install 에 명시. 그래야
`~/.local` 우연 마운트로 dev 에서만 동작하는 사고 방지.

```dockerfile
# api.def — 모든 의존성 명시
%post
    pip install --no-cache-dir \
        "fastapi>=0.115" "uvicorn[standard]>=0.32" \
        "sqlalchemy[asyncio]>=2.0.36" "alembic>=1.14" \
        "python-docx>=1.1" "python-pptx>=1.0" "openpyxl>=3.1" \
        ...
```

---

## F. start.sh — instance start 시 명시 bind

```bash
mkdir -p $DATA_DIR/app-tmp && chmod 777 $DATA_DIR/app-tmp

apptainer instance start \
  --bind "$REPO_ROOT:/workspace" \
  --bind "$DATA_DIR/app-tmp:/tmp" \                          # 일부 apptainer 가 /tmp auto-mount 안 함
  --env "PROXY_TARGET=${PROXY_TARGET:-http://127.0.0.1:8800}" \
  --env "NODE_TLS_REJECT_UNAUTHORIZED=${NODE_TLS_VERIFY:-1}" \
  ...
```

---

## G. Instance idempotency 인지

`apptainer instance start` 는 이름 같으면 skip. env 바꿔도 안 반영. 무조건 `restart.sh` (stop + start) 사이클.

---

## H. 필수 스크립트 묶음 (포팅 가능)

다른 Apptainer 앱에도 그대로 복사해갈 수 있는 18개 스크립트 패턴:

### 호스트 셋업 (sudo)
| 스크립트 | 핵심 한 줄 |
|---|---|
| `bootstrap-host.sh` | apt install apptainer + node + pnpm + python3-venv |
| `desudo.sh` | sudo 흔적 회복 (chown, /tmp, ~/.apptainer/root) |
| `firewall.sh` | ufw 룰 — RFC1918 LAN 만 허용 (사내망 권장) |

### 일반 lifecycle (sudo 금지)
| 스크립트 | 역할 |
|---|---|
| `build.sh` | `.sif` 빌드/풀 |
| `start.sh` | 명시 bind + env 로 instance start |
| `stop.sh` | instance stop |
| `restart.sh` | stop + start |
| `migrate.sh` | alembic upgrade head |
| `seed.sh` | 초기 데이터 |

### 회복
| 스크립트 | 데이터 | 시점 |
|---|---|---|
| `clean.sh` | data dir 비움 + chmod 777 (meili/minio 류) | 권한 꼬임 |
| `fresh.sh` | clean + start + migrate + seed | 새 서버 첫 셋업 |
| `recover.sh` | desudo + clean + start + migrate + seed + diag | 최후의 핵폭탄 |
| `reset-db.sh` | DB 만 (자동 백업) | DB 만 초기화 |
| `backup-db.sh` | DB → .sql.gz | dump |
| `restore-db.sh` | .sql.gz → DB | 복원 |

### 진단
| 스크립트 | 역할 |
|---|---|
| `status.sh` | 간단 헬스체크 |
| `diag.sh` | 7섹션 상세 (인스턴스 + 포트 + HTTP + 컨테이너→host 도달) |
| `errors.sh` | 인스턴스별 stderr (깊이/하이라이트/실시간) |
| `logs.sh <svc>` | 단일 인스턴스 `tail -F` |

### Entry point
| 스크립트 | 역할 |
|---|---|
| `quickstart.sh` | 0~7단계 전 과정 (preflight 포함) |
| `update.sh` | git pull + change-aware rebuild + restart + migrate |
| `bundles/install.sh` | 번들 풀고 fresh 자동 호출 |

---

## I. 진단 3단 구조

```
1. instance list                    apptainer instance list
2. port LISTEN                      ss -tlnp | grep <port>
3. HTTP 응답                        curl <health-url>
```

세 단계가 다 통과해야 "동작 중". 1만 통과하고 2가 비면 = 컨테이너 안 서비스 crash.
진단할 때 항상 이 3단 다 확인. `diag.sh` 가 자동 결합.

---

## J. 사내망 특이사항 체크리스트

| 항목 | 대응 |
|---|---|
| HTTPS proxy 통과 (docker hub / npm registry) | `FALLBACK_PROXY=http://<corp-proxy>:8080` 환경변수 |
| SSL 인터셉션 (사설 CA) | `NODE_TLS_VERIFY=0` 또는 `NODE_EXTRA_CA_CERTS` 로 사내 CA 등록 |
| 사설 IP 라우팅 (10.x.x.x) | "외부" 의 정확한 위치 확인. 사내망 PC / VPN / 인터넷 — 각각 다른 처방 |
| ufw default-deny | `firewall.sh` 로 LAN 만 허용 |
| docker.io 차단 | .sif 미리 빌드해서 번들에 동봉 (target 에서 pull 시도 안 함) |

---

## K. 신규 앱 만들 때 PR 체크리스트

- [ ] `.env.example` 은 placeholder 만 (구체 IP 박지 마)
- [ ] vite/uvicorn host bind 는 `0.0.0.0` (127.0.0.1 X)
- [ ] Vite 5+ `allowedHosts: true`
- [ ] CORS_ORIGINS 에 placeholder 추가 가능
- [ ] proxy target env 로 override 가능
- [ ] `.def` 에 corepack 안 쓰기 (npm install -g 로 직접 설치)
- [ ] `.def` 에 모든 deps 명시 (host의 ~/.local 안 의존)
- [ ] `.gitignore` 에 `*.sif`, `infra/data/`, `.env`
- [ ] `.sif` 는 git 으로 옮기지 말 것 (번들 또는 scp)
- [ ] start 시 `--bind /tmp` 또는 자체 tmp 디렉토리 bind
- [ ] 18개 스크립트 묶음 (desudo / clean / fresh / recover / diag / firewall / update / install)
- [ ] ufw 룰 자동화 스크립트
- [ ] `install.sh` 가 HOST_IP placeholder 치환

---

## L. 포트 인벤토리 — 같은 서버에 여러 앱 설치 시

### AIDataHub 가 쓰는 포트

| 서비스 | 포트 | env var | 외부 노출 |
|---|---|---|---|
| API (FastAPI + MCP) | **8001** | `API_PORT` | O (브라우저 + AI 클라이언트 + MCP) |
| Postgres | **5435** | `POSTGRES_PORT` | X (내부만) |

호스트 인스턴스 이름: `aidh_postgres`

### 여러 앱 — 포트 충돌 방지 전략

같은 서버에 비슷한 스택을 또 올릴 때 **앱별로 1000번대 offset** 으로 분리:

| 앱 | API | Postgres |
|---|---|---|
| AIDataHub | 8001 | 5435 |
| App2 | 9001 | 5535 |
| App3 | 10001 | 5635 |

각 앱의 `.env` 에서 `*_PORT` 만 바꾸면 끝. 코드 수정 X.

### 인스턴스 이름도 prefix 로 분리

`_common.sh` 에서:

```bash
INST_PREFIX="${APP_NAME:-aidh}"
INST_POSTGRES="${INST_PREFIX}_postgres"
```

`.env` 에 `APP_NAME=app2` 하면 자동으로 `app2_postgres` 식으로 분리.

### 충돌 사전 체크 — start.sh 앞에 박을 한 줄

```bash
for p in $POSTGRES_PORT $API_PORT; do
  if ss -tln 2>/dev/null | awk '{print $4}' | grep -qE ":${p}$"; then
    echo "✗ port $p already in use:"
    ss -tlnp 2>/dev/null | grep ":${p} "
    echo "  → .env 의 *_PORT 값을 다른 번호로 바꿔주세요"
    exit 1
  fi
done
```

`diag.sh` 의 [B] 섹션이 이 검증을 이미 수행 — 충돌 즉시 발견 가능.

---

## M. 한 줄 정리

**컨테이너 자체는 가벼워도 그 컨테이너가 host 와 맺는 모든 계약 (UID mapping,
/tmp, /home, /etc/apptainer, network namespace, ufw, TLS chain, npm registry
reachability) 이 dev 서버에서 동작했다고 target 서버에서도 동작한다는 보장은
없다. 모든 host-dependent assumption 을 opt-in env var 또는 자동 감지 +
placeholder 패턴으로 환원해야 portable 함.**

---

## N. AIDataHub 의 현재 구현 매핑

이 문서의 패턴이 AIDataHub 어디에 적용됐는지:

| 패턴 | AIDataHub 구현 위치 |
|---|---|
| A. env var 환원 | `.env.example` + `_common.sh::export_proxy` (3단 폴오버) |
| B. HOST_IP placeholder | `.env.example` + `install.sh` sed 치환 |
| C. 번들 exclude | `deploy/apptainer/bundle.sh` |
| D. install.sh | `install.sh` (project root) |
| E. .def 동결 | `postgres.def` — pgvector image 직접 사용 (얇음). API 는 native venv. |
| F. 명시 bind | `start_postgres.sh` — postgres data + postgres-run socket bind |
| G. idempotency | `restart.sh` |
| H. 18 스크립트 | bootstrap-host / desudo / restart / clean / fresh / recover / status / diag / backup-db / restore-db / reset-db / quickstart / update / install / bundle 등 14개 구현 |
| I. 3단 진단 | `diag.sh` [A] instance / [B] port / [C] HTTP |
| J. 사내망 | `BUILD_PROXY_HTTPS` 3단 폴오버 + `DEFAULT_FALLBACK_PROXY` 하드코딩 |
| K. PR 체크리스트 | 본 문서 + cross-host-deployment.md 참조 |
| L. 포트 인벤토리 | `INST_PREFIX` (APP_NAME-based) — multi-app 공존 지원 |

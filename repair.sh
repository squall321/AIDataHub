#!/usr/bin/env bash
# AI Data Hub — 어떤 문제든 자동 진단·회복·재시도하는 최종 보장 스크립트.
#
# 시나리오: setup.sh / install.sh / quickstart.sh 가 어딘가에서 멈췄거나
# 에러로 죽었을 때 이 스크립트를 실행. 7단계 진단 + 단계별 자동 회복 시도.
#
# 동작 원칙:
#   - 각 phase 가 실패하면 → 자동 진단 → 더 공격적 회복 → 재시도 (최대 3회)
#   - 최종 실패 시 정확한 에러 위치 + 다음 액션 안내
#   - --auto: 사용자 확인 없이 데이터 삭제까지 자동 (위험)
#   - --diagnose-only: 진단만, 변경 없음
#   - --keep-data: 데이터 삭제 회복 시도는 skip
#
# 사용:
#   bash repair.sh                # 기본 (대화형, 데이터 삭제 시 confirm)
#   bash repair.sh --auto         # 무인 자동 (CI 등)
#   bash repair.sh --diagnose-only
#   bash repair.sh --keep-data    # data dir 보존
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPT_DIR="$ROOT_DIR/deploy/apptainer"

AUTO=0
DRY=0
KEEP_DATA=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --auto)            AUTO=1; shift ;;
    --diagnose-only)   DRY=1; shift ;;
    --keep-data)       KEEP_DATA=1; shift ;;
    -h|--help)         sed -n '2,22p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "[ERROR] unknown arg: $1" >&2; exit 2 ;;
  esac
done

# ── 색상 / 로깅 ─────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  G="\033[32m"; R="\033[31m"; Y="\033[33m"; B="\033[34m"; C="\033[36m"; N="\033[0m"
else
  G=""; R=""; Y=""; B=""; C=""; N=""
fi

phase() { echo; printf "${C}═══ PHASE %s — %s ═══${N}\n" "$1" "$2"; }
ok()    { printf "  ${G}✓${N} %s\n" "$*"; }
fail()  { printf "  ${R}✗${N} %s\n" "$*"; }
warn()  { printf "  ${Y}!${N} %s\n" "$*"; }
info()  { printf "  ${B}·${N} %s\n" "$*"; }
hint()  { printf "    ${B}→${N} %s\n" "$*"; }

confirm() {
  if [[ $AUTO -eq 1 ]]; then return 0; fi
  if [[ $DRY -eq 1 ]]; then return 1; fi
  read -r -p "  계속하시겠습니까? [y/N] " R
  [[ "$R" =~ ^[Yy]$ ]]
}

run() {
  # $1 = description, rest = command
  local desc="$1"; shift
  if [[ $DRY -eq 1 ]]; then
    info "[DRY] $desc"
    return 0
  fi
  info "$desc"
  "$@"
}

# ── 컨피그 로드 (에러 무시하면서) ─────────────────────────────────────
if [[ -f "$APPT_DIR/.env" ]]; then
  set -a; . "$APPT_DIR/.env" 2>/dev/null || true; set +a
fi
APP_NAME="${APP_NAME:-aidh}"
INST_POSTGRES="${INST_POSTGRES:-${APP_NAME}_postgres}"
POSTGRES_PORT="${POSTGRES_PORT:-5435}"
POSTGRES_USER="${POSTGRES_USER:-aidh}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-aidh_change_me}"
POSTGRES_DB="${POSTGRES_DB:-aidh}"
API_PORT="${API_PORT:-8001}"
DATA_DIR="$APPT_DIR/data"
LOG_DIR="$APPT_DIR/logs"
mkdir -p "$LOG_DIR"

echo "════════════════════════════════════════════════════════════════"
echo " AI Data Hub — repair (any-issue auto-recovery)"
echo " mode: $([[ $DRY -eq 1 ]] && echo 'DIAGNOSE ONLY' || ([[ $AUTO -eq 1 ]] && echo 'AUTO (no prompts)' || echo 'INTERACTIVE'))"
echo " keep-data: $([[ $KEEP_DATA -eq 1 ]] && echo yes || echo no)"
echo "════════════════════════════════════════════════════════════════"

# ===================================================================
phase 0 "사전 요구 검사"
# ===================================================================
ERR=0
for cmd in apptainer python3 curl ss; do
  if command -v "$cmd" >/dev/null 2>&1; then
    ok "$cmd 설치됨"
  else
    fail "$cmd 미설치"
    ERR=1
  fi
done
if [[ $ERR -eq 1 ]]; then
  cat <<'EOH'

  Ubuntu 24.04 설치:
    sudo add-apt-repository -y ppa:apptainer/ppa
    sudo apt update
    sudo apt install -y apptainer python3.12 python3.12-venv curl iproute2

  설치 후 repair.sh 재실행.
EOH
  exit 1
fi

# .env 확인
if [[ ! -f "$APPT_DIR/.env" ]]; then
  if [[ -f "$APPT_DIR/.env.example" ]]; then
    warn ".env 없음 — .env.example 에서 복사 + HOST_IP 치환"
    if confirm; then
      cp "$APPT_DIR/.env.example" "$APPT_DIR/.env"
      HOST_IP=$(timeout 3 curl -s ifconfig.me 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')
      [[ -n "$HOST_IP" && "$HOST_IP" =~ ^[0-9.]+$ ]] || HOST_IP="127.0.0.1"
      sed -i "s|^HOST_IP=HOST_IP$|HOST_IP=$HOST_IP|" "$APPT_DIR/.env"
      ok ".env 생성됨 (HOST_IP=$HOST_IP)"
    fi
  else
    fail ".env.example 도 없음 — 코드 손상"
    exit 1
  fi
else
  ok ".env 존재"
fi

# ===================================================================
phase 1 "모든 서비스 강제 종료 (clean slate)"
# ===================================================================

# 1a. API uvicorn 종료
if [[ -f "$LOG_DIR/api.pid" ]]; then
  PID=$(cat "$LOG_DIR/api.pid" 2>/dev/null || echo "")
  if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
    if [[ $DRY -eq 0 ]]; then
      kill "$PID" 2>/dev/null || true
      for i in 1 2 3; do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
      kill -9 "$PID" 2>/dev/null || true
      rm -f "$LOG_DIR/api.pid"
    fi
    ok "API uvicorn (pid=$PID) 종료"
  else
    info "API pid file 있지만 프로세스 없음"
    [[ $DRY -eq 0 ]] && rm -f "$LOG_DIR/api.pid"
  fi
else
  info "API pid file 없음 (이미 종료 상태)"
fi

# 1b. Apptainer 인스턴스 종료 (force)
if apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$INST_POSTGRES"; then
  run "apptainer instance stop $INST_POSTGRES" apptainer instance stop "$INST_POSTGRES" 2>/dev/null || true
  sleep 2
  # 그래도 살아있으면 강제 종료
  if apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$INST_POSTGRES"; then
    warn "stop 후에도 인스턴스 살아있음 — force kill"
    [[ $DRY -eq 0 ]] && apptainer instance stop -F "$INST_POSTGRES" 2>/dev/null || true
    sleep 2
  fi
  ok "$INST_POSTGRES 정지"
else
  info "$INST_POSTGRES 인스턴스 이미 정지"
fi

# 1c. 좀비 프로세스 정리 (port 점유 중인 lingering postgres)
for p in "$POSTGRES_PORT" "$API_PORT"; do
  PIDS=$(ss -tlnp 2>/dev/null | grep -E "[:.]${p}\$" | grep -oP 'pid=\K[0-9]+' | sort -u || true)
  for pid in $PIDS; do
    if [[ $DRY -eq 0 ]]; then
      kill "$pid" 2>/dev/null || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
    fi
    warn "port $p 점유 프로세스 (pid=$pid) 종료"
  done
done

# 1d. ~/.apptainer/instances 상태 파일 정리
APPT_STATE="$HOME/.apptainer/instances"
if [[ -d "$APPT_STATE" ]]; then
  ORPHAN=$(find "$APPT_STATE" -name "${INST_POSTGRES}.json" 2>/dev/null | head -3)
  if [[ -n "$ORPHAN" ]]; then
    # 이미 stop 했는데도 json 이 남으면 — 강제 정리
    if ! apptainer instance list 2>/dev/null | grep -qx "$INST_POSTGRES"; then
      for f in $ORPHAN; do
        [[ $DRY -eq 0 ]] && rm -f "$f"
        warn "orphan state file 제거: $f"
      done
    fi
  fi
fi

# ===================================================================
phase 2 "데이터 디렉토리 권한 / 소유권 회복"
# ===================================================================

ME="$(id -un)"
ENSURE_DIRS=("$DATA_DIR" "$DATA_DIR/postgres" "$DATA_DIR/postgres-run" \
             "$DATA_DIR/attachments" "$DATA_DIR/figures" "$LOG_DIR")
for d in "${ENSURE_DIRS[@]}"; do
  if [[ ! -d "$d" ]]; then
    [[ $DRY -eq 0 ]] && mkdir -p "$d"
    info "디렉토리 생성: $d"
  fi
done

# 소유권 검사
WRONG=$(find "$DATA_DIR" "$LOG_DIR" -not -user "$ME" 2>/dev/null | head -10 || true)
if [[ -n "$WRONG" ]]; then
  fail "비-본인 소유 파일 발견 (sudo 흔적 가능):"
  echo "$WRONG" | head -5 | sed 's/^/      /'
  if [[ $KEEP_DATA -eq 0 ]]; then
    warn "→ chown 시도 (sudo 권한 필요할 수 있음)"
    if [[ $DRY -eq 0 ]]; then
      if find "$DATA_DIR" "$LOG_DIR" -not -user "$ME" -exec chown -h "$ME":"$ME" {} + 2>/dev/null; then
        ok "소유권 회복 완료"
      else
        warn "일반 chown 실패 — sudo 시도"
        if command -v sudo >/dev/null && sudo find "$DATA_DIR" "$LOG_DIR" -not -user "$ME" -exec chown -h "$ME":"$ME" {} +; then
          ok "sudo chown 완료"
        else
          fail "chown 실패 — 수동 처리 필요"
          hint "sudo chown -R $ME:$ME $DATA_DIR $LOG_DIR"
          exit 1
        fi
      fi
    fi
  fi
else
  ok "$DATA_DIR / $LOG_DIR — 소유권 정상"
fi

# postgres-run 권한 (소켓 디렉토리, 777 권장)
[[ $DRY -eq 0 ]] && chmod 777 "$DATA_DIR/postgres-run" 2>/dev/null || true
[[ $DRY -eq 0 ]] && chmod 700 "$DATA_DIR/postgres" 2>/dev/null || true

# ===================================================================
phase 3 "포트 충돌 검사"
# ===================================================================

CONFLICT=0
for p in "$POSTGRES_PORT" "$API_PORT"; do
  if ss -tnl 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${p}\$"; then
    PROC=$(ss -tlnp 2>/dev/null | grep -E "[:.]${p}\$" | head -1 || true)
    fail "포트 $p 이미 사용 중:"
    echo "      $PROC" | sed 's/^/      /'
    CONFLICT=1
  else
    ok "포트 $p — 가용"
  fi
done

if [[ $CONFLICT -eq 1 ]]; then
  cat <<EOH

  해결 방법:
    1. 위 점유 프로세스 종료 후 repair.sh 재실행
       lsof -i :${POSTGRES_PORT}    # 점유자 확인
       kill <pid>

    2. 또는 .env 의 포트 변경:
       nano $APPT_DIR/.env
       POSTGRES_PORT=5535            # 다른 번호로
       API_PORT=8002

  현재 repair 중단. 위 조치 후 재실행하세요.
EOH
  [[ $DRY -eq 0 ]] && exit 1
fi

# ===================================================================
phase 4 "SIF 무결성 + 빌드"
# ===================================================================

REBUILD=0
for sif in postgres-base.sif postgres.sif; do
  if [[ ! -f "$APPT_DIR/$sif" ]]; then
    fail "$sif 없음"
    REBUILD=1
  else
    SIZE=$(stat -c %s "$APPT_DIR/$sif" 2>/dev/null || echo 0)
    if [[ $SIZE -lt 10000000 ]]; then  # 10MB 미만은 손상으로 간주
      fail "$sif 사이즈 비정상 ($SIZE bytes)"
      REBUILD=1
    else
      ok "$sif ($(du -h "$APPT_DIR/$sif" | awk '{print $1}'))"
    fi
  fi
done

if [[ $REBUILD -eq 1 ]]; then
  warn "SIF 빌드 필요"
  if [[ $DRY -eq 0 ]]; then
    if bash "$APPT_DIR/build.sh" --force 2>&1 | tail -20; then
      ok "SIF 재빌드 완료"
    else
      fail "build.sh 실패 — 네트워크/프록시 문제 가능"
      hint "BUILD_PROXY_HTTPS 확인: cat $APPT_DIR/.env | grep PROXY"
      hint "또는 사전 빌드 SIF (.tar.gz) 를 $APPT_DIR/ 에 풀어두고 재실행"
      exit 1
    fi
  fi
fi

# ===================================================================
phase 5 "Postgres 기동 (단계별 retry)"
# ===================================================================

start_postgres_attempt() {
  local attempt="$1"
  info "시도 #$attempt"

  # instance start
  local HOST_NET_OPTS=()
  if [[ "${AIDH_APPT_HOST_NET:-0}" = "1" ]]; then
    HOST_NET_OPTS=(--net --network=host)
  fi

  if [[ $DRY -eq 1 ]]; then
    info "[DRY] apptainer instance start $INST_POSTGRES"
    return 0
  fi

  apptainer instance start \
    "${HOST_NET_OPTS[@]}" \
    --bind "$DATA_DIR/postgres:/var/lib/postgresql/data" \
    --bind "$DATA_DIR/postgres-run:/var/run/postgresql" \
    --env "POSTGRES_USER=${POSTGRES_USER}" \
    --env "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}" \
    --env "POSTGRES_DB=${POSTGRES_DB}" \
    --env "PGPORT=${POSTGRES_PORT}" \
    --env "PGDATA=/var/lib/postgresql/data/pgdata" \
    --env "LANG=C.UTF-8" \
    --env "LC_ALL=C.UTF-8" \
    "$APPT_DIR/postgres.sif" "$INST_POSTGRES" \
    > "$LOG_DIR/repair-pg-start-$attempt.log" 2>&1 || return 1

  # pg_isready 대기 (30초)
  local i
  for i in $(seq 1 30); do
    if apptainer exec "instance://$INST_POSTGRES" \
         pg_isready -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
         >/dev/null 2>&1; then
      ok "postgres ready (${i}s)"
      # vector 확장
      apptainer exec "instance://$INST_POSTGRES" \
        psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
             -c "CREATE EXTENSION IF NOT EXISTS vector;" \
        > "$LOG_DIR/repair-pg-ext-$attempt.log" 2>&1 || warn "vector 확장 생성 실패 (이미 있을 수 있음)"
      return 0
    fi
    sleep 1
  done
  return 2  # pg_isready timeout
}

PG_OK=0
for attempt in 1 2 3; do
  if start_postgres_attempt "$attempt"; then
    PG_OK=1
    break
  fi
  RC=$?
  warn "시도 #$attempt 실패 (rc=$RC) — 자동 진단 + 재시도"

  # 실패 원인 진단
  if [[ -f "$LOG_DIR/repair-pg-start-$attempt.log" ]]; then
    LAST=$(tail -10 "$LOG_DIR/repair-pg-start-$attempt.log" 2>/dev/null)
    if echo "$LAST" | grep -qi "already exists"; then
      warn "→ 이미 같은 이름 인스턴스 존재 (idempotency) — stop 후 재시도"
      apptainer instance stop -F "$INST_POSTGRES" 2>/dev/null || true
      sleep 2
    elif echo "$LAST" | grep -qi "permission denied"; then
      warn "→ 권한 문제 감지 — chown 강제 재시도"
      find "$DATA_DIR" -not -user "$ME" -exec chown -h "$ME":"$ME" {} + 2>/dev/null || \
        sudo find "$DATA_DIR" -not -user "$ME" -exec chown -h "$ME":"$ME" {} + 2>/dev/null || true
    fi
  fi

  # apptainer 인스턴스 로그도 확인
  APPT_LOG=$(apptainer logs "$INST_POSTGRES" 2>&1 | tail -10 || true)
  if echo "$APPT_LOG" | grep -qi "database directory.*not empty\|Permission denied\|chown.*Operation not permitted"; then
    warn "→ 데이터 디렉토리 충돌/권한 감지"
    if [[ $attempt -ge 2 && $KEEP_DATA -eq 0 ]]; then
      warn "→ 시도 #$attempt 이후 — data 디렉토리 강제 초기화 (DB 데이터 손실)"
      if [[ $AUTO -eq 1 ]] || confirm; then
        apptainer instance stop -F "$INST_POSTGRES" 2>/dev/null || true
        sleep 2
        rm -rf "$DATA_DIR/postgres" "$DATA_DIR/postgres-run"
        mkdir -p "$DATA_DIR/postgres" "$DATA_DIR/postgres-run"
        chmod 700 "$DATA_DIR/postgres"
        chmod 777 "$DATA_DIR/postgres-run"
        ok "data 디렉토리 초기화"
      else
        fail "사용자 거절 — repair 중단"
        exit 1
      fi
    fi
  fi
  apptainer instance stop -F "$INST_POSTGRES" 2>/dev/null || true
  sleep 2
done

if [[ $PG_OK -eq 0 ]]; then
  fail "postgres 3회 시도 모두 실패"
  hint "마지막 로그:"
  ls "$LOG_DIR"/repair-pg-start-*.log 2>/dev/null | tail -1 | xargs -I {} tail -20 {} | sed 's/^/      /'
  hint "apptainer logs $INST_POSTGRES | tail -30"
  exit 1
fi

# ===================================================================
phase 6 "API 서버 기동 (venv + alembic + uvicorn)"
# ===================================================================

cd "$ROOT_DIR/api_server"

# venv
if [[ ! -d .venv ]]; then
  info "venv 생성"
  [[ $DRY -eq 0 ]] && python3 -m venv .venv
fi
[[ $DRY -eq 0 ]] && source .venv/bin/activate

# pip install
info "pip install -r requirements.txt"
if [[ $DRY -eq 0 ]]; then
  python -m pip install --upgrade pip > "$LOG_DIR/repair-pip.log" 2>&1
  if ! python -m pip install -r requirements.txt >> "$LOG_DIR/repair-pip.log" 2>&1; then
    fail "pip install 실패"
    hint "로그: tail -30 $LOG_DIR/repair-pip.log"
    hint "프록시: cat $APPT_DIR/.env | grep PROXY"
    exit 1
  fi

  # embedder 패키지
  case "${EMBEDDING_PROVIDER:-hash}" in
    e5_*|sentence_transformers|st|sbert)
      python -m pip install "sentence-transformers>=3.0" >> "$LOG_DIR/repair-pip.log" 2>&1 || warn "sentence-transformers 설치 실패"
      ;;
    openai)
      python -m pip install "openai>=1.0" >> "$LOG_DIR/repair-pip.log" 2>&1 || warn "openai 설치 실패"
      ;;
  esac
fi
ok "deps 설치"

# alembic
case "${EMBEDDING_PROVIDER:-hash}" in
  e5_base)  export EMBEDDING_DIM="${EMBEDDING_DIM:-768}" ;;
  e5_large) export EMBEDDING_DIM="${EMBEDDING_DIM:-1024}" ;;
  *)        export EMBEDDING_DIM="${EMBEDDING_DIM:-384}" ;;
esac
export PYTHONPATH="$ROOT_DIR/api_server/src"
export POSTGRES_USER POSTGRES_PASSWORD POSTGRES_PORT POSTGRES_DB

info "alembic upgrade head (EMBEDDING_DIM=$EMBEDDING_DIM)"
if [[ $DRY -eq 0 ]]; then
  if ! alembic upgrade head > "$LOG_DIR/repair-alembic.log" 2>&1; then
    fail "alembic 실패"
    hint "로그: tail -30 $LOG_DIR/repair-alembic.log"
    # 흔한 원인: DB 존재하는데 alembic_version 만 빠짐
    if grep -qi "DuplicateTable\|already exists" "$LOG_DIR/repair-alembic.log"; then
      warn "→ 테이블 충돌 — 데이터 초기화 필요"
      if [[ $KEEP_DATA -eq 0 ]] && ([[ $AUTO -eq 1 ]] || confirm); then
        bash "$APPT_DIR/reset-db.sh" 2>&1 | tail -10 || true
        alembic upgrade head > "$LOG_DIR/repair-alembic.log" 2>&1 || { fail "재시도도 실패"; exit 1; }
      else
        exit 1
      fi
    else
      exit 1
    fi
  fi
fi
ok "alembic upgrade head"

# uvicorn 백그라운드
info "uvicorn 백그라운드 기동"
if [[ $DRY -eq 0 ]]; then
  cd "$ROOT_DIR/api_server"
  nohup .venv/bin/uvicorn api.main:app \
    --host "${API_HOST:-0.0.0.0}" --port "$API_PORT" \
    --proxy-headers --forwarded-allow-ips='*' \
    > "$LOG_DIR/api.log" 2>&1 &
  echo $! > "$LOG_DIR/api.pid"
  sleep 3
fi

# health check (10초 대기)
API_OK=0
if [[ $DRY -eq 0 ]]; then
  for i in $(seq 1 10); do
    if curl -s --max-time 2 "http://127.0.0.1:${API_PORT}/api/system/health" >/dev/null 2>&1; then
      API_OK=1; break
    fi
    sleep 1
  done
  if [[ $API_OK -eq 1 ]]; then
    ok "api health 200 OK"
  else
    fail "api 응답 없음"
    hint "로그: tail -30 $LOG_DIR/api.log"
    exit 1
  fi
fi

# ===================================================================
phase 7 "최종 검증"
# ===================================================================

if [[ $DRY -eq 0 ]]; then
  bash "$APPT_DIR/diag.sh" || true
fi

echo
echo "════════════════════════════════════════════════════════════════"
if [[ $DRY -eq 1 ]]; then
  echo " ✓ DIAGNOSE 완료 — 위 항목 중 ✗ 발견된 것 보고 조치"
elif [[ $PG_OK -eq 1 && $API_OK -eq 1 ]]; then
  printf "${G} ✓ repair 완료${N} — 정상 동작 상태\n"
else
  printf "${R} ✗ 일부 실패${N} — 로그 확인:\n"
  echo "    $LOG_DIR/*.log"
fi
echo "════════════════════════════════════════════════════════════════"
echo
echo "다음 단계:"
HOST_IP=$(grep '^HOST_IP=' "$APPT_DIR/.env" 2>/dev/null | cut -d= -f2 || echo "127.0.0.1")
echo "  Dashboard:  http://${HOST_IP}:${API_PORT}/dashboard/"
echo "  Health:     curl http://${HOST_IP}:${API_PORT}/api/system/health"
echo "  Status:     bash deploy/apptainer/status.sh"
echo "  Diag:       bash deploy/apptainer/diag.sh --tail-logs"

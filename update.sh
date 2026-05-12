#!/usr/bin/env bash
# AI Data Hub — 기존 설치 업데이트 (systemd 자동 통합).
#
# 동작 우선순위 (자동 감지):
#   1. systemd 사용자 모드 (aidh.service) → systemctl --user 로 stop/start
#   2. systemd 시스템 모드 → sudo systemctl 로 stop/start
#   3. 둘 다 없음 → boot.sh / stop.sh 직접 호출
#
# 업데이트 흐름 (어떤 모드든 동일):
#   1. 잠금 (concurrent update 방지)
#   2. 서비스 정지 (systemd 알아서 또는 stop.sh)
#   3. postgres 만 다시 기동 (alembic 마이그레이션 위해)
#   4. git pull → pip install → alembic upgrade head
#   5. 서비스 기동 (systemd 가 idempotent — PG 는 skip 되고 API 만 새로)
#   6. health verify (10초 timeout)
#   7. 실패 시 명확한 rollback hint
#
# 사용:
#   bash update.sh                  # 전체 (권장)
#   bash update.sh --pull-only      # git pull 만 (서비스 영향 X)
#   bash update.sh --skip-deps      # pip install 건너뜀 (코드만 바뀌었을 때)
#   bash update.sh --no-migrate     # alembic skip
#   bash update.sh --force-unlock   # 잠금 강제 해제 후 진행
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPT_DIR="$ROOT_DIR/deploy/apptainer"
API_DIR="$ROOT_DIR/api_server"
LOG_DIR="$APPT_DIR/logs"
LOCK="/tmp/aidh-update.lock"

mkdir -p "$LOG_DIR"

PULL_ONLY=0
SKIP_DEPS=0
NO_MIGRATE=0
FORCE_UNLOCK=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pull-only)     PULL_ONLY=1; shift ;;
    --skip-deps)     SKIP_DEPS=1; shift ;;
    --no-migrate)    NO_MIGRATE=1; shift ;;
    --force-unlock)  FORCE_UNLOCK=1; shift ;;
    -h|--help)       sed -n '2,21p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "[ERROR] unknown arg: $1" >&2; exit 2 ;;
  esac
done

# ── 색상 / 로깅 ──────────────────────────────────────────────────
if [[ -t 1 ]]; then G="\033[32m"; R="\033[31m"; Y="\033[33m"; B="\033[34m"; N="\033[0m"
else G=""; R=""; Y=""; B=""; N=""; fi
ok()   { printf "  ${G}✓${N} %s\n" "$*"; }
fail() { printf "  ${R}✗${N} %s\n" "$*"; }
warn() { printf "  ${Y}!${N} %s\n" "$*"; }
info() { printf "  ${B}·${N} %s\n" "$*"; }

# ── systemd 모드 자동 감지 ───────────────────────────────────────
SYSMODE="none"
if systemctl --user is-enabled aidh.service >/dev/null 2>&1; then
  SYSMODE="user"
elif systemctl is-enabled aidh.service >/dev/null 2>&1; then
  SYSMODE="system"
fi

svc_stop() {
  case "$SYSMODE" in
    user)   info "systemctl --user stop aidh.service"
            systemctl --user stop aidh.service 2>&1 | sed 's/^/    /' || true ;;
    system) info "sudo systemctl stop aidh.service"
            sudo systemctl stop aidh.service 2>&1 | sed 's/^/    /' || true ;;
    none)   info "bash deploy/apptainer/stop.sh"
            bash "$APPT_DIR/stop.sh" 2>&1 | sed 's/^/    /' || true ;;
  esac
}

svc_start() {
  case "$SYSMODE" in
    user)   info "systemctl --user start aidh.service"
            systemctl --user start aidh.service ;;
    system) info "sudo systemctl start aidh.service"
            sudo systemctl start aidh.service ;;
    none)   info "bash deploy/apptainer/boot.sh"
            bash "$APPT_DIR/boot.sh" ;;
  esac
}

# ── lock ──────────────────────────────────────────────────────
acquire_lock() {
  if [[ -f "$LOCK" ]]; then
    if [[ $FORCE_UNLOCK -eq 1 ]]; then
      warn "기존 lock 강제 해제 ($(cat "$LOCK" 2>/dev/null))"
      rm -f "$LOCK"
    else
      OWNER=$(cat "$LOCK" 2>/dev/null || echo "?")
      fail "이미 다른 update 진행 중 (lock=$OWNER)"
      fail "강제 해제: bash update.sh --force-unlock"
      exit 1
    fi
  fi
  echo "$(date '+%F %T') pid=$$" > "$LOCK"
  trap 'rm -f "$LOCK"' EXIT
}

# ── main ──────────────────────────────────────────────────────
echo "════════════════════════════════════════════════════════════════"
echo " AI Data Hub — update"
echo " systemd mode: $SYSMODE"
echo "════════════════════════════════════════════════════════════════"

acquire_lock

# [1] git pull
echo
echo "[1/6] git pull"
if [[ -d "$ROOT_DIR/.git" ]]; then
  cd "$ROOT_DIR"
  BEFORE=$(git rev-parse HEAD 2>/dev/null || echo "")
  if git pull --ff-only 2>&1 | sed 's/^/    /' | tail -5; then
    AFTER=$(git rev-parse HEAD 2>/dev/null || echo "")
    if [[ "$BEFORE" == "$AFTER" ]]; then
      info "이미 최신 — 변경 없음"
      if [[ $PULL_ONLY -eq 1 ]] || [[ "${SKIP_IF_NO_CHANGE:-0}" -eq 1 ]]; then
        ok "변경 없음 — 종료 (서비스 영향 X)"
        exit 0
      fi
    else
      ok "$BEFORE → $AFTER"
      git log --oneline "$BEFORE..$AFTER" 2>&1 | head -5 | sed 's/^/    /'
    fi
  else
    fail "git pull 실패 (네트워크 / merge conflict / dirty tree?)"
    exit 1
  fi
else
  warn ".git 없음 — bundle 기반 설치. 코드 갱신은 새 bundle 받아 install.sh"
fi

if [[ $PULL_ONLY -eq 1 ]]; then
  ok "--pull-only — 종료 (서비스 영향 X)"
  exit 0
fi

# [2] 서비스 정지
echo
echo "[2/6] 서비스 정지 (mode=$SYSMODE)"
svc_stop
sleep 2

# [3] postgres 만 임시 기동 (alembic 위해)
if [[ $NO_MIGRATE -eq 0 ]]; then
  echo
  echo "[3/6] postgres 임시 기동 (alembic 위해)"
  bash "$APPT_DIR/start_postgres.sh" 2>&1 | sed 's/^/    /' | tail -10
fi

# [4] deps + alembic
echo
echo "[4/6] 의존성 + 마이그레이션"
VENV_PY="$API_DIR/.venv/bin/python"

# pip install
if [[ $SKIP_DEPS -eq 1 ]]; then
  info "--skip-deps — pip 건너뜀"
else
  if [[ ! -x "$VENV_PY" ]]; then
    fail "$VENV_PY 없음 — start_api.sh 먼저 한 번 실행 필요"
    exit 1
  fi
  info "pip install -r requirements.txt"
  if ! "$VENV_PY" -m pip install -r "$API_DIR/requirements.txt" \
       > "$LOG_DIR/update-pip.log" 2>&1; then
    fail "pip install 실패 — tail $LOG_DIR/update-pip.log"
    exit 1
  fi
  ok "deps OK"
fi

# alembic
if [[ $NO_MIGRATE -eq 1 ]]; then
  info "--no-migrate — alembic 건너뜀"
else
  if [[ -f "$APPT_DIR/.env" ]]; then
    set -a; . "$APPT_DIR/.env" 2>/dev/null || true; set +a
  fi
  case "${EMBEDDING_PROVIDER:-hash}" in
    e5_base)  export EMBEDDING_DIM="${EMBEDDING_DIM:-768}" ;;
    e5_large) export EMBEDDING_DIM="${EMBEDDING_DIM:-1024}" ;;
    *)        export EMBEDDING_DIM="${EMBEDDING_DIM:-384}" ;;
  esac
  export PYTHONPATH="$API_DIR/src"
  cd "$API_DIR"
  info "alembic upgrade head (EMBEDDING_DIM=$EMBEDDING_DIM)"
  if ! "$VENV_PY" -m alembic upgrade head > "$LOG_DIR/update-alembic.log" 2>&1; then
    fail "alembic 실패 — tail $LOG_DIR/update-alembic.log"
    fail "rollback hint: git reset --hard $BEFORE (코드 되돌리고 다시 시도)"
    exit 1
  fi
  ok "alembic upgrade head"
  cd "$ROOT_DIR"
fi

# [5] 서비스 기동
echo
echo "[5/6] 서비스 기동 (mode=$SYSMODE)"
svc_start

# [6] health verify
echo
echo "[6/6] 검증"
API_PORT="${API_PORT:-8001}"
OK=0
for i in $(seq 1 15); do
  if curl -s --max-time 2 "http://127.0.0.1:${API_PORT}/api/system/health" >/dev/null 2>&1; then
    ok "api health 200 OK (${i}s)"
    OK=1; break
  fi
  sleep 1
done

if [[ $OK -eq 0 ]]; then
  fail "api 응답 없음 (15초 timeout)"
  fail "로그 확인: tail -30 $LOG_DIR/api.log"
  fail "서비스 상태:"
  case "$SYSMODE" in
    user)   systemctl --user status aidh.service --no-pager 2>&1 | head -15 ;;
    system) sudo systemctl status aidh.service --no-pager 2>&1 | head -15 ;;
    none)   bash "$APPT_DIR/status.sh" 2>&1 || true ;;
  esac
  echo
  fail "rollback 절차:"
  if [[ -n "${BEFORE:-}" ]]; then
    fail "  git reset --hard $BEFORE"
  fi
  fail "  bash update.sh --skip-deps --no-migrate  (이전 코드 + 서비스 재기동)"
  exit 1
fi

echo
echo "════════════════════════════════════════════════════════════════"
ok "update 완료 — 정상 동작 중"
echo "════════════════════════════════════════════════════════════════"
bash "$APPT_DIR/status.sh" 2>&1 || true

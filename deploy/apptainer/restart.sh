#!/usr/bin/env bash
# AI Data Hub — stop + start 재기동 (Issue #11 instance idempotency).
#
# 왜 필요한가:
#   - apptainer instance start 는 같은 이름 인스턴스 있으면 새로 안 만든다 (idempotent).
#   - 따라서 .env 또는 start_*.sh 변경 후 start_postgres.sh 다시 돌려도 변경 안 반영.
#   - 환경변수 / bind 변경은 instance lifecycle 동안 mutable 아님 — 재기동 필수.
#
# 사용:
#   bash deploy/apptainer/restart.sh           # postgres + api 둘 다
#   bash deploy/apptainer/restart.sh --pg      # postgres 만
#   bash deploy/apptainer/restart.sh --api     # api 만 (PG 는 그대로)
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

DO_PG=1
DO_API=1
case "${1:-}" in
  --pg)   DO_API=0 ;;
  --api)  DO_PG=0 ;;
  ""|--all) ;;
  *) echo "usage: restart.sh [--pg|--api|--all]"; exit 2 ;;
esac

echo "================================================================"
echo " AI Data Hub — restart (pg=$DO_PG api=$DO_API)"
echo "================================================================"

# 1) API 먼저 정지 (PG 의존성).
if [[ $DO_API -eq 1 && -f "$LOG_DIR/api.pid" ]]; then
  PID=$(cat "$LOG_DIR/api.pid" 2>/dev/null || echo "")
  if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "→ stop api (pid=$PID)"
    kill "$PID" || true
    # graceful wait
    for i in 1 2 3 4 5; do
      if ! kill -0 "$PID" 2>/dev/null; then break; fi
      sleep 1
    done
    if kill -0 "$PID" 2>/dev/null; then
      echo "  (kill -TERM 무응답 — kill -9)"
      kill -9 "$PID" || true
    fi
  fi
  rm -f "$LOG_DIR/api.pid"
fi

# 2) PG 정지 + 포트 release 대기.
if [[ $DO_PG -eq 1 ]] && instance_running "$INST_POSTGRES"; then
  echo "→ stop $INST_POSTGRES"
  apptainer instance stop "$INST_POSTGRES" || true
  echo "→ wait port $POSTGRES_PORT release..."
  for i in $(seq 1 20); do
    if ! ss -tnl 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${POSTGRES_PORT}\$"; then
      echo "  ✓ port free (${i}s)"
      break
    fi
    sleep 1
  done
fi

# 3) 재기동 (start_postgres.sh / start_api.sh 의 정상 흐름 그대로).
if [[ $DO_PG -eq 1 ]]; then
  bash "$APPT_DIR/start_postgres.sh"
fi
if [[ $DO_API -eq 1 ]]; then
  bash "$APPT_DIR/start_api.sh"
fi

echo
echo "✓ 재기동 완료. diag.sh 로 상태 확인:"
echo "    bash deploy/apptainer/diag.sh"

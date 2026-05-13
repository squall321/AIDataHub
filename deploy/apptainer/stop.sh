#!/usr/bin/env bash
# AI Data Hub — Apptainer instance + API 정지
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

# API
if [[ -f "$LOG_DIR/api.pid" ]]; then
  PID=$(cat "$LOG_DIR/api.pid")
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID" && echo "✓ api uvicorn (pid=$PID) 종료"
  else
    echo "  (api pid $PID 이미 죽음)"
  fi
  rm -f "$LOG_DIR/api.pid"
fi

# PG instance — EXTERNAL 모드면 절대 안 멈춤 (다른 프로젝트 거니까)
if [[ "${EXTERNAL_POSTGRES:-0}" = "1" ]]; then
  echo "  (EXTERNAL_POSTGRES=1 — 외부 PG 는 그대로 두고 종료)"
elif instance_running "$INST_POSTGRES"; then
  apptainer instance stop "$INST_POSTGRES" && echo "✓ $INST_POSTGRES 정지"
else
  echo "  ($INST_POSTGRES 이미 정지)"
fi

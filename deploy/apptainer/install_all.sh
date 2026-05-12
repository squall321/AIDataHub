#!/usr/bin/env bash
# AI Data Hub — Apptainer 한방 셋업
# 새 머신:
#   cd deploy/apptainer
#   cp .env.example .env      # 프록시/포트 조정 (선택)
#   bash install_all.sh
set -euo pipefail
APPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APPT_DIR"

# shellcheck source=/dev/null
source "$APPT_DIR/_common.sh"
load_env
export_proxy

echo "================================================================"
echo " AI Data Hub — Apptainer one-shot setup"
echo "================================================================"
require_apptainer
require_python_venv
require_disk
ensure_dirs

echo
echo "[1/3] SIF 빌드"
bash "$APPT_DIR/build.sh"

echo
echo "[2/3] Postgres 기동"
bash "$APPT_DIR/start_postgres.sh"

echo
echo "[3/3] API 기동"
bash "$APPT_DIR/start_api.sh"

echo
echo "✓ 셋업 완료"

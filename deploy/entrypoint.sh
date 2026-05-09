#!/usr/bin/env bash
# ===========================================================================
# AI Data Hub API — 컨테이너 진입점
#
# 1) alembic upgrade head        : DB 스키마 최신화 (멱등)
# 2) python -m api.seed          : 표준 에이전트 시드 (멱등)
# 3) uvicorn api.main:app        : API 서버 기동
# ===========================================================================
set -euo pipefail

cd /app/api_server

echo ""
echo "================================================================"
echo " AI Data Hub API — entrypoint"
echo "================================================================"
echo " DATABASE_URL     : ${DATABASE_URL:-<unset>}"
echo " EMBEDDING_PROVIDER: ${EMBEDDING_PROVIDER:-hash}"
echo " AUTH_REQUIRED    : ${AUTH_REQUIRED:-false}"
echo " ATTACHMENTS_DIR  : ${ATTACHMENTS_DIR:-attachments}"
echo " FIGURES_DIR      : ${FIGURES_DIR:-figures}"
echo "================================================================"

# 데이터 디렉터리 보장 (volume 마운트 후 비어 있어도 안전)
mkdir -p "${ATTACHMENTS_DIR:-/data/attachments}" "${FIGURES_DIR:-/data/figures}" || true

echo "[1/3] Alembic upgrade head..."
alembic upgrade head

echo "[2/3] Agent seed (멱등 upsert)..."
# 시드 실패는 치명적이지 않다 (이미 적용됐을 수 있음). 로그만 남기고 진행.
python -m api.seed -v || echo "[WARN] agent seed 실패 — 무시하고 진행"

echo "[3/3] uvicorn 기동 (host=0.0.0.0 port=${API_PORT:-8000})..."
exec uvicorn api.main:app \
    --host 0.0.0.0 \
    --port "${API_PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips="*"

#!/usr/bin/env bash
# AI Data Hub — API 서버를 native venv 로 기동 (Apptainer 없이).
# PG 는 Apptainer instance(start_postgres.sh) 가 제공.
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env
export_proxy
require_python_venv
ensure_dirs

DB_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT}/${POSTGRES_DB}"
ATTACH_DIR="$DATA_DIR/attachments"
FIG_DIR="$DATA_DIR/figures"

# api_server/.env 갱신 — 비ापtainer entrypoint 와 호환되는 키 세트.
cat > "$API_DIR/.env" <<EOF
DATABASE_URL=$DB_URL
API_HOST=$API_HOST
API_PORT=$API_PORT
API_RELOAD=false
AUTH_REQUIRED=$AUTH_REQUIRED
BOOTSTRAP_API_KEY=$BOOTSTRAP_API_KEY
LOG_LEVEL=$LOG_LEVEL
LOG_FORMAT=$LOG_FORMAT
EMBEDDING_PROVIDER=$EMBEDDING_PROVIDER
EMBEDDING_DIM=${EMBEDDING_DIM:-384}
AUTO_EMBED_ON_INSERT=$AUTO_EMBED_ON_INSERT
ATTACHMENTS_DIR=$ATTACH_DIR
FIGURES_DIR=$FIG_DIR
BUILD_SHA=$BUILD_SHA
# /api/ask LLM 모드 (선택, OpenAI 호환 백엔드도 지원)
OPENAI_API_KEY=${OPENAI_API_KEY:-}
OPENAI_BASE_URL=${OPENAI_BASE_URL:-}
OPENAI_ASK_MODEL=${OPENAI_ASK_MODEL:-}
EOF

cd "$API_DIR"
if [[ ! -d .venv ]]; then
  echo "→ create .venv ($($PYBIN --version))"
  "$PYBIN" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# pip 은 HTTP_PROXY/HTTPS_PROXY 환경변수를 자동 사용
echo "→ pip install -r requirements.txt (로그: $LOG_DIR/pip.log)"
python -m pip install --upgrade pip   > "$LOG_DIR/pip.log" 2>&1
python -m pip install -r requirements.txt >> "$LOG_DIR/pip.log" 2>&1

# 선택 임베더 — .env 의 EMBEDDING_PROVIDER 값에 따라 추가 패키지 자동 설치 +
# EMBEDDING_DIM 자동 매핑 (alembic 0013 의 vector(NNN) 컬럼과 정합 필요).
case "${EMBEDDING_PROVIDER:-hash}" in
  e5_small|e5_base|e5_large|sentence_transformers|st|sbert)
    if ! python -c "import sentence_transformers" 2>/dev/null; then
      echo "→ pip install sentence-transformers (${EMBEDDING_PROVIDER} 임베더용)"
      python -m pip install "sentence-transformers>=3.0" >> "$LOG_DIR/pip.log" 2>&1
    fi
    ;;
  openai)
    if ! python -c "import openai" 2>/dev/null; then
      echo "→ pip install openai (openai 임베더용)"
      python -m pip install "openai>=1.0" >> "$LOG_DIR/pip.log" 2>&1
    fi
    ;;
esac

# provider → dim 자동 매핑 (.env 에 EMBEDDING_DIM 명시값이 있으면 그것 우선)
case "${EMBEDDING_PROVIDER:-hash}" in
  e5_base)   AUTO_DIM=768 ;;
  e5_large)  AUTO_DIM=1024 ;;
  *)         AUTO_DIM=384 ;;  # hash / e5_small / openai / sentence_transformers
esac
export EMBEDDING_DIM="${EMBEDDING_DIM:-$AUTO_DIM}"

echo "  ✓ deps installed (embedder=${EMBEDDING_PROVIDER:-hash}, dim=${EMBEDDING_DIM})"

export PYTHONPATH="$API_DIR/src"

echo "→ alembic upgrade head"
alembic upgrade head > "$LOG_DIR/alembic.log" 2>&1

echo "→ seed agents (멱등)"
python -m api.seed -v > "$LOG_DIR/seed.log" 2>&1 || echo "  [WARN] seed 실패 — 무시"

# 기존 uvicorn 종료
if [[ -f "$LOG_DIR/api.pid" ]] && kill -0 "$(cat "$LOG_DIR/api.pid")" 2>/dev/null; then
  echo "  (기존 uvicorn 종료)"
  kill "$(cat "$LOG_DIR/api.pid")" || true
  sleep 1
fi

# 새로 띄우기 전 포트 확인 (혹시 외부 프로세스가 잡았다면 명시적 에러)
require_port_free "$API_PORT" "API"

echo "→ uvicorn api.main:app --host $API_HOST --port $API_PORT (백그라운드)"
nohup uvicorn api.main:app \
    --host "$API_HOST" --port "$API_PORT" \
    --proxy-headers --forwarded-allow-ips="*" \
    > "$LOG_DIR/uvicorn.log" 2>&1 &
echo $! > "$LOG_DIR/api.pid"
echo "  pid=$(cat "$LOG_DIR/api.pid")"

echo "→ /api/system/health 대기 (최대 60s)"
HEALTH="http://127.0.0.1:$API_PORT/api/system/health"
SUCCESS=0
for i in $(seq 1 30); do
  if curl -sf -o /dev/null "$HEALTH"; then
    echo "✓ API healthy (${i}회 시도)"
    SUCCESS=1
    break
  fi
  sleep 2
done
[ "$SUCCESS" -eq 1 ] || {
  echo "[ERROR] 60s 안에 응답 없음. tail $LOG_DIR/uvicorn.log" >&2
  exit 1
}

echo
echo "================================================================"
echo " API : $HEALTH"
echo " docs: http://127.0.0.1:$API_PORT/docs"
echo " 로그: tail -f $LOG_DIR/uvicorn.log"
echo " 종료: bash $APPT_DIR/stop.sh"
echo "================================================================"

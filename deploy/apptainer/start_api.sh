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
# 모델이 로컬 캐시에 있으면 HuggingFace 네트워크 확인 생략 (MCP 첫 호출 블로킹 방지)
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
EOF

cd "$API_DIR"
# venv 건강성 — .venv/bin/python 이 실제로 실행되는지 확인. 다른 머신에서
# 만든/깨진 venv 면 재생성. (새 서버: 'python' 명령은 없고 python3 만 있어
# bare `python` 의존이 깨지므로, 이후 모든 호출은 절대경로 $VENV_PY 사용.)
VENV_PY="$API_DIR/.venv/bin/python"
venv_healthy() { [[ -x "$VENV_PY" ]] && "$VENV_PY" -c 'import sys' >/dev/null 2>&1; }
if ! venv_healthy; then
  echo "→ create .venv ($("$PYBIN" --version))"
  rm -rf .venv
  "$PYBIN" -m venv .venv
  venv_healthy || {
    echo "[ERROR] venv 생성 실패 — python3.12-venv 패키지 필요할 수 있음:" >&2
    echo "        sudo apt install -y python3.12-venv  (또는 sudo bash bootstrap.sh)" >&2
    exit 1
  }
fi
# shellcheck disable=SC1091
source .venv/bin/activate 2>/dev/null || true

# pip 은 HTTP_PROXY/HTTPS_PROXY 환경변수를 자동 사용.
# set -euo pipefail 하에서 출력이 pip.log 로 리다이렉트돼 있어, 실패 시
# 에러 한 줄 없이 스크립트가 죽는 문제가 있었다 (새 서버에서 PyPI/프록시
# 미도달이 가장 흔함). 실패를 반드시 표면화한다.
echo "→ pip install -r requirements.txt (로그: $LOG_DIR/pip.log)"
_pip_fail() {
  echo >&2
  echo "[ERROR] pip install 실패 — 새 서버에서는 보통 PyPI/프록시 미도달입니다." >&2
  echo "        로그 마지막 30줄 ($LOG_DIR/pip.log):" >&2
  tail -30 "$LOG_DIR/pip.log" 2>/dev/null | sed 's/^/    /' >&2
  echo >&2
  echo "        점검:" >&2
  echo "          - 인터넷/프록시: .env 의 HTTPS_PROXY/BUILD_PROXY_HTTPS (사내망)" >&2
  echo "          - 외부망이면  : .env 에 BUILD_PROXY_HTTPS=off" >&2
  echo "          - 오프라인이면: requirements 휠을 사전 stage 후 PIP_FIND_LINKS 사용" >&2
  exit 1
}
# MXWP build.sh 의 _run_with_fallback 패턴을 호스트 pip 에 이식.
# 1차: 현재 env (export_proxy 가 강제한 사내 폴백 프록시일 수 있음)
# 2차: BUILD_PROXY 명시 재시도 (사내망)
# 3차: 프록시 완전 제거 재시도 (외부망 직결 서버 — 강제된 사내 프록시가
#      오히려 pip 를 막는 케이스. MXWP 가 사내망이라 안 겪던 상황).
_pip() {
  # $@ = pip 인자
  if "$VENV_PY" -m pip "$@" >> "$LOG_DIR/pip.log" 2>&1; then return 0; fi
  local fb_https="${BUILD_PROXY_HTTPS:-}"
  local fb_http="${BUILD_PROXY_HTTP:-${BUILD_PROXY_HTTPS:-}}"
  if [[ -n "$fb_https" && "$fb_https" != "off" ]]; then
    echo "  [WARN] pip 1차 실패 — BUILD_PROXY 재시도 ($fb_https)" >&2
    if env HTTPS_PROXY="$fb_https" https_proxy="$fb_https" \
           HTTP_PROXY="$fb_http" http_proxy="$fb_http" \
           "$VENV_PY" -m pip "$@" >> "$LOG_DIR/pip.log" 2>&1; then return 0; fi
  fi
  echo "  [WARN] pip 재시도 실패 — 프록시 제거 후 직결 재시도" >&2
  if env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy \
         "$VENV_PY" -m pip "$@" >> "$LOG_DIR/pip.log" 2>&1; then return 0; fi
  return 1
}
: > "$LOG_DIR/pip.log"
_pip install --upgrade pip || _pip_fail
_pip install -r requirements.txt || _pip_fail

# 선택 임베더 — .env 의 EMBEDDING_PROVIDER 값에 따라 추가 패키지 자동 설치 +
# EMBEDDING_DIM 자동 매핑 (alembic 0013 의 vector(NNN) 컬럼과 정합 필요).
case "${EMBEDDING_PROVIDER:-hash}" in
  e5_small|e5_base|e5_large|sentence_transformers|st|sbert)
    if ! "$VENV_PY" -c "import sentence_transformers" 2>/dev/null; then
      echo "→ pip install sentence-transformers (${EMBEDDING_PROVIDER} 임베더용)"
      "$VENV_PY" -m pip install "sentence-transformers>=3.0" >> "$LOG_DIR/pip.log" 2>&1
    fi
    ;;
  openai)
    if ! "$VENV_PY" -c "import openai" 2>/dev/null; then
      echo "→ pip install openai (openai 임베더용)"
      "$VENV_PY" -m pip install "openai>=1.0" >> "$LOG_DIR/pip.log" 2>&1
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
"$VENV_PY" -m alembic upgrade head > "$LOG_DIR/alembic.log" 2>&1

echo "→ seed agents (멱등)"
"$VENV_PY" -m api.seed -v > "$LOG_DIR/seed.log" 2>&1 || echo "  [WARN] seed 실패 — 무시"

# 기존 uvicorn 종료
if [[ -f "$LOG_DIR/api.pid" ]] && kill -0 "$(cat "$LOG_DIR/api.pid")" 2>/dev/null; then
  echo "  (기존 uvicorn 종료)"
  kill "$(cat "$LOG_DIR/api.pid")" || true
  sleep 1
fi

# 새로 띄우기 전 포트 확인 (혹시 외부 프로세스가 잡았다면 명시적 에러)
require_port_free "$API_PORT" "API"

echo "→ uvicorn api.main:app --host $API_HOST --port $API_PORT (백그라운드)"
# HuggingFace 모델이 로컬 캐시에 있으면 네트워크 확인을 건너뛴다.
# MCP 첫 호출 시 수십 초 블로킹을 막기 위해 프로세스 환경에 직접 export.
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
nohup "$VENV_PY" -m uvicorn api.main:app \
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

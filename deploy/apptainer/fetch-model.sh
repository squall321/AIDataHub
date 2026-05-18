#!/usr/bin/env bash
# AI Data Hub — 임베딩 모델 자동 다운로드/검증 (인터넷 되는 서버에서 1회).
#
# 왜: e5_* provider 는 HF 모델(수백MB~1.1GB)이 ~/.cache/huggingface 에
#     있어야 한다. 없으면 recommend/agent_search 가 internal_error.
#     이 스크립트를 인터넷 되는 (개발)서버에서 돌리면 캐시가 생기고,
#     그 캐시를 bundle.sh --with-model 로 폐쇄망 서버에 옮길 수 있다.
#
# 동작:
#   - .env 의 EMBEDDING_PROVIDER 를 보고 hash/openai 면 받을 모델 없음(skip).
#   - e5_*/sentence_transformers 면 venv python 으로 *런타임과 동일한*
#     get_embedder() 를 호출 → SentenceTransformer 가 모델을 캐시에 받음
#     → warmup encode 로 차원까지 검증 (매핑을 bash 에서 재구현 안 함 = drift 0).
#   - 다운로드를 위해 HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE 는 강제 해제.
#   - sentence-transformers 미설치면 venv 에 설치 (프록시 인지).
#
# 사용:
#   bash deploy/apptainer/fetch-model.sh         # .env provider 기준
#   EMBEDDING_PROVIDER=e5_base bash deploy/apptainer/fetch-model.sh   # 강제 지정
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env
export_proxy   # 사내망이면 프록시 통해 다운로드

# 옵션 — 건너뛰기 (모든 호출자가 이 게이트를 자동으로 따른다).
#   bash fetch-model.sh --skip        또는  AIDH_SKIP_MODEL=1
if [[ "${1:-}" = "--skip" || "${AIDH_SKIP_MODEL:-0}" = "1" ]]; then
  echo "[INFO] 임베딩 모델 셋업 skip (--skip / AIDH_SKIP_MODEL=1)."
  echo "       나중에 수동: bash deploy/apptainer/fetch-model.sh"
  exit 0
fi

PROV="${EMBEDDING_PROVIDER:-hash}"
case "$PROV" in
  hash|""|openai)
    echo "[INFO] EMBEDDING_PROVIDER=$PROV — 받을 HF 모델 없음 (skip)."
    exit 0 ;;
esac

VENV_PY="$API_DIR/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  echo "[ERROR] venv 없음 ($VENV_PY) — 먼저 한 번:" >&2
  echo "        bash deploy/apptainer/start_api.sh   (또는 bash setup.sh)" >&2
  exit 1
fi

# sentence-transformers 보장 (e5_* 는 필수). 없으면 venv 에 설치.
if ! "$VENV_PY" -c "import sentence_transformers" >/dev/null 2>&1; then
  echo "→ sentence-transformers 설치 (venv, 프록시 인지)"
  if ! "$VENV_PY" -m pip install "sentence-transformers>=3.0" \
       > "$LOG_DIR/fetch-model-pip.log" 2>&1; then
    echo "[ERROR] sentence-transformers 설치 실패 — tail $LOG_DIR/fetch-model-pip.log" >&2
    tail -15 "$LOG_DIR/fetch-model-pip.log" | sed 's/^/    /' >&2
    exit 1
  fi
fi

export PYTHONPATH="$API_DIR/src"
_warm() {  # $1 = offline(1/0). get_embedder() 로 로드+encode 검증.
  HF_HUB_OFFLINE="$1" TRANSFORMERS_OFFLINE="$1" "$VENV_PY" - <<'PY'
import time
from api.services.embedding import get_embedder
t0 = time.time()
e = get_embedder()
name = getattr(e, "_model_name", e.__class__.__name__)
dim = len(e.encode("warmup connectivity check"))
print(f"OK model={name} dim={dim} ({time.time()-t0:.1f}s)")
PY
}
# 1) 먼저 offline 로드 — 이미 캐시에 있으면 즉시 끝 (네트워크 0, 프록시 hang 회피).
echo "→ 캐시 확인 (offline 로드, provider=$PROV)"
if _warm 1; then
  RESULT=cached
# 2) 캐시에 없을 때만 online 다운로드 (첫 회 수백MB~1GB, 프록시 통해).
else
  echo "  · 캐시에 없음 → 다운로드 시도 (online, 시간 소요)"
  if _warm 0; then
    RESULT=downloaded
  else
    RESULT=fail
  fi
fi
if [[ "$RESULT" != "fail" ]]; then
  CACHE="${HF_HOME:-$HOME/.cache/huggingface}/hub"
  SZ="$(du -sh "$CACHE" 2>/dev/null | cut -f1 || echo '?')"
  echo
  echo "✓ 모델 준비 완료 (HF 캐시: $CACHE, ~$SZ)"
  echo "  폐쇄망 서버로 옮기려면:"
  echo "    bash deploy/apptainer/bundle.sh --with-model"
  echo "    → 생성된 *.model.tar.gz 를 타겟에서:"
  echo "      mkdir -p ~/.cache/huggingface/hub && tar -xzf *.model.tar.gz -C ~/.cache/huggingface/hub"
else
  echo >&2
  echo "[ERROR] 모델 다운로드/로드 실패." >&2
  echo "        점검:" >&2
  echo "          - 인터넷/프록시: 이 서버가 huggingface.co 에 닿아야 함" >&2
  echo "            (사내망 → .env 의 HTTPS_PROXY/BUILD_PROXY_HTTPS)" >&2
  echo "          - 디스크: HF 캐시에 수백MB~1GB 필요. df -h ~" >&2
  echo "          - 차원 정합: EMBEDDING_PROVIDER vs EMBEDDING_DIM (e5_base=768)" >&2
  echo "          - 우회: .env 의 EMBEDDING_PROVIDER=hash (모델 불필요, 품질↓)" >&2
  exit 1
fi

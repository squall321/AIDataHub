#!/usr/bin/env bash
# mcp_scripts 예제 — cross-encoder rerank 모델 사전 캐시 다운로드.
#
# 사용 시나리오:
#   타겟 서버 (폐쇄망 가능) 에서 한 번 호출해 ``BAAI/bge-reranker-v2-m3`` 가중치를
#   로컬 모델 디렉토리에 미리 받아둔다. 이후 AIDH_RERANK_PROVIDER=bge_m3 로
#   API 재기동하면 즉시 사용 가능 (첫 호출 latency 폭증 회피).
#
# 만약 huggingface_hub 가 venv 에 없으면 user-space install (root 불필요).
# 외부망 차단 환경이면 미리 받은 .safetensors 묶음을 풀어 두면 됨.
#
# 매니페스트: fetch_rerank_model.mcp.yaml
set -euo pipefail

MODEL_ID="BAAI/bge-reranker-v2-m3"
DEST_ROOT="${AIDH_MODELS_DIR:-/opt/models}"
FORCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model-id)  MODEL_ID="$2";   shift 2 ;;
    --dest-root) DEST_ROOT="$2";  shift 2 ;;
    --force)     FORCE=1;         shift   ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# 로컬 경로 — embedding.py 의 _local_or_repo 정책에 맞춰 basename 디렉토리.
BASENAME="$(echo "$MODEL_ID" | awk -F/ '{print $NF}')"
DEST_DIR="$DEST_ROOT/$BASENAME"

if [[ -f "$DEST_DIR/config.json" && "$FORCE" != "1" ]]; then
  printf '{"status":"already_cached","path":"%s"}\n' "$DEST_DIR"
  exit 0
fi

mkdir -p "$DEST_DIR"

# venv python 우선, 없으면 system python3.
PY="${AIDH_PY:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "/opt/aidh/api_server/.venv/bin/python" ]]; then
    PY="/opt/aidh/api_server/.venv/bin/python"
  else
    PY="$(command -v python3)"
  fi
fi
if [[ -z "$PY" ]]; then
  echo "python interpreter not found" >&2
  exit 1
fi

# huggingface_hub 로 snapshot_download. 없으면 user-space pip install.
if ! "$PY" -c "import huggingface_hub" >/dev/null 2>&1; then
  "$PY" -m pip install --user huggingface_hub >&2
fi

"$PY" - "$MODEL_ID" "$DEST_DIR" <<'PY' 2>&1 1>&2
import json, sys
from huggingface_hub import snapshot_download
mid, dest = sys.argv[1], sys.argv[2]
path = snapshot_download(
    repo_id=mid,
    local_dir=dest,
    local_dir_use_symlinks=False,
)
print(f"downloaded to {path}", file=sys.stderr)
PY

# JSON 응답 (return.format=json 매니페스트 정합)
printf '{"status":"downloaded","model_id":"%s","path":"%s"}\n' "$MODEL_ID" "$DEST_DIR"

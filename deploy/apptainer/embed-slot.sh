#!/usr/bin/env bash
# AI Data Hub — 임베딩 슬롯 백필 · 색인 · 상태를 API 와 같은 환경(.env · venv · 오프라인 모델)으로 돌린다.
#
#   bash deploy/apptainer/embed-slot.sh status --slot m3                  # 남은 행 수(읽기 전용)
#   bash deploy/apptainer/embed-slot.sh fill   --slot m3                  # 빈 행 채우기(앞에 두고 — 이어하기)
#   bash deploy/apptainer/embed-slot.sh fill   --slot m3 --table record_sections --limit 2000
#   bash deploy/apptainer/embed-slot.sh index  --slot m3 [--maintenance-work-mem 4GB] [--workers 2]
#
# 본체는 api.scripts.embed_backfill(fill · status) · api.scripts.embed_index(index)다. 인자는 그대로 넘긴다
# (각 --help 참조). DATABASE_URL 은 start_api.sh 가 쓴 api_server/.env 에서, EMBEDDING_PROVIDER · EMBEDDING_DIM ·
# AIDH_BGE_M3_* 는 deploy/apptainer/.env 에서 온다. 절차는 docs/embedder-bge-m3.md.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

cmd="${1:-}"
shift || true
case "$cmd" in
  status) mod=api.scripts.embed_backfill; set -- --status "$@" ;;
  fill)   mod=api.scripts.embed_backfill ;;
  index)  mod=api.scripts.embed_index ;;
  *) echo "사용: bash $0 {status|fill|index} --slot m3 [인자…]" >&2; exit 2 ;;
esac

VENV_PY="$API_DIR/.venv/bin/python"
[[ -x "$VENV_PY" ]] || { echo "[ERROR] venv 없음($VENV_PY) — 먼저 bash deploy/apptainer/start_api.sh" >&2; exit 1; }
[[ -f "$API_DIR/.env" ]] || { echo "[ERROR] $API_DIR/.env 없음(DATABASE_URL) — 먼저 start_api.sh" >&2; exit 1; }

cd "$API_DIR"
export PYTHONPATH="$API_DIR/src"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
exec "$VENV_PY" -m "$mod" "$@"

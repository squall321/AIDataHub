#!/usr/bin/env bash
# AI Data Hub — Apptainer 이미지 빌드 (PG + pgvector).
# 멱등: 이미 있으면 skip, --force 시 재빌드.
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env
export_proxy
require_apptainer

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

build_or_pull() {
  local sif="$1" src="$2" def="${3:-}"
  if [ "$FORCE" -eq 1 ] || [ ! -f "$sif" ]; then
    if [ -n "$def" ]; then
      echo "→ build $(basename "$sif") from $(basename "$def")"
      apptainer build --force "$sif" "$def"
    else
      echo "→ pull  $(basename "$sif") from $src"
      apptainer pull --force "$sif" "$src"
    fi
  else
    echo "✓ skip  $(basename "$sif") (exists)"
  fi
}

# 1) base 이미지 pull (Docker Hub → SIF 변환)
build_or_pull "$APPT_DIR/postgres-base.sif" "docker://pgvector/pgvector:pg16"

# 2) wrapper (startscript 추가) — instance start 가능하게
build_or_pull "$APPT_DIR/postgres.sif" "" "$APPT_DIR/postgres.def"

echo
echo "✓ images ready in $APPT_DIR"

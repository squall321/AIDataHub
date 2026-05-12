#!/usr/bin/env bash
# AI Data Hub — Apptainer 이미지 빌드 (PG + pgvector).
# 동작 우선순위:
#   1) SIF 가 이미 있으면 그것 우선 (skip).  --force 시 재빌드.
#   2) 빌드/풀 1차 시도는 현재 env 그대로 (HTTPS_PROXY 가 있으면 적용, 없으면 직통).
#   3) 1차 실패 + BUILD_PROXY_HTTPS 설정 시 폴백 프록시 주입해 자동 재시도.
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env
export_proxy
require_apptainer

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

# ── 폴백 프록시 적용해 명령 재실행하는 헬퍼 ────────────────────────────────
# 1차 실패 → BUILD_PROXY_HTTPS / BUILD_PROXY_HTTP 가 있으면 그것을 서브셸 env 에 주입해
# 동일 명령 재실행. 둘 다 비어 있으면 폴백 시도 안 함.
_run_with_fallback() {
  # $@ = 실행할 명령 (apptainer pull ... 또는 apptainer build ...)
  if "$@"; then
    return 0
  fi
  local rc=$?
  local fb_https="${BUILD_PROXY_HTTPS:-}"
  local fb_http="${BUILD_PROXY_HTTP:-${BUILD_PROXY_HTTPS:-}}"
  if [[ -z "$fb_https" && -z "$fb_http" ]]; then
    echo "[ERROR] 1차 시도 실패 (rc=$rc) — BUILD_PROXY_HTTPS 미설정이라 폴백 없음." >&2
    echo "        .env 에 BUILD_PROXY_HTTPS 설정 후 다시 실행하세요." >&2
    return "$rc"
  fi
  echo "[WARN] 1차 실패 (rc=$rc) — BUILD_PROXY 적용 후 재시도..."
  echo "       BUILD_PROXY_HTTPS=$fb_https"
  echo "       BUILD_PROXY_HTTP =$fb_http"
  # NO_PROXY 는 localhost 등 우회 유지.
  local np_extra="localhost,127.0.0.1,::1"
  local np="${NO_PROXY:-$np_extra}"
  if [[ ",$np," != *",localhost,"* ]]; then np="$np,$np_extra"; fi
  # 서브셸에 env 주입해 재실행 — 호스트 셸의 export 값은 건드리지 않는다.
  env \
    HTTPS_PROXY="$fb_https"  https_proxy="$fb_https" \
    HTTP_PROXY="$fb_http"    http_proxy="$fb_http" \
    NO_PROXY="$np"           no_proxy="$np" \
    "$@"
}

build_or_pull() {
  local sif="$1" src="$2" def="${3:-}"
  # (1) 우선순위 1 — 기존 SIF 가 있으면 그대로 사용.
  if [ "$FORCE" -eq 0 ] && [ -f "$sif" ]; then
    echo "✓ skip  $(basename "$sif") (exists — using pre-built image)"
    return 0
  fi
  # (2)/(3) — 빌드/풀 시도 (폴백 포함).
  if [ -n "$def" ]; then
    echo "→ build $(basename "$sif") from $(basename "$def")"
    _run_with_fallback apptainer build --force "$sif" "$def"
  else
    echo "→ pull  $(basename "$sif") from $src"
    _run_with_fallback apptainer pull --force "$sif" "$src"
  fi
}

# 1) base 이미지 pull (Docker Hub → SIF 변환)
build_or_pull "$APPT_DIR/postgres-base.sif" "docker://pgvector/pgvector:pg16"

# 2) wrapper (startscript 추가) — instance start 가능하게
build_or_pull "$APPT_DIR/postgres.sif" "" "$APPT_DIR/postgres.def"

echo
echo "✓ images ready in $APPT_DIR"
